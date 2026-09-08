"""What the LLM is allowed to return, and what a call summary looks like.

Both models are ``extra="forbid"``. A model that invents a field should fail
validation and trigger the repair retry rather than have the field quietly
dropped — a config that is silently missing half of what the model produced is
worse than one that failed loudly.

Note what is *not* here: no voice id, no phone number, no area code, no model
name, no provisioning status. The LLM describes the business; deterministic code
picks the infrastructure (docs/00_DECISIONS.md section 3).
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.business import BusinessHours, EscalationPolicy, StrictModel


class FAQItem(StrictModel):
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(min_length=1, max_length=1000)


class GeneratedAgentConfig(StrictModel):
    """The structured reading of one business's signup form."""

    business_summary: str = Field(min_length=1, max_length=1000)
    services: list[str] = Field(min_length=1, max_length=40)
    hours: BusinessHours
    greeting: str = Field(min_length=1, max_length=300)
    tone: str = Field(default="professional", max_length=40)
    escalation: EscalationPolicy
    call_handling_instructions: list[str] = Field(default_factory=list, max_length=20)
    fallback_behavior: str = Field(default="", max_length=1000)
    faq: list[FAQItem] = Field(default_factory=list, max_length=20)


class CallSummary(StrictModel):
    """The structured reading of one call transcript."""

    summary: str = Field(min_length=1, max_length=2000)
    caller_name: str | None = Field(default=None, max_length=200)
    #: Claimed by the caller, never verified. Cross-checked against the Twilio
    #: caller id before display, and never auto-dialled.
    callback_number: str | None = Field(default=None, max_length=32)
    intent: str | None = Field(default=None, max_length=64)
    reason_for_call: str | None = Field(default=None, max_length=500)
    key_details: list[str] = Field(default_factory=list, max_length=20)
    requested_follow_up: str | None = Field(default=None, max_length=500)
    urgency: int = Field(default=1, ge=1, le=5)
    needs_human: bool = False
    ai_handled_successfully: bool = True
