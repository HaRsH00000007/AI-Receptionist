"""Authentication endpoints.

Four routes, one credential lifecycle:

    POST   /auth/magic-link   ask for a login link
    POST   /auth/session      trade the link for a session
    GET    /auth/me           who am I
    DELETE /auth/session      log out

The magic-link endpoint is the one with a security shape worth stating: it
answers **identically** whether or not the address has an account. Same status,
same body, same audit path. A ``sent: true`` field, a 404 for unknown
addresses, or an error that names the reason would each turn the login form into
a free oracle for "is this business a customer of yours?" — which is worth money
to a competitor and is the first step of a targeted phish.

Delivery of the email is intentionally not done here. The endpoint stages a
notification row and returns; a provider outage must not make login *appear* to
fail when the link was in fact issued.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from app.api.auth_deps import (
    PrincipalDep,
    clear_session_cookie,
    client_ip,
    set_session_cookie,
)
from app.api.deps import SettingsDep
from app.core.errors import AuthenticationError
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.models.enums import NotificationKind
from app.models.identity import Membership, User
from app.models.operations import Notification
from app.models.tenant import Tenant
from app.schemas.auth import (
    MagicLinkExchange,
    MagicLinkRequest,
    MagicLinkResponse,
    SessionView,
    TenantMembershipView,
)
from app.services.auth import AuthService, Principal

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _login_limiter_key(request: Request, email: str) -> str:
    """Rate-limit key for login attempts.

    Keyed on address *and* source, not either alone. On address alone, one
    attacker could lock a real customer out of their own account by burning the
    limit — a denial of service handed out for free. On source alone, a single
    NAT'd office shares one budget. The pair limits the useful attack (many
    attempts at one account) without either side effect.
    """
    return f"{client_ip(request) or 'unknown'}|{email.strip().lower()}"


async def _memberships_view(
    session: SessionDep, principal: Principal
) -> list[TenantMembershipView]:
    """The organizations this user can act in.

    Only accepted memberships: an outstanding invitation grants nothing, and
    listing it as though it did would have the dashboard offer a tenant the
    API would then refuse.
    """
    result = await session.execute(
        # One join rather than a lookup per membership: the N+1 would be small
        # here, but this list is rendered on every dashboard load.
        select(Membership, Tenant)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(
            Membership.user_id == principal.user.id,
            Membership.accepted_at.is_not(None),
        )
    )
    return [
        TenantMembershipView(tenant_id=membership.tenant_id, name=tenant.name, role=membership.role)
        for membership, tenant in result.all()
    ]


def _session_view(
    principal: Principal,
    memberships: list[TenantMembershipView],
    *,
    token: str | None = None,
) -> SessionView:
    return SessionView(
        user_id=principal.user.id,
        email=principal.user.email,
        full_name=principal.user.full_name,
        is_platform_admin=principal.user.is_platform_admin,
        active_tenant_id=principal.session.active_tenant_id,
        memberships=memberships,
        token=token,
        impersonated=principal.is_impersonating,
    )


@router.post(
    "/magic-link",
    response_model=MagicLinkResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a sign-in link",
)
async def request_magic_link(
    payload: MagicLinkRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> MagicLinkResponse:
    """Issue a single-use sign-in link, if the address has an account.

    Always 202. See the module docstring for why the response cannot vary.
    """
    request.app.state.login_limiter.check(_login_limiter_key(request, payload.email))

    issued = await AuthService(session, settings).request_magic_link(
        payload.email,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    if issued is not None:
        # Staged as a notification rather than sent inline: an email provider
        # outage must not make a successfully-issued link look like a failure,
        # and the retry needs a durable row to find.
        session.add(
            Notification(
                tenant_id=issued.session.active_tenant_id,
                kind=NotificationKind.MAGIC_LINK,
                recipient=payload.email.strip().lower(),
                subject="Your sign-in link",
                template_id="magic_link.v1",
                template_vars_json={
                    "url": f"{settings.public_app_url}/auth/callback?token={issued.token}",
                    "expires_in_minutes": settings.magic_link_ttl_s // 60,
                },
            )
        )

    # Logged without the outcome. Recording "issued: false" at INFO would
    # reconstruct the enumeration oracle in the log aggregator, where it is
    # visible to anyone with log access.
    logger.info("magic link requested")
    return MagicLinkResponse()


@router.post(
    "/session",
    response_model=SessionView,
    summary="Exchange a sign-in link for a session",
)
async def create_session(
    payload: MagicLinkExchange,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> SessionView:
    """Consume the link and start a session.

    The session token is returned **both** as an HttpOnly cookie and in the
    body. The cookie is what the browser dashboard uses — JavaScript cannot read
    it, so an XSS bug cannot exfiltrate it. The body value is for non-browser
    API clients and for tests, which have no cookie jar.
    """
    service = AuthService(session, settings)
    issued = await service.exchange_magic_link(
        payload.token,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    user = await session.get(User, issued.session.user_id)
    if user is None:  # pragma: no cover - the exchange just verified this user
        raise AuthenticationError("invalid or expired login link")

    principal = Principal(
        user=user,
        session=issued.session,
        tenant_id=issued.session.active_tenant_id,
        role=None,
    )
    memberships = await _memberships_view(session, principal)

    set_session_cookie(response, settings, issued.token)
    return _session_view(principal, memberships, token=issued.token)


@router.get(
    "/me",
    response_model=SessionView,
    summary="The current user and their organizations",
)
async def read_me(
    principal: PrincipalDep,
    session: SessionDep,
) -> SessionView:
    memberships = await _memberships_view(session, principal)
    return _session_view(principal, memberships)


@router.delete(
    "/session",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out",
)
async def delete_session(
    principal: PrincipalDep,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> None:
    """Revoke this session and clear the cookie.

    Both halves matter. Clearing the cookie alone would leave a token that still
    authenticates anyone who captured it; revoking alone would leave the browser
    presenting a dead cookie on every request.
    """
    await AuthService(session, settings).logout(principal.session)
    clear_session_cookie(response, settings)
