"""Subscriptions, plans and the billing events that produced them.

The governing rule: **Stripe is a payment processor, not the source of truth for
entitlement.** Every "may this tenant provision / receive calls / add a number?"
question is answered from this table, never by calling Stripe. Stripe webhooks
write here; nothing reads back the other way.

That matters for two concrete reasons. A Stripe outage must not decide that
every customer is unpaid and take their phone lines down. And the money gate
sits on the provisioning path, where an extra network round trip to a third
party is both a latency cost and a new failure mode on the most expensive
operation in the system.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ENTITLED_SUBSCRIPTION_STATUSES,
    BillingEventType,
    SubscriptionStatus,
    TenantPlan,
    enum_column,
)

if TYPE_CHECKING:
    from app.models.tenant import Tenant


# ---------------------------------------------------------------------------
# Plan capabilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanCapabilities:
    """What a plan allows, as data rather than as branches.

    Business logic asks ``capabilities.max_numbers``, never
    ``if plan == TenantPlan.PRO``. The difference shows up the first time a plan
    is renamed or a fourth is added: with a capability object that is one entry
    in the table below, and with plan-name comparisons it is a search through
    every module for the ones that were missed.
    """

    included_minutes: int
    max_numbers: int
    max_members: int
    #: What happens at 100% of included minutes. Blocking a salon's phone line
    #: over $4 of overage is worse for both parties than billing it, so only
    #: the free trial blocks.
    block_on_overage: bool
    features: frozenset[str]

    def allows(self, feature: str) -> bool:
        return feature in self.features


#: Capabilities per plan. The single place a plan's meaning is defined.
PLAN_CAPABILITIES: dict[TenantPlan, PlanCapabilities] = {
    TenantPlan.TRIAL: PlanCapabilities(
        included_minutes=60,
        max_numbers=1,
        max_members=2,
        # The one plan that blocks: an unpaid trial has no payment method to
        # bill overage to, so continuing would be an uncollectable cost.
        block_on_overage=True,
        features=frozenset({"call_summaries"}),
    ),
    TenantPlan.STARTER: PlanCapabilities(
        included_minutes=500,
        max_numbers=1,
        max_members=3,
        block_on_overage=False,
        features=frozenset({"call_summaries", "email_notifications"}),
    ),
    TenantPlan.PRO: PlanCapabilities(
        included_minutes=2_000,
        max_numbers=5,
        max_members=15,
        block_on_overage=False,
        features=frozenset({"call_summaries", "email_notifications", "custom_voice", "api_access"}),
    ),
    TenantPlan.ENTERPRISE: PlanCapabilities(
        included_minutes=10_000,
        max_numbers=50,
        max_members=200,
        block_on_overage=False,
        features=frozenset(
            {
                "call_summaries",
                "email_notifications",
                "custom_voice",
                "api_access",
                "sso",
                "priority_support",
            }
        ),
    ),
}


def capabilities_for(plan: TenantPlan) -> PlanCapabilities:
    """Capabilities for ``plan``, falling back to the most restrictive.

    An unknown plan must not accidentally grant more than it should, so the
    fallback is TRIAL rather than a permissive default.
    """
    return PLAN_CAPABILITIES.get(plan, PLAN_CAPABILITIES[TenantPlan.TRIAL])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Subscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant's normalized billing state. One live row per tenant.

    Every column here is *our* vocabulary. ``stripe_*`` columns are foreign keys
    into the processor, kept so a support question can be traced across systems,
    but no decision is made by reading them.

    A trial is represented as a subscription with ``TRIALING`` and no Stripe
    customer, rather than as a separate table. It has the same lifecycle, the
    same expiry and the same entitlement question, so a second table would be
    two code paths answering one question — and the one that gets forgotten is
    always the one guarding the money.
    """

    __tablename__ = "subscriptions"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    plan: Mapped[TenantPlan] = mapped_column(
        enum_column(TenantPlan, "tenant_plan"),
        nullable=False,
        default=TenantPlan.TRIAL,
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        enum_column(SubscriptionStatus, "subscription_status"),
        nullable=False,
        default=SubscriptionStatus.TRIALING,
    )

    # ---- Processor references (never authoritative) ----------------------
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    stripe_price_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ---- Period ----------------------------------------------------------
    current_period_start: Mapped[datetime | None] = mapped_column(nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Cancelled but paid through the period. Access continues to period end —
    #: the customer paid for it.
    cancel_at_period_end: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    #: Whether a payment method exists. The distinction the money gate turns on:
    #: a trial with a card can be allowed to overrun and be billed, one without
    #: cannot.
    has_payment_method: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    tenant: Mapped[Tenant] = relationship(back_populates="subscription")

    __table_args__ = (
        # One live subscription per tenant. A second would make "what plan is
        # this tenant on?" ambiguous at exactly the moment it decides spending.
        Index(
            "uq_subscriptions_live_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text(
                "status IN ("
                + ", ".join(f"'{s.value}'" for s in sorted(ENTITLED_SUBSCRIPTION_STATUSES))
                + ")"
            ),
        ),
        Index("ix_subscriptions_status", "status"),
        Index("ix_subscriptions_stripe_customer_id", "stripe_customer_id"),
        # A trial must have an end date, or it is a free forever plan nobody
        # decided to offer.
        CheckConstraint(
            f"status <> '{SubscriptionStatus.TRIALING.value}' OR trial_ends_at IS NOT NULL",
            name="trial_requires_an_end_date",
        ),
    )

    @property
    def capabilities(self) -> PlanCapabilities:
        return capabilities_for(self.plan)

    def is_entitled_at(self, now: datetime | None = None) -> bool:
        """Whether this subscription currently entitles the tenant to service.

        The one predicate the billing gate consults. Checking both status *and*
        trial expiry matters because a row can sit at ``TRIALING`` past its end
        date until some job notices — and the row being stale must not be what
        authorizes a phone-number purchase.
        """
        moment = now or datetime.now(UTC)
        if self.status not in ENTITLED_SUBSCRIPTION_STATUSES:
            return False
        if self.status == SubscriptionStatus.TRIALING:
            return self.trial_ends_at is not None and self.trial_ends_at > moment
        return True


class BillingEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An append-only record of every change to billing state.

    Exists because "why does this customer have access?" and "why were they
    charged?" are questions asked months later, usually by someone unhappy, and
    a mutable ``subscriptions`` row cannot answer either. The raw processor
    payload is kept alongside our interpretation of it so a mis-mapped webhook
    can be replayed rather than guessed at.
    """

    __tablename__ = "billing_events"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[BillingEventType] = mapped_column(
        enum_column(BillingEventType, "billing_event_type"), nullable=False
    )
    #: The processor's event id. Unique, so a redelivered Stripe webhook cannot
    #: grant a second trial or record a second payment.
    provider_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    #: Minor units (cents). Never a float — binary floating point cannot
    #: represent 0.10, and money that does not add up is not money.
    amount_cents: Mapped[int | None] = mapped_column(nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_billing_events_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_billing_events_event_type", "event_type"),
    )
