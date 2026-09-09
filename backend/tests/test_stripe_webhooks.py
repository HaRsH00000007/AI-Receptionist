"""Stripe webhooks: the only way billing state changes.

Which makes this endpoint a security boundary, not a data feed. Without
signature verification, an unauthenticated POST could grant any tenant an
active subscription and walk straight through the money gate — so the forged
and replayed cases below matter more than the happy path.

The fake provider signs and verifies exactly as the real adapter does
(``t=<ts>,v1=<hmac>`` over ``<ts>.<payload>``). A fake that waved signatures
through would make these tests pass while proving nothing.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import BillingEvent, ProvisioningRun, Subscription, WebhookEvent
from app.models.enums import (
    ProvisioningStatus,
    SubscriptionStatus,
    TenantPlan,
    WebhookProvider,
    WebhookStatus,
)
from app.providers.fakes.payments import FAKE_WEBHOOK_SECRET, sign_payload
from app.services.billing import map_stripe_status
from tests.factories import make_subscription, make_tenant

STRIPE_PATH = "/api/v1/webhooks/stripe"


def stripe_event(
    event_type: str,
    obj: dict[str, Any],
    *,
    event_id: str | None = None,
) -> bytes:
    """A Stripe-shaped event body."""
    return json.dumps(
        {
            "id": event_id or f"evt_{uuid.uuid4().hex[:16]}",
            "type": event_type,
            "data": {"object": obj},
        }
    ).encode()


def subscription_object(
    *,
    customer_id: str,
    subscription_id: str = "sub_test_1",
    status: str = "active",
    tenant_id: str | None = None,
    period_start: int | None = None,
    trial_end: int | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    return {
        "id": subscription_id,
        "customer": customer_id,
        "status": status,
        "current_period_start": period_start if period_start is not None else now,
        "current_period_end": now + 30 * 86_400,
        "trial_end": trial_end,
        "cancel_at_period_end": False,
        "metadata": {"tenant_id": tenant_id} if tenant_id else {},
        "items": {"data": [{"price": {"id": "price_test_pro"}}]},
    }


async def post_event(client: AsyncClient, body: bytes, *, signature: str | None = None):  # type: ignore[no-untyped-def]
    return await client.post(
        STRIPE_PATH,
        content=body,
        headers={
            "stripe-signature": signature if signature is not None else sign_payload(body),
            "content-type": "application/json",
        },
    )


async def _tenant_with_subscription(api_app: FastAPI, **kwargs: Any) -> tuple[uuid.UUID, str]:
    """A tenant whose subscription is already linked to a Stripe customer."""
    customer_id = f"cus_{uuid.uuid4().hex[:12]}"
    async with api_app.state.session_factory() as session:
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()
        session.add(make_subscription(tenant, stripe_customer_id=customer_id, **kwargs))
        await session.commit()
        return tenant.id, customer_id


# ===========================================================================
# F — forged and malformed deliveries
# ===========================================================================


async def test_F_a_forged_signature_is_rejected(api_client: AsyncClient) -> None:
    """The whole reason this endpoint verifies anything."""
    body = stripe_event("customer.subscription.created", subscription_object(customer_id="cus_x"))

    response = await post_event(api_client, body, signature="t=1,v1=deadbeef")

    assert response.status_code == 200
    assert response.json()["status"] == WebhookStatus.REJECTED.value


async def test_F2_an_unsigned_delivery_is_rejected(api_client: AsyncClient) -> None:
    body = stripe_event("customer.subscription.created", subscription_object(customer_id="cus_x"))
    response = await post_event(api_client, body, signature="")
    assert response.json()["status"] == WebhookStatus.REJECTED.value


async def test_F3_a_tampered_body_is_rejected(api_client: AsyncClient) -> None:
    """A signature is only worth anything if it covers the payload."""
    original = stripe_event(
        "customer.subscription.created", subscription_object(customer_id="cus_x")
    )
    signature = sign_payload(original)
    tampered = original.replace(b"cus_x", b"cus_y")

    response = await post_event(api_client, tampered, signature=signature)
    assert response.json()["status"] == WebhookStatus.REJECTED.value


async def test_F4_a_replayed_old_signature_is_rejected(api_client: AsyncClient) -> None:
    """Bounds how long a captured webhook stays usable if intercepted."""
    body = stripe_event("customer.subscription.created", subscription_object(customer_id="cus_x"))
    stale = sign_payload(body, timestamp=int(time.time()) - 3_600)

    response = await post_event(api_client, body, signature=stale)
    assert response.json()["status"] == WebhookStatus.REJECTED.value


async def test_a_rejected_delivery_is_not_stored(api_client: AsyncClient, api_app: FastAPI) -> None:
    """An unverified payload is not evidence of anything.

    Storing attacker-controlled content into a table the admin panel renders is
    how a webhook endpoint becomes an injection vector.
    """
    body = stripe_event("customer.subscription.created", subscription_object(customer_id="cus_x"))
    await post_event(api_client, body, signature="t=1,v1=deadbeef")

    async with api_app.state.session_factory() as session:
        stored = (
            (
                await session.execute(
                    select(WebhookEvent).where(WebhookEvent.provider == WebhookProvider.STRIPE)
                )
            )
            .scalars()
            .all()
        )
    assert stored == []


# ===========================================================================
# E — idempotency
# ===========================================================================


async def test_E_a_repeated_delivery_is_a_duplicate(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Stripe retries by design. The second delivery must change nothing."""
    tenant_id, customer_id = await _tenant_with_subscription(api_app)
    body = stripe_event(
        "customer.subscription.updated",
        subscription_object(customer_id=customer_id, status="active"),
        event_id="evt_stable_1",
    )

    first = await post_event(api_client, body)
    second = await post_event(api_client, body)

    assert first.json()["status"] == WebhookStatus.PROCESSED.value
    assert second.json()["status"] == WebhookStatus.DUPLICATE.value

    async with api_app.state.session_factory() as session:
        events = (
            (
                await session.execute(
                    select(WebhookEvent).where(WebhookEvent.event_id == "evt_stable_1")
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1

        # And exactly one billing event was recorded, not two.
        billing = (
            (await session.execute(select(BillingEvent).where(BillingEvent.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        assert len(billing) == 1


async def test_E2_replay_cannot_corrupt_entitlement(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A cancellation replayed after a reactivation must not resurrect it.

    Applies cancel, then active, then replays the *original* cancel. The
    duplicate is refused by the event-id index, so the tenant stays active.
    """
    tenant_id, customer_id = await _tenant_with_subscription(api_app)

    cancel = stripe_event(
        "customer.subscription.deleted",
        subscription_object(customer_id=customer_id, status="canceled"),
        event_id="evt_cancel",
    )
    reactivate = stripe_event(
        "customer.subscription.updated",
        subscription_object(customer_id=customer_id, status="active"),
        event_id="evt_active",
    )

    await post_event(api_client, cancel)
    await post_event(api_client, reactivate)
    replayed = await post_event(api_client, cancel)

    assert replayed.json()["status"] == WebhookStatus.DUPLICATE.value

    async with api_app.state.session_factory() as session:
        subscription = (
            await session.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))
        ).scalar_one()
        assert subscription.status is SubscriptionStatus.ACTIVE


async def test_an_out_of_order_event_does_not_rewind_state(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Stripe does not guarantee ordering.

    A delayed update describing an older billing period must not overwrite
    newer state — otherwise a late duplicate can reactivate a cancelled plan.
    """
    tenant_id, customer_id = await _tenant_with_subscription(api_app)
    now = int(time.time())

    current = stripe_event(
        "customer.subscription.updated",
        subscription_object(customer_id=customer_id, status="canceled", period_start=now),
        event_id="evt_current",
    )
    stale = stripe_event(
        "customer.subscription.updated",
        subscription_object(
            customer_id=customer_id, status="active", period_start=now - 60 * 86_400
        ),
        event_id="evt_stale",
    )

    await post_event(api_client, current)
    response = await post_event(api_client, stale)

    assert response.json()["detail"] == "stale_event_ignored"

    async with api_app.state.session_factory() as session:
        subscription = (
            await session.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))
        ).scalar_one()
        assert subscription.status is SubscriptionStatus.CANCELED


# ===========================================================================
# Lifecycle
# ===========================================================================


@pytest.mark.parametrize(
    ("stripe_status", "expected"),
    [
        ("trialing", SubscriptionStatus.TRIALING),
        ("active", SubscriptionStatus.ACTIVE),
        ("past_due", SubscriptionStatus.PAST_DUE),
        ("canceled", SubscriptionStatus.CANCELED),
        ("unpaid", SubscriptionStatus.EXPIRED),
        ("incomplete", SubscriptionStatus.EXPIRED),
    ],
)
def test_stripe_statuses_map_onto_ours(stripe_status: str, expected: SubscriptionStatus) -> None:
    assert map_stripe_status(stripe_status) is expected


def test_an_unknown_stripe_status_fails_closed() -> None:
    """A status Stripe adds later must not accidentally authorize spending."""
    assert map_stripe_status("some_future_status") is SubscriptionStatus.EXPIRED


async def test_a_cancellation_revokes_entitlement(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    tenant_id, customer_id = await _tenant_with_subscription(
        api_app, status=SubscriptionStatus.ACTIVE, trial_ends_at=None
    )

    await post_event(
        api_client,
        stripe_event(
            "customer.subscription.deleted",
            subscription_object(customer_id=customer_id, status="canceled"),
        ),
    )

    async with api_app.state.session_factory() as session:
        subscription = (
            await session.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))
        ).scalar_one()
        assert subscription.status is SubscriptionStatus.CANCELED
        assert subscription.canceled_at is not None
        assert subscription.is_entitled_at() is False


async def test_an_unattributable_subscription_is_recorded_not_applied(
    api_client: AsyncClient,
) -> None:
    """A subscription we cannot match to a tenant is an operational problem.

    Answered 200 so Stripe stops retrying, but reported as not applied so it is
    visible rather than silently swallowed.
    """
    response = await post_event(
        api_client,
        stripe_event(
            "customer.subscription.updated",
            subscription_object(customer_id="cus_nobody_has_this"),
        ),
    )
    assert response.status_code == 200
    assert response.json()["detail"] == "tenant_not_found"


async def test_an_unhandled_event_type_is_acknowledged(api_client: AsyncClient) -> None:
    """Stripe sends far more types than we care about; 2xx stops the retries."""
    response = await post_event(
        api_client, stripe_event("customer.discount.created", {"id": "di_1"})
    )
    assert response.status_code == 200
    assert response.json()["detail"] == "event_type_not_handled"


async def test_a_failed_payment_does_not_immediately_revoke_service(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Dunning decides when a failed charge becomes past_due, not us.

    Cutting a business's phone line on the first declined card would be both
    wrong and a support incident.
    """
    tenant_id, customer_id = await _tenant_with_subscription(
        api_app, status=SubscriptionStatus.ACTIVE, trial_ends_at=None
    )

    await post_event(
        api_client,
        stripe_event(
            "invoice.payment_failed",
            {"id": "in_1", "customer": customer_id, "amount_due": 4900, "currency": "usd"},
        ),
    )

    async with api_app.state.session_factory() as session:
        subscription = (
            await session.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))
        ).scalar_one()
        assert subscription.status is SubscriptionStatus.ACTIVE
        assert subscription.is_entitled_at() is True


# ===========================================================================
# Un-parking
# ===========================================================================


async def test_paying_resumes_a_run_parked_on_billing(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The fast path out of BILLING_BLOCKED.

    A customer who has just paid should see provisioning resume in seconds
    rather than waiting for the slow self-heal re-check.
    """
    customer_id = f"cus_{uuid.uuid4().hex[:12]}"
    async with api_app.state.session_factory() as session:
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()
        session.add(
            make_subscription(
                tenant,
                stripe_customer_id=customer_id,
                status=SubscriptionStatus.EXPIRED,
                trial_ends_at=None,
            )
        )
        run = ProvisioningRun(
            tenant_id=tenant.id,
            status=ProvisioningStatus.BILLING_BLOCKED,
            correlation_id="parked",
            # Far in the future: only the webhook can bring this forward.
            next_attempt_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    await post_event(
        api_client,
        stripe_event(
            "customer.subscription.updated",
            subscription_object(customer_id=customer_id, status="active"),
        ),
    )

    async with api_app.state.session_factory() as session:
        run = await session.get(ProvisioningRun, run_id)
        assert run is not None
        # Re-armed for the next worker poll. The status is deliberately
        # unchanged — the gate is re-evaluated from scratch when the run is
        # picked up, so this can only ask the question again, never grant.
        assert run.next_attempt_at is not None
        assert run.next_attempt_at <= datetime.now(UTC) + timedelta(seconds=5)
        assert run.status is ProvisioningStatus.BILLING_BLOCKED


async def test_resuming_does_not_touch_another_tenants_run(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Requirement G, at the webhook layer."""
    customer_id = f"cus_{uuid.uuid4().hex[:12]}"
    async with api_app.state.session_factory() as session:
        payer, other = make_tenant(), make_tenant()
        session.add_all([payer, other])
        await session.flush()
        session.add(
            make_subscription(
                payer,
                stripe_customer_id=customer_id,
                status=SubscriptionStatus.EXPIRED,
                trial_ends_at=None,
            )
        )
        far_future = datetime.now(UTC) + timedelta(days=1)
        other_run = ProvisioningRun(
            tenant_id=other.id,
            status=ProvisioningStatus.BILLING_BLOCKED,
            correlation_id="other",
            next_attempt_at=far_future,
        )
        session.add(other_run)
        await session.commit()
        other_run_id = other_run.id

    await post_event(
        api_client,
        stripe_event(
            "customer.subscription.updated",
            subscription_object(customer_id=customer_id, status="active"),
        ),
    )

    async with api_app.state.session_factory() as session:
        untouched = await session.get(ProvisioningRun, other_run_id)
        assert untouched is not None
        assert untouched.next_attempt_at > datetime.now(UTC) + timedelta(hours=1)


# ===========================================================================
# Security
# ===========================================================================


async def test_no_stripe_secret_appears_in_the_response(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    _, customer_id = await _tenant_with_subscription(api_app)
    response = await post_event(
        api_client,
        stripe_event(
            "customer.subscription.updated",
            subscription_object(customer_id=customer_id, status="active"),
        ),
    )
    body = response.text
    assert FAKE_WEBHOOK_SECRET not in body
    assert "whsec_" not in body
    assert "sk_" not in body


async def test_a_client_cannot_assert_its_own_plan(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Requirement 11. Billing state comes from a signed webhook, never a client.

    An unmapped price id must leave the plan alone rather than promoting the
    tenant to whatever the payload claims.
    """
    tenant_id, customer_id = await _tenant_with_subscription(
        api_app, plan=TenantPlan.TRIAL, status=SubscriptionStatus.ACTIVE, trial_ends_at=None
    )

    payload = subscription_object(customer_id=customer_id, status="active")
    payload["items"] = {"data": [{"price": {"id": "price_enterprise_forged"}}]}
    await post_event(api_client, stripe_event("customer.subscription.updated", payload))

    async with api_app.state.session_factory() as session:
        subscription = (
            await session.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))
        ).scalar_one()
        # STRIPE_PRICE_PLANS is unset in tests, so no price maps to a plan and
        # the tenant stays where it was.
        assert subscription.plan is TenantPlan.TRIAL
