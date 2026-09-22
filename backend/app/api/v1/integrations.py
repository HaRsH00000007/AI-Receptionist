"""Integrations: the catalog a tenant is offered, and connecting to it.

Two kinds of route:

* **Tenant-scoped** (``/tenants/{id}/integrations/...``) — list, start a
  connection, disconnect, verify. Membership-authorized like the rest of the
  portal; anything that changes a connection needs ``admin`` or ``owner``.
* **The OAuth callback** (``/integrations/oauth/{provider}/callback``) — where
  Google or Microsoft send the browser back. It has no tenant in its path; the
  tenant comes from the signed ``state``, and the browser's own session must
  belong to the user who started the flow (see
  :mod:`app.integrations.oauth_state` for why that second check matters).

The callback always ends in a redirect to the dashboard, success or failure,
with a short code the page turns into a sentence. A customer never lands on a
JSON error from a consent screen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Query, Request
from fastapi.responses import RedirectResponse

from app.api.auth_deps import RequireAdmin, RequireMember
from app.api.deps import SettingsDep
from app.core.config import Settings
from app.core.errors import AppError, AuthenticationError, AuthorizationError, NotFoundError
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.integrations.oauth_state import verify_state
from app.integrations.service import IntegrationEntry, IntegrationService
from app.models import Tenant
from app.models.enums import IntegrationProvider, IntegrationStatus, MembershipRole
from app.schemas.portal import (
    ConnectStart,
    IntegrationCheckView,
    IntegrationListView,
    IntegrationView,
)
from app.services.audit import Actor
from app.services.auth import AuthService, Principal

logger = get_logger(__name__)

router = APIRouter(tags=["integrations"])

#: How far ahead "verify" looks when it asks a calendar for busy time.
VERIFY_WINDOW = timedelta(days=7)


def _transport(request: Request) -> httpx.AsyncBaseTransport | None:
    """A mock transport the test suite may install. Always None in production."""
    return getattr(request.app.state, "integration_transport", None)


def redirect_uri(settings: Settings, request: Request, provider: IntegrationProvider) -> str:
    """The callback URL, identical at the start and the end of a flow.

    Providers compare it byte for byte, so it is built one way in one place.
    ``PUBLIC_API_URL`` wins when set, because behind a proxy the URL this
    process sees is not the one the browser — and the provider — will use.
    """
    base = settings.public_api_url.rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}{settings.api_v1_prefix}/integrations/oauth/{provider.value}/callback"


def _view(entry: IntegrationEntry) -> IntegrationView:
    row = entry.connection
    status = "not_connected"
    if row is not None and row.status is IntegrationStatus.CONNECTED:
        status = "connected"
    elif row is not None and row.status is IntegrationStatus.ERROR:
        status = "error"
    spec = entry.definition
    return IntegrationView(
        id=spec.id.value,
        name=spec.name,
        vendor=spec.vendor,
        category=spec.category.value,
        description=spec.description,
        icon=spec.icon,
        auth_type=spec.auth_type.value,
        capabilities=[capability.value for capability in spec.capabilities],
        availability=entry.availability.value,
        status=status,
        account=row.external_account_id if row is not None and status != "not_connected" else None,
        connected_at=row.connected_at if row is not None and status != "not_connected" else None,
        last_error=row.last_error if status == "error" else None,
    )


async def _tenant(session: SessionDep, principal: Principal) -> Tenant:
    assert principal.tenant_id is not None
    tenant = await session.get(Tenant, principal.tenant_id)
    if tenant is None:  # pragma: no cover - the membership join guarantees it
        raise NotFoundError("not found")
    return tenant


def _provider(value: str) -> IntegrationProvider:
    try:
        return IntegrationProvider(value)
    except ValueError as exc:
        raise NotFoundError("integration not found") from exc


# ---------------------------------------------------------------------------
# Tenant-scoped
# ---------------------------------------------------------------------------


@router.get(
    "/tenants/{tenant_id}/integrations",
    response_model=IntegrationListView,
    summary="Integrations offered to this business, and their state",
)
async def list_integrations(
    principal: RequireMember,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> IntegrationListView:
    tenant = await _tenant(session, principal)
    service = IntegrationService(session, settings, transport=_transport(request))
    entries = await service.list_for(tenant)
    return IntegrationListView(
        business_type=tenant.business_type.value,
        integrations=[_view(entry) for entry in entries],
    )


@router.post(
    "/tenants/{tenant_id}/integrations/{provider}/connect",
    response_model=ConnectStart,
    summary="Start connecting an integration",
)
async def connect_integration(
    provider: str,
    principal: RequireAdmin,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> ConnectStart:
    """Return the provider's consent URL. Nothing is stored until it returns."""
    tenant = await _tenant(session, principal)
    chosen = _provider(provider)
    url = IntegrationService(session, settings, transport=_transport(request)).begin_connect(
        tenant,
        chosen,
        user=principal.user,
        redirect_uri=redirect_uri(settings, request, chosen),
    )
    return ConnectStart(authorization_url=url)


@router.post(
    "/tenants/{tenant_id}/integrations/{provider}/disconnect",
    response_model=IntegrationView,
    summary="Disconnect an integration and delete its credentials",
)
async def disconnect_integration(
    provider: str,
    principal: RequireAdmin,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> IntegrationView:
    tenant = await _tenant(session, principal)
    chosen = _provider(provider)
    service = IntegrationService(session, settings, transport=_transport(request))
    await service.disconnect(
        tenant, chosen, actor=Actor(user=principal.user, actor_type=principal.actor_type)
    )
    return _view(await service.entry(tenant, chosen))


@router.post(
    "/tenants/{tenant_id}/integrations/{provider}/verify",
    response_model=IntegrationCheckView,
    summary="Check a connected calendar by asking it for real availability",
)
async def verify_integration(
    provider: str,
    principal: RequireAdmin,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> IntegrationCheckView:
    """A live call to the provider — the same one the receptionist will make.

    Reports how many busy blocks the next week holds, never what they are:
    proving the connection works does not need the owner's appointments.
    """
    tenant = await _tenant(session, principal)
    chosen = _provider(provider)
    start = datetime.now(UTC)
    end = start + VERIFY_WINDOW
    busy = await IntegrationService(
        session, settings, transport=_transport(request)
    ).busy_intervals(tenant, chosen, start=start, end=end)
    return IntegrationCheckView(ok=True, window_start=start, window_end=end, busy_blocks=len(busy))


# ---------------------------------------------------------------------------
# OAuth callback
# ---------------------------------------------------------------------------


def _back_to_dashboard(settings: Settings, **params: str) -> RedirectResponse:
    target = f"{settings.public_app_url.rstrip('/')}/dashboard/integrations"
    # 303: the browser follows with a GET, and does not resubmit anything.
    return RedirectResponse(f"{target}?{urlencode(params)}", status_code=303)


@router.get(
    "/integrations/oauth/{provider}/callback",
    summary="Where the provider returns the browser after consent",
    include_in_schema=False,
)
async def oauth_callback(
    provider: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    state: Annotated[str | None, Query(max_length=2048)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
    session_cookie: Annotated[str | None, Cookie(alias="ai_receptionist_session")] = None,
) -> RedirectResponse:
    try:
        chosen = IntegrationProvider(provider)
    except ValueError:
        return _back_to_dashboard(settings, integration_error="unknown_integration")

    if error:
        # The customer pressed "Cancel" on the consent screen, or the provider
        # refused. Either way nothing was granted and nothing is stored.
        return _back_to_dashboard(
            settings,
            integration=chosen.value,
            integration_error="access_denied" if error == "access_denied" else "provider_error",
        )
    if not code or not state:
        return _back_to_dashboard(
            settings, integration=chosen.value, integration_error="invalid_request"
        )

    try:
        granted = verify_state(settings, state, provider=chosen)
    except AuthenticationError:
        return _back_to_dashboard(
            settings, integration=chosen.value, integration_error="expired_link"
        )

    # The browser must be signed in as the person who started the flow, and
    # still be allowed to manage integrations for that tenant.
    if not session_cookie:
        return _back_to_dashboard(
            settings, integration=chosen.value, integration_error="signed_out"
        )
    try:
        principal = await AuthService(session, settings).authenticate(
            session_cookie, tenant_id=granted.tenant_id
        )
        principal.require_role(MembershipRole.ADMIN)
    except (AuthenticationError, AuthorizationError):
        return _back_to_dashboard(
            settings, integration=chosen.value, integration_error="not_permitted"
        )
    if principal.user.id != granted.user_id:
        logger.warning(
            "oauth callback completed by a different user than started it",
            extra={"tenant_id": str(granted.tenant_id), "provider": chosen.value},
        )
        return _back_to_dashboard(
            settings, integration=chosen.value, integration_error="not_permitted"
        )

    tenant = await session.get(Tenant, granted.tenant_id)
    if tenant is None:  # pragma: no cover - the membership check implies it
        return _back_to_dashboard(
            settings, integration=chosen.value, integration_error="not_permitted"
        )

    try:
        await IntegrationService(session, settings, transport=_transport(request)).complete_connect(
            tenant,
            chosen,
            code=code,
            redirect_uri=redirect_uri(settings, request, chosen),
            actor=Actor(user=principal.user, actor_type=principal.actor_type),
        )
    except AppError as exc:
        # The code is single-use and the exchange failed; the customer starts
        # again. The reason is logged for us and summarized for them.
        logger.warning(
            "oauth exchange failed",
            extra={"tenant_id": str(tenant.id), "provider": chosen.value, "code": exc.code},
        )
        reason = (
            "no_refresh_token" if exc.code == "integration_no_refresh_token" else "exchange_failed"
        )
        return _back_to_dashboard(settings, integration=chosen.value, integration_error=reason)

    await session.commit()
    return _back_to_dashboard(settings, integration=chosen.value, connected=chosen.value)
