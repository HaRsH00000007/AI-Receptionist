"""Normalized provider data.

Every provider returns one of these, never a vendor SDK object or a raw dict.
Business logic then depends on a shape we own: swapping SendGrid for Resend, or
adopting a new Twilio API version, changes one adapter instead of every caller.
"""

from __future__ import annotations

from typing import Any, Literal

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


class Delivery(ProviderModel):
    """How the agent speaks, as opposed to what it says.

    Two settings the vendor owns, carried together because they are chosen
    together and pushed together:

    * ``speed`` is a multiplier on the voice. ElevenLabs accepts 0.7 to 1.2 and
      refuses anything else, which was checked against the live API.
    * ``eagerness`` is the vendor's own word — ``patient``, ``normal`` or
      ``eager`` — for how long it waits before deciding a caller has finished.
      Translated from our own enum by the service, so the adapter is handed the
      vendor's vocabulary and nothing has to guess at it twice.

    ``None`` on either means "send nothing and take the vendor's default",
    which is how every agent behaved before these were configurable.
    """

    speed: float | None = None
    eagerness: str | None = None


class VoiceRef(ProviderModel):
    """One voice the vendor offers, as the vendor describes it.

    Everything here comes from ElevenLabs' own catalogue — the name, the
    sentence under it and the sample a customer listens to. None of it is
    written by us, because a voice described in our words is a description that
    goes stale the moment the vendor retunes it, and a sample we generated is a
    sample of a different model than the one that answers the phone.

    Every field but the id is optional. The catalogue is read defensively: a
    voice that loses its ``description`` should lose a line of copy, not drop
    out of the picker.
    """

    voice_id: str
    #: As listed, which is often "Sarah - Mature, Reassuring, Confident". The
    #: short name and the tagline are separated where it is shown, not here.
    name: str
    description: str | None = None
    #: From the vendor's labels: "american", "british", "female", "young"...
    accent: str | None = None
    gender: str | None = None
    age: str | None = None
    use_case: str | None = None
    #: Two-letter code. Only English voices are offered today, because the call
    #: path sends ``"language": "en"`` and nothing else.
    language: str | None = None
    #: A public MP3 of this voice, played in the browser. ``None`` when the
    #: vendor has no sample, which the picker shows as "no sample".
    preview_url: str | None = None
    #: ``premade``, ``professional``, ``cloned``... Shown nowhere; used to keep
    #: the recommended list to voices the account is entitled to.
    category: str | None = None


class TransferTarget(ProviderModel):
    """One destination the voice agent may hand a live call to.

    The provider layer's own shape, deliberately not the ``TransferRule`` row:
    an adapter receives where to send the call and when, and nothing about who
    owns the rule, whether it is switched on, or where it sits in a list.

    ``kind`` is ours, not the vendor's — ``warm`` or ``cold`` — and each adapter
    maps it to whatever that vendor calls the same thing.
    """

    e164: str
    #: Natural language. The vendor's model reads this to decide whether a call
    #: is one of the ones this destination should get.
    condition: str
    kind: Literal["warm", "cold"] = "warm"


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
