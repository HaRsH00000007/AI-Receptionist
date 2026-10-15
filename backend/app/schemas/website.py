"""Request and response models for website-based setup.

User-scoped, not tenant-scoped: all of this happens before a business exists,
so the session's user is the only owner there is.

What a customer sends back on the review screens is **not** trusted to be what
we sent them. Every field is length-capped and every list is count-capped here,
because the draft ends up in an LLM prompt and then in a receptionist's
instructions: a 40,000-character "knowledge item" is a bill, and an item added
by hand is as much a part of the agent as one we extracted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import BusinessType, GreetingStyle, WebsiteOnboardingStatus

MAX_ITEMS = 40
MAX_QUESTIONS = 20
MAX_TEMPLATES = 10


class StrictInput(BaseModel):
    """Reject unknown keys, strip whitespace. The house style for writes."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Starting a crawl
# ---------------------------------------------------------------------------


class WebsiteSetupStart(StrictInput):
    """Step 1 and 2 of the flow: who you are, and where your website is.

    The URL is validated properly by :mod:`app.website.guard`, not here — a
    pydantic ``HttpUrl`` would accept ``http://localhost`` and
    ``http://169.254.169.254`` happily.
    """

    website_url: str = Field(min_length=3, max_length=2048)
    full_name: str = Field(default="", max_length=120)
    #: Where call summaries go. Defaults to the account's own address.
    notification_email: EmailStr | None = None
    contact_phone: str = Field(default="", max_length=32)
    #: "How did you hear about us?" — stored with the draft, never required.
    heard_about: str = Field(default="", max_length=200)


# ---------------------------------------------------------------------------
# The draft a customer edits
# ---------------------------------------------------------------------------


class KnowledgeItemInput(StrictInput):
    id: str = Field(default="", max_length=40)
    category: str = Field(default="business_description", max_length=40)
    title: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=1200)
    source_url: str = Field(default="", max_length=2048)
    needs_review: bool = False
    enabled: bool = True


class CallerQuestionInput(StrictInput):
    key: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=80)
    prompt: str = Field(default="", max_length=300)
    #: Name, email and phone. A customer may switch these off but not rename
    #: them: the agent's own instructions depend on their keys.
    builtin: bool = False
    enabled: bool = True


class SmsTemplateInput(StrictInput):
    key: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=320)
    enabled: bool = False


class KnowledgeUpdate(StrictInput):
    """A whole-draft save. Simpler than per-item patches, and the draft is
    small enough that sending it all back costs nothing."""

    items: list[KnowledgeItemInput] = Field(default_factory=list, max_length=MAX_ITEMS)
    questions: list[CallerQuestionInput] = Field(default_factory=list, max_length=MAX_QUESTIONS)
    sms_templates: list[SmsTemplateInput] = Field(default_factory=list, max_length=MAX_TEMPLATES)
    #: Editable because a crawl can read the name wrong, and because a business
    #: with a thin website needs to be able to fill these in by hand.
    business_name: str = Field(default="", max_length=200)
    business_summary: str = Field(default="", max_length=1200)
    operating_hours: str = Field(default="", max_length=600)
    services: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("services")
    @classmethod
    def _tidy(cls, value: list[str]) -> list[str]:
        return [entry.strip()[:80] for entry in value if entry.strip()]


class ChoicesUpdate(StrictInput):
    """Voice, greeting and the number search — the last few screens.

    ``voice_id`` is checked against the configured voice map by the route, so a
    customer cannot pick a voice this deployment has not been given.
    """

    business_type: BusinessType | None = None
    greeting_style: GreetingStyle | None = None
    custom_greeting: str = Field(default="", max_length=300)
    voice_id: str = Field(default="", max_length=64)
    #: Accepted so the UI can state what it set; English is the only value the
    #: call path sends today, and the route refuses anything else rather than
    #: pretending otherwise.
    language: str = Field(default="en", max_length=10)
    recording_acknowledged: bool = False
    area_code: str = Field(default="", max_length=10)
    selected_number: str = Field(default="", max_length=20)
    escalation_rules: str = Field(default="", max_length=2000)
    notification_email: EmailStr | None = None
    contact_phone: str = Field(default="", max_length=32)
    full_name: str = Field(default="", max_length=120)
    plan: str = Field(default="", max_length=32)


# ---------------------------------------------------------------------------
# What we send back
# ---------------------------------------------------------------------------


class CrawlSummaryView(BaseModel):
    """Shown on the review screen so a thin result is explained, not mysterious."""

    site: str
    pages_read: int
    page_urls: list[str]
    skipped: int
    failed: int
    duplicates: int
    robots_blocked: int
    truncated: bool
    reasons: dict[str, int]


class VoiceOptionView(BaseModel):
    """A voice this deployment can actually use.

    Read from configuration, never invented: offering a voice that is not in
    ``ELEVENLABS_VOICE_MAP`` would fail at the vendor on publish.
    """

    voice_id: str
    label: str
    #: The greeting style this voice is the default for, when it is one.
    style: str | None


class WebsiteSetupView(BaseModel):
    """The whole job, as the wizard sees it."""

    id: uuid.UUID
    status: WebsiteOnboardingStatus
    website_url: str
    attempt: int
    #: A sentence for the customer. Never an exception or an internal address.
    last_error: str | None
    #: True once there is something to review.
    ready: bool
    tenant_id: uuid.UUID | None
    crawl: CrawlSummaryView | None
    knowledge: dict[str, Any] | None
    choices: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
