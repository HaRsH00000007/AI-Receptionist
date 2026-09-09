"""A payment provider that moves no money.

Every automated test runs against this. That is what lets the whole billing
path — checkout, subscription lifecycle, webhook verification, the money gate
itself — be exercised on every commit with no Stripe account, no network, and
no possibility of a real charge.

The signing scheme mirrors Stripe's shape (``t=<ts>,v1=<hmac>`` over
``<ts>.<payload>``) rather than being a stub that accepts anything. A fake that
waves signatures through would make the forged-webhook test pass while proving
nothing about the code that matters.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import InvalidInputError
from app.core.logging import get_logger
from app.providers.fakes.support import Behaviour
from app.providers.models import (
    BillingPortalSession,
    CheckoutSession,
    CustomerRef,
    PaymentEvent,
    SubscriptionRef,
)

logger = get_logger(__name__)

#: Shared with the tests that build signed payloads. Not a credential — this
#: provider never talks to anything.
FAKE_WEBHOOK_SECRET = "whsec_fake_testing_secret"

#: Deliberately identical to the real adapter's window, so a replay that the
#: fake accepts is one production would accept too.
REPLAY_TOLERANCE_S = 300


def sign_payload(
    payload: bytes, *, secret: str = FAKE_WEBHOOK_SECRET, timestamp: int | None = None
) -> str:
    """Build a Stripe-shaped signature header for ``payload``.

    Exposed so tests can construct a *genuinely* signed webhook, and — by
    changing one byte — a genuinely forged one.
    """
    moment = timestamp if timestamp is not None else int(time.time())
    signed = f"{moment}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={moment},v1={digest}"


@dataclass
class FakePaymentProvider:
    """An in-memory Stripe.

    Customers and subscriptions live in dicts, so a test can assert on what the
    processor was asked to do without any of it leaving the process.
    """

    name: str = "fake-stripe"
    behaviour: Behaviour = field(default_factory=Behaviour)
    webhook_secret: str = FAKE_WEBHOOK_SECRET
    customers: dict[str, CustomerRef] = field(default_factory=dict)
    subscriptions: dict[str, SubscriptionRef] = field(default_factory=dict)
    #: Every checkout ever created, so a test can assert one was *not*.
    checkouts: list[CheckoutSession] = field(default_factory=list)
    _counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def _next(self, prefix: str) -> str:
        return f"{prefix}_fake_{next(self._counter):06d}"

    async def ensure_customer(self, *, tenant_id: str, email: str, name: str) -> CustomerRef:
        self.behaviour.record("ensure_customer", tenant_id=tenant_id)
        # Keyed by tenant, so a second call returns the first customer. A real
        # Stripe account that grows two customers for one tenant produces two
        # subscriptions and two invoices, which is a support problem.
        existing = self.customers.get(tenant_id)
        if existing is not None:
            return existing
        customer = CustomerRef(customer_id=self._next("cus"), email=email)
        self.customers[tenant_id] = customer
        return customer

    async def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
    ) -> CheckoutSession:
        self.behaviour.record(
            "create_checkout_session",
            customer_id=customer_id,
            price_id=price_id,
            idempotency_key=idempotency_key,
        )
        session = CheckoutSession(
            session_id=self._next("cs"),
            url=f"https://checkout.test/{self._next('pay')}",
        )
        self.checkouts.append(session)
        return session

    async def create_billing_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> BillingPortalSession:
        self.behaviour.record("create_billing_portal_session", customer_id=customer_id)
        return BillingPortalSession(url=f"https://portal.test/{customer_id}")

    async def get_subscription(self, *, subscription_id: str) -> SubscriptionRef | None:
        self.behaviour.record("get_subscription", subscription_id=subscription_id)
        return self.subscriptions.get(subscription_id)

    async def cancel_subscription(
        self, *, subscription_id: str, at_period_end: bool = True
    ) -> SubscriptionRef:
        self.behaviour.record("cancel_subscription", subscription_id=subscription_id)
        current = self.subscriptions.get(subscription_id)
        if current is None:
            raise InvalidInputError(
                "no such subscription", details={"subscription_id": subscription_id}
            )
        updated = current.model_copy(
            update={
                "cancel_at_period_end": at_period_end,
                "status": current.status if at_period_end else "canceled",
            }
        )
        self.subscriptions[subscription_id] = updated
        return updated

    def verify_webhook(self, *, payload: bytes, signature: str) -> PaymentEvent:
        """Verify exactly as the real adapter does — see module docstring."""
        self.behaviour.record("verify_webhook")
        timestamp, provided = _parse_signature_header(signature)

        if abs(time.time() - timestamp) > REPLAY_TOLERANCE_S:
            raise InvalidInputError(
                "webhook timestamp outside the replay window",
                details={"provider": self.name},
            )

        signed = f"{timestamp}.".encode() + payload
        expected = hmac.new(self.webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, provided):
            raise InvalidInputError("invalid webhook signature", details={"provider": self.name})

        return _parse_event(payload)

    # ---- test helpers ----------------------------------------------------
    def seed_subscription(self, subscription: SubscriptionRef) -> SubscriptionRef:
        """Place a subscription as though the processor had created it."""
        self.subscriptions[subscription.subscription_id] = subscription
        return subscription


def _parse_signature_header(header: str) -> tuple[int, str]:
    """Split ``t=<ts>,v1=<hmac>``. Malformed input is a rejection, not a crash."""
    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    raw_timestamp, provided = parts.get("t"), parts.get("v1")
    if not raw_timestamp or not provided:
        raise InvalidInputError("malformed webhook signature header")
    try:
        return int(raw_timestamp), provided
    except ValueError as exc:
        raise InvalidInputError("malformed webhook signature timestamp") from exc


def _parse_event(payload: bytes) -> PaymentEvent:
    try:
        body: dict[str, Any] = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InvalidInputError("webhook payload is not valid JSON") from exc

    event_id = body.get("id")
    event_type = body.get("type")
    if not isinstance(event_id, str) or not isinstance(event_type, str):
        raise InvalidInputError("webhook payload is missing id or type")

    return PaymentEvent(event_id=event_id, event_type=event_type, data=body.get("data", {}))
