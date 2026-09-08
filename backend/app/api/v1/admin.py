"""Admin actions.

The point of the whole system, per the plan: when a run fails at 3am someone
sees it at 9am with the error in front of them and a retry button, instead of
discovering a week later that leads were dropped.

Access is a shared secret in a header — not an auth system, and not pretending
to be one. Enough that retry and abandon are not open to the internet. When no
key is configured the routes are open in local/test and refused outright in a
production-like environment, so a forgotten key fails closed.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Header, Query
from sqlalchemy import select

from app.api.deps import ProvidersDep, SettingsDep
from app.core.config import Settings
from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.models import Agent, AgentConfig, ProvisioningRun, ProvisioningStepRecord, Tenant
from app.models.enums import (
    STEP_SEQUENCE,
    TERMINAL_RUN_STATUSES,
    AgentStatus,
    ProvisioningStatus,
    ProvisioningStep,
    StepStatus,
    TenantStatus,
)
from app.provisioning.compensation import compensate_run
from app.schemas.views import ActionResult, RunSummaryView
from app.services.idempotency import step_idempotency_key, tenant_resource_name

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminForbiddenError(AppError):
    code = "admin_forbidden"
    http_status = 403
    retryable = False


def _authorize(settings: Settings, provided: str | None) -> None:
    expected = settings.admin_api_key.get_secret_value()
    if expected:
        # Constant time: a plain `!=` leaks the length of the matching prefix.
        if not provided or not hmac.compare_digest(provided, expected):
            raise AdminForbiddenError("invalid or missing admin api key")
        return
    if settings.is_production_like:
        # Fail closed: an unset key in production must not mean "open".
        raise AdminForbiddenError("admin api key is not configured")


@router.get("/runs", response_model=list[RunSummaryView], summary="List provisioning runs")
async def list_runs(
    session: SessionDep,
    settings: SettingsDep,
    x_admin_key: str | None = Header(default=None),
    status_filter: ProvisioningStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[RunSummaryView]:
    _authorize(settings, x_admin_key)

    query = (
        select(ProvisioningRun, Tenant)
        .join(Tenant, Tenant.id == ProvisioningRun.tenant_id)
        .order_by(ProvisioningRun.created_at.desc())
        .limit(limit)
    )
    if status_filter is not None:
        query = query.where(ProvisioningRun.status == status_filter)

    rows = (await session.execute(query)).all()
    return [
        RunSummaryView(
            run_id=run.id,
            tenant_id=tenant.id,
            business_name=tenant.name,
            status=run.status,
            current_step=run.current_step.value if run.current_step else None,
            attempt=run.attempt,
            last_error=run.last_error,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )
        for run, tenant in rows
    ]


async def _load_run(session: SessionDep, run_id: uuid.UUID) -> ProvisioningRun:
    run = await session.get(ProvisioningRun, run_id)
    if run is None:
        raise NotFoundError("provisioning run not found", details={"run_id": str(run_id)})
    return run


@router.post("/runs/{run_id}/retry", response_model=ActionResult, summary="Retry a failed run")
async def retry_run(
    run_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    x_admin_key: str | None = Header(default=None),
    from_step: ProvisioningStep | None = Query(default=None),
) -> ActionResult:
    """Make a failed run eligible again.

    Failed steps are reset to PENDING and their attempt counters cleared. When
    ``from_step`` is given, that step and everything after it are reset *and
    given fresh idempotency keys* — because redoing a step deliberately is a new
    logical operation, not a retry of the old one, and reusing the key would let
    it adopt the very side effect the operator is trying to replace.
    """
    _authorize(settings, x_admin_key)
    run = await _load_run(session, run_id)

    tenant = await session.get(Tenant, run.tenant_id)
    if tenant is None:  # pragma: no cover - guarded by a foreign key
        raise NotFoundError("tenant not found", details={"run_id": str(run_id)})

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

    await session.commit()

    logger.info("run reset for retry", extra={"run_id": str(run.id), "steps_reset": len(reset)})
    return ActionResult(
        ok=True, detail=f"reset {len(reset)} step(s): {', '.join(reset) or 'none'}", run_id=run.id
    )


def _resume_status(run: ProvisioningRun) -> ProvisioningStatus:
    """Move a terminal run back into the machine without rewinding progress."""
    if run.status in TERMINAL_RUN_STATUSES:
        return ProvisioningStatus.DRAFT
    return run.status


@router.post(
    "/runs/{run_id}/abandon",
    response_model=ActionResult,
    summary="Abandon a run and release its vendor resources",
)
async def abandon_run(
    run_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
    x_admin_key: str | None = Header(default=None),
) -> ActionResult:
    """Stop the run and undo what it bought.

    The important half is releasing the Twilio number: an orphan bills monthly,
    forever, and nothing else in the system will notice.
    """
    _authorize(settings, x_admin_key)
    run = await _load_run(session, run_id)
    tenant = await session.get(Tenant, run.tenant_id)
    if tenant is None:  # pragma: no cover
        raise NotFoundError("tenant not found", details={"run_id": str(run_id)})

    report = await compensate_run(
        session, run=run, tenant=tenant, providers=providers, settings=settings
    )

    return ActionResult(
        ok=True,
        detail=(
            f"released={report.get('released_number') or 'none'}, "
            f"agent_deleted={report.get('deleted_agent') or 'none'}"
        ),
        run_id=run.id,
    )


@router.post(
    "/tenants/{tenant_id}/resync-agent",
    response_model=ActionResult,
    summary="Push the live config to the vendor again",
)
async def resync_agent(
    tenant_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
    x_admin_key: str | None = Header(default=None),
) -> ActionResult:
    """Reconcile ElevenLabs to our database.

    The database is the source of truth and the vendor object is a projection of
    it, so this is always safe to run: it overwrites the vendor's copy with the
    live config, never the other way round (docs/00_DECISIONS.md section 2).
    """
    _authorize(settings, x_admin_key)

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("tenant not found", details={"tenant_id": str(tenant_id)})

    agent = (
        await session.execute(
            select(Agent)
            .where(Agent.tenant_id == tenant_id)
            .where(Agent.status == AgentStatus.ACTIVE)
        )
    ).scalar_one_or_none()
    if agent is None or not agent.elevenlabs_agent_id:
        raise NotFoundError("tenant has no active agent", details={"tenant_id": str(tenant_id)})

    config = (
        await session.execute(
            select(AgentConfig)
            .where(AgentConfig.tenant_id == tenant_id)
            .where(AgentConfig.is_live.is_(True))
        )
    ).scalar_one_or_none()
    if config is None:
        raise NotFoundError("tenant has no live config", details={"tenant_id": str(tenant_id)})

    await providers.elevenlabs.update_agent(
        agent_id=agent.elevenlabs_agent_id,
        name=tenant_resource_name(tenant.id),
        system_prompt=config.system_prompt,
        first_message=config.first_message,
        voice_id=config.voice_id or settings.elevenlabs_default_voice_id,
    )

    agent.agent_config_id = config.id
    agent.synced_at = datetime.now(UTC)
    await session.commit()

    logger.info(
        "agent resynced from the database",
        extra={"tenant_id": str(tenant_id), "config_version": config.version},
    )
    return ActionResult(ok=True, detail=f"agent resynced to config v{config.version}")
