"""A tenant's connection to a third-party service."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import IntegrationProvider, IntegrationStatus, enum_column


class TenantIntegration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One provider, connected (or once connected) by one tenant.

    **Credentials are ciphertext.** ``credentials_encrypted`` is a Fernet token
    over the provider's token response, keyed by ``INTEGRATION_ENCRYPTION_KEY``.
    Nothing in this row is usable without that key, so a database dump or a
    misdirected backup does not hand anyone a customer's calendar. There is no
    plaintext column to fall back to, by design.

    **Disconnecting deletes the credential, not the row.** The row stays as the
    record that the connection existed, who made it and when it ended; the
    ciphertext is set to NULL so there is nothing left to leak or to use.

    One row per (tenant, provider): reconnecting reuses it.
    """

    __tablename__ = "tenant_integrations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[IntegrationProvider] = mapped_column(
        enum_column(IntegrationProvider, "integration_provider"), nullable=False
    )
    status: Mapped[IntegrationStatus] = mapped_column(
        enum_column(IntegrationStatus, "integration_status"),
        nullable=False,
        default=IntegrationStatus.CONNECTED,
    )

    #: The account at the provider, as the customer would recognise it — an
    #: email address for Google and Microsoft. Shown on the card; never used
    #: for authorization.
    external_account_id: Mapped[str | None] = mapped_column(String(320), nullable=True)

    credentials_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    #: When the access token inside the ciphertext expires, so a caller knows
    #: to refresh without decrypting first.
    token_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: The scopes the provider actually granted, which can be fewer than asked.
    scopes: Mapped[list[str]] = mapped_column(nullable=False, default=list)

    connected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    connected_at: Mapped[datetime | None] = mapped_column(nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Provider-specific, non-secret settings (a default calendar id, say).
    settings_json: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (
        # Also serves every "this tenant's integrations" lookup: tenant_id leads.
        UniqueConstraint("tenant_id", "provider", name="uq_tenant_integrations_tenant_provider"),
    )
