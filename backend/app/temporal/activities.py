"""Temporal activities — every side effect the workflow can cause.

**The step implementations are reused verbatim.** An activity does not
reimplement provisioning; it opens a session, does the same three-phase
bookkeeping the polling engine did, and calls
``app.provisioning.registry.implementation_for(step)``. That is the whole point
of M4: the money-safety guards built in M10 and the POC — the billing gate
immediately before the purchase, the Twilio adoption guard, the durable PENDING
row, the per-step idempotency keys — are the *same code*, unchanged. Temporal
changes when a step runs and how failure is handled. It does not change what a
step does.

Which matters because Temporal retries aggressively by design. An activity that
times out is retried even though it may have completed; that is normal, and it
is safe here only because each step already asks the vendor "did I already do
this?" before acting.

Two rules this module enforces:

**Errors are classified, never guessed.** Every failure leaves as an
``ApplicationError`` carrying the application's own error code and, crucially,
``non_retryable`` derived from :attr:`AppError.retryable`. A validation failure,
an authorization failure or a billing block is marked non-retryable so Temporal
stops immediately rather than burning its schedule on something that will fail
identically forever.

**Secrets never leave.** Activities take identifiers and load everything else
themselves. Nothing returned from here is a credential, and the error messages
that reach workflow history are the redacted ``AppError`` messages, not raw
vendor responses.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.core.config import Settings
from app.core.correlation import reset_correlation_id, set_correlation_id
from app.core.errors import AppError, BillingBlockedError
from app.core.logging import get_logger
from app.models import ProvisioningRun, ProvisioningStepRecord, Tenant
from app.models.enums import (
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
from app.services.idempotency import step_idempotency_key
from app.services.notifications import NotificationService
from app.temporal.shared import (
    ERROR_BILLING_BLOCKED,
    CompensationOutcome,
    ProvisionInput,
    StepInput,
    StepOutcome,
)

logger = get_logger(__name__)


def _to_application_error(exc: Exception) -> ApplicationError:
    """Translate an application error into one Temporal can act on.

    ``non_retryable`` is the important half. Temporal's default is to retry
    anything, which is right for a vendor timeout and badly wrong for a rejected
    signup or an unpaid tenant — it would hammer a permanent failure until the
    schedule ran out and turn a clear error into a slow one.

    An *unexpected* exception is left retryable. A crash is more often a blip
    than a permanent truth, and the retry policy's maximum-attempts cap stops it
    looping forever.
    """
    if isinstance(exc, AppError):
        return ApplicationError(
            exc.message,
            # The application's own code becomes the Temporal error type, which
            # is what the workflow branches on.
            type=exc.code,
            non_retryable=not exc.retryable,
        )
    return ApplicationError(f"{type(exc).__name__}: {exc}", type="unexpected_error")


class ProvisioningActivities:
    """Activities bound to one process's database and provider handles.

    A class rather than module-level functions so the session factory and the
    provider bundle are injected rather than reached for through a global — the
    same reason the rest of the application avoids ambient state, and what lets
    a test register activities backed by fakes.
    """

    def __init__(
        self,
        settings: Settings,
        providers: Providers,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.settings = settings
        self.providers = providers
        self.session_factory = session_factory

    # ---------------------------------------------------------------------
    @activity.defn(name="execute_provisioning_step")
    async def execute_step(self, payload: StepInput) -> StepOutcome:
        """Run one provisioning step and record it.

        Mirrors the polling engine's three-phase shape:

        1. mark the step RUNNING and bump its attempt — committed *before* the
           work, so a crash leaves evidence rather than silence;
        2. run the step, which may commit its own durable intent (the purchase
           step writes a PENDING ``phone_numbers`` row before spending);
        3. record the outcome and advance the run's status.

        Phase 1 is not redundant with Temporal's own history. The admin panel
        and the status page read ``provisioning_steps``, not workflow history,
        and an operator answering "what is this tenant doing right now?" must
        not have to open the Temporal UI.
        """
        run_id = uuid.UUID(payload.run_id)
        step = ProvisioningStep(payload.step)

        async with self.session_factory() as session:
            run, tenant, record = await self._load(session, run_id, step)
            token = set_correlation_id(run.correlation_id)
            try:
                # (1) Evidence before work.
                record.status = StepStatus.RUNNING
                record.attempt += 1
                record.started_at = datetime.now(UTC)
                run.current_step = step
                run.attempt = record.attempt
                await session.commit()

                info = activity.info()
                logger.info(
                    "step starting",
                    extra={
                        "run_id": str(run.id),
                        "tenant_id": str(tenant.id),
                        "step": step.value,
                        "attempt": record.attempt,
                        # Correlates the database row with workflow history,
                        # which is what makes a 3am investigation short.
                        "workflow_id": info.workflow_id,
                        "activity_attempt": info.attempt,
                    },
                )

                # (2) The work. Unchanged from the polling path.
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
                except Exception as exc:
                    await self._record_failure(session, run_id, step, exc)
                    raise _to_application_error(exc) from exc

                # (3) Outcome.
                record.status = StepStatus.SUCCEEDED
                record.request_json = result.request
                record.response_json = result.response
                record.error = None
                record.finished_at = datetime.now(UTC)

                run.status = status_after(step)
                run.last_error = None
                run.attempt = 0
                # Temporal owns scheduling now, so the polling column is cleared
                # rather than set. Leaving a due timestamp behind would let the
                # legacy claim query pick the run up and run a second, competing
                # orchestrator over the same steps.
                run.next_attempt_at = None
                if run.status is ProvisioningStatus.ACTIVE:
                    run.finished_at = datetime.now(UTC)
                await session.commit()

                logger.info(
                    "step succeeded",
                    extra={
                        "run_id": str(run.id),
                        "step": step.value,
                        "status": run.status.value,
                    },
                )
                return StepOutcome(step=step.value, status=run.status.value)
            finally:
                reset_correlation_id(token)

    async def _record_failure(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        step: ProvisioningStep,
        exc: Exception,
    ) -> None:
        """Persist the error, then let Temporal decide what happens next.

        The step is left PENDING rather than FAILED. Temporal's retry policy is
        the authority on whether there will be another attempt, and marking the
        row FAILED here would tell the admin panel the run was dead while
        Temporal was still working on it. The workflow marks it FAILED once the
        retries are genuinely exhausted.
        """
        await session.rollback()
        run = await session.get(ProvisioningRun, run_id)
        record = await self._record_for(session, run_id, step)
        if run is None or record is None:  # pragma: no cover - foreign keys
            return

        message = _error_message(exc)
        record.error = message
        record.status = StepStatus.PENDING
        run.last_error = message
        await session.commit()

        logger.warning(
            "step failed",
            extra={
                "run_id": str(run_id),
                "step": step.value,
                "attempt": record.attempt,
                "retryable": exc.retryable if isinstance(exc, AppError) else True,
                "error": message,
            },
        )

    # ---------------------------------------------------------------------
    @activity.defn(name="mark_provisioning_blocked")
    async def mark_blocked(self, payload: StepInput) -> None:
        """Park a run whose tenant is not entitled.

        BILLING_BLOCKED is an in-flight status, not a terminal one: the tenant
        keeps owning the run, no compensation is owed, and nothing is released.
        The workflow stays alive and waits — see the workflow's billing loop.

        ``next_attempt_at`` is deliberately left null. Under Temporal the
        workflow's own timer decides when to look again; writing a due timestamp
        would invite the legacy polling claim query to pick the run up and run a
        second orchestrator alongside this one.
        """
        run_id = uuid.UUID(payload.run_id)
        async with self.session_factory() as session:
            run = await session.get(ProvisioningRun, run_id)
            if run is None:  # pragma: no cover - foreign keys
                return
            run.status = ProvisioningStatus.BILLING_BLOCKED
            run.next_attempt_at = None
            await session.commit()

        logger.warning(
            "provisioning parked: tenant is not entitled",
            extra={"run_id": payload.run_id, "workflow_id": activity.info().workflow_id},
        )

    @activity.defn(name="fail_provisioning_run")
    async def fail_run(self, payload: StepInput) -> None:
        """Mark the run and its step failed once retries are exhausted.

        Called by the workflow, not by the failing activity, because only the
        workflow knows the retry budget is spent. An activity that marked itself
        FAILED would show an operator a dead run that Temporal was still
        retrying.
        """
        run_id = uuid.UUID(payload.run_id)
        step = ProvisioningStep(payload.step) if payload.step else None

        async with self.session_factory() as session:
            run = await session.get(ProvisioningRun, run_id)
            if run is None:  # pragma: no cover - foreign keys
                return
            tenant = await session.get(Tenant, run.tenant_id)

            if step is not None:
                record = await self._record_for(session, run_id, step)
                if record is not None:
                    record.status = StepStatus.FAILED
                    record.finished_at = datetime.now(UTC)

            run.status = ProvisioningStatus.FAILED
            run.next_attempt_at = None
            run.finished_at = datetime.now(UTC)
            if tenant is not None:
                tenant.status = TenantStatus.FAILED
            await session.commit()

        logger.error(
            "provisioning failed terminally",
            extra={"run_id": payload.run_id, "workflow_id": activity.info().workflow_id},
        )

    # ---------------------------------------------------------------------
    @activity.defn(name="compensate_provisioning_run")
    async def compensate(self, payload: ProvisionInput) -> CompensationOutcome:
        """Release whatever the failed run acquired.

        Delegates to the existing :func:`compensate_run`, which is already
        idempotent by construction — releasing an unknown SID and deleting a
        missing agent are both no-ops at the vendor. That property is what makes
        this safe as a Temporal activity, where a timeout can cause a second
        attempt after the first has already done the work.

        An interrupted compensation is therefore recoverable by simply running
        the activity again, which is exactly what Temporal does.
        """
        run_id = uuid.UUID(payload.run_id)
        async with self.session_factory() as session:
            run = await session.get(ProvisioningRun, run_id)
            if run is None:  # pragma: no cover - foreign keys
                return CompensationOutcome()
            tenant = await session.get(Tenant, run.tenant_id)
            if tenant is None:  # pragma: no cover - foreign keys
                return CompensationOutcome()

            token = set_correlation_id(run.correlation_id)
            try:
                report = await compensate_run(
                    session,
                    run=run,
                    tenant=tenant,
                    providers=self.providers,
                    settings=self.settings,
                )
            finally:
                reset_correlation_id(token)

        errors = report.get("errors") or []
        return CompensationOutcome(
            released_number=report.get("released_number"),  # type: ignore[arg-type]
            deleted_agent=report.get("deleted_agent"),  # type: ignore[arg-type]
            errors=len(errors),  # type: ignore[arg-type]
        )

    @activity.defn(name="notify_provisioning_failure")
    async def notify_failure(self, payload: ProvisionInput) -> None:
        """Tell the owner their setup did not complete.

        Failures here are swallowed. A provisioning run that has already failed
        must not be held open because the notification provider is also down —
        the compensation that stops money being spent matters more than the
        email, and the email is retried by its own machinery.
        """
        run_id = uuid.UUID(payload.run_id)
        async with self.session_factory() as session:
            run = await session.get(ProvisioningRun, run_id)
            if run is None:  # pragma: no cover - foreign keys
                return
            tenant = await session.get(Tenant, run.tenant_id)
            if tenant is None:  # pragma: no cover - foreign keys
                return
            reason = run.last_error or "provisioning failed"

        try:
            await NotificationService(self.providers.email, self.settings).send_failure(
                tenant=tenant, reason=reason
            )
        except AppError as exc:
            logger.warning(
                "failure notification could not be sent",
                extra={"run_id": payload.run_id, "code": exc.code},
            )

    @activity.defn(name="check_billing_entitlement")
    async def is_entitled(self, payload: ProvisionInput) -> bool:
        """Whether the tenant is entitled *right now*.

        Used only to decide whether the workflow's billing wait should end. It
        is emphatically **not** the money gate: the gate is re-evaluated inside
        ``purchase_number`` immediately before the vendor call, because
        entitlement can lapse between this check and the spend. Treating this as
        authorization would reintroduce exactly the race M10 closed.
        """
        from app.services.billing_gate import evaluate

        async with self.session_factory() as session:
            decision = await evaluate(session, self.settings, uuid.UUID(payload.tenant_id))
        return decision.allowed

    # ---- loading ---------------------------------------------------------
    async def _load(
        self, session: AsyncSession, run_id: uuid.UUID, step: ProvisioningStep
    ) -> tuple[ProvisioningRun, Tenant, ProvisioningStepRecord]:
        run = await session.get(ProvisioningRun, run_id)
        if run is None:
            raise ApplicationError(
                f"provisioning run {run_id} not found", type="not_found", non_retryable=True
            )
        tenant = await session.get(Tenant, run.tenant_id)
        if tenant is None:  # pragma: no cover - guarded by a foreign key
            raise ApplicationError(
                f"tenant for run {run_id} not found", type="not_found", non_retryable=True
            )

        record = await self._record_for(session, run_id, step)
        if record is None:
            # A run created before this step existed. Filled in rather than
            # skipped, so history stays complete — the same behaviour the
            # polling engine had.
            record = ProvisioningStepRecord(
                run_id=run_id,
                step_name=step,
                status=StepStatus.PENDING,
                idempotency_key=step_idempotency_key(run_id, step),
                attempt=0,
            )
            session.add(record)
            await session.flush()
        return run, tenant, record

    async def _record_for(
        self, session: AsyncSession, run_id: uuid.UUID, step: ProvisioningStep
    ) -> ProvisioningStepRecord | None:
        from sqlalchemy import select

        return (
            await session.execute(
                select(ProvisioningStepRecord)
                .where(ProvisioningStepRecord.run_id == run_id)
                .where(ProvisioningStepRecord.step_name == step)
                .limit(1)
            )
        ).scalar_one_or_none()

    def all_activities(self) -> list[Callable[..., Any]]:
        """Everything to register with the worker.

        Listed explicitly rather than discovered, so adding an activity is a
        deliberate act and a typo produces an import error rather than a
        workflow that hangs waiting for an activity nobody registered.
        """
        return [
            self.execute_step,
            self.mark_blocked,
            self.fail_run,
            self.compensate,
            self.notify_failure,
            self.is_entitled,
        ]


def _error_message(exc: Exception) -> str:
    """A message safe to persist and show. Never a traceback, never a secret."""
    if isinstance(exc, AppError):
        detail = ", ".join(f"{key}={value}" for key, value in sorted(exc.details.items()))
        return f"[{exc.code}] {exc.message}" + (f" ({detail})" if detail else "")
    return f"[unexpected] {type(exc).__name__}: {exc}"[:1000]


__all__ = [
    "ERROR_BILLING_BLOCKED",
    "BillingBlockedError",
    "ProvisioningActivities",
]
