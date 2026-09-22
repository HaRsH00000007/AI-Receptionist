"""The signed-in portal: Home numbers, calls, contacts, and editing the agent.

Every read is checked two ways — that it returns the tenant's own data, and that
another tenant's session cannot reach it. The second half is the one that
matters: a query missing its tenant filter passes the first half happily.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import AgentConfig, AuditLog, BusinessProfile, Call
from app.models.enums import AuditAction, CallStatus, MembershipRole
from app.providers.fakes.voice import FakeElevenLabsProvider
from tests.portal_support import add_member, live_owner, other_tenant, sign_up
from tests.support import grant

TURNS = [
    {"role": "agent", "message": "Thanks for calling Sunset Salon!", "time_in_call_secs": 0},
    {"role": "user", "message": "Hi, I'd like a haircut on Friday.", "time_in_call_secs": 3.5},
    {"role": "user", "message": "   "},
    "not a turn at all",
    {"role": "agent", "message": "Friday works. Can I take your name?", "tool_calls": ["x"]},
]


async def _add_calls(
    api_app: FastAPI, tenant_id: uuid.UUID, calls: list[dict[str, Any]]
) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    async with api_app.state.session_factory() as session:
        for index, values in enumerate(calls):
            call = Call(
                tenant_id=tenant_id,
                provider_call_id=f"conv-{uuid.uuid4().hex}",
                from_e164=values.get("from_e164", "+15551110000"),
                to_e164="+18055550100",
                started_at=values.get("started_at", datetime.now(UTC) - timedelta(hours=index)),
                duration_s=values.get("duration_s"),
                caller_name=values.get("caller_name"),
                callback_number=values.get("callback_number"),
                summary=values.get("summary"),
                intent=values.get("intent"),
                urgency=values.get("urgency"),
                status=values.get("status", CallStatus.SUMMARIZED),
                transcript_json=values.get("transcript_json"),
                agent_config_version=values.get("agent_config_version"),
            )
            session.add(call)
            ids.append(call.id)
        await session.commit()
    return ids


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


async def test_the_summary_counts_only_what_is_recorded(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await sign_up(api_client)
    await _add_calls(
        api_app,
        owner.tenant_id,
        [
            {"from_e164": "+15551110001", "duration_s": 60, "urgency": 5, "callback_number": "+1"},
            {"from_e164": "+15551110001", "duration_s": 120},
            {"from_e164": "+15551110002", "duration_s": None},
            # Outside a 30-day window: not answered-this-period, still a contact.
            {"from_e164": "+15551110003", "started_at": datetime.now(UTC) - timedelta(days=45)},
        ],
    )
    other_id, _ = await other_tenant(api_app)
    await _add_calls(api_app, other_id, [{"from_e164": "+15559990000", "duration_s": 999}])

    body = (
        await api_client.get(f"/api/v1/tenants/{owner.tenant_id}/dashboard", headers=owner.headers)
    ).json()

    assert body["window_days"] == 30
    assert body["answered_calls"] == 3
    assert body["average_duration_s"] == 90.0  # the unknown duration is not a zero
    assert body["urgent_calls"] == 1
    assert body["callbacks_requested"] == 1
    assert body["unique_callers"] == 2
    assert body["total_contacts"] == 3
    # Nothing is reported that the pipeline does not measure.
    assert "missed_calls" not in body and "spam_calls" not in body


async def test_an_empty_account_reports_zeros_and_no_average(api_client: AsyncClient) -> None:
    owner = await sign_up(api_client)
    body = (
        await api_client.get(f"/api/v1/tenants/{owner.tenant_id}/dashboard", headers=owner.headers)
    ).json()
    assert body["answered_calls"] == 0
    assert body["average_duration_s"] is None


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------


async def test_a_call_detail_carries_a_clean_transcript(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await sign_up(api_client)
    [call_id] = await _add_calls(
        api_app,
        owner.tenant_id,
        [
            {
                "transcript_json": {"text": "…", "turns": TURNS},
                "agent_config_version": 3,
                "summary": "Wants a Friday haircut.",
            }
        ],
    )

    response = await api_client.get(
        f"/api/v1/tenants/{owner.tenant_id}/calls/{call_id}", headers=owner.headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent_config_version"] == 3
    assert body["recording_available"] is False
    assert body["to_e164"] == "+18055550100"
    # Blank and malformed turns are dropped; vendor extras never come through.
    assert body["transcript"] == [
        {"role": "agent", "message": "Thanks for calling Sunset Salon!", "time_in_call_s": 0.0},
        {"role": "user", "message": "Hi, I'd like a haircut on Friday.", "time_in_call_s": 3.5},
        {"role": "agent", "message": "Friday works. Can I take your name?", "time_in_call_s": None},
    ]


async def test_another_tenants_call_is_not_found(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await sign_up(api_client)
    other_id, _ = await other_tenant(api_app)
    [their_call] = await _add_calls(api_app, other_id, [{"summary": "private"}])

    # Their call id, under our tenant: indistinguishable from a missing call.
    response = await api_client.get(
        f"/api/v1/tenants/{owner.tenant_id}/calls/{their_call}", headers=owner.headers
    )
    assert response.status_code == 404
    # And their tenant under our session: the tenant itself is hidden.
    response = await api_client.get(
        f"/api/v1/tenants/{other_id}/calls/{their_call}", headers=owner.headers
    )
    assert response.status_code == 404


async def test_the_status_link_cannot_read_a_transcript(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await sign_up(api_client)
    [call_id] = await _add_calls(api_app, owner.tenant_id, [{"transcript_json": {"turns": TURNS}}])
    response = await api_client.get(
        f"/api/v1/tenants/{owner.tenant_id}/calls/{call_id}",
        params=grant(api_app, owner.tenant_id),
    )
    assert response.status_code == 401


async def test_calls_can_be_filtered_to_one_caller(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await sign_up(api_client)
    await _add_calls(
        api_app,
        owner.tenant_id,
        [
            {"from_e164": "+15551110001"},
            {"from_e164": "+15551110002"},
            {"from_e164": "+15551110001"},
        ],
    )
    response = await api_client.get(
        f"/api/v1/tenants/{owner.tenant_id}/calls",
        params={"caller": "+15551110001"},
        headers=owner.headers,
    )
    assert response.status_code == 200
    assert {call["from_e164"] for call in response.json()} == {"+15551110001"}
    assert len(response.json()) == 2


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


async def test_contacts_are_derived_from_calls(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await sign_up(api_client)
    now = datetime.now(UTC)
    await _add_calls(
        api_app,
        owner.tenant_id,
        [
            # Dana: gave a name on the first call, not the second.
            {
                "from_e164": "+15551110001",
                "caller_name": "Dana",
                "started_at": now - timedelta(days=3),
                "summary": "First visit",
                "urgency": 4,
            },
            {
                "from_e164": "+15551110001",
                "started_at": now - timedelta(hours=1),
                "summary": "Rebooking",
                "intent": "booking",
            },
            {"from_e164": "+15551110002", "started_at": now - timedelta(days=1)},
            # No caller id: not a contact.
            {"from_e164": None, "started_at": now},
        ],
    )
    other_id, _ = await other_tenant(api_app)
    await _add_calls(api_app, other_id, [{"from_e164": "+15559990000"}])

    response = await api_client.get(
        f"/api/v1/tenants/{owner.tenant_id}/contacts", headers=owner.headers
    )
    assert response.status_code == 200
    contacts = response.json()
    assert [contact["phone_e164"] for contact in contacts] == ["+15551110001", "+15551110002"]
    dana = contacts[0]
    assert dana["name"] == "Dana"  # the latest name given, not the latest call's
    assert dana["call_count"] == 2
    assert dana["last_summary"] == "Rebooking"
    assert dana["last_intent"] == "booking"
    assert dana["has_urgent"] is True
    assert contacts[1]["name"] is None
    assert contacts[1]["has_urgent"] is False


async def test_contacts_are_membership_only(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await sign_up(api_client)
    _, stranger = await other_tenant(api_app)
    response = await api_client.get(f"/api/v1/tenants/{owner.tenant_id}/contacts", headers=stranger)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

EDIT: dict[str, Any] = {
    "services": "cuts, color, blowouts",
    "operating_hours": "Tue-Sat 10-7",
    "greeting_style": "professional",
    "custom_greeting": "  Sunset Salon,\nthis is the front desk.  ",
    "escalation_rules": "Call me for anything about a wedding party.",
}


async def test_saving_the_agent_publishes_a_new_version_and_syncs_it(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)

    response = await api_client.put(
        f"/api/v1/tenants/{owner.tenant_id}/agent/settings", json=EDIT, headers=owner.headers
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["previous_version"] == 1
    assert result["config_version"] == 2
    assert result["synced"] is True

    async with api_app.state.session_factory() as session:
        profile = (
            await session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == owner.tenant_id)
            )
        ).scalar_one()
        configs = (
            (
                await session.execute(
                    select(AgentConfig)
                    .where(AgentConfig.tenant_id == owner.tenant_id)
                    .order_by(AgentConfig.version)
                )
            )
            .scalars()
            .all()
        )
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == owner.tenant_id,
                    AuditLog.action == AuditAction.PROFILE_UPDATED,
                )
            )
        ).scalar_one()

    assert profile.services == ["cuts", "color", "blowouts"]
    assert profile.hours_raw == "Tue-Sat 10-7"
    # Normalized exactly as at signup: one speakable line.
    assert profile.greeting_custom == "Sunset Salon, this is the front desk."
    # Append-only: v1 still exists, unchanged, and is no longer live.
    assert [(config.version, config.is_live) for config in configs] == [(1, False), (2, True)]
    assert configs[1].first_message == "Sunset Salon, this is the front desk."
    # The audit row names the fields and the person, never the values.
    assert set(audit.meta_json["fields"]) >= {"services", "operating_hours", "custom_greeting"}
    assert audit.actor_user_id is not None

    # The vendor's copy was overwritten with the new version.
    voice = api_app.state.providers.elevenlabs
    assert isinstance(voice, FakeElevenLabsProvider)
    assert any(
        agent.first_message == "Sunset Salon, this is the front desk."
        for agent in voice.agents.values()
    )


async def test_saving_without_changes_publishes_nothing(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    first = await api_client.put(
        f"/api/v1/tenants/{owner.tenant_id}/agent/settings", json=EDIT, headers=owner.headers
    )
    again = await api_client.put(
        f"/api/v1/tenants/{owner.tenant_id}/agent/settings", json=EDIT, headers=owner.headers
    )
    assert again.status_code == 200
    assert again.json()["config_version"] == first.json()["config_version"]

    versions = (
        await api_client.get(
            f"/api/v1/tenants/{owner.tenant_id}/agent/versions", headers=owner.headers
        )
    ).json()
    assert [entry["version"] for entry in versions] == [2, 1]
    assert versions[0]["is_live"] is True
    # History never carries the prompt.
    assert "system_prompt" not in versions[0]


async def test_the_agent_cannot_be_edited_while_setup_is_running(
    api_client: AsyncClient,
) -> None:
    owner = await sign_up(api_client)  # not driven to active
    response = await api_client.put(
        f"/api/v1/tenants/{owner.tenant_id}/agent/settings", json=EDIT, headers=owner.headers
    )
    assert response.status_code == 409


async def test_a_plain_member_cannot_edit_the_agent(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    member = await add_member(api_app, owner.tenant_id, MembershipRole.MEMBER)
    response = await api_client.put(
        f"/api/v1/tenants/{owner.tenant_id}/agent/settings", json=EDIT, headers=member
    )
    assert response.status_code == 403
    # Reading is fine.
    read = await api_client.get(f"/api/v1/tenants/{owner.tenant_id}/dashboard", headers=member)
    assert read.status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"services": " , ; "},
        {"operating_hours": ""},
        {"custom_greeting": "x" * 301},
        {"greeting_style": "sarcastic"},
    ],
)
async def test_invalid_agent_settings_are_refused(
    api_client: AsyncClient, api_app: FastAPI, change: dict[str, Any]
) -> None:
    owner = await sign_up(api_client)
    response = await api_client.put(
        f"/api/v1/tenants/{owner.tenant_id}/agent/settings",
        json={**EDIT, **change},
        headers=owner.headers,
    )
    assert response.status_code == 422
