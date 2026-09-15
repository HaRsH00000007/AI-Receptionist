"""M9 — webhook security and replay protection.

These endpoints are the only unauthenticated write surface in the application.
Anyone on the internet can POST to them, so every property below is a security
property rather than a robustness one:

* a forged signature must not be able to create a call record or move billing;
* a captured delivery must not be replayable indefinitely;
* a redelivery must not produce a second call, summary or email;
* a malformed body must be *recorded and refused*, never crash the endpoint;
* nothing sensitive may be persisted into a table the admin panel renders.

The signature helpers themselves are exercised directly here as well as through
the endpoints, because a constant-time comparison that is accidentally correct
for the happy path is the kind of thing that only a negative test catches.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from httpx import AsyncClient

from app.services.signatures import (
    build_elevenlabs_signature,
    build_twilio_signature,
    form_encode,
    public_request_url,
    verify_elevenlabs,
    verify_twilio,
)
from app.services.webhook_payload import REDACTED, redact_payload
from tests.support import build_settings

SECRET = "test-webhook-secret"
AUTH_TOKEN = "test-token"


def _signed_body(payload: dict[str, Any], *, secret: str = SECRET) -> tuple[bytes, str]:
    body = json.dumps(payload).encode("utf-8")
    return body, build_elevenlabs_signature(payload=body, secret=secret, timestamp=int(time.time()))


# ===========================================================================
# Signature verification — the primitives
# ===========================================================================


def test_a_valid_elevenlabs_signature_verifies() -> None:
    body, header = _signed_body({"conversation_id": "conv_1"})
    assert verify_elevenlabs(payload=body, header=header, secret=SECRET) is True


def test_a_signature_over_different_bytes_is_refused() -> None:
    """The MAC covers the body, so a single edited byte invalidates it."""
    body, header = _signed_body({"conversation_id": "conv_1"})
    assert verify_elevenlabs(payload=body + b" ", header=header, secret=SECRET) is False


def test_a_signature_made_with_another_secret_is_refused() -> None:
    body, header = _signed_body({"conversation_id": "conv_1"}, secret="someone-elses-secret")
    assert verify_elevenlabs(payload=body, header=header, secret=SECRET) is False


def test_a_missing_signature_is_refused() -> None:
    body = b'{"conversation_id": "conv_1"}'
    assert verify_elevenlabs(payload=body, header="", secret=SECRET) is False


def test_a_malformed_signature_header_is_refused_not_crashed() -> None:
    """Header parsing runs before any HMAC, on attacker-controlled input."""
    body = b'{"conversation_id": "conv_1"}'
    for header in ("garbage", "t=", "v0=abc", "t=notanumber,v0=abc", "=,=", "t=1,v0="):
        assert verify_elevenlabs(payload=body, header=header, secret=SECRET) is False


def test_an_unconfigured_secret_never_verifies() -> None:
    """A blank secret must not make everything valid.

    HMAC with an empty key is still a valid HMAC, so without this guard an
    unconfigured deployment would accept a signature anyone could compute.
    """
    body = b'{"conversation_id": "conv_1"}'
    header = build_elevenlabs_signature(payload=body, secret="", timestamp=int(time.time()))
    assert verify_elevenlabs(payload=body, header=header, secret="") is False


# ===========================================================================
# Replay — the timestamp is inside the MAC
# ===========================================================================


def test_a_stale_delivery_is_refused() -> None:
    """A captured request must not stay valid forever."""
    body = b'{"conversation_id": "conv_1"}'
    old = int(time.time()) - 3600
    header = build_elevenlabs_signature(payload=body, secret=SECRET, timestamp=old)
    assert verify_elevenlabs(payload=body, header=header, secret=SECRET, tolerance_s=1800) is False


def test_a_delivery_from_the_future_is_refused() -> None:
    """Clock skew is bounded in both directions.

    Accepting arbitrarily future timestamps would let a captured request be
    pre-dated and replayed long after it was taken.
    """
    body = b'{"conversation_id": "conv_1"}'
    ahead = int(time.time()) + 7200
    header = build_elevenlabs_signature(payload=body, secret=SECRET, timestamp=ahead)
    assert verify_elevenlabs(payload=body, header=header, secret=SECRET, tolerance_s=1800) is False


def test_replaying_a_body_under_a_fresh_timestamp_fails() -> None:
    """The timestamp is signed, not merely sent alongside.

    An attacker who captures a valid delivery cannot refresh it: changing the
    timestamp invalidates the MAC, and they cannot recompute the MAC without
    the secret.
    """
    body = b'{"conversation_id": "conv_1"}'
    captured = build_elevenlabs_signature(payload=body, secret=SECRET, timestamp=1_000_000)
    mac = captured.split("v0=")[1]
    forged = f"t={int(time.time())},v0={mac}"
    assert verify_elevenlabs(payload=body, header=forged, secret=SECRET) is False


# ===========================================================================
# Twilio — URL-bound signatures and proxy correctness
# ===========================================================================


def test_a_valid_twilio_signature_verifies() -> None:
    url = "https://api.example.com/api/v1/webhooks/twilio/voice-status"
    params = {"CallSid": "CA1", "CallStatus": "completed"}
    header = build_twilio_signature(url=url, params=params, auth_token=AUTH_TOKEN)
    assert verify_twilio(url=url, params=params, header=header, auth_token=AUTH_TOKEN) is True


def test_a_twilio_signature_is_bound_to_its_url() -> None:
    """A signature captured on one endpoint cannot be replayed against another."""
    params = {"CallSid": "CA1", "CallStatus": "completed"}
    header = build_twilio_signature(
        url="https://api.example.com/api/v1/webhooks/twilio/voice-status",
        params=params,
        auth_token=AUTH_TOKEN,
    )
    assert (
        verify_twilio(
            url="https://api.example.com/api/v1/webhooks/twilio/inbound",
            params=params,
            header=header,
            auth_token=AUTH_TOKEN,
        )
        is False
    )


def test_a_twilio_signature_covers_every_parameter() -> None:
    """Changing a parameter — the caller's number, say — invalidates it."""
    url = "https://api.example.com/hook"
    header = build_twilio_signature(url=url, params={"From": "+15551110000"}, auth_token=AUTH_TOKEN)
    assert (
        verify_twilio(
            url=url, params={"From": "+15559998888"}, header=header, auth_token=AUTH_TOKEN
        )
        is False
    )


def test_the_public_url_is_what_gets_verified() -> None:
    """Behind a tunnel or load balancer, the observed URL is not the signed one.

    Twilio signs the address the *caller* used. Verifying against
    ``http://localhost:8000/...`` would fail every signature in any deployment
    that terminates TLS somewhere else — which is all of them.
    """
    rebuilt = public_request_url(
        observed_url="http://localhost:8000/api/v1/webhooks/twilio/voice-status?x=1",
        path="/api/v1/webhooks/twilio/voice-status",
        query="x=1",
        public_base="https://api.example.com",
    )
    assert rebuilt == "https://api.example.com/api/v1/webhooks/twilio/voice-status?x=1"

    # With nothing in front, the observed URL is already correct.
    assert (
        public_request_url(
            observed_url="http://localhost:8000/hook", path="/hook", query="", public_base=""
        )
        == "http://localhost:8000/hook"
    )


# ===========================================================================
# Enforcement: production-like environments cannot opt out
# ===========================================================================


def test_signatures_are_mandatory_in_production_like_environments() -> None:
    for environment in ("staging", "production"):
        settings = build_settings(environment=environment, require_webhook_signature=False)
        assert settings.webhooks_require_signature is True


def test_local_development_may_raise_the_bar_but_defaults_to_recording() -> None:
    """Unsigned local deliveries are recorded with `signature_valid=False`.

    That keeps a DRY_RUN loop usable without a secret, while leaving an
    unverified event visibly different from a verified one in the audit trail.
    """
    assert build_settings(environment="local").webhooks_require_signature is False
    assert (
        build_settings(
            environment="local", require_webhook_signature=True
        ).webhooks_require_signature
        is True
    )


# ===========================================================================
# Endpoint behaviour
# ===========================================================================


async def test_an_unsigned_delivery_is_refused_when_signatures_are_required(
    api_client: AsyncClient,
) -> None:
    response = await api_client.post(
        "/api/v1/webhooks/elevenlabs/post-call",
        content=b'{"conversation_id": "conv_forged"}',
        headers={"content-type": "application/json"},
    )
    # Always 200: a 4xx would make the provider retry a payload that can never
    # succeed, which is how a rejection turns into a retry storm.
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


async def test_a_malformed_body_is_refused_without_raising(api_client: AsyncClient) -> None:
    body = b"this is not json"
    header = build_elevenlabs_signature(payload=body, secret=SECRET, timestamp=int(time.time()))
    response = await api_client.post(
        "/api/v1/webhooks/elevenlabs/post-call",
        content=body,
        headers={"content-type": "application/json", "elevenlabs-signature": header},
    )
    assert response.status_code == 200
    assert response.json()["detail"] == "malformed payload"


async def test_a_json_array_is_refused_rather_than_indexed(api_client: AsyncClient) -> None:
    """A payload that is valid JSON but not an object must not reach the parser."""
    body = b'["not", "an", "object"]'
    header = build_elevenlabs_signature(payload=body, secret=SECRET, timestamp=int(time.time()))
    response = await api_client.post(
        "/api/v1/webhooks/elevenlabs/post-call",
        content=body,
        headers={"content-type": "application/json", "elevenlabs-signature": header},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


async def test_a_redelivery_is_reported_as_a_duplicate(api_client: AsyncClient) -> None:
    """Providers retry by design; a retry must not produce a second call row."""
    body, header = _signed_body({"conversation_id": "conv_replay", "type": "post_call"})
    headers = {"content-type": "application/json", "elevenlabs-signature": header}

    first = await api_client.post(
        "/api/v1/webhooks/elevenlabs/post-call", content=body, headers=headers
    )
    second = await api_client.post(
        "/api/v1/webhooks/elevenlabs/post-call", content=body, headers=headers
    )

    assert first.json()["status"] != "duplicate"
    assert second.json()["status"] == "duplicate"


async def test_a_forged_stripe_signature_cannot_grant_entitlement(
    api_client: AsyncClient,
) -> None:
    """The money path. Stripe is refused *before* anything is stored.

    Without verification here, an unauthenticated POST could mark any tenant
    subscribed and walk straight through the provisioning money gate.
    """
    response = await api_client.post(
        "/api/v1/webhooks/stripe",
        content=json.dumps({"id": "evt_forged", "type": "customer.subscription.created"}).encode(),
        headers={"content-type": "application/json", "stripe-signature": "t=1,v1=deadbeef"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


async def test_twilio_status_changes_are_not_collapsed_into_one_event(
    api_client: AsyncClient,
) -> None:
    """`ringing` then `completed` are two events about one call, not a duplicate."""
    url = "http://testserver/api/v1/webhooks/twilio/voice-status"
    statuses = ["ringing", "completed"]
    results = []
    for status in statuses:
        params = {"CallSid": "CA_sequence", "CallStatus": status}
        header = build_twilio_signature(url=url, params=params, auth_token=AUTH_TOKEN)
        response = await api_client.post(
            "/api/v1/webhooks/twilio/voice-status",
            content=form_encode(params),
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "x-twilio-signature": header,
            },
        )
        results.append(response.json()["status"])

    assert results == ["processed", "processed"]


# ===========================================================================
# Payload minimization — what is allowed to be persisted
# ===========================================================================


def test_credentials_are_stripped_from_a_stored_payload() -> None:
    """Matched by key *pattern*, so a field a provider adds later is caught too."""
    stored = redact_payload(
        {
            "account_sid": "AC123",
            "auth_token": "super-secret",
            "apiKey": "sk-live-xyz",
            "nested": {"client_secret": "shh", "harmless": "ok"},
        }
    )
    assert stored["auth_token"] == REDACTED
    assert stored["apiKey"] == REDACTED
    assert stored["nested"]["client_secret"] == REDACTED
    # Non-sensitive structure survives, so the row still explains itself.
    assert stored["account_sid"] == "AC123"
    assert stored["nested"]["harmless"] == "ok"


def test_transcripts_are_summarized_rather_than_duplicated() -> None:
    """A transcript belongs on `calls`, under one retention policy, once."""
    stored = redact_payload(
        {"data": {"transcript": [{"role": "user", "message": "my card number is ..."}]}}
    )
    summary = stored["data"]["transcript"]
    assert isinstance(summary, str)
    assert "omitted" in summary
    assert "card number" not in json.dumps(stored)


def test_a_hostile_payload_cannot_exhaust_the_walker() -> None:
    """Unbounded recursion over an attacker-supplied document is a DoS."""
    deep: dict[str, Any] = {"level": 0}
    node = deep
    for level in range(1, 50):
        node["child"] = {"level": level}
        node = node["child"]

    stored = json.dumps(redact_payload(deep))
    assert "too deeply nested" in stored


def test_long_strings_are_truncated_with_their_length_recorded() -> None:
    stored = redact_payload({"blob": "x" * 5000})
    assert len(stored["blob"]) < 700
    assert "truncated" in stored["blob"]


@pytest.mark.parametrize("payload", [None, 42, "plain", [], {}])
def test_redaction_accepts_any_shape_a_provider_might_send(payload: Any) -> None:
    """It runs on unvalidated input, so it must never raise."""
    redact_payload(payload)
