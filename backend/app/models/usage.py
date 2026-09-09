"""Metered usage: the raw events, and the aggregates billing reads.

Two tables rather than one, because they answer different questions and are
written by different things. ``usage_events`` is an append-only ledger — one row
per measurable thing that happened, never updated. ``usage_daily`` is a rollup
the dashboard and the plan-limit check read, rebuilt nightly from the ledger.

The rule this schema exists to enforce: **an in-app measurement is an estimate,
not an invoice.** A call the webhook delivered twice, or never delivered, makes
our minute count wrong in a way that is invisible until a customer disputes it.
So every row records where its number came from (:class:`UsageSource`), and a
provider-reported figure supersedes a measured one during reconciliation rather
than being added to it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import UsageKind, UsageSource, enum_column

if TYPE_CHECKING:
    pass


class UsageEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One metered occurrence. Append-only; never updated in place.

    A correction is a new row — including a negative one — rather than an edit,
    so the ledger always reconstructs how a total was reached. Editing history
    to make a total look right is how billing disputes become unwinnable.
    """

    __tablename__ = "usage_events"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: The call this usage belongs to, where there is one. Nullable because a
    #: phone number's monthly charge belongs to no particular call.
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calls.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[UsageKind] = mapped_column(enum_column(UsageKind, "usage_kind"), nullable=False)
    source: Mapped[UsageSource] = mapped_column(
        enum_column(UsageSource, "usage_source"),
        nullable=False,
        default=UsageSource.MEASURED,
    )
    #: Which vendor incurred it — "twilio", "elevenlabs", "anthropic". A plain
    #: string rather than an enum: the set changes with commercial decisions,
    #: not with code, and a migration per vendor swap is friction for no safety.
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: The measured amount in the smallest sensible unit for ``kind`` — seconds
    #: for minutes, tokens for LLM, characters for TTS. Integers throughout: a
    #: float total that disagrees with the sum of its parts is unauditable.
    #: Signed, so a correction can subtract.
    quantity: Mapped[int] = mapped_column(nullable=False)

    #: What it cost us, in cents, where known. Distinct from what the customer
    #: is charged — this is the margin question, which is the number the
    #: business will actually ask about.
    cost_cents: Mapped[int | None] = mapped_column(nullable=True)

    #: When the usage happened, which is not when the row was written: a
    #: post-call webhook can arrive minutes late and must still land in the
    #: right billing period.
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)

    #: Vendor-side identifier, so reconciliation can match our row against the
    #: provider's line item. Unique per provider, which is what makes importing
    #: a provider usage report idempotent.
    provider_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)

    meta_json: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (
        # The dashboard and the nightly rollup both read this way.
        Index("ix_usage_events_tenant_id_occurred_at", "tenant_id", "occurred_at"),
        Index("ix_usage_events_kind_occurred_at", "kind", "occurred_at"),
        # Importing the same provider usage line twice must not double-bill.
        UniqueConstraint(
            "provider",
            "provider_reference",
            name="uq_usage_events_provider_provider_reference",
        ),
        CheckConstraint(
            "cost_cents IS NULL OR cost_cents >= 0",
            name="cost_not_negative",
        ),
    )


class UsageDaily(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One tenant's usage of one kind on one day.

    A rollup, not a source: everything here is derivable from ``usage_events``
    and is rebuilt rather than incremented. That is what makes a late-arriving
    webhook safe — recomputing the day is idempotent, whereas incrementing a
    counter twice is a silent overcharge nobody can later prove happened.

    It exists because "minutes used this period" is asked on every dashboard
    load and every plan-limit check, and scanning a month of raw events for it
    would put a growing table on a hot path.
    """

    __tablename__ = "usage_daily"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: The tenant's local day, not UTC. A business's "today" is what a monthly
    #: allowance is measured against, and a salon closing at 8pm Pacific should
    #: not have its evening counted against tomorrow.
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[UsageKind] = mapped_column(enum_column(UsageKind, "usage_kind"), nullable=False)

    quantity: Mapped[int] = mapped_column(nullable=False, default=0)
    cost_cents: Mapped[int] = mapped_column(nullable=False, default=0)
    #: How many ledger rows produced this figure. A cheap integrity check: if it
    #: disagrees with a recount, the rollup is stale.
    event_count: Mapped[int] = mapped_column(nullable=False, default=0)
    #: When this row was last rebuilt from the ledger.
    computed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "usage_date",
            "kind",
            name="uq_usage_daily_tenant_id_usage_date_kind",
        ),
        Index("ix_usage_daily_usage_date", "usage_date"),
    )
