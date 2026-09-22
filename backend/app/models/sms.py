"""SMS compliance — a tenant's A2P 10DLC registration."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    SmsBrandType,
    SmsRegistrationStatus,
    SmsUseCase,
    enum_column,
)


class SmsRegistration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """What a business submits so its number may send texts, and where that stands.

    US carriers refuse application-to-person SMS from a 10-digit number that is
    not registered to a vetted **brand** (the business) and **campaign** (what
    the texts are for). The fields here are the ones Twilio's brand and campaign
    registration asks for, collected once and kept, so that submitting to Twilio
    is a mapping rather than a second form.

    Status only moves forward through review. The customer can edit a DRAFT or a
    REJECTED registration and submit it; nothing they send can set APPROVED or
    ENABLED — those are written by review (an operator today, Twilio's status
    callbacks once the submission is automated). ``can_send`` on the service is
    the one place that decides whether a campaign may go out.

    One per tenant.
    """

    __tablename__ = "sms_registrations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[SmsRegistrationStatus] = mapped_column(
        enum_column(SmsRegistrationStatus, "sms_registration_status"),
        nullable=False,
        default=SmsRegistrationStatus.DRAFT,
    )

    # ---- brand: who is sending ------------------------------------------
    brand_type: Mapped[SmsBrandType] = mapped_column(
        enum_column(SmsBrandType, "sms_brand_type"),
        nullable=False,
        default=SmsBrandType.STANDARD,
    )
    legal_business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The EIN for a standard brand. A business identifier, not a personal one;
    #: required by carriers and shown back only to the tenant's own members.
    tax_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="US")
    contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ---- campaign: what is being sent -----------------------------------
    use_case: Mapped[SmsUseCase | None] = mapped_column(
        enum_column(SmsUseCase, "sms_use_case"), nullable=True
    )
    campaign_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Examples of real messages, as carriers require (at least two).
    sample_messages: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    #: How recipients agree to receive texts — the "message flow" carriers vet.
    opt_in_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- review ----------------------------------------------------------
    #: Twilio object ids, filled as the submission is registered with Twilio.
    #: Identifiers, not credentials; kept for reconciliation and support.
    twilio_customer_profile_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    twilio_brand_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    twilio_campaign_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    twilio_messaging_service_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Why review said no, in words the customer can act on.
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    enabled_at: Mapped[datetime | None] = mapped_column(nullable=True)
