"""Normalized provider data.

Every provider returns one of these, never a vendor SDK object or a raw dict.
Business logic then depends on a shape we own: swapping SendGrid for Resend, or
adopting a new Twilio API version, changes one adapter instead of every caller.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProviderModel(BaseModel):
    """Frozen, and tolerant of extra vendor fields we do not model."""

    model_config = ConfigDict(frozen=True, extra="ignore")


# ---------------------------------------------------------------------------
# Telephony
# ---------------------------------------------------------------------------
class AvailableNumber(ProviderModel):
    """A number Twilio is offering for sale."""

    e164: str
    locality: str | None = None
    region: str | None = None
    iso_country: str = "US"
    #: Present so the purchase step can reject a number that cannot take calls.
    voice_enabled: bool = True


class PurchasedNumber(ProviderModel):
    """A number we own."""

    sid: str
    e164: str
    #: We always set this to ``tenant:{tenant_id}``, which is what makes an
    #: interrupted purchase adoptable instead of repeatable.
    friendly_name: str | None = None


# ---------------------------------------------------------------------------
# Voice agent
# ---------------------------------------------------------------------------
class AgentRef(ProviderModel):
    """An ElevenLabs conversational agent, as the vendor reports it."""

    agent_id: str
    name: str
    voice_id: str | None = None
    first_message: str | None = None
    system_prompt: str | None = None


class PhoneNumberRef(ProviderModel):
    """A phone number as ElevenLabs sees it, after import."""

    phone_id: str
    e164: str
    #: ``None`` until the number is assigned. The verify step asserts this
    #: matches our agent rather than assuming the assign call worked.
    assigned_agent_id: str | None = None
    label: str | None = None


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
class LLMResponse(ProviderModel):
    """Raw text from a model, plus what produced it."""

    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
class EmailMessage(ProviderModel):
    to: str
    subject: str
    html: str
    text: str


class EmailResult(ProviderModel):
    message_id: str
    provider: str


# ---------------------------------------------------------------------------
# Inbound webhook payloads, normalized
# ---------------------------------------------------------------------------
class PostCallEvent(ProviderModel):
    """An ElevenLabs post-call webhook, reduced to what we store.

    Deliberately narrow: the raw payload carries far more than the POC needs,
    and transcripts are sensitive. Only the turns are kept, and only as text.
    """

    provider_call_id: str
    agent_id: str | None = None
    called_number: str | None = None
    caller_number: str | None = None
    started_at_epoch: int | None = None
    duration_s: int | None = None
    transcript: list[dict[str, Any]] = Field(default_factory=list)

    def transcript_text(self) -> str:
        """The conversation as plain text, for the summarizer."""
        lines: list[str] = []
        for turn in self.transcript:
            role = str(turn.get("role", "unknown"))
            message = str(turn.get("message") or turn.get("text") or "").strip()
            if message:
                lines.append(f"{role}: {message}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
class CustomerRef(ProviderModel):
    """A billing customer at the processor.

    Deliberately thin. Everything the application decides with — plan, status,
    entitlement — lives in our ``subscriptions`` table; this is only the handle
    needed to talk to Stripe about it.
    """

    customer_id: str
    email: str | None = None


class CheckoutSession(ProviderModel):
    """A hosted checkout the customer is redirected to.

    We never see card details: they go to Stripe's page, not ours, which keeps
    the application out of PCI scope entirely.
    """

    session_id: str
    url: str


class BillingPortalSession(ProviderModel):
    """A hosted portal for changing a card or cancelling."""

    url: str


class SubscriptionRef(ProviderModel):
    """A subscription as the processor reports it.

    ``status`` is the processor's own vocabulary — "trialing", "past_due" and so
    on. It is mapped onto our :class:`~app.models.enums.SubscriptionStatus` in
    the billing service, never used raw, so that a change to Stripe's status set
    is an adapter concern rather than a business-logic one.
    """

    subscription_id: str
    customer_id: str
    status: str
    price_id: str | None = None
    current_period_start: int | None = None
    current_period_end: int | None = None
    trial_end: int | None = None
    cancel_at_period_end: bool = False


class PaymentEvent(ProviderModel):
    """A verified webhook event from the processor."""

    event_id: str
    event_type: str
    #: The already-parsed payload. The raw bytes stay in ``webhook_events`` for
    #: replay; this is the shape the handler works with.
    data: dict[str, Any] = Field(default_factory=dict)
