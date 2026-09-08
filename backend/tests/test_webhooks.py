"""Webhook ingestion: signatures, deduplication, malformed payloads."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Call, PhoneNumber, WebhookEvent
from app.models.enums import CallStatus, WebhookStatus
from app.services.signatures import (
    build_elevenlabs_signature,
    build_twilio_signature,
    verify_elevenlabs,
    verify_twilio,
)

POST_CALL_URL = "/api/v1/webhooks/elevenlabs/post-call"
TWILIO_URL = "/api/v1/webhooks/twilio/voice-status"
SECRET = "test-webhook-secret"

SIGNUP = {
    "business_name": "Sunset Salon",
    "business_type": "salon",
    "services": "cuts, color",
    "operating_hours": "Mon-Fri 9-6",
    "greeting_style": "friendly",
    "escalation_rules": "Text the owner for emergencies",
    "notification_email": "owner@sunsetsalon.example.com",
    "area_code": "805",
    "plan": "starter",
    "contact_phone": "8055550142",
}


# ---------------------------------------------------------------------------
# Signature verification, in isolation
# ---------------------------------------------------------------------------
def test_a_valid_elevenlabs_signature_verifies() -> None:
    body = b'{"hello":"world"}'
    header = build_elevenlabs_signature(payload=body, secret=SECRET, timestamp=int(time.time()))
    assert verify_elevenlabs(payload=body, header=header, secret=SECRET) is True


def test_a_tampered_body_fails_verification() -> None:
    body = b'{"hello":"world"}'
    header = build_elevenlabs_signature(payload=body, secret=SECRET, timestamp=int(time.time()))
    assert verify_elevenlabs(payload=b'{"hello":"evil"}', header=header, secret=SECRET) is False


def test_the_wrong_secret_fails_verification() -> None:
    body = b"{}"
    header = build_elevenlabs_signature(payload=body, secret=SECRET, timestamp=int(time.time()))
    assert verify_elevenlabs(payload=body, header=header, secret="other-secret") is False


def test_an_old_delivery_is_refused() -> None:
    """The timestamp is inside the MAC, so a captured request cannot be replayed."""
    body = b"{}"
    stale = int(time.time()) - 7200
    header = build_elevenlabs_signature(payload=body, secret=SECRET, timestamp=stale)
    assert verify_elevenlabs(payload=body, header=header, secret=SECRET) is False


@pytest.mark.parametrize("header", ["", "garbage", "t=abc,v0=def", "v0=onlymac", "t=123"])
def test_malformed_signature_headers_fail_closed(header: str) -> None:
    assert verify_elevenlabs(payload=b"{}", header=header, secret=SECRET) is False


def test_no_secret_means_no_verification() -> None:
    assert verify_elevenlabs(payload=b"{}", header="t=1,v0=x", secret="") is False


def test_twilio_signature_round_trips() -> None:
    url = "https://api.example.com/hook"
    params = {"CallSid": "CA1", "CallStatus": "completed"}
    header = build_twilio_signature(url=url, params=params, auth_token="tok")
    assert verify_twilio(url=url, params=params, header=header, auth_token="tok") is True


def test_twilio_signature_detects_a_changed_parameter() -> None:
    url = "https://api.example.com/hook"
    header = build_twilio_signature(url=url, params={"CallSid": "CA1"}, auth_token="tok")
    assert (
        verify_twilio(url=url, params={"CallSid": "CA2"}, header=header, auth_token="tok") is False
    )


# ---------------------------------------------------------------------------
# Helpers for the endpoint tests
# ---------------------------------------------------------------------------
def post_call_payload(
    *, conversation_id: str = "conv_abc", called: str = "+18055551000", **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": conversation_id,
            "agent_id": "agent_00000001",
            "metadata": {
                "start_time_unix_secs": 1757100000,
                "call_duration_secs": 96,
                "phone_call": {"agent_number": called, "external_number": "+15559998888"},
            },
            "transcript": [
                {"role": "agent", "message": "Thanks for calling Sunset Salon."},
                {"role": "user", "message": "Do you have anything Thursday morning?"},
                {"role": "agent", "message": "Let me take your name and number."},
            ],
        },
    }
    payload.update(overrides)
    return payload


def signed(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    header = build_elevenlabs_signature(payload=body, secret=SECRET, timestamp=int(time.time()))
    return body, {"elevenlabs-signature": header, "content-type": "application/json"}


async def provision_tenant(api_client: AsyncClient, api_app: FastAPI) -> tuple[uuid.UUID, str]:
    """Create a tenant and drive it to ACTIVE so it owns a real number."""
    from app.worker import Worker

    response = await api_client.post("/api/v1/signups", json=SIGNUP)
    tenant_id = uuid.UUID(response.json()["tenant_id"])

    worker = Worker(api_app.state.settings, api_app.state.providers, api_app.state.session_factory)
    for _ in range(10):
        await worker.tick()

    async with api_app.state.session_factory() as session:
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()
    return tenant_id, number.e164


# ---------------------------------------------------------------------------
# Post-call webhook
# ---------------------------------------------------------------------------
async def test_a_valid_post_call_creates_a_call(api_client: AsyncClient, api_app: FastAPI) -> None:
    tenant_id, e164 = await provision_tenant(api_client, api_app)
    body, headers = signed(post_call_payload(called=e164))

    response = await api_client.post(POST_CALL_URL, content=body, headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == WebhookStatus.PROCESSED.value

    async with api_app.state.session_factory() as session:
        call = (await session.execute(select(Call))).scalar_one()

    assert call.tenant_id == tenant_id
    assert call.provider_call_id == "conv_abc"
    assert call.from_e164 == "+15559998888"
    assert call.duration_s == 96
    assert call.status is CallStatus.RECEIVED
    # Stored as text for the summarizer, plus the turns.
    assert "Thursday morning" in call.transcript_json["text"]


async def test_a_redelivery_does_not_create_a_second_call(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Providers retry by design; a retry must be a visible no-op."""
    _, e164 = await provision_tenant(api_client, api_app)
    body, headers = signed(post_call_payload(called=e164))

    first = await api_client.post(POST_CALL_URL, content=body, headers=headers)
    second = await api_client.post(POST_CALL_URL, content=body, headers=headers)

    assert first.json()["status"] == WebhookStatus.PROCESSED.value
    assert second.json()["status"] == WebhookStatus.DUPLICATE.value

    async with api_app.state.session_factory() as session:
        calls = (await session.execute(select(Call))).scalars().all()
    assert len(calls) == 1


async def test_a_new_event_id_for_a_known_call_is_still_a_duplicate(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Dedupe on the call, not only on the delivery."""
    _, e164 = await provision_tenant(api_client, api_app)

    first_body, first_headers = signed(post_call_payload(called=e164))
    await api_client.post(POST_CALL_URL, content=first_body, headers=first_headers)

    # Same conversation, different envelope field, so a different event id.
    payload = post_call_payload(called=e164)
    payload["delivery"] = "second-attempt"
    body, headers = signed(payload)
    second = await api_client.post(POST_CALL_URL, content=body, headers=headers)

    assert second.json()["status"] == WebhookStatus.DUPLICATE.value
    async with api_app.state.session_factory() as session:
        assert len((await session.execute(select(Call))).scalars().all()) == 1


async def test_an_invalid_signature_is_recorded_not_trusted(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Outside production the delivery is accepted but flagged, so a
    misconfigured secret is visible rather than silent."""
    _, e164 = await provision_tenant(api_client, api_app)
    body = json.dumps(post_call_payload(called=e164)).encode()

    response = await api_client.post(
        POST_CALL_URL,
        content=body,
        headers={"elevenlabs-signature": "t=1,v0=deadbeef", "content-type": "application/json"},
    )

    assert response.status_code == 200
    async with api_app.state.session_factory() as session:
        event = (await session.execute(select(WebhookEvent))).scalar_one()
    assert event.signature_valid is False


async def test_production_refuses_an_unsigned_delivery(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    api_app.state.settings.environment = "production"
    try:
        response = await api_client.post(
            POST_CALL_URL, json=post_call_payload(), headers={"elevenlabs-signature": "bad"}
        )
    finally:
        api_app.state.settings.environment = "test"

    assert response.json()["status"] == WebhookStatus.REJECTED.value
    # Refused before anything was stored against a tenant.
    async with api_app.state.session_factory() as session:
        assert (await session.execute(select(WebhookEvent))).scalars().all() == []


async def test_a_malformed_body_is_rejected_not_raised(api_client: AsyncClient) -> None:
    body = b"this is not json"
    header = build_elevenlabs_signature(payload=body, secret=SECRET, timestamp=int(time.time()))

    response = await api_client.post(
        POST_CALL_URL, content=body, headers={"elevenlabs-signature": header}
    )

    assert response.status_code == 200
    assert response.json()["status"] == WebhookStatus.REJECTED.value


async def test_a_payload_without_a_conversation_id_is_recorded_and_rejected(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Recorded, so "we never got it" and "we could not read it" stay distinct."""
    body, headers = signed({"type": "post_call_transcription", "data": {"nothing": True}})

    response = await api_client.post(POST_CALL_URL, content=body, headers=headers)

    assert response.json()["status"] == WebhookStatus.REJECTED.value
    async with api_app.state.session_factory() as session:
        event = (await session.execute(select(WebhookEvent))).scalar_one()
    assert event.status is WebhookStatus.REJECTED
    assert event.error and "conversation id" in event.error


async def test_a_call_to_an_unknown_number_is_rejected_with_a_reason(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    body, headers = signed(post_call_payload(called="+19995550000"))

    response = await api_client.post(POST_CALL_URL, content=body, headers=headers)

    assert response.json()["status"] == WebhookStatus.REJECTED.value
    async with api_app.state.session_factory() as session:
        event = (await session.execute(select(WebhookEvent))).scalar_one()
        assert (await session.execute(select(Call))).scalars().all() == []
    assert event.error and "no tenant owns" in event.error


async def test_the_webhook_answers_2xx_even_when_it_rejects(
    api_client: AsyncClient,
) -> None:
    """A 4xx would make the provider retry a payload that can never succeed."""
    body, headers = signed({"data": {}})
    response = await api_client.post(POST_CALL_URL, content=body, headers=headers)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Twilio status callback
# ---------------------------------------------------------------------------
async def test_twilio_status_callbacks_are_recorded(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    params = {"CallSid": "CA123", "CallStatus": "completed", "From": "+15551110000"}
    response = await api_client.post(TWILIO_URL, data=params)

    assert response.status_code == 200
    async with api_app.state.session_factory() as session:
        event = (await session.execute(select(WebhookEvent))).scalar_one()
    assert event.event_type == "completed"
    assert event.event_id == "CallSid:CA123"


async def test_a_repeated_twilio_callback_is_a_duplicate(api_client: AsyncClient) -> None:
    params = {"CallSid": "CA123", "CallStatus": "completed"}
    await api_client.post(TWILIO_URL, data=params)
    second = await api_client.post(TWILIO_URL, data=params)
    assert second.json()["status"] == WebhookStatus.DUPLICATE.value
