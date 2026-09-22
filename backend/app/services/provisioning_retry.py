"""Making a stopped provisioning run eligible again.

Two callers need this: the operator's retry button in the admin panel, and the
customer's own "try again" on the setup page. One implementation serves both so
that "what does a retry reset?" has exactly one answer — a customer retry that
quietly reset less (or more) than an operator one would be a second, untested
state machine.

The reset itself is always safe to repeat. Steps adopt their earlier side effects
by idempotency key, so re-walking a run never buys a second number; and the
money gate is re-checked by ``purchase_number`` itself, so a retry cannot walk
around it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from app.core.config import Settings
from app.core.logging import get_logger
from app.models import ProvisioningRun, ProvisioningStepRecord, Tenant
from app.models.enums import (
    STEP_SEQUENCE,
    TERMINAL_RUN_STATUSES,
    ProvisioningStatus,
    ProvisioningStep,
    StepStatus,
    TenantStatus,
)
from app.services.idempotency import step_idempotency_key
from app.temporal.client import start_provisioning

logger = get_logger(__name__)


async def reset_run_for_retry(
    session: AsyncSession,
    *,
    run: ProvisioningRun,
    tenant: Tenant,
    from_step: ProvisioningStep | None = None,
) -> list[str]:
    """Reset a run's failed steps so the orchestrator picks it up again.

    Failed steps are reset to PENDING and their attempt counters cleared. When
    ``from_step`` is given, that step and everything after it are reset *and
    given fresh idempotency keys* — because redoing a step deliberately is a new
    logical operation, not a retry of the old one, and reusing the key would let
    it adopt the very side effect the operator is trying to replace.

    Returns the names of the steps that were reset. Does not commit.
    """
    records = {
        record.step_name: record
        for record in (
            await session.execute(
                select(ProvisioningStepRecord).where(ProvisioningStepRecord.run_id == run.id)
            )
        ).scalars()
    }

    reset_from = STEP_SEQUENCE.index(from_step) if from_step else None
    reset: list[str] = []

    for index, step in enumerate(STEP_SEQUENCE):
        record = records.get(step)
        if record is None:
            continue
        forced = reset_from is not None and index >= reset_from
        if not forced and record.status is not StepStatus.FAILED:
            continue

        record.status = StepStatus.PENDING
        record.attempt = 0
        record.error = None
        record.finished_at = None
        record.started_at = None
        if forced:
            # New operation, new key.
            record.idempotency_key = step_idempotency_key(
                run.id, step, attempt_group=int(datetime.now(UTC).timestamp())
            )
        reset.append(step.value)

    run.status = ProvisioningStatus.DRAFT if reset_from == 0 else _resume_status(run)
    run.last_error = None
    run.attempt = 0
    run.finished_at = None
    run.next_attempt_at = None
    if tenant.status is TenantStatus.FAILED:
        tenant.status = TenantStatus.PENDING

    return reset


async def restart_workflow(
    temporal: Client | None,
    settings: Settings,
    run: ProvisioningRun,
    tenant: Tenant,
) -> None:
    """Start a new workflow execution for a reset run. Never fails the retry.

    The database reset is the part that matters and has already committed. If
    Temporal is unreachable the run is simply left eligible, and a later retry
    starts it — which is better than a 500 that leaves the caller unsure
    whether the reset happened.

    The previous execution has closed — it either failed or was compensated — so
    a fresh one is started against the same run id. Temporal rejects a duplicate
    id for a *running* execution, which is what stops a double-click producing
    two orchestrators over one run.
    """
    if temporal is None:
        logger.error(
            "temporal is unavailable; the run was reset but not restarted",
            extra={"run_id": str(run.id)},
        )
        return
    try:
        await start_provisioning(
            temporal,
            settings,
            run_id=run.id,
            tenant_id=tenant.id,
            correlation_id=run.correlation_id,
        )
    except Exception:
        logger.exception(
            "could not restart the provisioning workflow",
            extra={"run_id": str(run.id)},
        )


def _resume_status(run: ProvisioningRun) -> ProvisioningStatus:
    """Move a terminal run back into the machine without rewinding progress."""
    if run.status in TERMINAL_RUN_STATUSES:
        return ProvisioningStatus.DRAFT
    return run.status
