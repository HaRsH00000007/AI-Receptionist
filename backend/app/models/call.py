"""Calls, transcripts and the post-call intelligence derived from them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CallDirection, CallStatus, enum_column

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class Call(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One conversation, plus what the summarizer made of it.

    ``provider_call_id`` is globally unique, which is the whole webhook-replay
    defence in the POC: ElevenLabs retries its post-call webhook, and a second
    delivery must not produce a second row, a second summary and a second email
    (docs/01_PLAN_POC.md, "UNIQUE (provider_call_id) on calls").

    ``callback_number`` is written from the LLM's reading of the transcript and
    is therefore *claimed*, not verified. It is cross-checked against Twilio's
    caller id before display and is never auto-dialled
    (docs/00_DECISIONS.md section 3).
    """

    __tablename__ = "calls"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    provider_call_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    direction: Mapped[CallDirection] = mapped_column(
        enum_column(CallDirection, "call_direction"),
        nullable=False,
        default=CallDirection.INBOUND,
    )

    from_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    duration_s: Mapped[int | None] = mapped_column(nullable=True)

    #: Vendor-shaped. Left untyped because the POC does not yet own the media
    #: path and should not invent a schema for someone else's payload.
    transcript_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    caller_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    callback_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    urgency: Mapped[int | None] = mapped_column(nullable=True)

    status: Mapped[CallStatus] = mapped_column(
        enum_column(CallStatus, "call_status"),
        nullable=False,
        default=CallStatus.RECEIVED,
    )

    # Summarization is an external call that can fail, so it needs the same
    # durable retry bookkeeping a provisioning step has. Without it a transient
    # LLM outage would silently drop a customer's message.
    attempt: Mapped[int] = mapped_column(nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="calls")

    __table_args__ = (
        CheckConstraint(
            "urgency IS NULL OR (urgency >= 1 AND urgency <= 5)",
            name="urgency_within_range",
        ),
        CheckConstraint(
            "duration_s IS NULL OR duration_s >= 0",
            name="duration_not_negative",
        ),
        # The dashboard's only real query: this tenant's calls, newest first.
        Index("ix_calls_tenant_id_started_at", "tenant_id", "started_at"),
        # Serves the call-processing poller.
        Index("ix_calls_status_next_attempt_at", "status", "next_attempt_at"),
    )
