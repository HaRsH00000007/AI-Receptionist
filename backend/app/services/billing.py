"""Billing state: granting trials, and applying what Stripe tells us.

The governing rule, restated because everything here depends on it: **Stripe is
a payment processor, not the source of truth for entitlement.** Webhooks write
the ``subscriptions`` table; the money gate reads it. Nothing calls Stripe to
ask whether a tenant may be provisioned.

Idempotency is structural rather than defensive. Stripe retries deliveries by
design — for days, on any non-2xx — so a handler that is merely "usually called
once" will eventually double-apply. Two mechanisms:

* the unique ``(provider, event_id)`` index on ``webhook_events`` refuses a
  second delivery of the same event before it is interpreted at all;
* every apply here is a *convergent* write. Handlers set state to what the event
  says it should be rather than incrementing or toggling, so applying the same
  event twice lands in the same place as applying it once.

Ordering is the other half. Stripe does not guarantee delivery order, so a
stale ``customer.subscription.updated`` can arrive after the cancellation that
superseded it. :func:`_is_stale` drops events older than the state already
recorded, which stops a late duplicate from resurrecting a cancelled plan.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.billing import BillingEvent, Subscription
from app.models.enums import (
    ENTITLED_SUBSCRIPTION_STATUSES,
    BillingEventType,
    ProvisioningStatus,
    SubscriptionStatus,
    TenantPlan,
)
from app.models.provisioning import ProvisioningRun
from app.models.tenant import Tenant

logger = get_logger(__name__)

#: Stripe's subscription statuses mapped onto ours.
#:
#: Stripe's vocabulary is theirs and changes on their schedule; ours is what the
#: money gate reads. Mapping in one place means a new Stripe status is an entry
#: here rather than a scattered set of string comparisons.
#:
#: ``incomplete`` and ``unpaid`` map to EXPIRED rather than PAST_DUE on purpose:
#: PAST_DUE keeps a customer's phone line working through a failed renewal,
#: which is right for someone who has been paying, and wrong for a subscription
#: whose *first* payment never succeeded.
_STRIPE_STATUS: dict[str, SubscriptionStatus] = {
    "trialing": SubscriptionStatus.TRIALING,
    "active": SubscriptionStatus.ACTIVE,
    "past_due": SubscriptionStatus.PAST_DUE,
    "canceled": SubscriptionStatus.CANCELED,
    "incomplete": SubscriptionStatus.EXPIRED,
    "incomplete_expired": SubscriptionStatus.EXPIRED,
    "unpaid": SubscriptionStatus.EXPIRED,
    "paused": SubscriptionStatus.EXPIRED,
}


def map_stripe_status(raw: str) -> SubscriptionStatus:
    """Stripe's status → ours. Unknown values are treated as not entitled.

    Failing closed matters: a status Stripe adds later must not accidentally
    authorize spending because it did not match anything.
    """
    mapped = _STRIPE_STATUS.get(raw)
    if mapped is None:
        logger.warning("unrecognised Stripe subscription status", extra={"status": raw})
        return SubscriptionStatus.EXPIRED
    return mapped


def _timestamp(value: Any) -> datetime | None:
    """Stripe sends Unix seconds; we store timezone-aware datetimes."""
    if not isinstance(value, int):
        return None
    return datetime.fromtimestamp(value, tz=UTC)


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """What a webhook changed, for the caller's log line and the response."""

    applied: bool
    reason: str
    subscription_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    #: True when this change made an unentitled tenant entitled, which is the
    #: signal to un-park any provisioning run waiting on billing.
    entitlement_granted: bool = False


class BillingService:
    """Owns every write to ``subscriptions`` and ``billing_events``."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # ---------------------------------------------------------------------
    # Trials
    # ---------------------------------------------------------------------
    async def grant_trial(self, tenant: Tenant) -> Subscription | None:
        """Give a new tenant its trial. Idempotent.

        Returns ``None`` when the tenant already holds an entitled subscription,
        so a resubmitted signup cannot extend a trial by resubmitting — which
        would otherwise be a free renewable subscription for anyone who noticed.

        ``trial_days = 0`` means no trial is granted at all: the deployment
        requires a card before anything is provisioned, and the money gate will
        park the run until checkout completes.
        """
        existing = await self._entitled_subscription(tenant.id)
        if existing is not None:
            return None

        if self.settings.trial_days <= 0:
            logger.info(
                "no trial granted; a payment method is required first",
                extra={"tenant_id": str(tenant.id)},
            )
            return None

        subscription = Subscription(
            tenant_id=tenant.id,
            plan=TenantPlan.TRIAL,
            status=SubscriptionStatus.TRIALING,
            trial_ends_at=datetime.now(UTC) + timedelta(days=self.settings.trial_days),
            has_payment_method=False,
        )
        self.session.add(subscription)
        await self.session.flush()

        self.session.add(
            BillingEvent(
                tenant_id=tenant.id,
                subscription_id=subscription.id,
                event_type=BillingEventType.TRIAL_GRANTED,
                payload_json={"trial_days": self.settings.trial_days},
            )
        )
        logger.info(
            "trial granted",
            extra={"tenant_id": str(tenant.id), "trial_days": self.settings.trial_days},
        )
        return subscription

    # ---------------------------------------------------------------------
    # Stripe events
    # ---------------------------------------------------------------------
    async def apply_event(self, event_type: str, data: dict[str, Any]) -> ApplyResult:
        """Apply one verified Stripe event to our billing state.

        The event has already had its signature verified and its id deduplicated
        by the caller; this only decides what it *means*.
        """
        obj = data.get("object")
        if not isinstance(obj, dict):
            return ApplyResult(applied=False, reason="event_has_no_object")

        match event_type:
            case (
                "customer.subscription.created"
                | "customer.subscription.updated"
                | "customer.subscription.deleted"
            ):
                return await self._apply_subscription(event_type, obj)
            case "checkout.session.completed":
                return await self._apply_checkout_completed(obj)
            case "invoice.payment_succeeded" | "invoice.payment_failed":
                return await self._apply_invoice(event_type, obj)
            case _:
                # Not an error. Stripe sends far more event types than any
                # integration cares about, and a 2xx for the rest stops it
                # retrying something we deliberately ignore.
                return ApplyResult(applied=False, reason="event_type_not_handled")

    async def _apply_subscription(self, event_type: str, obj: dict[str, Any]) -> ApplyResult:
        stripe_subscription_id = obj.get("id")
        customer_id = obj.get("customer")
        if not isinstance(stripe_subscription_id, str) or not isinstance(customer_id, str):
            return ApplyResult(applied=False, reason="missing_subscription_identifiers")

        subscription = await self._locate(stripe_subscription_id, customer_id, obj)
        if subscription is None:
            # Recorded, not silently dropped: a subscription we cannot attribute
            # is a real operational problem worth seeing in the admin panel.
            logger.warning(
                "Stripe subscription could not be matched to a tenant",
                extra={"stripe_subscription_id": stripe_subscription_id},
            )
            return ApplyResult(applied=False, reason="tenant_not_found")

        raw_status = obj.get("status")
        new_status = (
            SubscriptionStatus.CANCELED
            if event_type == "customer.subscription.deleted"
            else map_stripe_status(str(raw_status))
        )

        period_start = _timestamp(obj.get("current_period_start"))
        if _is_stale(subscription, period_start):
            return ApplyResult(
                applied=False,
                reason="stale_event_ignored",
                subscription_id=subscription.id,
                tenant_id=subscription.tenant_id,
            )

        was_entitled = subscription.status in ENTITLED_SUBSCRIPTION_STATUSES

        # Convergent assignment throughout: set to what the event says, never
        # increment or toggle, so a redelivery lands in the same place.
        subscription.stripe_subscription_id = stripe_subscription_id
        subscription.stripe_customer_id = customer_id
        subscription.status = new_status
        subscription.current_period_start = period_start
        subscription.current_period_end = _timestamp(obj.get("current_period_end"))
        subscription.trial_ends_at = _timestamp(obj.get("trial_end"))
        subscription.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))

        price_id = _price_from(obj)
        if price_id:
            subscription.stripe_price_id = price_id
            subscription.plan = self._plan_for_price(price_id, subscription.plan)

        if new_status is SubscriptionStatus.CANCELED and subscription.canceled_at is None:
            subscription.canceled_at = datetime.now(UTC)
        if new_status in ENTITLED_SUBSCRIPTION_STATUSES and new_status is not (
            SubscriptionStatus.TRIALING
        ):
            # A subscription that is active past its trial necessarily has a
            # payment method behind it.
            subscription.has_payment_method = True

        await self.session.flush()

        self.session.add(
            BillingEvent(
                tenant_id=subscription.tenant_id,
                subscription_id=subscription.id,
                event_type=_billing_event_type(event_type),
                payload_json={"status": new_status.value, "stripe_status": raw_status},
            )
        )

        now_entitled = subscription.is_entitled_at()
        return ApplyResult(
            applied=True,
            reason=f"subscription_{new_status.value}",
            subscription_id=subscription.id,
            tenant_id=subscription.tenant_id,
            entitlement_granted=(not was_entitled) and now_entitled,
        )

    async def _apply_checkout_completed(self, obj: dict[str, Any]) -> ApplyResult:
        """A customer finished paying.

        The subscription details arrive separately in
        ``customer.subscription.created``, which may land *before* this event.
        So this handler records the customer link and the payment method, and
        leaves status to the subscription events — rather than guessing at a
        status from a checkout object.
        """
        customer_id = obj.get("customer")
        tenant_id = (obj.get("metadata") or {}).get("tenant_id")
        if not isinstance(customer_id, str) or not isinstance(tenant_id, str):
            return ApplyResult(applied=False, reason="missing_checkout_identifiers")

        try:
            tenant_uuid = uuid.UUID(tenant_id)
        except ValueError:
            return ApplyResult(applied=False, reason="malformed_tenant_id")

        subscription = await self._for_tenant(tenant_uuid)
        if subscription is None:
            subscription = Subscription(
                tenant_id=tenant_uuid,
                plan=TenantPlan.STARTER,
                status=SubscriptionStatus.ACTIVE,
            )
            self.session.add(subscription)

        was_entitled = subscription.status in ENTITLED_SUBSCRIPTION_STATUSES
        subscription.stripe_customer_id = customer_id
        subscription.has_payment_method = True
        await self.session.flush()

        return ApplyResult(
            applied=True,
            reason="checkout_completed",
            subscription_id=subscription.id,
            tenant_id=tenant_uuid,
            entitlement_granted=(not was_entitled) and subscription.is_entitled_at(),
        )

    async def _apply_invoice(self, event_type: str, obj: dict[str, Any]) -> ApplyResult:
        """Record a payment outcome.

        Deliberately does *not* change entitlement. Stripe's dunning process
        decides when a failed payment becomes ``past_due`` or ``unpaid``, and it
        emits a subscription event when it does. Cutting a customer off here, on
        the first failed charge, would take a paying business's phone line down
        over a temporarily declined card.
        """
        customer_id = obj.get("customer")
        if not isinstance(customer_id, str):
            return ApplyResult(applied=False, reason="missing_customer")

        subscription = await self._by_customer(customer_id)
        if subscription is None:
            return ApplyResult(applied=False, reason="tenant_not_found")

        succeeded = event_type == "invoice.payment_succeeded"
        self.session.add(
            BillingEvent(
                tenant_id=subscription.tenant_id,
                subscription_id=subscription.id,
                event_type=(
                    BillingEventType.PAYMENT_SUCCEEDED
                    if succeeded
                    else BillingEventType.PAYMENT_FAILED
                ),
                provider_event_id=obj.get("id") if isinstance(obj.get("id"), str) else None,
                amount_cents=obj.get("amount_paid") if succeeded else obj.get("amount_due"),
                currency=(obj.get("currency") or "usd")[:3],
                payload_json={"invoice": obj.get("id")},
            )
        )
        if succeeded:
            subscription.has_payment_method = True

        return ApplyResult(
            applied=True,
            reason="payment_succeeded" if succeeded else "payment_failed",
            subscription_id=subscription.id,
            tenant_id=subscription.tenant_id,
        )

    # ---------------------------------------------------------------------
    # Un-parking
    # ---------------------------------------------------------------------
    async def resume_blocked_runs(self, tenant_id: uuid.UUID) -> int:
        """Wake any provisioning parked on billing for this tenant.

        The fast path out of ``BILLING_BLOCKED``. Setting ``next_attempt_at`` to
        now makes the run eligible for the very next worker poll, so a customer
        who has just paid sees provisioning resume in seconds rather than
        waiting for the slow self-heal re-check.

        Only the schedule is touched — not the status, and not the step rows.
        The gate is re-evaluated from scratch when the run is picked up, so this
        can never *grant* entitlement; it only asks the question again.
        """
        runs = (
            (
                await self.session.execute(
                    select(ProvisioningRun)
                    .where(ProvisioningRun.tenant_id == tenant_id)
                    .where(ProvisioningRun.status == ProvisioningStatus.BILLING_BLOCKED)
                )
            )
            .scalars()
            .all()
        )

        for run in runs:
            run.next_attempt_at = datetime.now(UTC)

        if runs:
            logger.info(
                "resuming provisioning parked on billing",
                extra={"tenant_id": str(tenant_id), "runs": len(runs)},
            )
        return len(runs)

    # ---------------------------------------------------------------------
    # Lookups
    # ---------------------------------------------------------------------
    async def _entitled_subscription(self, tenant_id: uuid.UUID) -> Subscription | None:
        return (
            await self.session.execute(
                select(Subscription)
                .where(Subscription.tenant_id == tenant_id)
                .where(Subscription.status.in_(tuple(ENTITLED_SUBSCRIPTION_STATUSES)))
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _for_tenant(self, tenant_id: uuid.UUID) -> Subscription | None:
        return (
            await self.session.execute(
                select(Subscription)
                .where(Subscription.tenant_id == tenant_id)
                .order_by(Subscription.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _by_customer(self, customer_id: str) -> Subscription | None:
        return (
            await self.session.execute(
                select(Subscription)
                .where(Subscription.stripe_customer_id == customer_id)
                .order_by(Subscription.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _locate(
        self, stripe_subscription_id: str, customer_id: str, obj: dict[str, Any]
    ) -> Subscription | None:
        """Find the row this Stripe subscription belongs to.

        Three routes, most specific first: the subscription id we already
        recorded, the customer id, then ``metadata.tenant_id`` — which is the
        only one that works for the very first event about a brand-new
        subscription.
        """
        found = (
            await self.session.execute(
                select(Subscription)
                .where(Subscription.stripe_subscription_id == stripe_subscription_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if found is not None:
            return found

        found = await self._by_customer(customer_id)
        if found is not None:
            return found

        raw_tenant = (obj.get("metadata") or {}).get("tenant_id")
        if isinstance(raw_tenant, str):
            try:
                return await self._for_tenant(uuid.UUID(raw_tenant))
            except ValueError:
                return None
        return None

    def _plan_for_price(self, price_id: str, current: TenantPlan) -> TenantPlan:
        """Map a Stripe price to a plan.

        Configured rather than inferred: price ids are account-specific and
        change between test and live mode, so they belong in configuration. An
        unmapped price leaves the plan unchanged rather than silently
        downgrading a paying customer to TRIAL.
        """
        return self.settings.stripe_price_plan_map.get(price_id, current)


def _price_from(obj: dict[str, Any]) -> str | None:
    items = (obj.get("items") or {}).get("data") or []
    if not items:
        return None
    price = (items[0] or {}).get("price") or {}
    price_id = price.get("id")
    return price_id if isinstance(price_id, str) else None


def _is_stale(subscription: Subscription, period_start: datetime | None) -> bool:
    """Whether this event describes a period older than what we already hold.

    Stripe does not guarantee delivery order. Without this, a delayed
    ``updated`` from last month could arrive after this month's cancellation and
    quietly reactivate a subscription nobody is paying for.
    """
    if period_start is None or subscription.current_period_start is None:
        return False
    return period_start < subscription.current_period_start


def _billing_event_type(stripe_event_type: str) -> BillingEventType:
    match stripe_event_type:
        case "customer.subscription.created":
            return BillingEventType.SUBSCRIPTION_CREATED
        case "customer.subscription.deleted":
            return BillingEventType.SUBSCRIPTION_CANCELED
        case _:
            return BillingEventType.SUBSCRIPTION_UPDATED
