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
    #: "How did you hear about us?" — asked on the first screen and carried
    #: here, because the first screen is no longer saved on its way past.
    heard_about: str = Field(default="", max_length=200)
    plan: str = Field(default="", max_length=32)


class ConfirmRequest(StrictInput):
    """Everything the customer decided, sent in one go when they create.

    The wizard keeps its screens in the browser and posts them here, so a draft
    that is abandoned half way leaves nothing behind but the crawl it already
    paid for. Both halves are optional: a client that saved as it went (or a
    repeat of an already-confirmed request) sends neither, and the stored draft
    is used exactly as before.
    """

    knowledge: KnowledgeUpdate | None = None
    choices: ChoicesUpdate | None = None


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

    Everything describing it comes from the vendor's own catalogue — the name,
    the sentence, the sample. Nothing here is written by us: a voice described
    in our words goes stale the moment the vendor retunes it, and a sample we
    generated would be a sample of a different model than the one that answers
    the phone. See :mod:`app.services.voice_catalogue`.

    Only the first three fields are guaranteed. A deployment that cannot reach
    the catalogue still offers its configured voices, with no name, no sample
    and no sentence — fewer options and a working form, rather than invented
    copy.
    """

    voice_id: str
    #: The short name: "Sarah", not "Sarah - Mature, Reassuring, Confident".
    label: str
    #: The greeting style this voice is the default for, when it is one.
    style: str | None
    #: The line under the name: "American · Warm and friendly female voice".
    summary: str = ""
    #: The vendor's full sentence, shown when a customer opens the full list.
    description: str | None = None
    accent: str | None = None
    gender: str | None = None
    age: str | None = None
    #: Two-letter code. Only English is offered while the call path sends it.
    language: str = "en"
    #: A public MP3, played in the browser. ``None`` when there is no sample,
    #: which the picker says rather than showing a play button that does
    #: nothing.
    preview_url: str | None = None
    #: Whether to show this one before the customer asks for the full list.
    recommended: bool = False
    #: Which drawn face represents this voice. Chosen from the vendor's own
    #: gender and age labels (:mod:`app.services.avatars`), so the picture on
    #: the card matches the voice a caller will hear.
    avatar: str = ""


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
