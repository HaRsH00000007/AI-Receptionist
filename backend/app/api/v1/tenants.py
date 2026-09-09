"""Tenant-scoped read endpoints.

What the status page and the client dashboard poll.

**Authorization.** Every route here depends on
:data:`~app.api.auth_deps.TenantReadDep`, which grants access two ways: a
logged-in session with an accepted membership, or a signed, expiring status
grant for the post-signup page. It is a dependency rather than a check inside
each handler so that a new endpoint added to this module cannot forget it.

The POC accepted a bare tenant UUID as proof — the id in the URL *was* the
credential. That is closed: the path id is now only ever a request, and
``tenant_id`` in the handlers below is the one the dependency authorized. The
two are compared by the dependency, never by the handler.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.auth_deps import TenantReadDep
from app.api.deps import SettingsDep
from app.core.errors import NotFoundError
from app.db.session import SessionDep
from app.models import Agent, AgentConfig, Call, PhoneNumber, ProvisioningRun, Tenant
from app.models.enums import STEP_SEQUENCE, AgentStatus, PhoneNumberStatus, StepStatus
from app.schemas.views import (
    AgentView,
    BillingView,
    CallView,
    PhoneNumberView,
    ProvisioningView,
    StepView,
    TenantView,
)
from app.services.billing_gate import BillingDecision, evaluate

router = APIRouter(prefix="/tenants", tags=["tenants"])


async def _load_tenant(session: SessionDep, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("tenant not found", details={"tenant_id": str(tenant_id)})
    return tenant


@router.get("/{tenant_id}", response_model=TenantView, summary="Tenant detail")
async def get_tenant(tenant_id: TenantReadDep, session: SessionDep) -> TenantView:
    tenant = await _load_tenant(session, tenant_id)
    return TenantView(
        id=tenant.id,
        name=tenant.name,
        business_type=tenant.business_type.value,
        status=tenant.status,
        plan=tenant.plan.value,
        timezone=tenant.timezone,
        contact_email=tenant.contact_email,
        area_code=tenant.area_code,
        created_at=tenant.created_at,
    )


@router.get(
    "/{tenant_id}/provisioning",
    response_model=ProvisioningView,
    summary="Provisioning state and per-step timeline",
)
async def get_provisioning(
    tenant_id: TenantReadDep, session: SessionDep, settings: SettingsDep
) -> ProvisioningView:
    await _load_tenant(session, tenant_id)

    run = (
        await session.execute(
            select(ProvisioningRun)
            .where(ProvisioningRun.tenant_id == tenant_id)
            .order_by(ProvisioningRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("no provisioning run for tenant", details={"tenant_id": str(tenant_id)})

    await session.refresh(run, ["steps"])
    by_name = {record.step_name: record for record in run.steps}

    # Ordered by the state machine, not by insertion, so the UI always shows the
    # same seven rows in the same order even if some have not been created yet.
    steps = [
        StepView(
            step_name=step.value,
            status=by_name[step].status if step in by_name else StepStatus.PENDING,
            attempt=by_name[step].attempt if step in by_name else 0,
            error=by_name[step].error if step in by_name else None,
            started_at=by_name[step].started_at if step in by_name else None,
            finished_at=by_name[step].finished_at if step in by_name else None,
        )
        for step in STEP_SEQUENCE
    ]

    return ProvisioningView(
        run_id=run.id,
        tenant_id=run.tenant_id,
        status=run.status,
        current_step=run.current_step.value if run.current_step else None,
        attempt=run.attempt,
        last_error=run.last_error,
        correlation_id=run.correlation_id,
        next_attempt_at=run.next_attempt_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        steps=steps,
        completed_steps=sum(1 for step in steps if step.status is StepStatus.SUCCEEDED),
        total_steps=len(STEP_SEQUENCE),
        # Evaluated server-side from the subscription row, through the same
        # function the money gate uses -- so what the status page reports and
        # what the gate decides can never disagree.
        billing=_billing_view(await evaluate(session, settings, tenant_id)),
    )


def _billing_view(decision: BillingDecision) -> BillingView:
    return BillingView(
        entitled=decision.allowed,
        plan=decision.plan.value if decision.plan else None,
        status=decision.status.value if decision.status else None,
        trial_ends_at=decision.trial_ends_at,
        # Only surfaced on refusal: "entitled" needs no explanation, and the
        # reason code is only actionable when something is blocked.
        reason=None if decision.allowed else decision.reason,
    )


@router.get(
    "/{tenant_id}/phone", response_model=PhoneNumberView, summary="The tenant's live number"
)
async def get_phone(tenant_id: TenantReadDep, session: SessionDep) -> PhoneNumberView:
    await _load_tenant(session, tenant_id)
    number = (
        await session.execute(
            select(PhoneNumber)
            .where(PhoneNumber.tenant_id == tenant_id)
            .where(PhoneNumber.status == PhoneNumberStatus.ACTIVE)
        )
    ).scalar_one_or_none()
    if number is None:
        raise NotFoundError("tenant has no active number", details={"tenant_id": str(tenant_id)})
    return PhoneNumberView(
        e164=number.e164,
        status=number.status,
        area_code=number.area_code,
        purchased_at=number.purchased_at,
        released_at=number.released_at,
    )


@router.get("/{tenant_id}/agent", response_model=AgentView, summary="The tenant's voice agent")
async def get_agent(tenant_id: TenantReadDep, session: SessionDep) -> AgentView:
    await _load_tenant(session, tenant_id)
    agent = (
        await session.execute(
            select(Agent)
            .where(Agent.tenant_id == tenant_id)
            .where(Agent.status == AgentStatus.ACTIVE)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise NotFoundError("tenant has no active agent", details={"tenant_id": str(tenant_id)})

    config = await session.get(AgentConfig, agent.agent_config_id)
    return AgentView(
        status=agent.status,
        elevenlabs_agent_id=agent.elevenlabs_agent_id,
        config_version=config.version if config else None,
        voice_id=config.voice_id if config else None,
        generated_by=config.generated_by.value if config else None,
        synced_at=agent.synced_at,
    )


@router.get("/{tenant_id}/calls", response_model=list[CallView], summary="Recent calls")
async def list_calls(
    tenant_id: TenantReadDep,
    session: SessionDep,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[CallView]:
    await _load_tenant(session, tenant_id)
    calls = (
        (
            await session.execute(
                select(Call)
                .where(Call.tenant_id == tenant_id)
                .order_by(Call.started_at.desc().nulls_last(), Call.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        CallView(
            id=call.id,
            provider_call_id=call.provider_call_id,
            direction=call.direction.value,
            from_e164=call.from_e164,
            started_at=call.started_at,
            duration_s=call.duration_s,
            status=call.status,
            summary=call.summary,
            caller_name=call.caller_name,
            callback_number=call.callback_number,
            intent=call.intent,
            urgency=call.urgency,
        )
        for call in calls
    ]
