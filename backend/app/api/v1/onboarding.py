"""Onboarding: the business-setup form, for someone already signed in.

The account-first flow is

    POST /auth/register        create the account, get a session
    PUT  /onboarding/draft     save the form as it is filled in (resumable)
    POST /onboarding           create the business; provisioning starts
    ->   /dashboard/setup      watch it, signed in, until it is live

``POST /onboarding`` is the anonymous ``POST /signups`` with one difference that
matters: the owner of the new business is the session's user, never whoever the
form's email names. The anonymous route stays for the Tally webhook and any
existing integration.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import delete, select

from app.api.auth_deps import PrincipalDep
from app.api.deps import SettingsDep, TemporalClientDep
from app.api.v1.signups import _start_workflow
from app.core.correlation import get_correlation_id, new_correlation_id
from app.core.errors import AuthorizationError
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.models.enums import AuditAction
from app.models.onboarding import OnboardingDraft
from app.schemas.signup import (
    OnboardingDraftInput,
    OnboardingDraftView,
    OnboardingRequest,
    SignupResponse,
)
from app.services.audit import AuditService
from app.services.signup import SignupService
from app.services.status_tokens import issue_status_token

logger = get_logger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post(
    "",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a business for the signed-in account",
    responses={200: {"description": "This account's existing live business was returned."}},
)
async def create_business(
    payload: OnboardingRequest,
    principal: PrincipalDep,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    temporal: TemporalClientDep,
) -> SignupResponse:
    if principal.is_impersonating:
        # A business is a contract and a phone bill in the customer's name.
        raise AuthorizationError("a business cannot be created while impersonating")
    await request.app.state.signup_rate_limiter.check(f"onboarding|{principal.user.id}")

    correlation_id = get_correlation_id() or new_correlation_id()
    result = await SignupService(session, settings).submit(
        payload, correlation_id=correlation_id, owner=principal.user
    )

    # Scope this session to the new business, so the dashboard opens it
    # directly, and drop the draft: the form it held has been submitted.
    principal.session.active_tenant_id = result.tenant.id
    await session.execute(
        delete(OnboardingDraft).where(OnboardingDraft.user_id == principal.user.id)
    )
    if result.created:
        AuditService(session).record(
            AuditAction.BUSINESS_CREATED,
            actor_type=principal.actor_type,
            actor=principal.user,
            tenant_id=result.tenant.id,
            entity_type="tenant",
            entity_id=result.tenant.id,
            meta={"business_type": result.tenant.business_type.value},
        )

    if settings.uses_temporal:
        # Committed first: the workflow's activities read this run.
        await session.commit()
        await _start_workflow(temporal, settings, result)

    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    logger.info(
        "business created from onboarding",
        extra={"tenant_id": str(result.tenant.id), "business_created": result.created},
    )
    return SignupResponse(
        tenant_id=result.tenant.id,
        run_id=result.run.id,
        tenant_status=result.tenant.status,
        provisioning_status=result.run.status,
        correlation_id=result.run.correlation_id,
        business_name=result.tenant.name,
        contact_email=result.tenant.contact_email,
        timezone=result.tenant.timezone,
        created=result.created,
        status_token=issue_status_token(settings, result.tenant.id),
        signed_in=True,
    )


# ---------------------------------------------------------------------------
# The draft
# ---------------------------------------------------------------------------


async def _draft(session: SessionDep, principal: PrincipalDep) -> OnboardingDraft | None:
    return (
        await session.execute(
            select(OnboardingDraft).where(OnboardingDraft.user_id == principal.user.id)
        )
    ).scalar_one_or_none()


@router.get("/draft", response_model=OnboardingDraftView, summary="The unfinished setup form")
async def get_draft(principal: PrincipalDep, session: SessionDep) -> OnboardingDraftView:
    draft = await _draft(session, principal)
    return OnboardingDraftView(
        data=draft.data if draft else None,
        updated_at=draft.updated_at if draft else None,
    )


@router.put("/draft", response_model=OnboardingDraftView, summary="Save the setup form so far")
async def save_draft(
    payload: OnboardingDraftInput, principal: PrincipalDep, session: SessionDep
) -> OnboardingDraftView:
    draft = await _draft(session, principal)
    if draft is None:
        draft = OnboardingDraft(user_id=principal.user.id, data=payload.data)
        session.add(draft)
    else:
        draft.data = payload.data
    await session.flush()
    await session.refresh(draft, ["updated_at"])
    return OnboardingDraftView(data=draft.data, updated_at=draft.updated_at)


@router.delete("/draft", status_code=status.HTTP_204_NO_CONTENT, summary="Discard the setup form")
async def delete_draft(principal: PrincipalDep, session: SessionDep) -> None:
    await session.execute(
        delete(OnboardingDraft).where(OnboardingDraft.user_id == principal.user.id)
    )
