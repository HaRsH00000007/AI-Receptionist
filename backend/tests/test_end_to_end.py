"""The whole POC, in one test.

    signup -> worker -> LLM config -> Twilio -> ElevenLabs -> link -> verify
           -> ACTIVE -> activation email -> inbound call -> post-call webhook
           -> call record -> summary -> summary email -> dashboard

Everything real except the four vendors, which are fakes. This is the test that
answers "does the thing work", and the one to read first when it does not.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import AgentConfig, Call, PhoneNumber
from app.models.enums import CallStatus, ProvisioningStatus, TenantStatus
from app.services.signatures import build_elevenlabs_signature
from app.worker import Worker

SIGNUP: dict[str, Any] = {
    "business_name": "Sunset Salon",
    "business_type": "salon",
    "services": "haircuts, colour, walk-ins",
    "operating_hours": "Mon-Fri 9-6, Sat till 2, closed Sun",
    "greeting_style": "friendly",
    "escalation_rules": "Text the owner if a caller says it is an emergency",
    "notification_email": "owner@sunsetsalon.example.com",
    "area_code": "805",
    "plan": "pro",
    "contact_phone": "(805) 555-0142",
}

WEBHOOK_SECRET = "test-webhook-secret"


async def test_the_whole_poc_runs_end_to_end(api_client: AsyncClient, api_app: FastAPI) -> None:
    providers = api_app.state.providers
    worker = Worker(api_app.state.settings, providers, api_app.state.session_factory)

    # ---- 1. A business signs up -----------------------------------------
    signup = await api_client.post("/api/v1/signups", json=SIGNUP)
    assert signup.status_code == 201
    tenant_id = signup.json()["tenant_id"]
    assert signup.json()["provisioning_status"] == ProvisioningStatus.DRAFT.value

    # Nothing has been bought yet: signup does not call a provider.
    assert providers.twilio.behaviour.count("purchase_number") == 0

    # ---- 2. The worker provisions ---------------------------------------
    for _ in range(12):
        await worker.tick()

    provisioning = (await api_client.get(f"/api/v1/tenants/{tenant_id}/provisioning")).json()
    assert provisioning["status"] == ProvisioningStatus.ACTIVE.value
    assert provisioning["completed_steps"] == 7

    tenant = (await api_client.get(f"/api/v1/tenants/{tenant_id}")).json()
    assert tenant["status"] == TenantStatus.ACTIVE.value

    # ---- 3. A number was bought and an agent created ---------------------
    phone = (await api_client.get(f"/api/v1/tenants/{tenant_id}/phone")).json()
    agent = (await api_client.get(f"/api/v1/tenants/{tenant_id}/agent")).json()

    assert phone["e164"].startswith("+1805")  # the area code that was asked for
    assert agent["elevenlabs_agent_id"]
    assert agent["config_version"] == 1
    assert providers.twilio.behaviour.count("purchase_number") == 1

    # ---- 4. The agent runs our config, not the model's raw output --------
    async with api_app.state.session_factory() as session:
        config = (
            await session.execute(
                select(AgentConfig).where(AgentConfig.tenant_id == uuid.UUID(tenant_id))
            )
        ).scalar_one()
    vendor_agent = next(iter(providers.elevenlabs.agents.values()))
    assert vendor_agent.system_prompt == config.system_prompt
    assert "Sunset Salon" in config.system_prompt
    assert "haircuts" in config.system_prompt
    # Voice from the chosen greeting style, deterministically.
    assert vendor_agent.voice_id == api_app.state.settings.voice_map["friendly"]

    # ---- 5. The number is really linked to the agent ---------------------
    vendor_phone = await providers.elevenlabs.find_phone_number(e164=phone["e164"])
    assert vendor_phone is not None
    assert vendor_phone.assigned_agent_id == agent["elevenlabs_agent_id"]

    # ---- 6. The owner was told ------------------------------------------
    activation = providers.email.last_to("owner@sunsetsalon.example.com")
    assert activation is not None
    assert phone["e164"] in activation.text

    # ---- 7. A customer calls; ElevenLabs posts the transcript ------------
    payload = {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": "conv_e2e_1",
            "agent_id": agent["elevenlabs_agent_id"],
            "metadata": {
                "start_time_unix_secs": int(time.time()) - 120,
                "call_duration_secs": 84,
                "phone_call": {
                    "agent_number": phone["e164"],
                    "external_number": "+15559998888",
                },
            },
            "transcript": [
                {"role": "agent", "message": "Hi, thanks for calling Sunset Salon!"},
                {"role": "user", "message": "Hi, it's Jamie. Anything Thursday morning?"},
                {"role": "agent", "message": "I'll take your number and we'll confirm."},
            ],
        },
    }
    body = json.dumps(payload).encode()
    webhook = await api_client.post(
        "/api/v1/webhooks/elevenlabs/post-call",
        content=body,
        headers={
            "elevenlabs-signature": build_elevenlabs_signature(
                payload=body, secret=WEBHOOK_SECRET, timestamp=int(time.time())
            ),
            "content-type": "application/json",
        },
    )
    assert webhook.status_code == 200
    assert webhook.json()["status"] == "processed"

    # ---- 8. The worker summarizes and notifies ---------------------------
    for _ in range(4):
        await worker.tick()

    calls = (await api_client.get(f"/api/v1/tenants/{tenant_id}/calls")).json()
    assert len(calls) == 1
    assert calls[0]["status"] == CallStatus.NOTIFIED.value
    assert calls[0]["summary"]
    assert calls[0]["intent"] == "booking_request"
    assert calls[0]["from_e164"] == "+15559998888"

    summary_email = providers.email.last_to("owner@sunsetsalon.example.com")
    assert summary_email is not None
    assert "New call" in summary_email.subject
    assert calls[0]["summary"] in summary_email.text

    # ---- 9. It is all visible, and it settles ----------------------------
    runs = (await api_client.get("/api/v1/admin/runs")).json()
    assert len(runs) == 1
    assert runs[0]["status"] == ProvisioningStatus.ACTIVE.value
    assert runs[0]["last_error"] is None

    before = (worker.stats.steps_executed, worker.stats.calls_processed)
    for _ in range(3):
        await worker.tick()
    assert (worker.stats.steps_executed, worker.stats.calls_processed) == before


async def test_a_redelivered_webhook_does_not_email_twice(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The whole point of durable dedupe, end to end."""
    providers = api_app.state.providers
    worker = Worker(api_app.state.settings, providers, api_app.state.session_factory)

    signup = await api_client.post("/api/v1/signups", json=SIGNUP)
    tenant_id = uuid.UUID(signup.json()["tenant_id"])
    for _ in range(12):
        await worker.tick()

    async with api_app.state.session_factory() as session:
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()

    payload = {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": "conv_dupe",
            "metadata": {
                "call_duration_secs": 30,
                "phone_call": {
                    "agent_number": number.e164,
                    "external_number": "+15551112222",
                },
            },
            "transcript": [{"role": "user", "message": "Are you open Saturday?"}],
        },
    }
    body = json.dumps(payload).encode()
    headers = {
        "elevenlabs-signature": build_elevenlabs_signature(
            payload=body, secret=WEBHOOK_SECRET, timestamp=int(time.time())
        ),
        "content-type": "application/json",
    }

    # Three deliveries of the same call, as a retrying provider would send.
    for _ in range(3):
        await api_client.post(
            "/api/v1/webhooks/elevenlabs/post-call", content=body, headers=headers
        )
    for _ in range(4):
        await worker.tick()

    async with api_app.state.session_factory() as session:
        calls = (await session.execute(select(Call))).scalars().all()

    assert len(calls) == 1
    summary_emails = [
        message for message in providers.email.outbox if "New call" in message.subject
    ]
    assert len(summary_emails) == 1


async def test_a_failed_run_leaves_nothing_billing(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The money-leak guarantee, end to end.

    A run that fails after the purchase must hand the number back, tell the
    owner, and leave the tenant visibly abandoned rather than half-built.
    """
    from app.core.errors import VendorError

    providers = api_app.state.providers
    worker = Worker(api_app.state.settings, providers, api_app.state.session_factory)

    providers.elevenlabs.behaviour.fail(
        "create_agent",
        VendorError.from_status("agent rejected", vendor="fake-elevenlabs", status_code=422),
        times=10,
    )

    signup = await api_client.post("/api/v1/signups", json=SIGNUP)
    tenant_id = signup.json()["tenant_id"]

    for _ in range(15):
        await worker.tick()

    provisioning = (await api_client.get(f"/api/v1/tenants/{tenant_id}/provisioning")).json()
    assert provisioning["status"] == ProvisioningStatus.COMPENSATED.value
    assert provisioning["last_error"]

    tenant = (await api_client.get(f"/api/v1/tenants/{tenant_id}")).json()
    assert tenant["status"] == TenantStatus.ABANDONED.value

    # Nothing left at the vendor, so nothing left billing.
    assert providers.twilio.purchased == {}
    assert providers.elevenlabs.agents == {}

    failure_email = providers.email.last_to("owner@sunsetsalon.example.com")
    assert failure_email is not None
    assert "could not finish" in failure_email.subject.lower()
