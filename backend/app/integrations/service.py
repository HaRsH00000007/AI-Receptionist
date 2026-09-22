"""Reading and changing a tenant's integrations.

The one place that decides what a customer sees on the Integrations page, and
the only code that writes ``tenant_integrations``. Four rules hold throughout:

* **Only a completed OAuth exchange produces "connected".** There is no code
  path that marks a row connected without real tokens from the provider.
* **Offered means offered to this business type.** A provider outside the
  tenant's vertical is a 404 here, exactly as if it did not exist, so the API
  cannot be used to connect something the page would never show.
* **Credentials are encrypted before they are written**, and decrypted only to
  make a call (:mod:`app.integrations.vault`).
* **The backend makes the calendar calls.** Tokens never leave this module;
  callers ask for availability or a booking, not for a token.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, VendorError
from app.core.logging import get_logger
from app.integrations.catalog import IntegrationDefinition, definition, integrations_for, offered_to
from app.integrations.oauth_state import issue_state
from app.integrations.providers import adapter_for
from app.integrations.providers.base import (
    BusyInterval,
    CalendarEvent,
    CalendarIntegration,
    OAuthIntegration,
    TokenSet,
)
from app.integrations.vault import CredentialVault, vault_configured
from app.models import Tenant
from app.models.enums import AuditAction, IntegrationProvider, IntegrationStatus
from app.models.identity import User
from app.models.integration import TenantIntegration
from app.services.audit import Actor, AuditService

logger = get_logger(__name__)

#: Refresh this long before expiry, so a token never expires mid-request.
_REFRESH_MARGIN = timedelta(seconds=90)


class Availability(StrEnum):
    """Whether a customer can connect this integration here, today."""

    #: Connectable: an adapter exists and the operator has configured it.
    AVAILABLE = "available"
    #: An adapter exists, but this deployment has no OAuth client or no
    #: encryption key for it. An operator task, not a customer one.
    CONFIGURATION_REQUIRED = "configuration_required"
    #: On the roadmap; no adapter yet.
    COMING_SOON = "coming_soon"


@dataclass(frozen=True, slots=True)
class IntegrationEntry:
    definition: IntegrationDefinition
    availability: Availability
    connection: TenantIntegration | None


class IntegrationService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.transport = transport
        self.audit = AuditService(session)

    # ---- reads -----------------------------------------------------------
    def availability(self, provider: IntegrationProvider) -> Availability:
        adapter = adapter_for(provider, self.settings)
        if adapter is None or not definition(provider).implemented:
            return Availability.COMING_SOON
        if not adapter.client_configured() or not vault_configured(self.settings):
            return Availability.CONFIGURATION_REQUIRED
        return Availability.AVAILABLE

    async def list_for(self, tenant: Tenant) -> list[IntegrationEntry]:
        rows = {
            row.provider: row
            for row in (
                await self.session.execute(
                    select(TenantIntegration).where(TenantIntegration.tenant_id == tenant.id)
                )
            ).scalars()
        }
        return [
            IntegrationEntry(
                definition=entry,
                availability=self.availability(entry.id),
                connection=rows.get(entry.id),
            )
            for entry in integrations_for(tenant.business_type)
        ]

    async def entry(self, tenant: Tenant, provider: IntegrationProvider) -> IntegrationEntry:
        self._require_offered(tenant, provider)
        return IntegrationEntry(
            definition=definition(provider),
            availability=self.availability(provider),
            connection=await self._row(tenant.id, provider),
        )

    # ---- connecting ------------------------------------------------------
    def begin_connect(
        self,
        tenant: Tenant,
        provider: IntegrationProvider,
        *,
        user: User,
        redirect_uri: str,
    ) -> str:
        """The provider's consent URL, carrying a signed state for this flow."""
        adapter = self._connectable(tenant, provider)
        state = issue_state(self.settings, tenant_id=tenant.id, user_id=user.id, provider=provider)
        return adapter.authorization_url(state=state, redirect_uri=redirect_uri)

    async def complete_connect(
        self,
        tenant: Tenant,
        provider: IntegrationProvider,
        *,
        code: str,
        redirect_uri: str,
        actor: Actor,
    ) -> TenantIntegration:
        """Trade the authorization code for tokens and store them encrypted.

        The caller has already verified the signed state *and* that the browser
        session belongs to the user who started the flow.
        """
        adapter = self._connectable(tenant, provider)
        vault = CredentialVault(self.settings)

        tokens = await adapter.exchange_code(code=code, redirect_uri=redirect_uri)
        if not tokens.refresh_token:
            # Without one we could use the calendar for an hour and then never
            # again — a connection that silently dies. Refuse it now instead.
            raise ConflictError(
                "the provider did not grant offline access; please connect again",
                code="integration_no_refresh_token",
            )
        account = await adapter.account_identity(access_token=tokens.access_token)

        now = datetime.now(UTC)
        row = await self._row(tenant.id, provider)
        if row is None:
            row = TenantIntegration(tenant_id=tenant.id, provider=provider)
            self.session.add(row)
        row.status = IntegrationStatus.CONNECTED
        row.credentials_encrypted = vault.encrypt(tokens.to_credentials())
        row.token_expires_at = tokens.expires_at
        row.scopes = tokens.scopes or list(adapter.scopes)
        row.external_account_id = account
        row.connected_by_user_id = actor.user.id
        row.connected_at = now
        row.disconnected_at = None
        row.last_error = None
        await self.session.flush()

        self.audit.record(
            AuditAction.INTEGRATION_CONNECTED,
            actor_type=actor.actor_type,
            actor=actor.user,
            tenant_id=tenant.id,
            entity_type="tenant_integration",
            entity_id=row.id,
            # The account is the customer's own and is what they would expect to
            # see in the log. Never the tokens, never the scopes' secrets.
            meta={"provider": provider.value, "account": account},
        )
        logger.info(
            "integration connected",
            extra={"tenant_id": str(tenant.id), "provider": provider.value},
        )
        return row

    async def disconnect(
        self, tenant: Tenant, provider: IntegrationProvider, *, actor: Actor
    ) -> TenantIntegration:
        self._require_offered(tenant, provider)
        row = await self._row(tenant.id, provider)
        if row is None or row.status is IntegrationStatus.DISCONNECTED:
            raise ConflictError("this integration is not connected")

        adapter = adapter_for(provider, self.settings, self.transport)
        if adapter is not None and row.credentials_encrypted and vault_configured(self.settings):
            try:
                credentials = CredentialVault(self.settings).decrypt(row.credentials_encrypted)
                await adapter.revoke(credentials=credentials)
            except Exception:  # noqa: BLE001 - revocation is best effort
                # Revocation is hygiene. Deleting our copy below is what
                # disconnecting means, and it must happen even if the provider
                # is down or the grant is already gone.
                logger.warning(
                    "could not revoke the grant at the provider; deleting our copy anyway",
                    extra={"tenant_id": str(tenant.id), "provider": provider.value},
                )

        row.status = IntegrationStatus.DISCONNECTED
        row.credentials_encrypted = None
        row.token_expires_at = None
        row.disconnected_at = datetime.now(UTC)
        row.last_error = None

        self.audit.record(
            AuditAction.INTEGRATION_DISCONNECTED,
            actor_type=actor.actor_type,
            actor=actor.user,
            tenant_id=tenant.id,
            entity_type="tenant_integration",
            entity_id=row.id,
            meta={"provider": provider.value},
        )
        return row

    # ---- using -----------------------------------------------------------
    async def busy_intervals(
        self, tenant: Tenant, provider: IntegrationProvider, *, start: datetime, end: datetime
    ) -> list[BusyInterval]:
        adapter, token = await self._calendar(tenant, provider)
        return await self._guarded(
            tenant, provider, adapter.busy_intervals(access_token=token, start=start, end=end)
        )

    async def create_event(
        self, tenant: Tenant, provider: IntegrationProvider, event: CalendarEvent
    ) -> str:
        """Book an event, refusing if it would double-book the owner.

        The busy check and the create are two calls, so a race with another
        booking tool is possible in principle; the check still stops the
        receptionist from booking over anything it can see.
        """
        adapter, token = await self._calendar(tenant, provider)
        busy = await self._guarded(
            tenant,
            provider,
            adapter.busy_intervals(access_token=token, start=event.start, end=event.end),
        )
        if any(slot.start < event.end and event.start < slot.end for slot in busy):
            raise ConflictError("that time is already booked", code="slot_unavailable")
        return await self._guarded(
            tenant, provider, adapter.create_event(access_token=token, event=event)
        )

    # ---- internals -------------------------------------------------------
    def _require_offered(self, tenant: Tenant, provider: IntegrationProvider) -> None:
        if not offered_to(provider, tenant.business_type):
            # Indistinguishable from an unknown provider, on purpose.
            raise NotFoundError("integration not found")

    def _connectable(self, tenant: Tenant, provider: IntegrationProvider) -> OAuthIntegration:
        self._require_offered(tenant, provider)
        availability = self.availability(provider)
        if availability is Availability.COMING_SOON:
            raise ConflictError("this integration is not available yet", code="coming_soon")
        if availability is Availability.CONFIGURATION_REQUIRED:
            raise ConflictError(
                "this integration has not been set up on this server yet",
                code="configuration_required",
            )
        adapter = adapter_for(provider, self.settings, self.transport)
        assert adapter is not None  # availability() just checked
        return adapter

    async def _row(
        self, tenant_id: uuid.UUID, provider: IntegrationProvider
    ) -> TenantIntegration | None:
        return (
            await self.session.execute(
                select(TenantIntegration).where(
                    TenantIntegration.tenant_id == tenant_id,
                    TenantIntegration.provider == provider,
                )
            )
        ).scalar_one_or_none()

    async def _calendar(
        self, tenant: Tenant, provider: IntegrationProvider
    ) -> tuple[CalendarIntegration, str]:
        self._require_offered(tenant, provider)
        adapter = adapter_for(provider, self.settings, self.transport)
        if not isinstance(adapter, CalendarIntegration):
            raise NotFoundError("integration not found")
        row = await self._row(tenant.id, provider)
        if row is None or row.status is not IntegrationStatus.CONNECTED:
            raise ConflictError("connect this calendar first", code="integration_not_connected")
        return adapter, await self._access_token(tenant, adapter, row)

    async def _access_token(
        self, tenant: Tenant, adapter: OAuthIntegration, row: TenantIntegration
    ) -> str:
        """A usable access token, refreshing (and re-encrypting) when it is due."""
        assert row.credentials_encrypted is not None
        vault = CredentialVault(self.settings)
        credentials = vault.decrypt(row.credentials_encrypted)

        due = (
            row.token_expires_at is None
            or row.token_expires_at - _REFRESH_MARGIN <= datetime.now(UTC)
        )
        if not due:
            return str(credentials["access_token"])

        refresh_token = credentials.get("refresh_token")
        if not refresh_token:
            await self._mark_broken(row, "The connection expired. Reconnect to continue.")
            raise ConflictError("the connection expired; reconnect it", code="integration_expired")
        try:
            tokens: TokenSet = await adapter.refresh(refresh_token=str(refresh_token))
        except VendorError as exc:
            if not exc.retryable:
                # 400/401 from a token endpoint: the grant was revoked or the
                # password changed. Only the customer can fix that.
                await self._mark_broken(
                    row, "Access was removed at the provider. Reconnect to continue."
                )
                raise ConflictError(
                    "access was removed at the provider; reconnect it",
                    code="integration_revoked",
                ) from exc
            raise

        row.credentials_encrypted = vault.encrypt(tokens.to_credentials())
        row.token_expires_at = tokens.expires_at
        row.status = IntegrationStatus.CONNECTED
        row.last_error = None
        # Committed now, not with the request. Microsoft rotates refresh tokens
        # on use; if the call that follows fails and the request rolls back, the
        # rotated token would be lost with it.
        await self.session.commit()
        return tokens.access_token

    async def _guarded[T](
        self, tenant: Tenant, provider: IntegrationProvider, call: Awaitable[T]
    ) -> T:
        """Await a provider call, marking the connection broken on a 401."""
        try:
            return await call
        except VendorError as exc:
            if exc.status_code == 401:
                row = await self._row(tenant.id, provider)
                if row is not None:
                    await self._mark_broken(
                        row, "Access was removed at the provider. Reconnect to continue."
                    )
            raise

    async def _mark_broken(self, row: TenantIntegration, reason: str) -> None:
        """Record that the customer must reconnect — and make it stick.

        Committed immediately because the caller is about to raise, and the
        request's transaction rolls back on an error: without the commit, the
        page would keep saying "connected" about a connection that is dead.
        """
        row.status = IntegrationStatus.ERROR
        row.last_error = reason
        await self.session.commit()
