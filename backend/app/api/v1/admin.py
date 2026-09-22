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

from fastapi import APIRouter, Header, Query
from sqlalchemy import select

from app.api.deps import ProvidersDep, SettingsDep, TemporalClientDep
from app.core.config import Settings
from app.core.errors import AppError, InvalidInputError, NotFoundError
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.models import Agent, AgentConfig, ProvisioningRun, Tenant
from app.models.enums import (
    AgentStatus,
    ProvisioningStatus,
    ProvisioningStep,
)
from app.provisioning.compensation import compensate_run
from app.schemas.portal import SmsReviewDecision, SmsStateView
from app.schemas.views import (
    ActionResult,
    AgentConfigDetailView,
    AgentConfigView,
    RunSummaryView,
)
from app.services.config_publishing import push_config_to_agent
from app.services.config_versions import ConfigVersionService
from app.services.provisioning_retry import reset_run_for_retry, restart_workflow
from app.services.sms_compliance import SmsComplianceService

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
    temporal: TemporalClientDep,
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

    reset = await reset_run_for_retry(session, run=run, tenant=tenant, from_step=from_step)
    await session.commit()

    if settings.uses_temporal:
        # The previous execution has closed — it either failed or was
        # compensated — so a fresh one is started against the same run id.
        # Temporal rejects a duplicate id for a *running* execution, which is
        # what stops an impatient operator clicking retry twice from producing
        # two orchestrators over one run.
        #
        # The step rows were just reset above, so the new execution re-walks the
        # sequence from the first step that is not SUCCEEDED. It cannot skip the
        # money gate: `purchase_number` re-checks entitlement itself.
        await restart_workflow(temporal, settings, run, tenant)

    logger.info("run reset for retry", extra={"run_id": str(run.id), "steps_reset": len(reset)})
    return ActionResult(
        ok=True, detail=f"reset {len(reset)} step(s): {', '.join(reset) or 'none'}", run_id=run.id
    )


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

    if agent.is_shared:
        # Refused, not quietly skipped. Pushing this tenant's prompt onto a
        # shared vertical agent would overwrite the prompt every other tenant on
        # that vertical is being served by — one operator click causing a
        # fleet-wide incident. A shared agent's prompt is changed deliberately,
        # as a fleet operation, never as a side effect of fixing one customer.
        raise InvalidInputError(
            "this tenant is served by a shared vertical agent, which must not be "
            "overwritten with one tenant's configuration",
            details={"tenant_id": str(tenant_id), "agent_id": agent.elevenlabs_agent_id},
        )

    config = (
        await session.execute(
            select(AgentConfig)
            .where(AgentConfig.tenant_id == tenant_id)
            .where(AgentConfig.is_live.is_(True))
        )
    ).scalar_one_or_none()
    if config is None:
        raise NotFoundError("tenant has no live config", details={"tenant_id": str(tenant_id)})

    await push_config_to_agent(
        settings=settings,
        voice=providers.elevenlabs,
        tenant=tenant,
        agent=agent,
        config=config,
    )
    await session.commit()

    logger.info(
        "agent resynced from the database",
        extra={"tenant_id": str(tenant_id), "config_version": config.version},
    )
    return ActionResult(ok=True, detail=f"agent resynced to config v{config.version}")


@router.get(
    "/tenants/{tenant_id}/configs",
    response_model=list[AgentConfigView],
    summary="The history of a tenant's agent configurations",
)
async def list_configs(
    tenant_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    x_admin_key: str | None = Header(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AgentConfigView]:
    """Every version this tenant has had, newest first.

    The audit answer to "what have we been telling this agent, and since when?".
    """
    _authorize(settings, x_admin_key)

    configs = await ConfigVersionService(session).history(tenant_id, limit=limit)
    return [_config_view(config) for config in configs]


@router.get(
    "/tenants/{tenant_id}/configs/{version}",
    response_model=AgentConfigDetailView,
    summary="One agent configuration version, including its prompt",
)
async def get_config(
    tenant_id: uuid.UUID,
    version: int,
    session: SessionDep,
    settings: SettingsDep,
    x_admin_key: str | None = Header(default=None),
) -> AgentConfigDetailView:
    _authorize(settings, x_admin_key)

    config = await ConfigVersionService(session).get_version(tenant_id, version)
    if config is None:
        raise NotFoundError(
            "no such config version",
            details={"tenant_id": str(tenant_id), "version": version},
        )
    return AgentConfigDetailView(
        **_config_view(config).model_dump(),
        system_prompt=config.system_prompt,
        first_message=config.first_message,
        model_params=config.model_params_json,
    )


@router.post(
    "/tenants/{tenant_id}/configs/{version}/rollback",
    response_model=ActionResult,
    summary="Make an earlier configuration version live again",
)
async def rollback_config(
    tenant_id: uuid.UUID,
    version: int,
    session: SessionDep,
    settings: SettingsDep,
    x_admin_key: str | None = Header(default=None),
) -> ActionResult:
    """Roll back to a previous prompt.

    Deliberately does **not** call ElevenLabs. Moving the flag is the whole
    operation and it must succeed even when the vendor is unreachable — which
    is exactly when retiring a bad prompt is most urgent. Pushing the restored
    prompt to the vendor is the separate, retryable `resync-agent` action, and
    until it runs the vendor is simply out of date, which is a discrepancy the
    resync fixes rather than a loss.
    """
    _authorize(settings, x_admin_key)

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("tenant not found", details={"tenant_id": str(tenant_id)})

    config = await ConfigVersionService(session).rollback_to(tenant_id=tenant_id, version=version)
    await session.commit()

    return ActionResult(
        ok=True,
        detail=(f"config v{config.version} is live; run resync-agent to push it to the vendor"),
    )


def _config_view(config: AgentConfig) -> AgentConfigView:
    return AgentConfigView(
        version=config.version,
        is_live=config.is_live,
        generated_by=config.generated_by.value,
        generator_detail=config.generator_detail,
        template_version=config.template_version,
        voice_id=config.voice_id,
        created_at=config.created_at,
    )


@router.post(
    "/tenants/{tenant_id}/sms/review",
    response_model=SmsStateView,
    summary="Record the outcome of an SMS registration review",
)
async def review_sms_registration(
    tenant_id: uuid.UUID,
    decision: SmsReviewDecision,
    session: SessionDep,
    settings: SettingsDep,
    x_admin_key: str | None = Header(default=None),
) -> SmsStateView:
    """Move a submitted registration through review.

    The operator's half of SMS compliance until submission to Twilio is
    automated: record what Trust Hub decided. Transitions that skip a step are
    refused, a rejection needs a reason the customer can act on, and "enabled"
    needs the messaging service the number was attached to.
    """
    _authorize(settings, x_admin_key)
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("tenant not found", details={"tenant_id": str(tenant_id)})
    return await SmsComplianceService(session).record_review(tenant, decision)
