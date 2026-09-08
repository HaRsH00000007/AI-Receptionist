"""Durable record of every webhook delivery.

Twilio and ElevenLabs both retry deliveries, and a retry that produced a second
call record would mean a second summary and a second email to the customer.
Deduplication therefore has to be durable rather than in-memory: the unique
constraint on ``(provider, event_id)`` is what makes a redelivery a visible
no-op instead of a duplicate.

The row is also the audit trail. A delivery that failed signature validation,
or whose payload could not be parsed, is stored with the reason — otherwise a
misconfigured secret looks exactly like silence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import WebhookProvider, WebhookStatus, enum_column


class WebhookEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One inbound webhook delivery, stored before it is acted upon."""

    __tablename__ = "webhook_events"

    provider: Mapped[WebhookProvider] = mapped_column(
        enum_column(WebhookProvider, "webhook_provider"), nullable=False
    )
    #: The provider's own id for the event. Falls back to a hash of the body
    #: when a provider does not supply one, so dedupe still works.
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)

    status: Mapped[WebhookStatus] = mapped_column(
        enum_column(WebhookStatus, "webhook_status"),
        nullable=False,
        default=WebhookStatus.RECEIVED,
    )
    signature_valid: Mapped[bool] = mapped_column(nullable=False, default=False)

    payload_json: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Resolved during processing; null when the event did not match a tenant.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        # The whole deduplication guarantee, in one line.
        UniqueConstraint("provider", "event_id", name="uq_webhook_events_provider_event_id"),
        Index("ix_webhook_events_provider_status", "provider", "status"),
    )
