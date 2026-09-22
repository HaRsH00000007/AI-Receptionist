"""Read and write models for the signed-in customer portal.

Kept apart from :mod:`app.schemas.views` because the rule there — nothing
carries a transcript — is what makes those views safe to serve to the anonymous
post-signup status page. The shapes here are served **only** behind an accepted
membership (``RequireMember`` and above), never behind a status grant, and one
of them carries the call transcript the business is entitled to read.

Still nothing here carries a system prompt, a vendor id, a credential or a token.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import GreetingStyle, SmsBrandType, SmsRegistrationStatus, SmsUseCase
from app.schemas.views import CallView

# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


class DashboardSummaryView(BaseModel):
    """The numbers on the Home page, over a trailing window.

    Only what is actually recorded. Hang-ups, spam, transfers, texts and booked
    events are *not* here, because nothing in the call pipeline measures them
    yet — the page says "not tracked yet" rather than showing a zero that looks
    like a measurement.
    """

    window_days: int
    window_start: datetime
    #: Conversations the receptionist had. Every stored call was answered: the
    #: row is written from the agent's post-call report.
    answered_calls: int
    #: Mean of the calls that report a duration. ``None`` with no such calls.
    average_duration_s: float | None
    urgent_calls: int
    callbacks_requested: int
    #: Distinct caller numbers in the window.
    unique_callers: int
    #: Distinct caller numbers ever — the size of the contacts list.
    total_contacts: int


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------


class TranscriptTurnView(BaseModel):
    """One line of the conversation. Speaker and words only.

    The vendor's turn objects carry timing, tool calls and internal metadata;
    none of that is the business's concern, and some of it is ours.
    """

    role: str
    message: str
    #: Seconds from the start of the call, when the vendor reported it.
    time_in_call_s: float | None = None


class CallDetailView(CallView):
    """A single call, as its business sees it."""

    to_e164: str | None
    #: Which configuration version answered — "what did it say, and why?".
    agent_config_version: int | None
    transcript: list[TranscriptTurnView]
    #: Recordings are not captured yet. Explicit so the UI can say so rather
    #: than implying a recording exists somewhere it cannot reach.
    recording_available: bool = False


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


class ContactView(BaseModel):
    """A caller, derived from the calls they made.

    Not a CRM record: there is no contacts table. Everything here is read off
    the call history, keyed on the caller's number, so it is always consistent
    with it and needs no second source of truth to keep in step.
    """

    phone_e164: str
    #: The most recent name a caller gave. Claimed, never verified.
    name: str | None
    call_count: int
    first_call_at: datetime | None
    last_call_at: datetime | None
    last_summary: str | None
    last_intent: str | None
    #: Whether any of their calls was flagged urgent (4 or 5).
    has_urgent: bool


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class AgentSettingsUpdate(BaseModel):
    """What a customer may change about their receptionist.

    Deliberately narrow. The business name and type are absent: the type picks
    the vertical (and, for shared agents, which agent answers), so changing it
    is an operator decision, not a form field. Nothing here reaches a provider
    credential, a vendor id or the prompt itself — the prompt is regenerated
    from these fields, never edited directly.
    """

    #: Comma-separated text or an already-split list, as at signup.
    services: str | list[str] = Field(min_length=1, max_length=2000)
    operating_hours: str = Field(min_length=1, max_length=1000)
    greeting_style: GreetingStyle
    #: Blank means "write one for me". Normalized exactly as at signup.
    custom_greeting: str = Field(default="", max_length=300)
    escalation_rules: str = Field(default="", max_length=2000)

    @field_validator("services")
    @classmethod
    def _services_are_real(cls, value: str | list[str]) -> list[str]:
        """The same normalizer signup uses, so an edit and a signup agree."""
        from app.services.normalization import normalize_services

        services = normalize_services(value)
        if not services:
            raise ValueError("at least one service is required")
        return services

    @field_validator("operating_hours", "escalation_rules")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return value.strip()

    @field_validator("custom_greeting")
    @classmethod
    def _greeting_is_speakable(cls, value: str) -> str:
        from app.services.normalization import normalize_greeting

        return normalize_greeting(value)


class AgentUpdateResult(BaseModel):
    """What happened when the settings were saved.

    ``synced`` is reported separately from success on purpose. The new version
    is live in our database — the source of truth — the moment this returns. A
    vendor that was unreachable leaves its copy stale, which the next sync
    repairs; it does not undo the change.
    """

    config_version: int
    previous_version: int | None
    generated_by: str
    synced: bool
    detail: str


class ConfigVersionView(BaseModel):
    """One entry in the customer's configuration history. No prompt body."""

    version: int
    is_live: bool
    generated_by: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


class RetryResult(BaseModel):
    run_id: uuid.UUID
    status: str
    steps_reset: list[str]


# ---------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------


class IntegrationView(BaseModel):
    """One card on the Integrations page.

    ``availability`` is whether this deployment can connect it at all;
    ``status`` is this tenant's connection. They are separate so the page can
    say "coming soon" and "not connected" without conflating the two — and so
    that no combination of them can read as connected without a real grant.
    """

    id: str
    name: str
    vendor: str
    category: str
    description: str
    icon: str
    auth_type: str
    capabilities: list[str]
    #: ``available`` | ``configuration_required`` | ``coming_soon``
    availability: str
    #: ``not_connected`` | ``connected`` | ``error``
    status: str
    #: The connected account (an email address), when connected.
    account: str | None = None
    connected_at: datetime | None = None
    #: Why a connection needs attention, in words the customer can act on.
    last_error: str | None = None


class IntegrationListView(BaseModel):
    business_type: str
    integrations: list[IntegrationView]


class ConnectStart(BaseModel):
    """Where to send the browser next: the provider's consent screen."""

    authorization_url: str


class IntegrationCheckView(BaseModel):
    """The result of asking a connected calendar for real availability."""

    ok: bool
    window_start: datetime
    window_end: datetime
    busy_blocks: int


# ---------------------------------------------------------------------------
# SMS compliance
# ---------------------------------------------------------------------------

_EIN = re.compile(r"^\d{2}-?\d{7}$")


class SmsRegistrationInput(BaseModel):
    """The brand and campaign details, as the customer fills them in.

    Every field is optional here because a draft is saved as it is typed.
    Completeness is checked on *submit*, against the rules for the brand type,
    and reported field by field.
    """

    model_config = ConfigDict(extra="forbid")

    brand_type: SmsBrandType = SmsBrandType.STANDARD
    legal_business_name: str | None = Field(default=None, max_length=255)
    tax_id: str | None = Field(default=None, max_length=32)
    website: str | None = Field(default=None, max_length=255)
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=32)
    use_case: SmsUseCase | None = None
    campaign_description: str | None = Field(default=None, max_length=2000)
    sample_messages: list[str] = Field(default_factory=list, max_length=5)
    opt_in_description: str | None = Field(default=None, max_length=2000)

    @field_validator(
        "legal_business_name",
        "tax_id",
        "website",
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "contact_name",
        "contact_phone",
        "campaign_description",
        "opt_in_description",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("tax_id")
    @classmethod
    def _ein_shape(cls, value: str | None) -> str | None:
        if value is not None and not _EIN.match(value):
            raise ValueError("an EIN has nine digits, like 12-3456789")
        return value

    @field_validator("website")
    @classmethod
    def _website_is_a_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.lower().startswith(("http://", "https://")):
            value = f"https://{value}"
        if "." not in value.split("://", 1)[1]:
            raise ValueError("enter your website's address, like https://example.com")
        return value

    @field_validator("contact_phone")
    @classmethod
    def _phone_is_us(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from app.core.errors import InvalidInputError
        from app.services.normalization import normalize_phone

        try:
            return normalize_phone(value)
        except InvalidInputError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("sample_messages")
    @classmethod
    def _samples_trimmed(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(message.split()) for message in value]
        for message in cleaned:
            if len(message) > 320:
                raise ValueError("keep each sample message under 320 characters")
        return [message for message in cleaned if message]


class SmsRegistrationView(BaseModel):
    """The registration as its business sees it. No Twilio ids."""

    status: str
    brand_type: str
    legal_business_name: str | None
    tax_id: str | None
    website: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    region: str | None
    postal_code: str | None
    country: str
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    use_case: str | None
    campaign_description: str | None
    sample_messages: list[str]
    opt_in_description: str | None
    rejection_reason: str | None
    submitted_at: datetime | None
    approved_at: datetime | None
    enabled_at: datetime | None


class SmsStateView(BaseModel):
    """Where SMS stands for a tenant, and what they may do next.

    ``state`` extends the stored status with the two states that are read off
    the tenant rather than stored: ``not_configured`` (no number to text from
    yet) and ``compliance_required`` (a number, but no registration started).
    """

    state: str
    phone_number: str | None
    #: The one bit the campaign UI must obey. True only when ``enabled``.
    can_send: bool
    #: Whether the customer can still edit and (re)submit the registration.
    editable: bool
    registration: SmsRegistrationView | None


class SmsReviewDecision(BaseModel):
    """An operator recording the outcome of review. Never customer-callable."""

    model_config = ConfigDict(extra="forbid")

    status: SmsRegistrationStatus
    rejection_reason: str | None = Field(default=None, max_length=2000)
    twilio_customer_profile_sid: str | None = Field(default=None, max_length=64)
    twilio_brand_sid: str | None = Field(default=None, max_length=64)
    twilio_campaign_sid: str | None = Field(default=None, max_length=64)
    twilio_messaging_service_sid: str | None = Field(default=None, max_length=64)
