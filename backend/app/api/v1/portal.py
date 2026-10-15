"""The signed-in customer portal.

Everything the dashboard needs beyond the status-page reads in
:mod:`app.api.v1.tenants`: the Home numbers, a call with its transcript, the
contacts list, the configuration history, and the two things a customer can
*do* — change their receptionist, and retry a setup that stopped.

**Authorization is a membership, never a status grant.** Every route depends on
``RequireMember`` or stronger, which accepts only a signed-in session with an
accepted membership of the tenant in the path. The anonymous status grant that
opens the post-signup page opens none of these: a transcript is not something a
link in a browser history should reveal. A tenant the caller cannot see is a
404, never a 403 (see :class:`app.api.auth_deps.TenantAccess`).

Writes need ``admin`` or ``owner``. A member can read the dashboard; changing
what the receptionist says to callers is a decision for the people who run the
business.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import Float, cast, distinct, func, select

from app.api.auth_deps import RequireAdmin, RequireMember, RequireOwner
from app.api.deps import CacheDep, ProvidersDep, SettingsDep, TemporalClientDep
from app.core.errors import AuthorizationError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.models import Call, ProvisioningRun, Tenant
from app.models.enums import AuditAction, ProvisioningStatus, TenantStatus
from app.schemas.portal import (
    AgentSettingsUpdate,
    AgentUpdateResult,
    CallDetailView,
    ConfigVersionView,
    DashboardSummaryView,
    DeletionResultView,
    ReceptionistDeletion,
    RetryResult,
    TranscriptTurnView,
)
from app.schemas.views import TimezoneOptionView
from app.services.agent_settings import AgentSettingsService
from app.services.audit import Actor, AuditService
from app.services.auth import Principal
from app.services.config_versions import ConfigVersionService
from app.services.provisioning_retry import reset_run_for_retry, restart_workflow
from app.services.receptionist_deletion import ReceptionistDeletionService

logger = get_logger(__name__)

router = APIRouter(prefix="/tenants", tags=["portal"])

#: A call is urgent at 4 or 5 on the summarizer's 1-5 scale — the same line the
#: dashboard has always drawn (`isUrgent` in the frontend).
URGENT_AT = 4

#: Transcripts are bounded on the way out. A runaway call should not become a
#: multi-megabyte response.
MAX_TRANSCRIPT_TURNS = 500


def _tenant_of(principal: Principal) -> uuid.UUID:
    """The tenant the dependency authorized. Never the raw path value."""
    tenant_id = principal.tenant_id
    assert tenant_id is not None  # TenantAccess guarantees it
    return tenant_id


async def _load_tenant(session: SessionDep, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:  # pragma: no cover - the membership join guarantees it
        raise NotFoundError("not found")
    return tenant


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


@router.get(
    "/{tenant_id}/dashboard",
    response_model=DashboardSummaryView,
    summary="The numbers on the Home page",
)
async def dashboard_summary(
    principal: RequireMember,
    session: SessionDep,
    days: int = Query(default=30, ge=1, le=365),
) -> DashboardSummaryView:
    tenant_id = _tenant_of(principal)
    since = datetime.now(UTC) - timedelta(days=days)
    started = func.coalesce(Call.started_at, Call.created_at)

    row = (
        await session.execute(
            select(
                func.count(Call.id),
                func.avg(cast(Call.duration_s, Float)),
                func.count(Call.id).filter(Call.urgency >= URGENT_AT),
                func.count(Call.id).filter(Call.callback_number.is_not(None)),
                func.count(distinct(Call.from_e164)),
            ).where(Call.tenant_id == tenant_id, started >= since)
        )
    ).one()

    total_contacts = (
        await session.execute(
            select(func.count(distinct(Call.from_e164))).where(
                Call.tenant_id == tenant_id, Call.from_e164.is_not(None)
            )
        )
    ).scalar_one()

    answered, average, urgent, callbacks, unique_callers = row
    return DashboardSummaryView(
        window_days=days,
        window_start=since,
        answered_calls=answered,
        average_duration_s=round(float(average), 1) if average is not None else None,
        urgent_calls=urgent,
        callbacks_requested=callbacks,
        unique_callers=unique_callers,
        total_contacts=total_contacts,
    )


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------


@router.get(
    "/{tenant_id}/calls/{call_id}",
    response_model=CallDetailView,
    summary="One call, with its transcript",
)
async def get_call(
    call_id: uuid.UUID,
    principal: RequireMember,
    session: SessionDep,
) -> CallDetailView:
    tenant_id = _tenant_of(principal)
    # Scoped in the query, not checked afterwards: another tenant's call id is
    # indistinguishable from one that does not exist.
    call = (
        await session.execute(select(Call).where(Call.id == call_id, Call.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if call is None:
        raise NotFoundError("call not found")

    return CallDetailView(
        id=call.id,
        provider_call_id=call.provider_call_id,
        direction=call.direction.value,
        from_e164=call.from_e164,
        to_e164=call.to_e164,
        started_at=call.started_at,
        duration_s=call.duration_s,
        status=call.status,
        summary=call.summary,
        caller_name=call.caller_name,
        callback_number=call.callback_number,
        intent=call.intent,
        urgency=call.urgency,
        agent_config_version=call.agent_config_version,
        transcript=_transcript(call.transcript_json),
    )


def _transcript(document: dict[str, Any] | None) -> list[TranscriptTurnView]:
    """The turns, reduced to speaker, words and time.

    Vendor-shaped input, so every field is checked rather than trusted: a turn
    that is not a mapping, or has no words, is skipped rather than failing the
    whole page.
    """
    if not document:
        return []
    turns = document.get("turns")
    if not isinstance(turns, list):
        return []

    result: list[TranscriptTurnView] = []
    for turn in turns[:MAX_TRANSCRIPT_TURNS]:
        if not isinstance(turn, dict):
            continue
        message = str(turn.get("message") or turn.get("text") or "").strip()
        if not message:
            continue
        role = str(turn.get("role") or "unknown").strip().lower()[:32]
        offset = turn.get("time_in_call_secs")
        result.append(
            TranscriptTurnView(
                role=role,
                message=message,
                time_in_call_s=float(offset) if isinstance(offset, int | float) else None,
            )
        )
    return result


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------
#
# Moved to `app/api/v1/contacts.py`. Contacts used to be a read-only view over
# the call history; they are now records a business can edit, block and import,
# so they have their own module. Call statistics are still read from the calls
# table there, which is why the two can never disagree.


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


@router.get(
    "/{tenant_id}/agent/versions",
    response_model=list[ConfigVersionView],
    summary="Configuration history",
)
async def list_config_versions(
    principal: RequireMember,
    session: SessionDep,
    limit: int = Query(default=20, ge=1, le=50),
) -> list[ConfigVersionView]:
    history = await ConfigVersionService(session).history(_tenant_of(principal), limit=limit)
    return [
        ConfigVersionView(
            version=config.version,
            is_live=config.is_live,
            generated_by=config.generated_by.value,
            created_at=config.created_at,
        )
        for config in history
    ]


@router.get(
    "/{tenant_id}/agent/timezones",
    response_model=list[TimezoneOptionView],
    summary="The timezones a receptionist may be set to",
)
async def list_timezones(principal: RequireMember) -> list[TimezoneOptionView]:
    """Served rather than hard-coded in the client, so the list a customer
    picks from is exactly the list the validator accepts.

    Membership is required for no deeper reason than that everything under
    ``/tenants/{id}`` is: the list itself is not sensitive.
    """
    from app.data.timezones import timezone_options

    return [
        TimezoneOptionView(timezone=option.timezone, label=option.label)
        for option in timezone_options()
    ]


@router.put(
    "/{tenant_id}/agent/settings",
    response_model=AgentUpdateResult,
    summary="Change what the receptionist knows, and publish a new version",
)
async def update_agent_settings(
    payload: AgentSettingsUpdate,
    principal: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
) -> AgentUpdateResult:
    tenant = await _load_tenant(session, _tenant_of(principal))
    return await AgentSettingsService(session, settings, providers).update(
        tenant=tenant,
        update=payload,
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )


# ---------------------------------------------------------------------------
# Deleting the receptionist
# ---------------------------------------------------------------------------


@router.post(
    "/{tenant_id}/receptionist/delete",
    response_model=DeletionResultView,
    summary="Delete this receptionist, optionally releasing its number",
)
async def delete_receptionist(
    payload: ReceptionistDeletion,
    principal: RequireOwner,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
    cache: CacheDep,
) -> DeletionResultView:
    """Owner only, and never while impersonating.

    POST rather than DELETE because the request carries a decision — whether to
    hand the phone number back — and that choice must not be guessable from the
    method alone.

    Idempotent: calling it again re-attempts only what failed, so a vendor
    outage during the first call is recoverable without support. The work itself
    is in :mod:`app.services.receptionist_deletion`, which stops the calls
    before it touches a vendor.
    """
    if principal.is_impersonating:
        # Releasing a customer's number and deleting their agent is their
        # decision, not an operator's with their session borrowed.
        raise AuthorizationError("a receptionist cannot be deleted while impersonating")

    tenant = await _load_tenant(session, _tenant_of(principal))
    outcome = await ReceptionistDeletionService(session, settings, providers, cache).delete(
        tenant=tenant,
        release_number=payload.release_number,
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )
    return DeletionResultView(
        complete=outcome.complete,
        tenant_status=outcome.tenant_status,
        agent_deleted=outcome.agent_deleted,
        shared_agent_detached=outcome.shared_agent_detached,
        number_released=outcome.number_released,
        number_retained=outcome.number_retained,
        release_refused=outcome.release_refused,
        errors=outcome.errors,
    )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@router.post(
    "/{tenant_id}/provisioning/retry",
    response_model=RetryResult,
    summary="Try a setup that stopped again",
)
async def retry_provisioning(
    principal: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
    temporal: TemporalClientDep,
) -> RetryResult:
    """The customer's own "try again".

    Narrower than the operator's retry on purpose: only a run that *failed* can
    be retried here, and always from where it stopped. A run that was rolled
    back has released its number, and restarting it is a decision for support —
    it would buy a new one. Nothing here can skip the money gate or buy twice:
    see :mod:`app.services.provisioning_retry`.
    """
    tenant = await _load_tenant(session, _tenant_of(principal))
    if tenant.status in (TenantStatus.CANCELLED, TenantStatus.ABANDONED):
        raise ConflictError("this receptionist is no longer active")

    run = (
        await session.execute(
            select(ProvisioningRun)
            .where(ProvisioningRun.tenant_id == tenant.id)
            .order_by(ProvisioningRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("no provisioning run for tenant")
    if run.status is not ProvisioningStatus.FAILED:
        raise ConflictError(
            "only a setup that has stopped on an error can be retried",
            details={"status": run.status.value},
        )

    reset = await reset_run_for_retry(session, run=run, tenant=tenant)
    AuditService(session).record(
        AuditAction.PROVISIONING_RETRIED,
        actor_type=principal.actor_type,
        actor=principal.user,
        tenant_id=tenant.id,
        entity_type="provisioning_run",
        entity_id=run.id,
        meta={"steps_reset": reset, "by": "customer"},
    )
    # Committed before the workflow starts: its activities read this run.
    await session.commit()
    if settings.uses_temporal:
        await restart_workflow(temporal, settings, run, tenant)

    logger.info(
        "customer retried provisioning",
        extra={"tenant_id": str(tenant.id), "run_id": str(run.id), "steps_reset": len(reset)},
    )
    return RetryResult(run_id=run.id, status=run.status.value, steps_reset=reset)
