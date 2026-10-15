"""A website-based setup in progress, before any business exists.

Keyed on the **user**, not a tenant, because the whole point is that it runs
before a tenant is created: a crawl and an LLM call take too long for a request,
so the work is a job and the customer may close the tab while it runs.

One row per user, like :class:`app.models.onboarding.OnboardingDraft`. Starting
a new website setup replaces the old one; keeping a history of abandoned drafts
would mean deciding which one "Resume" means.

Everything the customer edits on the review screens lives in JSONB rather than
in child tables. These are drafts: they are rewritten wholesale on every save,
never queried by field, and they stop mattering the moment the business is
created — at which point the parts that matter are copied onto the business
profile, which is what the agent is actually built from.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import WebsiteOnboardingStatus, enum_column


class WebsiteOnboarding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One attempt at building a receptionist from a business website."""

    __tablename__ = "website_onboardings"

    #: The account that started it. Unique: one website setup at a time.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    #: Set only once the customer has confirmed and the business exists. It is
    #: the evidence that this draft was used, and what makes ``confirm``
    #: idempotent: a second confirm returns the business instead of making one.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True
    )

    #: Normalized and SSRF-checked by ``app.website.guard`` before it is stored.
    #: Never trusted on the way back out: it is re-checked before every fetch.
    website_url: Mapped[str] = mapped_column(String(2048), nullable=False)

    status: Mapped[WebsiteOnboardingStatus] = mapped_column(
        enum_column(WebsiteOnboardingStatus, "website_onboarding_status"),
        nullable=False,
        default=WebsiteOnboardingStatus.PENDING,
    )

    # ---- job bookkeeping, the same shape as provisioning_runs ----
    attempt: Mapped[int] = mapped_column(nullable=False, default=0)
    #: Doubles as the worker's lease: a claimed job is pushed into the future,
    #: so a worker that dies mid-crawl releases it when the lease lapses.
    next_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: What to tell the customer. A sentence, never a stack trace or a URL we
    #: refused to fetch — see ``app.website.guard`` on not leaking the network.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # ---- what the crawl found ----
    #: Counts and reasons: pages read, skipped, failed, duplicates. Shown on the
    #: review screen so a thin result is explained rather than mysterious.
    crawl_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    #: The structured draft: knowledge items, suggested caller questions and
    #: suggested SMS templates, each with the page it came from.
    knowledge_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    #: The customer's choices: voice, greeting, area code, chosen number, and
    #: the basics (name, email, phone, how they heard about us).
    choices_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    __table_args__ = (
        # The claim query: status in the claimable set, then due. Mirrors
        # ix_provisioning_runs_status_next_attempt_at.
        Index("ix_website_onboardings_status_next_attempt_at", "status", "next_attempt_at"),
    )
