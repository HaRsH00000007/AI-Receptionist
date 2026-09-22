"""Signup endpoints.

``POST /signups`` is the front door. It validates, persists and returns — it
never calls a provider, so a Twilio outage cannot stop a business signing up.

``POST /signups/tally`` is the same thing behind an adapter, so the existing
Tally form keeps working while the Next.js form takes over.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.api.auth_deps import client_ip, set_session_cookie
from app.api.deps import SettingsDep, TemporalClientDep
from app.core.config import Settings
from app.core.correlation import get_correlation_id, new_correlation_id
from app.core.errors import InvalidInputError
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.schemas.signup import SignupRequest, SignupResponse, TallyWebhook
from app.services.auth import AuthService
from app.services.signup import SignupResult, SignupService
from app.services.status_tokens import issue_status_token
from app.services.tally import tally_to_signup
from app.temporal.client import start_provisioning

logger = get_logger(__name__)

router = APIRouter(prefix="/signups", tags=["signups"])


def _client_key(request: Request) -> str:
    """Rate-limit key. Honours one proxy hop, which is what Railway/Fly add."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _submit(
    payload: SignupRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: Settings,
    temporal: TemporalClientDep,
) -> SignupResponse:
    await request.app.state.signup_rate_limiter.check(_client_key(request))

    correlation_id = get_correlation_id() or new_correlation_id()
    result = await SignupService(session, settings).submit(payload, correlation_id=correlation_id)

    # Sign the owner in, but only when this very request created their account
    # with the password they just typed. An address that already had an account
    # is never signed in here: signup is anonymous, so doing that would give a
    # stranger's account to anyone who entered their email.
    signed_in = False
    if result.created and result.password_set and result.owner is not None:
        issued = await AuthService(session, settings).sign_in_new_account(
            result.owner,
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        set_session_cookie(response, settings, issued.token)
        signed_in = True

    if settings.uses_temporal:
        # Committed first: the workflow's activities read this run, so starting
        # before the commit would race a worker against an uncommitted row.
        await session.commit()
        await _start_workflow(temporal, settings, result)

    # 201 for a new tenant, 200 when an existing live signup was returned. Both
    # carry the same body, so a client that resubmits needs no special handling.
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK

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
        signed_in=signed_in,
    )


async def _start_workflow(
    temporal: TemporalClientDep,
    settings: Settings,
    result: SignupResult,
) -> None:
    """Hand the run to Temporal. Never fails the signup.

    Two independent reasons this is best-effort. The workflow id is derived from
    the run id, so a resubmitted signup adopts the existing execution rather
    than starting a second one — starting twice is already harmless. And a
    Temporal outage must not break the front door: the POC's defining property
    is that signup records a tenant in milliseconds and never calls a provider,
    which is what keeps the form up when something downstream is down.

    The cost is that a run whose workflow never started sits in DRAFT until an
    operator retries it. That is visible in the admin panel and is the honest
    trade against failing signups outright.
    """
    if temporal is None:
        logger.error(
            "temporal is unavailable; provisioning was not started",
            extra={"tenant_id": str(result.tenant.id), "run_id": str(result.run.id)},
        )
        return

    try:
        await start_provisioning(
            temporal,
            settings,
            run_id=result.run.id,
            tenant_id=result.tenant.id,
            correlation_id=result.run.correlation_id,
        )
    except Exception:
        logger.exception(
            "could not start the provisioning workflow",
            extra={"tenant_id": str(result.tenant.id), "run_id": str(result.run.id)},
        )


@router.post(
    "",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a signup",
    responses={200: {"description": "An existing live signup was returned unchanged."}},
)
async def create_signup(
    payload: SignupRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    temporal: TemporalClientDep,
) -> SignupResponse:
    return await _submit(payload, request, response, session, settings, temporal)


@router.post(
    "/tally",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Accept a Tally form submission",
)
async def create_signup_from_tally(
    payload: dict[str, Any],
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    temporal: TemporalClientDep,
) -> SignupResponse:
    """Adapter for the existing Tally form.

    The mapping lives in :mod:`app.services.tally` so the vendor's field shape
    never reaches the domain; from here on this is an ordinary signup.
    """
    expected = settings.tally_signing_secret.get_secret_value()
    if expected:
        provided = request.headers.get("tally-signature", "")
        if not hmac.compare_digest(provided, expected):
            raise InvalidInputError("invalid tally signature", details={"source": "tally"})

    webhook = TallyWebhook.model_validate(payload)
    signup = tally_to_signup(webhook)
    logger.info("tally submission accepted", extra={"event_id": webhook.eventId})
    return await _submit(signup, request, response, session, settings, temporal)
