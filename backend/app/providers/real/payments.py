"""The Stripe adapter.

**Not tested against the live API.** Every automated test runs the fake
(:mod:`app.providers.fakes.payments`), which mirrors this adapter's signature
verification exactly. What is proven in CI is the application's behaviour, not
Stripe's; the notes below record what still needs a real account to confirm.

Written against Stripe's REST API over the shared
:class:`~app.providers.http.ProviderHTTPClient` rather than the ``stripe``
SDK, for the same reason the Twilio and ElevenLabs adapters are: one place
decides how a vendor failure becomes an :class:`~app.core.errors.AppError`, and
the SDK's own exception hierarchy would bypass it.

Stripe's API is form-encoded, not JSON — including nested keys like
``items[0][price]``. :func:`_form` handles that flattening.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from app.core.config import Settings
from app.core.errors import ConfigurationError, InvalidInputError
from app.core.logging import get_logger
from app.providers.http import ProviderHTTPClient
from app.providers.models import (
    BillingPortalSession,
    CheckoutSession,
    CustomerRef,
    PaymentEvent,
    SubscriptionRef,
)

logger = get_logger(__name__)

#: Stripe's own recommendation. Rejecting anything older bounds how long a
#: captured webhook stays replayable if it is intercepted in transit.
REPLAY_TOLERANCE_S = 300


class StripeProvider:
    """Stripe, behind the application's :class:`PaymentProvider` protocol."""

    name = "stripe"

    def __init__(self, settings: Settings) -> None:
        secret = settings.stripe_secret_key.get_secret_value()
        if not secret:  # pragma: no cover - Settings validates this at startup
            raise ConfigurationError("STRIPE_SECRET_KEY is required for the Stripe provider")

        self._webhook_secret = settings.stripe_webhook_secret.get_secret_value()
        self._client = ProviderHTTPClient(
            vendor="stripe",
            base_url=settings.stripe_api_base_url,
            timeout_s=settings.provider_timeout_s,
            headers={
                "authorization": f"Bearer {secret}",
                "content-type": "application/x-www-form-urlencoded",
                # Pinned. An unpinned integration silently changes shape when
                # Stripe rolls a new version, which is how a webhook handler
                # starts reading a field that has moved.
                "stripe-version": "2024-06-20",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- customers -------------------------------------------------------
    async def ensure_customer(self, *, tenant_id: str, email: str, name: str) -> CustomerRef:
        """Find the tenant's customer, or create one.

        Searched by metadata rather than by email: an email address can change,
        and two tenants can legitimately share one. ``metadata['tenant_id']`` is
        the durable link, and searching first is what stops a retried checkout
        from creating a second customer — which would produce two subscriptions
        and two invoices for one business.
        """
        found = await self._client.get(
            "/v1/customers/search",
            params={"query": f"metadata['tenant_id']:'{tenant_id}'", "limit": 1},
        )
        existing = (found or {}).get("data") or []
        if existing:
            return CustomerRef(customer_id=existing[0]["id"], email=existing[0].get("email"))

        created = await self._client.post(
            "/v1/customers",
            content=_form({"email": email, "name": name, "metadata": {"tenant_id": tenant_id}}),
        )
        return CustomerRef(customer_id=created["id"], email=created.get("email"))

    # ---- checkout --------------------------------------------------------
    async def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
    ) -> CheckoutSession:
        """Start a hosted checkout.

        Card details go to Stripe's page, never to this application, which keeps
        it out of PCI scope. The idempotency key is Stripe's own — a
        double-clicked upgrade button must not create two subscriptions.
        """
        payload = await self._client.post(
            "/v1/checkout/sessions",
            content=_form(
                {
                    "customer": customer_id,
                    "mode": "subscription",
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                    "line_items": [{"price": price_id, "quantity": 1}],
                }
            ),
            headers={"idempotency-key": idempotency_key},
        )
        return CheckoutSession(session_id=payload["id"], url=payload["url"])

    async def create_billing_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> BillingPortalSession:
        payload = await self._client.post(
            "/v1/billing_portal/sessions",
            content=_form({"customer": customer_id, "return_url": return_url}),
        )
        return BillingPortalSession(url=payload["url"])

    # ---- subscriptions ---------------------------------------------------
    async def get_subscription(self, *, subscription_id: str) -> SubscriptionRef | None:
        payload = await self._client.get(f"/v1/subscriptions/{subscription_id}")
        return _subscription_from(payload) if payload else None

    async def cancel_subscription(
        self, *, subscription_id: str, at_period_end: bool = True
    ) -> SubscriptionRef:
        if at_period_end:
            # Not an immediate cancel: the customer paid through the period, and
            # cutting their phone line off early is both wrong and a chargeback.
            payload = await self._client.post(
                f"/v1/subscriptions/{subscription_id}",
                content=_form({"cancel_at_period_end": True}),
            )
        else:
            payload = await self._client.delete(f"/v1/subscriptions/{subscription_id}")
        return _subscription_from(payload)

    # ---- webhooks --------------------------------------------------------
    def verify_webhook(self, *, payload: bytes, signature: str) -> PaymentEvent:
        """Verify Stripe's ``Stripe-Signature`` header.

        The security boundary for billing state. Without it, an unauthenticated
        POST could grant any tenant an active subscription — so a missing
        secret is a refusal, never a pass-through.

        Signature is checked over the *raw* body. Re-serializing parsed JSON
        would change byte-for-byte content (key order, whitespace) and every
        verification would fail.
        """
        if not self._webhook_secret:
            raise ConfigurationError("STRIPE_WEBHOOK_SECRET is required to accept Stripe webhooks")

        timestamp, candidates = _parse_signature_header(signature)

        if abs(time.time() - timestamp) > REPLAY_TOLERANCE_S:
            raise InvalidInputError(
                "webhook timestamp outside the replay window", details={"provider": self.name}
            )

        signed = f"{timestamp}.".encode() + payload
        expected = hmac.new(self._webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
        # Stripe may send several v1 signatures during a secret rotation, so any
        # match is accepted — each compared in constant time.
        if not any(hmac.compare_digest(expected, candidate) for candidate in candidates):
            raise InvalidInputError("invalid webhook signature", details={"provider": self.name})

        import json

        try:
            body: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise InvalidInputError("webhook payload is not valid JSON") from exc

        return PaymentEvent(
            event_id=str(body.get("id", "")),
            event_type=str(body.get("type", "")),
            data=body.get("data", {}),
        )


# ---------------------------------------------------------------------------
def _parse_signature_header(header: str) -> tuple[int, list[str]]:
    """Split ``t=<ts>,v1=<sig>[,v1=<sig>]`` into its parts."""
    timestamp: int | None = None
    signatures: list[str] = []
    for piece in header.split(","):
        key, _, value = piece.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise InvalidInputError("malformed webhook signature timestamp") from exc
        elif key == "v1":
            signatures.append(value)

    if timestamp is None or not signatures:
        raise InvalidInputError("malformed webhook signature header")
    return timestamp, signatures


def _form(data: dict[str, Any], prefix: str = "") -> str:
    """Flatten a nested dict into Stripe's bracketed form encoding.

    Stripe takes ``metadata[tenant_id]=x`` and ``line_items[0][price]=y``
    rather than JSON, so nesting has to be expressed in the key names.
    """
    from urllib.parse import urlencode

    pairs: list[tuple[str, str]] = []

    def walk(value: Any, key: str) -> None:
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                walk(sub_value, f"{key}[{sub_key}]" if key else str(sub_key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{key}[{index}]")
        elif isinstance(value, bool):
            # Stripe expects "true"/"false", not Python's "True"/"False".
            pairs.append((key, "true" if value else "false"))
        elif value is not None:
            pairs.append((key, str(value)))

    walk(data, prefix)
    return urlencode(pairs)


def _subscription_from(payload: dict[str, Any]) -> SubscriptionRef:
    items = (payload.get("items") or {}).get("data") or []
    price_id = items[0].get("price", {}).get("id") if items else None
    return SubscriptionRef(
        subscription_id=payload["id"],
        customer_id=payload["customer"],
        status=payload["status"],
        price_id=price_id,
        current_period_start=payload.get("current_period_start"),
        current_period_end=payload.get("current_period_end"),
        trial_end=payload.get("trial_end"),
        cancel_at_period_end=bool(payload.get("cancel_at_period_end")),
    )
