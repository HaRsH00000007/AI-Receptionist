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
from sqlalchemy import Float, and_, cast, distinct, func, select

from app.api.auth_deps import RequireAdmin, RequireMember
from app.api.deps import ProvidersDep, SettingsDep, TemporalClientDep
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.models import Call, ProvisioningRun, Tenant
from app.models.enums import AuditAction, ProvisioningStatus, TenantStatus
from app.schemas.portal import (
    AgentSettingsUpdate,
    AgentUpdateResult,
    CallDetailView,
    ConfigVersionView,
    ContactView,
    DashboardSummaryView,
    RetryResult,
    TranscriptTurnView,
)
from app.services.agent_settings import AgentSettingsService
from app.services.audit import Actor, AuditService
from app.services.auth import Principal
from app.services.config_versions import ConfigVersionService
from app.services.provisioning_retry import reset_run_for_retry, restart_workflow

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


@router.get(
    "/{tenant_id}/contacts",
    response_model=list[ContactView],
    summary="Callers, derived from call history",
)
async def list_contacts(
    principal: RequireMember,
    session: SessionDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ContactView]:
    """Everyone who has called, most recent first.

    Two queries rather than one clever one. The first aggregates per number; the
    second picks each number's latest call (``DISTINCT ON``) for the name and
    summary. Both are filtered by tenant, and the second only over the numbers
    the first returned.
    """
    tenant_id = _tenant_of(principal)
    started = func.coalesce(Call.started_at, Call.created_at)
    scoped = and_(Call.tenant_id == tenant_id, Call.from_e164.is_not(None))

    aggregates = (
        await session.execute(
            select(
                Call.from_e164,
                func.count(Call.id),
                func.min(started),
                func.max(started),
                func.bool_or(func.coalesce(Call.urgency, 0) >= URGENT_AT),
            )
            .where(scoped)
            .group_by(Call.from_e164)
            .order_by(func.max(started).desc())
            .limit(limit)
        )
    ).all()
    if not aggregates:
        return []

    numbers = [row[0] for row in aggregates]
    latest = {
        row.from_e164: row
        for row in (
            await session.execute(
                select(Call.from_e164, Call.summary, Call.intent)
                .where(scoped, Call.from_e164.in_(numbers))
                .order_by(Call.from_e164, started.desc())
                .distinct(Call.from_e164)
            )
        ).all()
    }
    # The latest name *given*, which is not always on the latest call: a caller
    # who said their name once and not the second time is still that person.
    names = {
        row.from_e164: row.caller_name
        for row in (
            await session.execute(
                select(Call.from_e164, Call.caller_name)
                .where(scoped, Call.from_e164.in_(numbers), Call.caller_name.is_not(None))
                .order_by(Call.from_e164, started.desc())
                .distinct(Call.from_e164)
            )
        ).all()
    }

    return [
        ContactView(
            phone_e164=number,
            name=names.get(number),
            call_count=count,
            first_call_at=first,
            last_call_at=last,
            last_summary=latest[number].summary if number in latest else None,
            last_intent=latest[number].intent if number in latest else None,
            has_urgent=bool(urgent),
        )
        for number, count, first, last, urgent in aggregates
    ]


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
