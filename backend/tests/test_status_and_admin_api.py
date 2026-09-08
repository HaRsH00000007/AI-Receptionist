"""The status endpoints the frontend polls, and the admin actions."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from app.models import Agent, Call, PhoneNumber, ProvisioningRun, ProvisioningStepRecord
from app.models.enums import (
    AgentStatus,
    PhoneNumberStatus,
    ProvisioningStatus,
    ProvisioningStep,
    StepStatus,
    TenantStatus,
)

SIGNUP: dict[str, Any] = {
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


async def signup(api_client: AsyncClient, **overrides: Any) -> uuid.UUID:
    response = await api_client.post("/api/v1/signups", json={**SIGNUP, **overrides})
    assert response.status_code == 201
    return uuid.UUID(response.json()["tenant_id"])


async def drive_to_active(api_app: FastAPI, ticks: int = 12) -> None:
    from app.worker import Worker

    worker = Worker(api_app.state.settings, api_app.state.providers, api_app.state.session_factory)
    for _ in range(ticks):
        await worker.tick()


# ---------------------------------------------------------------------------
# Status endpoints
# ---------------------------------------------------------------------------
async def test_tenant_detail(api_client: AsyncClient) -> None:
    tenant_id = await signup(api_client)
    response = await api_client.get(f"/api/v1/tenants/{tenant_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Sunset Salon"
    assert body["status"] == TenantStatus.PENDING.value
    assert body["timezone"] == "America/Los_Angeles"


async def test_an_unknown_tenant_is_a_404(api_client: AsyncClient) -> None:
    response = await api_client.get(f"/api/v1/tenants/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_provisioning_shows_the_whole_plan_immediately(
    api_client: AsyncClient,
) -> None:
    """All seven steps from the first second, so "stuck at 3 of 7" is answerable."""
    tenant_id = await signup(api_client)
    response = await api_client.get(f"/api/v1/tenants/{tenant_id}/provisioning")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == ProvisioningStatus.DRAFT.value
    assert body["total_steps"] == 7
    assert body["completed_steps"] == 0
    assert [step["step_name"] for step in body["steps"]] == [
        step.value for step in ProvisioningStep
    ]
    assert body["correlation_id"]


async def test_provisioning_reflects_progress(api_client: AsyncClient, api_app: FastAPI) -> None:
    tenant_id = await signup(api_client)
    await drive_to_active(api_app)

    body = (await api_client.get(f"/api/v1/tenants/{tenant_id}/provisioning")).json()

    assert body["status"] == ProvisioningStatus.ACTIVE.value
    assert body["completed_steps"] == 7
    assert body["last_error"] is None
    assert all(step["status"] == StepStatus.SUCCEEDED.value for step in body["steps"])


async def test_phone_and_agent_appear_once_active(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    tenant_id = await signup(api_client)
    await drive_to_active(api_app)

    phone = (await api_client.get(f"/api/v1/tenants/{tenant_id}/phone")).json()
    agent = (await api_client.get(f"/api/v1/tenants/{tenant_id}/agent")).json()

    assert phone["e164"].startswith("+1805")
    assert phone["status"] == PhoneNumberStatus.ACTIVE.value
    assert agent["status"] == AgentStatus.ACTIVE.value
    assert agent["elevenlabs_agent_id"]
    assert agent["config_version"] == 1


async def test_phone_is_404_before_provisioning(api_client: AsyncClient) -> None:
    tenant_id = await signup(api_client)
    assert (await api_client.get(f"/api/v1/tenants/{tenant_id}/phone")).status_code == 404


async def test_calls_list_is_empty_then_populated(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    tenant_id = await signup(api_client)
    await drive_to_active(api_app)

    assert (await api_client.get(f"/api/v1/tenants/{tenant_id}/calls")).json() == []

    async with api_app.state.session_factory() as session:
        session.add(
            Call(
                tenant_id=tenant_id,
                provider_call_id="conv_1",
                from_e164="+15559998888",
                summary="Caller asked about Thursday.",
                intent="booking_request",
                urgency=2,
            )
        )
        await session.commit()

    calls = (await api_client.get(f"/api/v1/tenants/{tenant_id}/calls")).json()
    assert len(calls) == 1
    assert calls[0]["summary"] == "Caller asked about Thursday."
    # The transcript is never published; only the summary is.
    assert "transcript" not in calls[0]


async def test_a_tenant_cannot_see_another_tenants_calls(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Isolation is in the query, not in a check someone might forget."""
    first = await signup(api_client)
    second = await signup(api_client, notification_email="other@salon.example.com")

    async with api_app.state.session_factory() as session:
        session.add(Call(tenant_id=first, provider_call_id="conv_first", summary="first"))
        await session.commit()

    assert (await api_client.get(f"/api/v1/tenants/{second}/calls")).json() == []
    assert len((await api_client.get(f"/api/v1/tenants/{first}/calls")).json()) == 1


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
async def test_admin_lists_runs(api_client: AsyncClient) -> None:
    await signup(api_client)
    response = await api_client.get("/api/v1/admin/runs")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["business_name"] == "Sunset Salon"
    assert rows[0]["status"] == ProvisioningStatus.DRAFT.value


async def test_admin_filters_by_status(api_client: AsyncClient, api_app: FastAPI) -> None:
    await signup(api_client)
    await drive_to_active(api_app)

    active = await api_client.get("/api/v1/admin/runs", params={"status": "active"})
    failed = await api_client.get("/api/v1/admin/runs", params={"status": "failed"})

    assert len(active.json()) == 1
    assert failed.json() == []


async def test_admin_requires_the_key_when_one_is_set(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    # SecretStr, because assigning to a settings attribute at runtime skips
    # pydantic validation and would otherwise leave a plain str behind.
    api_app.state.settings.admin_api_key = SecretStr("s3cret")
    try:
        without = await api_client.get("/api/v1/admin/runs")
        wrong = await api_client.get("/api/v1/admin/runs", headers={"x-admin-key": "nope"})
        right = await api_client.get("/api/v1/admin/runs", headers={"x-admin-key": "s3cret"})
    finally:
        api_app.state.settings.admin_api_key = SecretStr("")

    assert without.status_code == 403
    assert wrong.status_code == 403
    assert right.status_code == 200


async def test_admin_fails_closed_in_production_without_a_key(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """An unset key in production must not mean "open"."""
    api_app.state.settings.environment = "production"
    try:
        response = await api_client.get("/api/v1/admin/runs")
    finally:
        api_app.state.settings.environment = "test"

    assert response.status_code == 403


async def test_retry_resets_a_failed_run(api_client: AsyncClient, api_app: FastAPI) -> None:
    """Definition of done 2: a failed run succeeds on retry from the panel."""
    from app.core.errors import VendorError

    tenant_id = await signup(api_client)
    api_app.state.providers.twilio.behaviour.fail(
        "purchase_number",
        VendorError.from_status("nope", vendor="fake-twilio", status_code=400),
        times=1,
    )
    await drive_to_active(api_app)

    before = (await api_client.get(f"/api/v1/tenants/{tenant_id}/provisioning")).json()
    assert before["status"] == ProvisioningStatus.COMPENSATED.value

    run_id = before["run_id"]
    retried = await api_client.post(f"/api/v1/admin/runs/{run_id}/retry")
    assert retried.status_code == 200
    assert retried.json()["ok"] is True

    await drive_to_active(api_app)

    after = (await api_client.get(f"/api/v1/tenants/{tenant_id}/provisioning")).json()
    assert after["status"] == ProvisioningStatus.ACTIVE.value


async def test_retry_from_a_step_rotates_the_idempotency_key(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Deliberately redoing a step is a new operation, so it needs a new key.

    Reusing it would let the step adopt the very side effect the operator is
    trying to replace.
    """
    tenant_id = await signup(api_client)
    await drive_to_active(api_app)

    async with api_app.state.session_factory() as session:
        run = (
            await session.execute(
                select(ProvisioningRun).where(ProvisioningRun.tenant_id == tenant_id)
            )
        ).scalar_one()
        before = (
            (
                await session.execute(
                    select(ProvisioningStepRecord)
                    .where(ProvisioningStepRecord.run_id == run.id)
                    .where(ProvisioningStepRecord.step_name == ProvisioningStep.PURCHASE_NUMBER)
                )
            )
            .scalar_one()
            .idempotency_key
        )

    await api_client.post(
        f"/api/v1/admin/runs/{run.id}/retry", params={"from_step": "purchase_number"}
    )

    async with api_app.state.session_factory() as session:
        after = (
            await session.execute(
                select(ProvisioningStepRecord)
                .where(ProvisioningStepRecord.run_id == run.id)
                .where(ProvisioningStepRecord.step_name == ProvisioningStep.PURCHASE_NUMBER)
            )
        ).scalar_one()

    assert after.idempotency_key != before
    assert after.status is StepStatus.PENDING
    assert after.attempt == 0


async def test_abandon_releases_the_number(api_client: AsyncClient, api_app: FastAPI) -> None:
    tenant_id = await signup(api_client)
    await drive_to_active(api_app)

    run_id = (await api_client.get(f"/api/v1/tenants/{tenant_id}/provisioning")).json()["run_id"]
    response = await api_client.post(f"/api/v1/admin/runs/{run_id}/abandon")

    assert response.status_code == 200
    assert "+1805" in response.json()["detail"]

    async with api_app.state.session_factory() as session:
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()
    assert number.status is PhoneNumberStatus.RELEASED
    # Actually handed back at the vendor, not merely marked in our database.
    assert number.twilio_sid in api_app.state.providers.twilio.released
    assert api_app.state.providers.twilio.purchased == {}


async def test_resync_pushes_the_live_config_to_the_vendor(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The database is the source of truth; the vendor object is a projection."""
    tenant_id = await signup(api_client)
    await drive_to_active(api_app)

    # Someone edits the agent at the vendor, out of band.
    stored = next(iter(api_app.state.providers.elevenlabs.agents.values()))
    stored.system_prompt = "tampered"

    response = await api_client.post(f"/api/v1/admin/tenants/{tenant_id}/resync-agent")

    assert response.status_code == 200
    assert "config v1" in response.json()["detail"]
    assert stored.system_prompt != "tampered"
    assert "Sunset Salon" in stored.system_prompt


async def test_resync_needs_an_agent(api_client: AsyncClient) -> None:
    tenant_id = await signup(api_client)
    response = await api_client.post(f"/api/v1/admin/tenants/{tenant_id}/resync-agent")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    ["/api/v1/admin/runs/{id}/retry", "/api/v1/admin/runs/{id}/abandon"],
)
async def test_admin_actions_on_an_unknown_run_are_404(api_client: AsyncClient, path: str) -> None:
    response = await api_client.post(path.format(id=uuid.uuid4()))
    assert response.status_code == 404


async def test_agents_and_numbers_are_not_shared_between_tenants(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    first = await signup(api_client)
    second = await signup(api_client, notification_email="other@salon.example.com")
    await drive_to_active(api_app, ticks=20)

    async with api_app.state.session_factory() as session:
        numbers = (await session.execute(select(PhoneNumber))).scalars().all()
        agents = (await session.execute(select(Agent))).scalars().all()

    assert {number.tenant_id for number in numbers} == {first, second}
    assert len({agent.elevenlabs_agent_id for agent in agents}) == 2
