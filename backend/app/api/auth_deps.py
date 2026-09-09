"""Authentication and authorization dependencies.

Every protected endpoint depends on something in this module. That is the point:
authorization is a dependency the route cannot forget to declare, rather than a
check inside a handler that a new endpoint might omit.

Two rules the shapes here enforce:

**The tenant in the URL grants nothing.** ``require_tenant`` takes the path's
tenant id and verifies it against an accepted membership before anything else
runs. The POC's vulnerability was the inverse — the id in the path *was* the
credential — so the id is treated as a request, never as proof.

**A tenant the caller cannot see returns 404, not 403.** A 403 confirms the
tenant exists, which turns any protected endpoint into an oracle for
enumerating other customers. Both cases are answered identically.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Cookie, Depends, Header, Query, Request, Response

from app.api.deps import SettingsDep
from app.core.config import Settings
from app.core.errors import AuthenticationError, AuthorizationError, NotFoundError
from app.db.session import SessionDep
from app.models.enums import MembershipRole
from app.services.auth import AuthService, Principal
from app.services.status_tokens import verify_status_token


def client_ip(request: Request) -> str | None:
    """The caller's address, honouring a single proxy hop.

    One hop, not the whole chain: ``X-Forwarded-For`` is client-controlled, and
    trusting the leftmost entry blindly lets a caller forge any address they
    like. The first entry is right when exactly one trusted proxy sits in front,
    which is what the deployment topology provides.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _bearer_token(authorization: str | None) -> str | None:
    """Extract a bearer token from an Authorization header, if well-formed."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


async def get_principal(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    session_cookie: Annotated[str | None, Cookie(alias="ai_receptionist_session")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Resolve the caller, or raise 401.

    Two transports, one credential. The cookie serves the browser dashboard; the
    bearer header serves API clients and tests. The cookie is preferred when
    both are present, because a stale ``Authorization`` header left on a fetch
    is a likelier mistake than a stale cookie.

    Note the cookie alias is a literal rather than ``settings.session_cookie_name``:
    FastAPI reads dependency signatures at import time, before any Settings
    instance exists. :func:`set_session_cookie` writes the configured name, and
    a test asserting on both is what keeps them from drifting.
    """
    token = session_cookie or _bearer_token(authorization)
    if not token:
        raise AuthenticationError("authentication required")

    return await AuthService(session, settings).authenticate(token)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def get_platform_admin(principal: PrincipalDep) -> Principal:
    """Require a platform operator.

    Raises 404, not 403 — the admin surface should not confirm its own existence
    to a caller who has no business there.
    """
    if not principal.user.is_platform_admin:
        raise NotFoundError("not found")
    return principal


PlatformAdminDep = Annotated[Principal, Depends(get_platform_admin)]


class TenantAccess:
    """Dependency factory: require membership of the path's tenant, at a role.

    Used as ``Depends(TenantAccess(MembershipRole.ADMIN))``, which puts the
    required privilege in the route's signature where it is visible in the
    OpenAPI schema and in review — rather than buried in a handler body.
    """

    def __init__(self, minimum_role: MembershipRole = MembershipRole.MEMBER) -> None:
        self.minimum_role = minimum_role

    async def __call__(
        self,
        tenant_id: uuid.UUID,
        request: Request,
        session: SessionDep,
        settings: SettingsDep,
        session_cookie: Annotated[str | None, Cookie(alias="ai_receptionist_session")] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Principal:
        token = session_cookie or _bearer_token(authorization)
        if not token:
            raise AuthenticationError("authentication required")

        try:
            principal = await AuthService(session, settings).authenticate(
                token, tenant_id=tenant_id
            )
        except AuthorizationError as exc:
            # Deliberate downgrade to 404. The caller is authenticated but has
            # no membership here, and saying so would confirm the tenant exists.
            raise NotFoundError("not found") from exc

        principal.require_role(self.minimum_role)
        return principal


#: The three common shapes, pre-built so routes read declaratively.
RequireMember = Annotated[Principal, Depends(TenantAccess(MembershipRole.MEMBER))]
RequireAdmin = Annotated[Principal, Depends(TenantAccess(MembershipRole.ADMIN))]
RequireOwner = Annotated[Principal, Depends(TenantAccess(MembershipRole.OWNER))]


async def require_tenant_read(
    tenant_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    session_cookie: Annotated[str | None, Cookie(alias="ai_receptionist_session")] = None,
    authorization: Annotated[str | None, Header()] = None,
    status_token: Annotated[str | None, Query(alias="status_token")] = None,
) -> uuid.UUID:
    """Read access to one tenant, by session **or** by signed status grant.

    Two credentials because there are genuinely two callers. The dashboard has a
    logged-in user with a membership. The post-signup status page has neither —
    the business has just submitted a form and has no account yet — so it
    carries a signed, expiring, tenant-scoped grant instead
    (:mod:`app.services.status_tokens`).

    What is *not* accepted is the thing the POC accepted: a bare tenant UUID.
    Returning the id rather than a :class:`Principal` keeps that explicit —
    callers get a tenant they have been authorized for, not one they asked for.
    """
    if status_token:
        granted = verify_status_token(settings, status_token)
        if granted != tenant_id:
            # A grant for a different tenant is not a hint that this one exists.
            raise NotFoundError("not found")
        return granted

    token = session_cookie or _bearer_token(authorization)
    if not token:
        raise AuthenticationError("authentication required")

    try:
        principal = await AuthService(session, settings).authenticate(token, tenant_id=tenant_id)
    except AuthorizationError as exc:
        raise NotFoundError("not found") from exc

    assert principal.tenant_id is not None  # authenticate() guarantees it here
    return principal.tenant_id


#: Read access to the tenant named in the path, however it was granted.
TenantReadDep = Annotated[uuid.UUID, Depends(require_tenant_read)]


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    """Write the session cookie with its security attributes.

    A function rather than a dict of kwargs, so the attributes are type-checked
    at the call site. They are not incidental:

    ``httponly`` unconditionally — JavaScript has no reason to read the session,
    and denying it removes the payoff from an XSS bug.

    ``secure`` from configuration, because a Secure cookie is silently dropped
    over plain HTTP: local development would appear to log in and then not stay
    logged in. Production-like environments refuse to boot with it disabled, so
    the local convenience cannot escape.

    ``samesite=lax`` by default. ``strict`` would break the magic-link flow,
    where the browser arrives from a link in an email client and would present
    no cookie at all.
    """
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_s,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Remove the session cookie.

    The attributes must match those it was set with — a browser will not delete
    a cookie whose path or security flags differ, and the stale cookie would
    keep being presented on every request.
    """
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )
