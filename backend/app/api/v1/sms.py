"""SMS: where a tenant's texting compliance stands, and the form that starts it.

The customer can read their state, save a draft registration and submit it.
Nothing here can approve or enable anything — review decides that, through the
operator endpoint in :mod:`app.api.v1.admin`. Campaign creation and sending are
not built yet; when they are, they must check
:func:`app.services.sms_compliance.can_send` first.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.auth_deps import RequireAdmin, RequireMember
from app.core.errors import NotFoundError
from app.db.session import SessionDep
from app.models import Tenant
from app.schemas.portal import SmsRegistrationInput, SmsStateView
from app.services.audit import Actor
from app.services.auth import Principal
from app.services.sms_compliance import SmsComplianceService

router = APIRouter(prefix="/tenants", tags=["sms"])


async def _tenant(session: SessionDep, principal: Principal) -> Tenant:
    assert principal.tenant_id is not None
    tenant = await session.get(Tenant, principal.tenant_id)
    if tenant is None:  # pragma: no cover - the membership join guarantees it
        raise NotFoundError("not found")
    return tenant


@router.get("/{tenant_id}/sms", response_model=SmsStateView, summary="Where SMS stands")
async def get_sms_state(principal: RequireMember, session: SessionDep) -> SmsStateView:
    return await SmsComplianceService(session).state(await _tenant(session, principal))


@router.put(
    "/{tenant_id}/sms/registration",
    response_model=SmsStateView,
    summary="Save the SMS registration as a draft",
)
async def save_sms_registration(
    payload: SmsRegistrationInput, principal: RequireAdmin, session: SessionDep
) -> SmsStateView:
    return await SmsComplianceService(session).save_draft(
        await _tenant(session, principal),
        payload,
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )


@router.post(
    "/{tenant_id}/sms/registration/submit",
    response_model=SmsStateView,
    summary="Submit the SMS registration for review",
)
async def submit_sms_registration(principal: RequireAdmin, session: SessionDep) -> SmsStateView:
    return await SmsComplianceService(session).submit(
        await _tenant(session, principal),
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )
