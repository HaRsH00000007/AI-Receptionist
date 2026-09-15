"""M20 — the full local end-to-end journey.

One test walks the whole product, in order, through the real application:

    signup → billing gate → LLM config → number → agent → link → verify
           → ACTIVE → inbound call → webhook → call record → transcript
           → summary → notification → usage → dashboard → admin

Everything runs against the real PostgreSQL and the real HTTP stack. Every
vendor is a fake, which is what makes this repeatable from a clean environment
and safe to run on every push: nothing buys a number, charges a card or sends
mail. That is a deliberate limit, not an oversight — the fakes prove *our*
orchestration, and the vendors' own API shapes are proven by the adapter tests
and, ultimately, by a credentialed integration run.

The failure scenarios at the end matter as much as the happy path. A system
that only works when everything works is a demo; the plan this project exists
to replace failed precisely because nobody had tested what happened when a step
did not.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models import (
    Agent,
    AgentConfig,
    Call,
    Notification,
    PhoneNumber,
    ProvisioningRun,
    Tenant,
    UsageEvent,
    WebhookEvent,
)
from app.models.enums import (
    AgentStatus,
    CallStatus,
    NotificationStatus,
    PhoneNumberStatus,
    ProvisioningStatus,
    TenantStatus,
)
from app.services.signatures import build_elevenlabs_signature, build_twilio_signature, form_encode
from app.services.status_tokens import issue_status_token
from app.worker import Worker

SIGNUP: dict[str, Any] = {
    "business_name": "Sunset Salon",
    "business_type": "salon",
    "services": "haircut, colour, styling",
    "operating_hours": "Tue-Sat 9-6, closed Sun and Mon",
    "greeting_style": "friendly",
    "escalation_rules": "Text the owner for complaints",
    "notification_email": "owner@sunset-e2e.example.com",
    "area_code": "805",
    "plan": "starter",
    "contact_phone": "8055550142",
}

AUTH_TOKEN = "test-token"
WEBHOOK_SECRET = "test-webhook-secret"


async def _drive(app: FastAPI, ticks: int = 20) -> None:
    """Run the worker until there is nothing left to do."""
    worker = Worker(app.state.settings, app.state.providers, app.state.session_factory)
    for _ in range(ticks):
        await worker.tick()


def _post_call_body(*, called: str, caller: str, conversation_id: str) -> bytes:
    return json.dumps(
        {
            "type": "post_call_transcription",
            "conversation_id": conversation_id,
            "data": {
                "conversation_id": conversation_id,
                "agent_id": "agent_e2e",
                "metadata": {
                    "phone_call": {"agent_number": called, "external_number": caller},
                    "call_duration_secs": 143,
                    "start_time_unix_secs": int(time.time()) - 200,
                },
                "transcript": [
                    {"role": "agent", "message": "Thanks for calling Sunset Salon."},
                    {"role": "user", "message": "Do you have anything Thursday morning?"},
                    {"role": "agent", "message": "I can take a message for the owner."},
                    {"role": "user", "message": "It's Dana, on 555 999 8888."},
                ],
            },
        }
    ).encode()


async def test_the_whole_journey(api_client: AsyncClient, api_app: FastAPI) -> None:
    """Signup to dashboard, through the real application."""

    # ---- 1. Signup ------------------------------------------------------
    # Returns immediately with `draft`: the front door never calls a provider,
    # so a vendor outage cannot make the form appear broken.
    response = await api_client.post("/api/v1/signups", json=SIGNUP)
    assert response.status_code == 201, response.text

    created = response.json()
    tenant_id = uuid.UUID(created["tenant_id"])
    status_token = created["status_token"]
    assert created["provisioning_status"] == "draft"
    assert created["tenant_status"] == "pending"
    # The raw id is not a credential; a signed, expiring grant is issued instead.
    assert status_token

    # ---- 2. Provisioning ------------------------------------------------
    await _drive(api_app)

    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        assert tenant.status is TenantStatus.ACTIVE

        run = (
            await session.execute(
                select(ProvisioningRun).where(ProvisioningRun.tenant_id == tenant_id)
            )
        ).scalar_one()
        assert run.status is ProvisioningStatus.ACTIVE

        # 3. The LLM wrote a config, and it is versioned and live.
        config = (
            await session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == tenant_id)
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one()
        assert config.version == 1
        assert "Sunset Salon" in config.system_prompt

        # 4. A number was provisioned — exactly one.
        number = (
            await session.execute(
                select(PhoneNumber)
                .where(PhoneNumber.tenant_id == tenant_id)
                .where(PhoneNumber.status == PhoneNumberStatus.ACTIVE)
            )
        ).scalar_one()

        # 5. An agent exists and points at the live config.
        agent = (
            await session.execute(
                select(Agent)
                .where(Agent.tenant_id == tenant_id)
                .where(Agent.status == AgentStatus.ACTIVE)
            )
        ).scalar_one()
        assert agent.elevenlabs_agent_id
        assert agent.agent_config_id == config.id

        e164 = number.e164

    # ---- 6. An inbound call reaches the right tenant ---------------------
    params = {"To": e164, "From": "+15559998888", "CallSid": "CA_e2e"}
    twiml = await api_client.post(
        "/api/v1/voice/inbound",
        content=form_encode(params),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "x-twilio-signature": build_twilio_signature(
                url="http://testserver/api/v1/voice/inbound",
                params=params,
                auth_token=AUTH_TOKEN,
            ),
        },
    )
    assert twiml.status_code == 200
    assert agent.elevenlabs_agent_id in twiml.text

    # The conversation asks who it is answering for.
    init = await api_client.post("/api/v1/voice/init", json={"agent_number": e164})
    variables = init.json()["dynamic_variables"]
    assert variables["business_name"] == "Sunset Salon"
    assert variables["config_version"] == "1"

    # ---- 7. The post-call webhook ---------------------------------------
    body = _post_call_body(called=e164, caller="+15559998888", conversation_id="conv_e2e")
    webhook = await api_client.post(
        "/api/v1/webhooks/elevenlabs/post-call",
        content=body,
        headers={
            "content-type": "application/json",
            "elevenlabs-signature": build_elevenlabs_signature(
                payload=body, secret=WEBHOOK_SECRET, timestamp=int(time.time())
            ),
        },
    )
    assert webhook.json()["status"] == "processed"

    # ---- 8. Summary, notification and metering --------------------------
    await _drive(api_app)

    async with api_app.state.session_factory() as session:
        call = (
            await session.execute(select(Call).where(Call.provider_call_id == "conv_e2e"))
        ).scalar_one()
        assert call.tenant_id == tenant_id
        assert call.status is CallStatus.NOTIFIED
        assert call.summary
        # The transcript is stored, and the version that served it is recorded.
        assert call.transcript_json is not None
        assert call.agent_config_version == 1

        # A notification was queued and delivered, exactly once.
        notifications = (
            (await session.execute(select(Notification).where(Notification.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        summaries = [n for n in notifications if n.call_id == call.id]
        assert len(summaries) == 1
        assert summaries[0].status is NotificationStatus.SENT

        # Usage was metered once, from the call's real duration.
        usage = (
            (await session.execute(select(UsageEvent).where(UsageEvent.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        assert len(usage) == 1
        assert usage[0].quantity == 143

    # ---- 9. The customer can see all of it ------------------------------
    grant = {"status_token": status_token}

    tenant_view = await api_client.get(f"/api/v1/tenants/{tenant_id}", params=grant)
    assert tenant_view.json()["status"] == "active"

    phone_view = await api_client.get(f"/api/v1/tenants/{tenant_id}/phone", params=grant)
    assert phone_view.json()["e164"] == e164

    calls_view = await api_client.get(f"/api/v1/tenants/{tenant_id}/calls", params=grant)
    assert len(calls_view.json()) == 1
    assert calls_view.json()[0]["summary"]

    usage_view = await api_client.get(f"/api/v1/tenants/{tenant_id}/usage", params=grant)
    assert usage_view.json()["call_count"] == 1
    # 143 seconds bills as three minutes, the way vendors bill.
    assert usage_view.json()["call_minutes"] == 3

    # ---- 10. The operator can see it too --------------------------------
    admin_key = api_app.state.settings.admin_api_key.get_secret_value()
    runs = await api_client.get("/api/v1/admin/runs", headers={"x-admin-key": admin_key})
    assert runs.status_code == 200
    assert any(row["tenant_id"] == str(tenant_id) for row in runs.json())

    # And the metrics recorded the journey without leaking who it was about.
    metrics = (await api_client.get("/metrics")).text
    assert "inbound_calls_total{" in metrics
    assert str(tenant_id) not in metrics
    assert e164 not in metrics


async def test_the_journey_is_repeatable_from_a_clean_state(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Two independent tenants, provisioned in the same process.

    Catches the class of bug where the first run leaves state the second
    inherits — a cached number lookup, a reused idempotency key, a config
    version counter that is global rather than per tenant.
    """
    ids = []
    for index in range(2):
        response = await api_client.post(
            "/api/v1/signups",
            json={**SIGNUP, "notification_email": f"repeat{index}@example.com"},
        )
        assert response.status_code == 201
        ids.append(uuid.UUID(response.json()["tenant_id"]))

    await _drive(api_app)

    async with api_app.state.session_factory() as session:
        for tenant_id in ids:
            tenant = await session.get(Tenant, tenant_id)
            assert tenant is not None and tenant.status is TenantStatus.ACTIVE

            # Each tenant's config numbering starts at 1 — per tenant, not global.
            config = (
                await session.execute(
                    select(AgentConfig)
                    .where(AgentConfig.tenant_id == tenant_id)
                    .where(AgentConfig.is_live.is_(True))
                )
            ).scalar_one()
            assert config.version == 1

        # Two tenants, two distinct numbers.
        numbers = (
            (await session.execute(select(PhoneNumber.e164).where(PhoneNumber.tenant_id.in_(ids))))
            .scalars()
            .all()
        )
        assert len(set(numbers)) == 2


# ===========================================================================
# Failure scenarios — the half that the replaced system never tested
# ===========================================================================


async def test_a_call_to_a_number_we_do_not_own_is_refused(
    api_client: AsyncClient,
) -> None:
    params = {"To": "+14155550999", "From": "+15551110000", "CallSid": "CA_nobody"}
    response = await api_client.post(
        "/api/v1/voice/inbound",
        content=form_encode(params),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "x-twilio-signature": build_twilio_signature(
                url="http://testserver/api/v1/voice/inbound",
                params=params,
                auth_token=AUTH_TOKEN,
            ),
        },
    )
    assert "<Reject" in response.text


async def test_a_vendor_outage_mid_journey_still_takes_the_message(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The caller is a real person who dialled a real business.

    Silence loses that customer and the business never learns they called.
    """
    response = await api_client.post(
        "/api/v1/signups", json={**SIGNUP, "notification_email": "outage@example.com"}
    )
    tenant_id = uuid.UUID(response.json()["tenant_id"])
    await _drive(api_app)

    async with api_app.state.session_factory() as session:
        e164 = (
            await session.execute(
                select(PhoneNumber.e164).where(PhoneNumber.tenant_id == tenant_id)
            )
        ).scalar_one()

    breaker = api_app.state.circuits.for_vendor("elevenlabs")
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()

    params = {"To": e164, "From": "+15551110000", "CallSid": "CA_outage"}
    response = await api_client.post(
        "/api/v1/voice/inbound",
        content=form_encode(params),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "x-twilio-signature": build_twilio_signature(
                url="http://testserver/api/v1/voice/inbound",
                params=params,
                auth_token=AUTH_TOKEN,
            ),
        },
    )
    assert "<Record" in response.text
    assert "<Connect" not in response.text


async def test_a_duplicate_webhook_does_not_double_bill_or_double_email(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The end-to-end version of the dedupe guarantees.

    Three deliveries of one call must produce one call row, one usage event and
    one email — the failure that would otherwise reach the customer's inbox and
    their invoice at the same time.
    """
    response = await api_client.post(
        "/api/v1/signups", json={**SIGNUP, "notification_email": "dupes@example.com"}
    )
    tenant_id = uuid.UUID(response.json()["tenant_id"])
    await _drive(api_app)

    async with api_app.state.session_factory() as session:
        e164 = (
            await session.execute(
                select(PhoneNumber.e164)
                .where(PhoneNumber.tenant_id == tenant_id)
                .where(PhoneNumber.status == PhoneNumberStatus.ACTIVE)
            )
        ).scalar_one()

    body = _post_call_body(called=e164, caller="+15551110000", conversation_id="conv_dupe_e2e")
    headers = {
        "content-type": "application/json",
        "elevenlabs-signature": build_elevenlabs_signature(
            payload=body, secret=WEBHOOK_SECRET, timestamp=int(time.time())
        ),
    }
    for _ in range(3):
        await api_client.post(
            "/api/v1/webhooks/elevenlabs/post-call", content=body, headers=headers
        )

    await _drive(api_app)

    async with api_app.state.session_factory() as session:
        calls = (
            await session.execute(select(func.count(Call.id)).where(Call.tenant_id == tenant_id))
        ).scalar_one()
        usage = (
            await session.execute(
                select(func.count(UsageEvent.id)).where(UsageEvent.tenant_id == tenant_id)
            )
        ).scalar_one()
        summaries = (
            await session.execute(
                select(func.count(Notification.id))
                .where(Notification.tenant_id == tenant_id)
                .where(Notification.call_id.is_not(None))
            )
        ).scalar_one()

    assert calls == 1
    assert usage == 1
    assert summaries == 1


async def test_a_forged_post_call_reaches_nothing(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The end-to-end version: no call, no usage, no email, no stored event."""
    response = await api_client.post(
        "/api/v1/signups", json={**SIGNUP, "notification_email": "forged@example.com"}
    )
    tenant_id = uuid.UUID(response.json()["tenant_id"])
    await _drive(api_app)

    async with api_app.state.session_factory() as session:
        e164 = (
            await session.execute(
                select(PhoneNumber.e164)
                .where(PhoneNumber.tenant_id == tenant_id)
                .where(PhoneNumber.status == PhoneNumberStatus.ACTIVE)
            )
        ).scalar_one()

    body = _post_call_body(called=e164, caller="+15551110000", conversation_id="conv_forged_e2e")
    await api_client.post(
        "/api/v1/webhooks/elevenlabs/post-call",
        content=body,
        headers={"content-type": "application/json", "elevenlabs-signature": "t=1,v0=nope"},
    )
    await _drive(api_app)

    async with api_app.state.session_factory() as session:
        calls = (
            await session.execute(select(func.count(Call.id)).where(Call.tenant_id == tenant_id))
        ).scalar_one()
        events = (
            await session.execute(
                select(func.count(WebhookEvent.id)).where(
                    WebhookEvent.event_id == "conversation_id:conv_forged_e2e"
                )
            )
        ).scalar_one()

    assert calls == 0
    assert events == 0


async def test_another_tenants_grant_opens_nothing(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Tenant isolation, asserted against a fully provisioned pair."""
    ids = []
    for index in range(2):
        response = await api_client.post(
            "/api/v1/signups",
            json={**SIGNUP, "notification_email": f"isolated{index}@example.com"},
        )
        ids.append(uuid.UUID(response.json()["tenant_id"]))
    await _drive(api_app)

    mine, theirs = ids
    my_grant = issue_status_token(api_app.state.settings, mine)

    for path in ("", "/calls", "/phone", "/usage", "/provisioning"):
        response = await api_client.get(
            f"/api/v1/tenants/{theirs}{path}", params={"status_token": my_grant}
        )
        assert response.status_code in (401, 403, 404), path
