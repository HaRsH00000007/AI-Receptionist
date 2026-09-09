"""The provisioning state machine.

Executes **one step per call** and returns. The worker loops; the engine does
not. That split is what makes the whole thing restart-safe: every decision the
engine makes is written to Postgres before it returns, so a process that dies
mid-loop loses at most the step it was running, and that step's row still says
RUNNING with an attempt count.

Transaction shape, in three commits:

1. mark the step RUNNING and bump its attempt — committed *before* the work, so
   a crash leaves evidence rather than silence;
2. run the step (which may commit its own durable intent, as the purchase step
   does before spending money);
3. record the outcome and schedule what happens next.

Each step maps 1:1 onto a future Temporal activity; the engine is the part that
Temporal would replace (docs/00_DECISIONS.md section 4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.correlation import reset_correlation_id, set_correlation_id
from app.core.errors import AppError, BillingBlockedError
from app.core.logging import get_logger
from app.models import ProvisioningRun, ProvisioningStepRecord, Tenant
from app.models.enums import (
    STEP_SEQUENCE,
    TERMINAL_RUN_STATUSES,
    ProvisioningStatus,
    ProvisioningStep,
    StepStatus,
    TenantStatus,
    status_after,
)
from app.providers.registry import Providers
from app.provisioning.compensation import compensate_run
from app.provisioning.context import StepContext
from app.provisioning.registry import implementation_for
from app.provisioning.retry import next_attempt_at, should_retry
from app.services.idempotency import step_idempotency_key
from app.services.notifications import NotificationService

logger = get_logger(__name__)


class Selection(StrEnum):
    """Why the engine did or did not have work to do.

    ``COMPLETE`` and ``BLOCKED`` must never be confused: the first means every
    step succeeded and the run is finished, the second means a step is held by
    another worker or has failed terminally. Collapsing them into "no record"
    is how a blocked run gets marked ACTIVE without ever provisioning anything.
    """

    READY = "ready"
    BLOCKED = "blocked"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """What one engine call did. Returned for tests and the worker's logs."""

    run_id: uuid.UUID
    step: ProvisioningStep | None
    outcome: str  # advanced | retrying | failed | finished | idle
    status: ProvisioningStatus


async def claim_runs(
    session: AsyncSession, *, batch_size: int, lease_s: int, now: datetime | None = None
) -> list[uuid.UUID]:
    """Take ownership of up to ``batch_size`` runs that are due.

    ``FOR UPDATE SKIP LOCKED`` lets several workers poll the same table without
    tripping over each other — the second worker skips the locked rows instead
    of blocking.

    The row lock is released immediately; what actually keeps another worker off
    a claimed run is the *lease*: ``next_attempt_at`` is pushed into the future,
    so the run is not due again until the lease expires. If this worker dies
    mid-step, the lease lapses and the run is picked up again. That is the whole
    crash-recovery story, and it needs no external lock service.
    """
    moment = now or datetime.now(UTC)

    result = await session.execute(
        select(ProvisioningRun)
        .where(ProvisioningRun.status.notin_(tuple(TERMINAL_RUN_STATUSES)))
        .where(
            (ProvisioningRun.next_attempt_at.is_(None))
            | (ProvisioningRun.next_attempt_at <= moment)
        )
        .order_by(ProvisioningRun.next_attempt_at.nulls_first(), ProvisioningRun.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    runs = list(result.scalars().all())

    lease_until = moment + timedelta(seconds=lease_s)
    for run in runs:
        run.next_attempt_at = lease_until
        if run.started_at is None:
            run.started_at = moment
    await session.commit()

    return [run.id for run in runs]


class ProvisioningEngine:
    """Runs one step of one run."""

    def __init__(self, settings: Settings, providers: Providers) -> None:
        self.settings = settings
        self.providers = providers

    async def execute_next_step(self, session: AsyncSession, run_id: uuid.UUID) -> ExecutionReport:
        run = await session.get(ProvisioningRun, run_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            status = run.status if run else ProvisioningStatus.FAILED
            return ExecutionReport(run_id, None, "idle", status)

        tenant = await session.get(Tenant, run.tenant_id)
        if tenant is None:  # pragma: no cover - guarded by a foreign key
            return ExecutionReport(run_id, None, "idle", run.status)

        token = set_correlation_id(run.correlation_id)
        try:
            return await self._execute(session, run, tenant)
        finally:
            reset_correlation_id(token)

    async def _execute(
        self, session: AsyncSession, run: ProvisioningRun, tenant: Tenant
    ) -> ExecutionReport:
        if run.status is ProvisioningStatus.COMPENSATING:
            # A previous compensation was interrupted — the process died between
            # marking the run COMPENSATING and finishing the release. Without
            # this the run would be reclaimed forever, blocked on its failed
            # step, while the number it bought kept billing. Compensation is
            # idempotent by construction, so the safe move is to finish it.
            logger.warning(
                "resuming an interrupted compensation",
                extra={"run_id": str(run.id), "tenant_id": str(tenant.id)},
            )
            await compensate_run(
                session,
                run=run,
                tenant=tenant,
                providers=self.providers,
                settings=self.settings,
            )
            return ExecutionReport(run.id, None, "compensated", run.status)

        selection, record = await self._next_pending_step(session, run)

        if selection is Selection.COMPLETE:
            run.status = ProvisioningStatus.ACTIVE
            run.finished_at = datetime.now(UTC)
            run.next_attempt_at = None
            await session.commit()
            return ExecutionReport(run.id, None, "finished", run.status)

        if selection is Selection.BLOCKED or record is None:
            # Held by another worker, or failed terminally and awaiting an admin.
            # Either way this worker does nothing and, crucially, does not
            # declare the run finished.
            # Read before the rollback: afterwards the instance is expired, and
            # touching an attribute would be lazy IO the async session cannot do.
            run_id, status = run.id, run.status
            await session.rollback()
            return ExecutionReport(run_id, None, "idle", status)

        step = record.step_name

        # (1) Evidence before work.
        record.status = StepStatus.RUNNING
        record.attempt += 1
        # Stamped every attempt, not just the first. `_next_pending_step`
        # measures staleness from this value, so keeping the original start
        # time would make a step that had been retried for longer than
        # `provisioning_step_timeout_s` look permanently abandoned — and two
        # workers would then run it at once.
        record.started_at = datetime.now(UTC)
        run.current_step = step
        run.attempt = record.attempt
        await session.commit()

        logger.info(
            "step starting",
            extra={
                "run_id": str(run.id),
                "tenant_id": str(tenant.id),
                "step": step.value,
                "attempt": record.attempt,
            },
        )

        # Captured before the work, because the failure path rolls back and
        # must re-read these rows rather than touch objects left in a bad state.
        ids = (run.id, tenant.id, record.id)

        # (2) The work itself.
        try:
            result = await implementation_for(step)(
                StepContext(
                    session=session,
                    settings=self.settings,
                    providers=self.providers,
                    tenant=tenant,
                    run=run,
                    record=record,
                )
            )
        except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
            return await self._handle_failure(session, *ids, exc)

        # (3) Outcome.
        record.status = StepStatus.SUCCEEDED
        record.request_json = result.request
        record.response_json = result.response
        record.error = None
        record.finished_at = datetime.now(UTC)

        run.status = status_after(step)
        run.last_error = None
        run.attempt = 0
        if run.status is ProvisioningStatus.ACTIVE:
            run.finished_at = datetime.now(UTC)
            run.next_attempt_at = None
        else:
            # Due immediately: the next step should start on the next poll.
            run.next_attempt_at = None
        await session.commit()

        logger.info(
            "step succeeded",
            extra={"run_id": str(run.id), "step": step.value, "status": run.status.value},
        )
        outcome = "finished" if run.status is ProvisioningStatus.ACTIVE else "advanced"
        return ExecutionReport(run.id, step, outcome, run.status)

    # ---- failure ---------------------------------------------------------
    async def _handle_failure(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        tenant_id: uuid.UUID,
        record_id: uuid.UUID,
        exc: Exception,
    ) -> ExecutionReport:
        # The step may have left the session dirty or its objects expired, so
        # everything is re-read by primary key. Merging instead would try to
        # lazy-load attributes of a rolled-back object, which is IO in a place
        # the async session cannot perform it.
        await session.rollback()
        run = await session.get(ProvisioningRun, run_id)
        tenant = await session.get(Tenant, tenant_id)
        record = await session.get(ProvisioningStepRecord, record_id)
        if run is None or tenant is None or record is None:  # pragma: no cover
            raise exc

        message = _error_message(exc)
        record.error = message
        run.last_error = message

        # A billing refusal is not a failure. Nothing is broken, nothing was
        # half-done, and no compensation is owed -- the tenant simply has not
        # paid yet. Failing the run here would release resources for a customer
        # who is one card entry away from being entitled, so it is parked
        # instead. Handled before the retry ladder so it never consumes the
        # attempt budget a genuine vendor timeout needs.
        if isinstance(exc, BillingBlockedError):
            return await self._park_for_billing(session, run, record, message)

        retry = should_retry(
            exc, attempt=record.attempt, max_attempts=self.settings.provisioning_max_attempts
        )

        if retry:
            record.status = StepStatus.PENDING
            run.next_attempt_at = next_attempt_at(record.attempt, self.settings.backoff_schedule_s)
            await session.commit()
            logger.warning(
                "step failed, will retry",
                extra={
                    "run_id": str(run.id),
                    "step": record.step_name.value,
                    "attempt": record.attempt,
                    "retryable": True,
                    "error": message,
                },
            )
            return ExecutionReport(run.id, record.step_name, "retrying", run.status)

        record.status = StepStatus.FAILED
        record.finished_at = datetime.now(UTC)
        run.status = ProvisioningStatus.FAILED
        run.next_attempt_at = None
        tenant.status = TenantStatus.FAILED
        await session.commit()

        logger.error(
            "step failed terminally",
            extra={
                "run_id": str(run.id),
                "tenant_id": str(tenant.id),
                "step": record.step_name.value,
                "attempt": record.attempt,
                "error": message,
            },
        )

        await self._after_terminal_failure(session, run, tenant, message)
        return ExecutionReport(run.id, record.step_name, "failed", run.status)

    async def _park_for_billing(
        self,
        session: AsyncSession,
        run: ProvisioningRun,
        record: ProvisioningStepRecord,
        message: str,
    ) -> ExecutionReport:
        """Park a run whose tenant is not entitled, ready to resume.

        The step goes back to PENDING and the run to BILLING_BLOCKED — an
        in-flight status, so the tenant keeps owning this run and a duplicate
        signup cannot start a second one alongside it.

        ``next_attempt_at`` is set to a slow poll rather than left null. The
        Stripe webhook un-parks the run the moment entitlement is granted, and
        that is the fast path — but a webhook that is never delivered must not
        strand a paying customer forever, so the run also re-checks on its own.
        Belt and braces, because the failure mode is a customer who paid and
        never got a phone number.

        The tenant's own status is deliberately left alone: it stays PENDING,
        never ACTIVE (which would claim a working receptionist that does not
        exist) and never FAILED (which would be untrue and would show the
        customer an error for something they can fix by paying).
        """
        record.status = StepStatus.PENDING
        # Not counted as an attempt: the run has not failed, and letting a long
        # unpaid period exhaust the budget would turn "hasn't paid yet" into a
        # permanent failure.
        record.attempt = max(record.attempt - 1, 0)
        run.status = ProvisioningStatus.BILLING_BLOCKED
        run.next_attempt_at = datetime.now(UTC) + timedelta(
            seconds=self.settings.billing_recheck_interval_s
        )
        await session.commit()

        logger.warning(
            "provisioning parked: tenant is not entitled",
            extra={
                "run_id": str(run.id),
                "tenant_id": str(run.tenant_id),
                "step": record.step_name.value,
                "error": message,
            },
        )
        return ExecutionReport(run.id, record.step_name, "billing_blocked", run.status)

    async def _after_terminal_failure(
        self, session: AsyncSession, run: ProvisioningRun, tenant: Tenant, reason: str
    ) -> None:
        """Release anything the failed run bought, then tell the owner."""
        await compensate_run(
            session, run=run, tenant=tenant, providers=self.providers, settings=self.settings
        )
        try:
            await NotificationService(self.providers.email, self.settings).send_failure(
                tenant=tenant, reason=reason
            )
        except AppError as exc:
            logger.warning(
                "failure notification could not be sent",
                extra={"tenant_id": str(tenant.id), "code": exc.code},
            )

    # ---- step selection --------------------------------------------------
    async def _next_pending_step(
        self, session: AsyncSession, run: ProvisioningRun
    ) -> tuple[Selection, ProvisioningStepRecord | None]:
        """The first step in sequence that has not succeeded.

        Derived from the sequence rather than from the run's status, so a step
        row that was reset by an admin retry is picked up without anyone having
        to also rewind the run's status by hand.
        """
        result = await session.execute(
            select(ProvisioningStepRecord).where(ProvisioningStepRecord.run_id == run.id)
        )
        by_name = {record.step_name: record for record in result.scalars()}

        stale_before = datetime.now(UTC) - timedelta(
            seconds=self.settings.provisioning_step_timeout_s
        )

        for step in STEP_SEQUENCE:
            record = by_name.get(step)
            if record is None:
                # A run created before this step existed. Fill the gap rather
                # than skip it, so history stays complete.
                record = ProvisioningStepRecord(
                    run_id=run.id,
                    step_name=step,
                    status=StepStatus.PENDING,
                    idempotency_key=step_idempotency_key(run.id, step),
                    attempt=0,
                )
                session.add(record)
                await session.flush()
                return Selection.READY, record

            if record.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED):
                continue
            if record.status is StepStatus.FAILED:
                # Terminal until an admin resets it.
                return Selection.BLOCKED, None
            if record.status is StepStatus.RUNNING:
                started = record.started_at
                if started is not None and started > stale_before:
                    # Another worker holds it, or it is genuinely still running.
                    return Selection.BLOCKED, None
                logger.warning(
                    "reclaiming a step orphaned by a crash",
                    extra={"run_id": str(run.id), "step": step.value},
                )
            return Selection.READY, record
        return Selection.COMPLETE, None


def _error_message(exc: Exception) -> str:
    """A message safe to persist and show in the admin panel.

    Uses the error's own code and message for known failures. An unexpected
    exception contributes its type and text but never a traceback — the log has
    the traceback; the customer-facing record does not need one.
    """
    if isinstance(exc, AppError):
        detail = ", ".join(f"{key}={value}" for key, value in sorted(exc.details.items()))
        return f"[{exc.code}] {exc.message}" + (f" ({detail})" if detail else "")
    return f"[unexpected] {type(exc).__name__}: {exc}"[:1000]
