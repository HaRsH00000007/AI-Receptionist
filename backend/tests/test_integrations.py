"""Integrations: the vertical-aware catalog, and real OAuth connections.

Google and Microsoft are replaced by a mock HTTP transport that speaks their
token, identity and calendar endpoints, so the whole flow runs for real — the
consent URL, the signed state, the callback, the code exchange, encryption at
rest, refresh, revocation and disconnect — without a network.

The properties under test, in order of how much damage their failure would do:

1. A connection is only ever "connected" after a real code exchange.
2. The callback refuses a browser signed in as anyone other than the person
   who started the flow (the CSRF that would plant an attacker's calendar).
3. Tokens are stored encrypted, and never appear in the database in the clear.
4. A business is offered exactly its vertical's integrations, and the API
   refuses the others as if they did not exist.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.errors import AuthenticationError, ConfigurationError
from app.integrations.catalog import CATALOG, Capability, integrations_for
from app.integrations.oauth_state import STATE_TTL_S, issue_state, verify_state
from app.integrations.vault import CredentialVault, vault_configured
from app.main import create_app
from app.models import AuditLog
from app.models.enums import (
    AuditAction,
    BusinessType,
    IntegrationProvider,
    IntegrationStatus,
    MembershipRole,
)
from app.models.integration import TenantIntegration
from tests.portal_support import COOKIE, Owner, add_member, other_tenant, sign_up
from tests.support import build_settings

ENCRYPTION_KEY = Fernet.generate_key().decode()
APP_URL = "http://app.test"
API_URL = "http://api.test"


# ---------------------------------------------------------------------------
# A fake Google and Microsoft
# ---------------------------------------------------------------------------


class FakeProviders:
    """Answers the provider endpoints the adapters call, and records the calls."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.issue_refresh_token = True
        self.refresh_fails = False
        self.access_counter = 0

    def _tokens(self, *, refresh: bool) -> dict[str, Any]:
        self.access_counter += 1
        body: dict[str, Any] = {
            "access_token": f"access-{self.access_counter}",
            "expires_in": 3599,
            "token_type": "Bearer",
            "scope": "openid email https://www.googleapis.com/auth/calendar.events",
        }
        if refresh:
            body["refresh_token"] = "refresh-original"
        return body

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        form = parse_qs(request.content.decode()) if request.method == "POST" else {}

        if path.endswith("/token"):
            grant = form.get("grant_type", [""])[0]
            if grant == "authorization_code":
                return httpx.Response(200, json=self._tokens(refresh=self.issue_refresh_token))
            if grant == "refresh_token":
                if self.refresh_fails:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self._tokens(refresh=False))
        if host == "oauth2.googleapis.com" and path == "/revoke":
            return httpx.Response(200)
        if host == "openidconnect.googleapis.com" and path == "/v1/userinfo":
            return httpx.Response(200, json={"email": "owner@gmail.example.com"})
        if host == "www.googleapis.com" and path == "/calendar/v3/freeBusy":
            return httpx.Response(
                200,
                json={
                    "calendars": {
                        "primary": {
                            "busy": [
                                {"start": "2030-01-01T10:00:00Z", "end": "2030-01-01T11:00:00Z"},
                                {"start": "2030-01-02T14:00:00Z", "end": "2030-01-02T15:00:00Z"},
                            ]
                        }
                    }
                },
            )
        if host == "graph.microsoft.com" and path == "/v1.0/me":
            return httpx.Response(
                200, json={"mail": None, "userPrincipalName": "owner@outlook.test"}
            )
        if host == "graph.microsoft.com" and path == "/v1.0/me/calendarView":
            return httpx.Response(
                200,
                json={
                    "value": [
                        _graph_event("busy"),
                        _graph_event("free"),
                        _graph_event("tentative"),
                        {**_graph_event("busy"), "isCancelled": True},
                    ]
                },
            )
        return httpx.Response(404, json={"error": f"unexpected {request.method} {request.url}"})

    def last(self, host: str, path_suffix: str) -> httpx.Request:
        return next(
            request
            for request in reversed(self.requests)
            if request.url.host == host and request.url.path.endswith(path_suffix)
        )


def _graph_event(show_as: str) -> dict[str, Any]:
    return {
        "showAs": show_as,
        "isCancelled": False,
        "start": {"dateTime": "2030-01-01T10:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2030-01-01T10:30:00.0000000", "timeZone": "UTC"},
    }


@pytest.fixture
def fake() -> FakeProviders:
    return FakeProviders()


@pytest.fixture
async def integration_app(migrated_database: str, fake: FakeProviders) -> AsyncIterator[FastAPI]:
    """The API with both OAuth clients and the token vault configured."""
    from app.db.session import create_engine
    from tests.worker_fixtures import truncate_all

    settings = build_settings(
        database_url=migrated_database,
        twilio_account_sid="ACtest",
        twilio_auth_token="test-token",
        elevenlabs_webhook_secret="test-webhook-secret",
        integration_encryption_key=ENCRYPTION_KEY,
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret="google-client-secret",
        microsoft_oauth_client_id="microsoft-client-id",
        microsoft_oauth_client_secret="microsoft-client-secret",
        public_app_url=APP_URL,
        public_api_url=API_URL,
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        application.state.integration_transport = httpx.MockTransport(fake.handler)
        try:
            yield application
        finally:
            engine = create_engine(settings)
            try:
                await truncate_all(engine)
            finally:
                await engine.dispose()


@pytest.fixture
async def client(integration_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=integration_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE}={token}"}


async def _connect(
    client: AsyncClient,
    owner: Owner,
    provider: str = "google_calendar",
    *,
    callback_token: str | None = None,
) -> httpx.Response:
    """Run the whole flow: start, "consent", and land on the callback."""
    started = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/{provider}/connect",
        headers=owner.headers,
    )
    assert started.status_code == 200, started.text
    query = parse_qs(urlparse(started.json()["authorization_url"]).query)
    return await client.get(
        f"/api/v1/integrations/oauth/{provider}/callback",
        params={"code": "auth-code-from-provider", "state": query["state"][0]},
        headers=_cookie(callback_token or owner.token),
    )


def _redirect_params(response: httpx.Response) -> dict[str, str]:
    assert response.status_code == 303, response.text
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        f"{APP_URL}/dashboard/integrations"
    )
    return {key: values[0] for key, values in parse_qs(location.query).items()}


async def _row(
    app: FastAPI, tenant_id: uuid.UUID, provider: IntegrationProvider
) -> TenantIntegration | None:
    async with app.state.session_factory() as session:
        return (
            await session.execute(
                select(TenantIntegration).where(
                    TenantIntegration.tenant_id == tenant_id,
                    TenantIntegration.provider == provider,
                )
            )
        ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("business_type", "expected"),
    [
        (BusinessType.LEGAL, ["clio", "google_calendar", "microsoft_outlook", "zapier"]),
        (BusinessType.REAL_ESTATE, ["google_calendar", "microsoft_outlook", "zapier"]),
        (
            BusinessType.SALON,
            ["acuity_scheduling", "google_calendar", "microsoft_outlook", "zapier"],
        ),
        (BusinessType.MEDICAL, ["acuity_scheduling", "google_calendar", "microsoft_outlook"]),
        (
            BusinessType.OTHER,
            ["acuity_scheduling", "google_calendar", "microsoft_outlook", "zapier"],
        ),
    ],
)
def test_each_vertical_is_offered_its_own_integrations(
    business_type: BusinessType, expected: list[str]
) -> None:
    assert [entry.id.value for entry in integrations_for(business_type)] == expected


def test_only_the_calendars_have_working_adapters() -> None:
    implemented = {entry.id for entry in CATALOG if entry.implemented}
    assert implemented == {
        IntegrationProvider.GOOGLE_CALENDAR,
        IntegrationProvider.MICROSOFT_OUTLOOK,
    }


def test_clio_does_not_claim_a_free_busy_query() -> None:
    """Clio has no availability endpoint; the catalog must not pretend it does."""
    clio = next(entry for entry in CATALOG if entry.id is IntegrationProvider.CLIO)
    assert Capability.READ_AVAILABILITY not in clio.capabilities
    assert Capability.READ_CALENDAR in clio.capabilities


async def test_the_page_shows_the_vertical_and_real_availability(
    client: AsyncClient,
) -> None:
    owner = await sign_up(client, business_type="legal")
    response = await client.get(
        f"/api/v1/tenants/{owner.tenant_id}/integrations", headers=owner.headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["business_type"] == "legal"
    by_id = {entry["id"]: entry for entry in body["integrations"]}
    assert list(by_id) == ["clio", "google_calendar", "microsoft_outlook", "zapier"]
    assert by_id["google_calendar"]["availability"] == "available"
    assert by_id["clio"]["availability"] == "coming_soon"
    assert by_id["zapier"]["availability"] == "coming_soon"
    assert {entry["status"] for entry in body["integrations"]} == {"not_connected"}


async def test_without_credentials_the_calendars_need_configuration(
    api_client: AsyncClient,
) -> None:
    """The default test app has no OAuth clients and no vault key."""
    owner = await sign_up(api_client)
    body = (
        await api_client.get(
            f"/api/v1/tenants/{owner.tenant_id}/integrations", headers=owner.headers
        )
    ).json()
    by_id = {entry["id"]: entry for entry in body["integrations"]}
    assert by_id["google_calendar"]["availability"] == "configuration_required"
    assert by_id["microsoft_outlook"]["availability"] == "configuration_required"

    refused = await api_client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/connect",
        headers=owner.headers,
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "configuration_required"


async def test_an_integration_outside_the_vertical_does_not_exist(client: AsyncClient) -> None:
    owner = await sign_up(client, business_type="salon")
    for provider in ("clio", "not-a-provider"):
        response = await client.post(
            f"/api/v1/tenants/{owner.tenant_id}/integrations/{provider}/connect",
            headers=owner.headers,
        )
        assert response.status_code == 404, provider


async def test_a_coming_soon_integration_cannot_be_connected(client: AsyncClient) -> None:
    owner = await sign_up(client, business_type="salon")
    response = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/acuity_scheduling/connect",
        headers=owner.headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "coming_soon"


# ---------------------------------------------------------------------------
# Connecting Google Calendar
# ---------------------------------------------------------------------------


async def test_the_consent_url_asks_google_for_an_offline_calendar_grant(
    client: AsyncClient,
) -> None:
    owner = await sign_up(client)
    response = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/connect",
        headers=owner.headers,
    )
    url = urlparse(response.json()["authorization_url"])
    query = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com"
    assert query["client_id"] == "google-client-id"
    assert query["redirect_uri"] == f"{API_URL}/api/v1/integrations/oauth/google_calendar/callback"
    assert query["access_type"] == "offline"
    assert "https://www.googleapis.com/auth/calendar.events" in query["scope"]
    # The secret never travels through the browser.
    assert "google-client-secret" not in response.text


async def test_connecting_google_stores_encrypted_tokens(
    client: AsyncClient, integration_app: FastAPI, fake: FakeProviders
) -> None:
    owner = await sign_up(client)
    params = _redirect_params(await _connect(client, owner))
    assert params == {"integration": "google_calendar", "connected": "google_calendar"}

    # The exchange sent the same redirect URI and the client secret, server-side.
    exchange = parse_qs(fake.last("oauth2.googleapis.com", "/token").content.decode())
    assert exchange["redirect_uri"] == [
        f"{API_URL}/api/v1/integrations/oauth/google_calendar/callback"
    ]
    assert exchange["client_secret"] == ["google-client-secret"]

    row = await _row(integration_app, owner.tenant_id, IntegrationProvider.GOOGLE_CALENDAR)
    assert row is not None
    assert row.status is IntegrationStatus.CONNECTED
    assert row.external_account_id == "owner@gmail.example.com"
    assert row.credentials_encrypted is not None
    # Ciphertext only: neither token is recoverable without the key.
    assert b"access-1" not in row.credentials_encrypted
    assert b"refresh-original" not in row.credentials_encrypted
    vault = CredentialVault(integration_app.state.settings)
    assert vault.decrypt(row.credentials_encrypted) == {
        "access_token": "access-1",
        "refresh_token": "refresh-original",
    }

    listed = (
        await client.get(f"/api/v1/tenants/{owner.tenant_id}/integrations", headers=owner.headers)
    ).json()
    google = next(entry for entry in listed["integrations"] if entry["id"] == "google_calendar")
    assert google["status"] == "connected"
    assert google["account"] == "owner@gmail.example.com"
    # The list never carries a token, in any field.
    assert "access-1" not in json.dumps(listed)

    async with integration_app.state.session_factory() as session:
        audit = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.INTEGRATION_CONNECTED)
            )
        ).scalar_one()
    assert audit.tenant_id == owner.tenant_id
    assert "access-1" not in json.dumps(audit.meta_json)


async def test_a_grant_without_offline_access_is_refused(
    client: AsyncClient, integration_app: FastAPI, fake: FakeProviders
) -> None:
    """A connection that would die in an hour is refused, not stored."""
    fake.issue_refresh_token = False
    owner = await sign_up(client)
    params = _redirect_params(await _connect(client, owner))
    assert params["integration_error"] == "no_refresh_token"
    assert await _row(integration_app, owner.tenant_id, IntegrationProvider.GOOGLE_CALENDAR) is None


# ---------------------------------------------------------------------------
# The callback's defences
# ---------------------------------------------------------------------------


async def test_the_callback_refuses_a_different_signed_in_user(
    client: AsyncClient, integration_app: FastAPI
) -> None:
    """The CSRF case: someone else's browser completing this flow gets nothing."""
    owner = await sign_up(client)
    colleague = await add_member(integration_app, owner.tenant_id, MembershipRole.ADMIN)
    colleague_token = colleague["Authorization"].removeprefix("Bearer ")

    params = _redirect_params(await _connect(client, owner, callback_token=colleague_token))
    assert params["integration_error"] == "not_permitted"
    assert await _row(integration_app, owner.tenant_id, IntegrationProvider.GOOGLE_CALENDAR) is None


async def test_the_callback_needs_a_signed_in_browser(
    client: AsyncClient, integration_app: FastAPI
) -> None:
    owner = await sign_up(client)
    started = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/connect",
        headers=owner.headers,
    )
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    response = await client.get(
        "/api/v1/integrations/oauth/google_calendar/callback",
        params={"code": "x", "state": state},
    )
    assert _redirect_params(response)["integration_error"] == "signed_out"


async def test_a_forged_or_crossed_state_is_refused(client: AsyncClient) -> None:
    owner = await sign_up(client)
    started = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/connect",
        headers=owner.headers,
    )
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]

    tampered = state[:-2] + ("AA" if not state.endswith("AA") else "BB")
    for provider, candidate in (
        ("google_calendar", tampered),
        # A valid Google state presented to the Outlook callback.
        ("microsoft_outlook", state),
    ):
        response = await client.get(
            f"/api/v1/integrations/oauth/{provider}/callback",
            params={"code": "x", "state": candidate},
            headers=_cookie(owner.token),
        )
        assert _redirect_params(response)["integration_error"] == "expired_link", provider


async def test_cancelling_at_the_consent_screen_stores_nothing(
    client: AsyncClient, integration_app: FastAPI
) -> None:
    owner = await sign_up(client)
    response = await client.get(
        "/api/v1/integrations/oauth/google_calendar/callback",
        params={"error": "access_denied", "state": "whatever"},
        headers=_cookie(owner.token),
    )
    assert _redirect_params(response)["integration_error"] == "access_denied"
    assert await _row(integration_app, owner.tenant_id, IntegrationProvider.GOOGLE_CALENDAR) is None


async def test_a_plain_member_cannot_start_a_connection(
    client: AsyncClient, integration_app: FastAPI
) -> None:
    owner = await sign_up(client)
    member = await add_member(integration_app, owner.tenant_id, MembershipRole.MEMBER)
    response = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/connect", headers=member
    )
    assert response.status_code == 403


async def test_another_tenant_cannot_see_or_touch_this_ones_integrations(
    client: AsyncClient, integration_app: FastAPI
) -> None:
    owner = await sign_up(client)
    _redirect_params(await _connect(client, owner))
    _, stranger = await other_tenant(integration_app)

    for method, suffix in (
        ("GET", ""),
        ("POST", "/google_calendar/disconnect"),
        ("POST", "/google_calendar/verify"),
    ):
        response = await client.request(
            method, f"/api/v1/tenants/{owner.tenant_id}/integrations{suffix}", headers=stranger
        )
        assert response.status_code == 404, suffix


# ---------------------------------------------------------------------------
# Using, refreshing, disconnecting
# ---------------------------------------------------------------------------


async def test_verify_asks_the_calendar_for_real_availability(
    client: AsyncClient, fake: FakeProviders
) -> None:
    owner = await sign_up(client)
    _redirect_params(await _connect(client, owner))

    response = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/verify",
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["busy_blocks"] == 2
    freebusy = fake.last("www.googleapis.com", "/freeBusy")
    assert freebusy.headers["authorization"] == "Bearer access-1"


async def test_an_expired_token_is_refreshed_and_re_encrypted(
    client: AsyncClient, integration_app: FastAPI, fake: FakeProviders
) -> None:
    owner = await sign_up(client)
    _redirect_params(await _connect(client, owner))
    async with integration_app.state.session_factory() as session:
        row = (
            await session.execute(
                select(TenantIntegration).where(TenantIntegration.tenant_id == owner.tenant_id)
            )
        ).scalar_one()
        row.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    response = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/verify",
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text
    assert fake.last("www.googleapis.com", "/freeBusy").headers["authorization"] == (
        "Bearer access-2"
    )
    row = await _row(integration_app, owner.tenant_id, IntegrationProvider.GOOGLE_CALENDAR)
    assert row is not None and row.credentials_encrypted is not None
    stored = CredentialVault(integration_app.state.settings).decrypt(row.credentials_encrypted)
    # Google did not send a new refresh token; the original is kept.
    assert stored == {"access_token": "access-2", "refresh_token": "refresh-original"}
    assert row.token_expires_at is not None and row.token_expires_at > datetime.now(UTC)


async def test_a_revoked_grant_marks_the_connection_for_reconnection(
    client: AsyncClient, integration_app: FastAPI, fake: FakeProviders
) -> None:
    owner = await sign_up(client)
    _redirect_params(await _connect(client, owner))
    async with integration_app.state.session_factory() as session:
        row = (
            await session.execute(
                select(TenantIntegration).where(TenantIntegration.tenant_id == owner.tenant_id)
            )
        ).scalar_one()
        row.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    fake.refresh_fails = True

    response = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/verify",
        headers=owner.headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "integration_revoked"

    # The error state survived the failed request.
    listed = (
        await client.get(f"/api/v1/tenants/{owner.tenant_id}/integrations", headers=owner.headers)
    ).json()
    google = next(entry for entry in listed["integrations"] if entry["id"] == "google_calendar")
    assert google["status"] == "error"
    assert "Reconnect" in google["last_error"]


async def test_disconnecting_revokes_and_deletes_the_credential(
    client: AsyncClient, integration_app: FastAPI, fake: FakeProviders
) -> None:
    owner = await sign_up(client)
    _redirect_params(await _connect(client, owner))

    response = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/disconnect",
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "not_connected"
    revoke = fake.last("oauth2.googleapis.com", "/revoke")
    assert parse_qs(revoke.content.decode())["token"] == ["refresh-original"]

    row = await _row(integration_app, owner.tenant_id, IntegrationProvider.GOOGLE_CALENDAR)
    assert row is not None
    assert row.status is IntegrationStatus.DISCONNECTED
    assert row.credentials_encrypted is None
    assert row.disconnected_at is not None

    # Disconnecting twice is a clear refusal, not a silent success.
    again = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/disconnect",
        headers=owner.headers,
    )
    assert again.status_code == 409

    # And the calendar can no longer be used.
    verify = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/verify",
        headers=owner.headers,
    )
    assert verify.status_code == 409


# ---------------------------------------------------------------------------
# Outlook
# ---------------------------------------------------------------------------


async def test_outlook_connects_and_reads_only_blocking_events(
    client: AsyncClient, integration_app: FastAPI, fake: FakeProviders
) -> None:
    owner = await sign_up(client, business_type="real_estate")
    started = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/microsoft_outlook/connect",
        headers=owner.headers,
    )
    url = urlparse(started.json()["authorization_url"])
    assert url.netloc == "login.microsoftonline.com"
    assert url.path == "/common/oauth2/v2.0/authorize"
    assert "Calendars.ReadWrite" in parse_qs(url.query)["scope"][0]

    params = _redirect_params(await _connect(client, owner, "microsoft_outlook"))
    assert params["connected"] == "microsoft_outlook"
    row = await _row(integration_app, owner.tenant_id, IntegrationProvider.MICROSOFT_OUTLOOK)
    assert row is not None and row.external_account_id == "owner@outlook.test"

    response = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/microsoft_outlook/verify",
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text
    # busy + tentative; the free and the cancelled events do not block time.
    assert response.json()["busy_blocks"] == 2
    view = fake.last("graph.microsoft.com", "/me/calendarView")
    assert view.headers["prefer"] == 'outlook.timezone="UTC"'


# ---------------------------------------------------------------------------
# The pieces, directly
# ---------------------------------------------------------------------------


def test_state_is_bound_to_provider_and_expires() -> None:
    settings = build_settings()
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    state = issue_state(
        settings,
        tenant_id=tenant_id,
        user_id=user_id,
        provider=IntegrationProvider.GOOGLE_CALENDAR,
        now=1_000_000,
    )
    granted = verify_state(
        settings, state, provider=IntegrationProvider.GOOGLE_CALENDAR, now=1_000_000
    )
    assert (granted.tenant_id, granted.user_id) == (tenant_id, user_id)

    with pytest.raises(AuthenticationError):
        verify_state(
            settings,
            state,
            provider=IntegrationProvider.GOOGLE_CALENDAR,
            now=1_000_000 + STATE_TTL_S + 1,
        )
    with pytest.raises(AuthenticationError):
        verify_state(settings, state, provider=IntegrationProvider.MICROSOFT_OUTLOOK, now=1_000_000)
    # Signed with a different secret: a different deployment's state is noise.
    with pytest.raises(AuthenticationError):
        verify_state(
            build_settings(auth_secret_key="another-secret-key-entirely-for-this-test"),
            state,
            provider=IntegrationProvider.GOOGLE_CALENDAR,
            now=1_000_000,
        )


def test_the_vault_needs_a_real_key_and_refuses_a_foreign_one() -> None:
    assert not vault_configured(build_settings())
    assert not vault_configured(build_settings(integration_encryption_key="not-a-fernet-key"))
    with pytest.raises(ConfigurationError):
        CredentialVault(build_settings())

    ours = CredentialVault(build_settings(integration_encryption_key=ENCRYPTION_KEY))
    ciphertext = ours.encrypt({"refresh_token": "secret"})
    theirs = CredentialVault(
        build_settings(integration_encryption_key=Fernet.generate_key().decode())
    )
    with pytest.raises(ConfigurationError):
        theirs.decrypt(ciphertext)


def test_the_vault_rotates_keys() -> None:
    """New key first, old key kept: old rows still decrypt."""
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    ciphertext = CredentialVault(build_settings(integration_encryption_key=old)).encrypt(
        {"refresh_token": "kept"}
    )
    rotated = CredentialVault(build_settings(integration_encryption_key=f"{new},{old}"))
    assert rotated.decrypt(ciphertext) == {"refresh_token": "kept"}
