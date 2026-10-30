"""Appointments the receptionist booked into a business's own calendar.

The calendar is the truth about the owner's time; this table is the record of
what *we* put there, and it exists for three reasons the calendar cannot serve:

* **Idempotency.** A voice agent can call a tool twice for one request — a
  retried webhook, a model that repeats itself after a slow answer. Each
  booking carries a key derived from the conversation and the slot, unique per
  tenant, so the second call finds the first booking instead of making another.
* **Our own overlap check.** Two callers can ask for the same slot within the
  second it takes the calendar to show the first booking as busy. The booking
  path serializes per tenant and checks this table as well as the calendar.
* **The dashboard.** A call's detail can show the appointment it produced
  without asking Google for it.

A row is written only once the calendar event exists, so there is no "pending"
booking. Caller details are what the caller said on the phone: personal data,
deleted with the tenant.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AppointmentStatus, IntegrationProvider, enum_column


class Appointment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One event the receptionist created in a connected calendar."""

    __tablename__ = "appointments"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: Which calendar holds the event, and its id there — what cancelling it
    #: later needs.
    provider: Mapped[IntegrationProvider] = mapped_column(
        enum_column(IntegrationProvider, "appointment_provider"), nullable=False
    )
    external_event_id: Mapped[str] = mapped_column(String(1024), nullable=False)

    status: Mapped[AppointmentStatus] = mapped_column(
        enum_column(AppointmentStatus, "appointment_status"),
        nullable=False,
        default=AppointmentStatus.BOOKED,
    )

    starts_at: Mapped[datetime] = mapped_column(nullable=False)
    ends_at: Mapped[datetime] = mapped_column(nullable=False)
    #: The business's timezone when it was booked, so the time can be read back
    #: the way the caller heard it even if the business later moves.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    caller_name: Mapped[str] = mapped_column(String(120), nullable=False)
    caller_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    caller_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The ElevenLabs conversation that made it. Joins to ``calls`` once the
    #: post-call webhook has written that row.
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Same request, same key: see the module docstring.
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_appointments_tenant_key"),
        # Every overlap check is "this tenant's bookings around this time".
        Index("ix_appointments_tenant_starts_at", "tenant_id", "starts_at"),
        Index("ix_appointments_conversation", "conversation_id"),
    )
