"""The money gate — the one place that decides whether a tenant may be provisioned.

This module exists because of a specific, measured leak in the POC: every
anonymous form submission bought a real Twilio number, which is a recurring
monthly charge, with nothing checking whether the business had paid or was even
real. `docs/02_PLAN_PRODUCTION.md` §4 calls it "the single biggest leak", and it
is the reason M10 exists.

**One decision function, used twice.** :func:`evaluate` is called by the
``BILLING_GATE`` provisioning step *and* again inside ``purchase_number``
immediately before the vendor call. Two call sites, one implementation — so
there is no way for the gate and the purchase to disagree about what "entitled"
means, and no second copy of the rule to forget to update.

**Read from PostgreSQL, never from Stripe.** Entitlement is answered from the
``subscriptions`` row every time. Calling Stripe here would put a third party on
the provisioning path, add latency to the most expensive operation in the
system, and mean a Stripe outage decides that every customer is unpaid. Stripe
webhooks *write* this table; nothing reads back the other way.

**Re-read, never cached.** The gate deliberately takes a session and issues a
query rather than accepting a passed-in ``Subscription``. A subscription object
loaded at the start of a provisioning run may be minutes stale by the time the
purchase happens — during which a card could have failed or a trial expired.
The authoritative answer is the one read in the same moment as the spend.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import BillingBlockedError
from app.core.logging import get_logger
from app.models.billing import Subscription, capabilities_for
from app.models.enums import ENTITLED_SUBSCRIPTION_STATUSES, SubscriptionStatus, TenantPlan

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BillingDecision:
    """Why the gate said yes or no.

    The reason is recorded on the provisioning step and shown in the admin
    panel, because "blocked" without a reason turns every billing question into
    a database session.
    """

    allowed: bool
    #: A stable machine-readable code, not a sentence. Rendered for humans at
    #: the edge; matched on in tests and in the admin panel.
    reason: str
    plan: TenantPlan | None = None
    status: SubscriptionStatus | None = None
    subscription_id: uuid.UUID | None = None
    trial_ends_at: datetime | None = None

    def as_metadata(self) -> dict[str, object]:
        """Persistable summary. Contains no Stripe identifiers or secrets."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "plan": self.plan.value if self.plan else None,
            "status": self.status.value if self.status else None,
            "trial_ends_at": self.trial_ends_at.isoformat() if self.trial_ends_at else None,
        }


async def evaluate(
    session: AsyncSession,
    settings: Settings,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> BillingDecision:
    """Decide whether ``tenant_id`` may be provisioned. Never raises.

    Returns a decision rather than raising so that both callers can record it —
    the gate step writes it to the step row, the purchase guard logs it. Use
    :func:`require_entitlement` when the caller wants the refusal to stop
    execution.
    """
    moment = now or datetime.now(UTC)

    if not settings.billing_gate_enabled:
        # An operator escape hatch, not a default. Production-like environments
        # refuse to boot with this disabled (see Settings._production_hardening),
        # so it can only ever be off in local or test.
        logger.warning(
            "billing gate is disabled; provisioning is not money-gated",
            extra={"tenant_id": str(tenant_id), "environment": settings.environment},
        )
        return BillingDecision(allowed=True, reason="gate_disabled")

    # Scoped to the tenant in the query itself. A subscription belonging to
    # another tenant must never be able to authorize this one, so the filter is
    # part of the lookup rather than a check on the result.
    subscription = (
        await session.execute(
            select(Subscription)
            .where(Subscription.tenant_id == tenant_id)
            .where(Subscription.status.in_(tuple(ENTITLED_SUBSCRIPTION_STATUSES)))
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if subscription is None:
        # Covers both "never had one" and "the only row is canceled/expired",
        # because the query filters on entitled statuses. Reported as one reason
        # because the consequence is identical.
        return BillingDecision(allowed=False, reason="no_active_subscription")

    if not subscription.is_entitled_at(moment):
        # Reached when the row still says TRIALING but the trial has elapsed and
        # no job has caught up yet. A stale row must not authorize a purchase.
        reason = (
            "trial_expired"
            if subscription.status is SubscriptionStatus.TRIALING
            else "subscription_not_entitled"
        )
        return BillingDecision(
            allowed=False,
            reason=reason,
            plan=subscription.plan,
            status=subscription.status,
            subscription_id=subscription.id,
            trial_ends_at=subscription.trial_ends_at,
        )

    # A trial with no card is allowed to provision — that is the product — but
    # only within the plan's limits. The capability check below is what stops a
    # free trial from quietly acquiring five numbers.
    capabilities = capabilities_for(subscription.plan)
    if capabilities.max_numbers < 1:  # pragma: no cover - no such plan today
        return BillingDecision(
            allowed=False,
            reason="plan_forbids_numbers",
            plan=subscription.plan,
            status=subscription.status,
            subscription_id=subscription.id,
        )

    return BillingDecision(
        allowed=True,
        reason="entitled",
        plan=subscription.plan,
        status=subscription.status,
        subscription_id=subscription.id,
        trial_ends_at=subscription.trial_ends_at,
    )


async def require_entitlement(
    session: AsyncSession,
    settings: Settings,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> BillingDecision:
    """:func:`evaluate`, but raise :class:`BillingBlockedError` on refusal.

    The form used immediately before spending money, where the only safe
    response to "not entitled" is to stop.
    """
    decision = await evaluate(session, settings, tenant_id, now=now)
    if not decision.allowed:
        logger.warning(
            "billing gate refused provisioning",
            extra={"tenant_id": str(tenant_id), "reason": decision.reason},
        )
        raise BillingBlockedError(
            "this tenant is not entitled to provisioning",
            details={"reason": decision.reason, "tenant_id": str(tenant_id)},
        )
    return decision
