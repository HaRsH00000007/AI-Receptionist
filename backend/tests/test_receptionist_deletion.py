"""Deleting a receptionist, with and without handing the number back.

Two properties carry this feature, and each has a test that fails loudly if it
regresses:

* **The calls stop even when the vendors do not cooperate.** Cancelling the
  tenant is committed before any vendor call, so a Twilio outage cannot leave a
  deleted receptionist answering the phone.
* **Nothing claims success it cannot stand behind.** A failed release leaves the
  number ACTIVE with its SID, reports ``complete: false``, and a repeat
  finishes the job.

The money tests matter most: releasing a number that was never bought, or
releasing somebody else's, are both things the fakes would happily do if the
service asked them to.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.cache.lookup import NumberDirectory
from app.core.errors import VendorError
from app.models import Agent, AuditLog, Call, PhoneNumber, Tenant
from app.models.enums import (
    AgentStatus,
    AuditAction,
    CallStatus,
    MembershipRole,
    PhoneNumberStatus,
    TenantStatus,
)
from app.telephony.routing import CallDisposition, RoutingInput, decide
from tests.portal_support import Owner, add_member, live_owner, other_tenant

DELETE = "/api/v1/tenants/{tenant_id}/receptionist/delete"


async def _delete(
    client: AsyncClient, owner: Owner, *, release: bool = False
) -> tuple[int, dict[str, Any]]:
    response = await client.post(
        DELETE.format(tenant_id=owner.tenant_id),
        json={"release_number": release},
        headers=owner.headers,
    )
    return response.status_code, response.json()


async def _state(
    api_app: FastAPI, tenant_id: uuid.UUID
) -> tuple[Tenant, Agent | None, PhoneNumber | None]:
    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        agent = (
            await session.execute(select(Agent).where(Agent.tenant_id == tenant_id))
        ).scalar_one_or_none()
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one_or_none()
        return tenant, agent, number


# ---------------------------------------------------------------------------
# Deleting, keeping the number
# ---------------------------------------------------------------------------


async def test_deleting_keeps_the_number_in_the_account(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    twilio = api_app.state.providers.twilio
    elevenlabs = api_app.state.providers.elevenlabs
    _, _, before = await _state(api_app, owner.tenant_id)
    assert before is not None and before.twilio_sid

    status, body = await _delete(api_client, owner)

    assert status == 200, body
    assert body["complete"] is True
    assert body["agent_deleted"] is True
    assert body["number_released"] is False
    assert body["number_retained"] == before.e164
    # The number was never handed back, so it is still ours at the vendor.
    assert twilio.behaviour.count("release_number") == 0
    # But its link to the agent is gone, so it cannot reach a deleted agent.
    assert elevenlabs.behaviour.count("delete_phone_number") == 1
    assert elevenlabs.behaviour.count("delete_agent") == 1

    tenant, agent, number = await _state(api_app, owner.tenant_id)
    assert tenant.status is TenantStatus.CANCELLED
    assert agent is not None and agent.status is AgentStatus.DELETED
    assert number is not None
    assert number.status is PhoneNumberStatus.ACTIVE, "kept for reuse"
    assert number.elevenlabs_phone_id is None, "the vendor import is gone"


async def test_a_cancelled_receptionist_stops_answering_calls(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The point of the whole feature: no caller reaches a deleted agent."""
    owner = await live_owner(api_client, api_app)
    _, _, number = await _state(api_app, owner.tenant_id)
    assert number is not None
    dialled = number.e164

    await _delete(api_client, owner)

    lookup = NumberDirectory(api_app.state.cache, api_app.state.settings)
    async with api_app.state.session_factory() as session:
        resolved = await lookup.resolve(session, dialled)
    assert resolved is not None
    assert resolved.tenant_status is TenantStatus.CANCELLED
    decision = decide(
        RoutingInput(
            dialled_e164=dialled,
            tenant_id=resolved.tenant_id,
            tenant_status=resolved.tenant_status,
            agent_id=None,
            business_name="Sunset Salon",
        )
    )
    assert decision.disposition is CallDisposition.UNAVAILABLE
    assert "no longer in service" in decision.message


async def test_history_survives_the_delete(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    async with api_app.state.session_factory() as session:
        session.add(
            Call(
                tenant_id=owner.tenant_id,
                provider_call_id=f"conv-{uuid.uuid4().hex}",
                from_e164="+15551110000",
                to_e164="+18055550100",
                started_at=datetime.now(UTC) - timedelta(hours=2),
                duration_s=61,
                summary="Booked a cut for Friday.",
                status=CallStatus.SUMMARIZED,
                transcript_json=[{"role": "agent", "message": "Thanks for calling!"}],
            )
        )
        await session.commit()

    await _delete(api_client, owner, release=True)

    async with api_app.state.session_factory() as session:
        calls = (
            (await session.execute(select(Call).where(Call.tenant_id == owner.tenant_id)))
            .scalars()
            .all()
        )
        tenant = await session.get(Tenant, owner.tenant_id)
    # The tenant row stays, so the business keeps its own records.
    assert tenant is not None
    assert len(calls) == 1
    assert calls[0].summary == "Booked a cut for Friday."
    assert calls[0].transcript_json


async def test_the_delete_is_audited(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    await _delete(api_client, owner, release=True)

    async with api_app.state.session_factory() as session:
        actions = (
            (
                await session.execute(
                    select(AuditLog.action).where(AuditLog.tenant_id == owner.tenant_id)
                )
            )
            .scalars()
            .all()
        )
    assert AuditAction.RECEPTIONIST_DELETED in actions
    assert AuditAction.NUMBER_RELEASED in actions


# ---------------------------------------------------------------------------
# Releasing the number
# ---------------------------------------------------------------------------


async def test_deleting_can_release_the_number(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    twilio = api_app.state.providers.twilio
    _, _, before = await _state(api_app, owner.tenant_id)
    assert before is not None and before.twilio_sid
    sid = before.twilio_sid

    status, body = await _delete(api_client, owner, release=True)

    assert status == 200, body
    assert body["complete"] is True
    assert body["number_released"] is True
    assert body["number_retained"] is None
    assert twilio.behaviour.count("release_number") == 1
    assert sid in twilio.released, "the number was really handed back"

    _, _, number = await _state(api_app, owner.tenant_id)
    assert number is not None
    assert number.status is PhoneNumberStatus.RELEASED
    assert number.released_at is not None, "the evidence the release happened"
    assert number.twilio_sid == sid, "the SID is kept for the audit trail"


async def test_only_this_tenants_number_is_released(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A release must never reach across businesses."""
    mine = await live_owner(api_client, api_app)
    theirs = await live_owner(api_client, api_app, area_code="213")
    _, _, their_number = await _state(api_app, theirs.tenant_id)
    assert their_number is not None

    await _delete(api_client, mine, release=True)

    their_tenant, their_agent, still_theirs = await _state(api_app, theirs.tenant_id)
    assert still_theirs is not None
    assert still_theirs.status is PhoneNumberStatus.ACTIVE
    assert still_theirs.released_at is None
    assert their_tenant.status is TenantStatus.ACTIVE
    assert their_agent is not None and their_agent.status is AgentStatus.ACTIVE


async def test_a_number_that_was_never_bought_is_not_released(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A PENDING row is intent, not a purchase: there is nothing to hand back."""
    owner = await live_owner(api_client, api_app)
    twilio = api_app.state.providers.twilio
    async with api_app.state.session_factory() as session:
        number = (
            await session.execute(
                select(PhoneNumber).where(PhoneNumber.tenant_id == owner.tenant_id)
            )
        ).scalar_one()
        number.status = PhoneNumberStatus.PENDING
        number.twilio_sid = None
        await session.commit()
    twilio.behaviour.reset()

    status, body = await _delete(api_client, owner, release=True)

    assert status == 200, body
    assert body["number_released"] is False
    assert twilio.behaviour.count("release_number") == 0, "no vendor call for a phantom number"
    _, _, after = await _state(api_app, owner.tenant_id)
    assert after is not None and after.status is PhoneNumberStatus.FAILED


async def test_deleting_without_a_number_works(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    async with api_app.state.session_factory() as session:
        number = (
            await session.execute(
                select(PhoneNumber).where(PhoneNumber.tenant_id == owner.tenant_id)
            )
        ).scalar_one()
        number.status = PhoneNumberStatus.RELEASED
        number.released_at = datetime.now(UTC)
        await session.commit()

    status, body = await _delete(api_client, owner, release=True)

    assert status == 200, body
    assert body["complete"] is True
    assert body["number_released"] is False
    assert body["number_retained"] is None


# ---------------------------------------------------------------------------
# When a vendor fails
# ---------------------------------------------------------------------------


async def test_a_failed_agent_delete_is_reported_not_swallowed(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    api_app.state.providers.elevenlabs.behaviour.fail(
        "delete_agent", VendorError("elevenlabs is down", vendor="elevenlabs")
    )

    status, body = await _delete(api_client, owner)

    assert status == 200, body
    assert body["complete"] is False, "a failed cleanup is not a success"
    assert body["agent_deleted"] is False
    assert any("delete_agent" in error for error in body["errors"])
    assert "down" not in str(body["errors"]), "codes only, never the vendor's message"

    tenant, agent, _ = await _state(api_app, owner.tenant_id)
    # Calls stopped anyway, and the row is left where a retry will find it.
    assert tenant.status is TenantStatus.CANCELLED
    assert agent is not None and agent.status is AgentStatus.ACTIVE


async def test_a_failed_release_keeps_the_sid_and_can_be_retried(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    twilio = api_app.state.providers.twilio
    twilio.behaviour.fail("release_number", VendorError("twilio is down", vendor="twilio"))
    _, _, before = await _state(api_app, owner.tenant_id)
    assert before is not None
    sid = before.twilio_sid

    status, body = await _delete(api_client, owner, release=True)

    assert status == 200, body
    assert body["complete"] is False
    assert body["number_released"] is False
    assert body["number_retained"] == before.e164, "still ours, still billing"
    assert any("release_number" in error for error in body["errors"])

    _, _, kept = await _state(api_app, owner.tenant_id)
    assert kept is not None
    assert kept.status is PhoneNumberStatus.ACTIVE
    assert kept.twilio_sid == sid, "losing the SID would orphan the number forever"

    # The retry is the whole point: the same request finishes the job.
    status, body = await _delete(api_client, owner, release=True)
    assert status == 200, body
    assert body["complete"] is True
    assert body["number_released"] is True
    _, _, released = await _state(api_app, owner.tenant_id)
    assert released is not None and released.status is PhoneNumberStatus.RELEASED


async def test_repeating_the_delete_does_not_release_twice(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    twilio = api_app.state.providers.twilio

    first_status, _ = await _delete(api_client, owner, release=True)
    second_status, second = await _delete(api_client, owner, release=True)

    assert first_status == 200
    assert second_status == 200
    assert second["complete"] is True
    assert second["number_released"] is False, "there was nothing left to release"
    assert twilio.behaviour.count("release_number") == 1, "one number, one release"


async def test_a_shared_agent_is_detached_not_deleted(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Deleting a shared vertical agent would take every other tenant down."""
    owner = await live_owner(api_client, api_app)
    elevenlabs = api_app.state.providers.elevenlabs
    async with api_app.state.session_factory() as session:
        agent = (
            await session.execute(select(Agent).where(Agent.tenant_id == owner.tenant_id))
        ).scalar_one()
        agent.is_shared = True
        await session.commit()
    elevenlabs.behaviour.reset()

    status, body = await _delete(api_client, owner)

    assert status == 200, body
    assert body["complete"] is True
    assert body["shared_agent_detached"] is True
    assert body["agent_deleted"] is False
    assert elevenlabs.behaviour.count("delete_agent") == 0, "the shared agent is untouched"

    _, agent_row, _ = await _state(api_app, owner.tenant_id)
    assert agent_row is not None and agent_row.status is AgentStatus.DELETED


# ---------------------------------------------------------------------------
# Who is allowed to do this
# ---------------------------------------------------------------------------


async def test_another_tenants_owner_cannot_delete_this_receptionist(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    _, stranger = await other_tenant(api_app)

    response = await api_client.post(
        DELETE.format(tenant_id=owner.tenant_id),
        json={"release_number": True},
        headers=stranger,
    )

    # A tenant you cannot see is a 404, never a 403.
    assert response.status_code == 404, response.text
    tenant, agent, number = await _state(api_app, owner.tenant_id)
    assert tenant.status is TenantStatus.ACTIVE
    assert agent is not None and agent.status is AgentStatus.ACTIVE
    assert number is not None and number.status is PhoneNumberStatus.ACTIVE


async def test_an_admin_cannot_delete_the_receptionist(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Admins edit the agent; ending the service is the owner's call."""
    owner = await live_owner(api_client, api_app)
    admin = await add_member(api_app, owner.tenant_id, MembershipRole.ADMIN)

    response = await api_client.post(
        DELETE.format(tenant_id=owner.tenant_id), json={"release_number": False}, headers=admin
    )

    assert response.status_code == 403, response.text
    tenant, _, _ = await _state(api_app, owner.tenant_id)
    assert tenant.status is TenantStatus.ACTIVE


async def test_deleting_needs_a_session(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    response = await api_client.post(
        DELETE.format(tenant_id=owner.tenant_id), json={"release_number": False}
    )
    assert response.status_code == 401


async def test_a_setup_still_running_cannot_be_deleted(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Provisioning could buy a number moments after we cancelled the tenant."""
    owner = await live_owner(api_client, api_app)
    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, owner.tenant_id)
        assert tenant is not None
        tenant.status = TenantStatus.PENDING
        await session.commit()

    status, body = await _delete(api_client, owner, release=True)

    assert status == 409, body
    assert api_app.state.providers.twilio.behaviour.count("release_number") == 0
