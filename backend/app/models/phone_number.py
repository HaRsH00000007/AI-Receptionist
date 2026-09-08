"""Twilio numbers. Every row here is a recurring monthly charge."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PhoneNumberStatus, enum_column

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class PhoneNumber(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A number bought from Twilio on a tenant's behalf.

    An orphaned row here bills forever, so the lifecycle is explicit:
    ``PENDING`` is written before Twilio is called, ``ACTIVE`` once the SID comes
    back, ``RELEASED`` once the number is handed back. ``released_at`` is the
    evidence the nightly reaper acted (docs/01_PLAN_POC.md, compensation).

    The partial unique index on ``tenant_id`` is the constraint the plan calls
    out by name: a tenant cannot own two live numbers, whatever the worker
    believes.
    """

    __tablename__ = "phone_numbers"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    e164: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Null only while ``PENDING``. Twilio SIDs are globally unique and never
    #: reused, which makes this the natural idempotency anchor for a purchase.
    twilio_sid: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    area_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    #: ElevenLabs assigns its own id when the Twilio number is imported. It is
    #: a different identifier from the Twilio SID and is what the assign and
    #: verify steps address, so it has to be stored rather than re-derived.
    elevenlabs_phone_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    status: Mapped[PhoneNumberStatus] = mapped_column(
        enum_column(PhoneNumberStatus, "phone_number_status"),
        nullable=False,
        default=PhoneNumberStatus.PENDING,
    )

    purchased_at: Mapped[datetime | None] = mapped_column(nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="phone_numbers")

    __table_args__ = (
        # "UNIQUE (tenant_id) WHERE status = 'active'" - docs/01_PLAN_POC.md.
        Index(
            "uq_phone_numbers_active_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text(f"status = '{PhoneNumberStatus.ACTIVE.value}'"),
        ),
        # The same number cannot be live for two tenants. Released numbers are
        # excluded because Twilio may reissue a number to someone else later.
        Index(
            "uq_phone_numbers_active_e164",
            "e164",
            unique=True,
            postgresql_where=text(f"status = '{PhoneNumberStatus.ACTIVE.value}'"),
        ),
        # A number that claims to be provisioned must carry the SID that proves
        # it was really bought. PENDING and FAILED are exempt precisely because
        # they describe a purchase that never completed: PENDING is intent
        # recorded before the call, FAILED is what compensation marks a row that
        # never reached the vendor. Requiring a SID there would make the
        # compensation path unable to record its own outcome.
        CheckConstraint(
            f"status IN ('{PhoneNumberStatus.PENDING.value}', "
            f"'{PhoneNumberStatus.FAILED.value}') OR twilio_sid IS NOT NULL",
            name="twilio_sid_required_once_purchased",
        ),
        CheckConstraint(
            f"status <> '{PhoneNumberStatus.RELEASED.value}' OR released_at IS NOT NULL",
            name="released_at_required_once_released",
        ),
    )
