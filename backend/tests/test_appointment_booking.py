"""Booking appointments from a live call.

A business is provisioned for real (fake vendors, real worker), connects Google
through the real OAuth flow against a mock Google, and then the voice agent's
two tools are called the way ElevenLabs calls them — with the URL and the
credential taken from the tool the agent was actually given.

The properties under test, in order of how much damage their failure would do:

1. A tool call books only for the business whose credential it carries.
2. Nothing is booked over a busy time, outside opening hours, or twice — not by
   a repeated call, and not by two callers racing for one slot.
3. A failure is reported as "not booked", never as a booking.
4. The agent has the tools exactly while a calendar is connected, and is told
   how to use them.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.integrations.providers.base import CalendarEvent
from app.integrations.providers.google_calendar import GoogleCalendar
from app.main import create_app
from app.models import Agent, Appointment, BusinessProfile, Tenant
from app.models.enums import AgentStatus, AppointmentStatus
from app.providers.models import ToolParameter, WebhookTool
from app.providers.real.voice import ElevenLabsRestProvider
from app.services.calendar_tools import (
    BOOK_APPOINTMENT,
    CHECK_AVAILABILITY,
    TOKEN_HEADER,
    tool_token,
)
from tests.portal_support import COOKIE, Owner, live_owner, other_tenant
from tests.support import build_settings

ENCRYPTION_KEY = Fernet.generate_key().decode()
APP_URL = "http://app.test"
API_URL = "http://api.test"
TIMEZONE = "America/New_York"
ZONE = ZoneInfo(TIMEZONE)

WEEKDAY_HOURS = {
    "timezone": TIMEZONE,
    "days": [
        {"day": day, "opens_at": "09:00", "closes_at": "17:00"}
        for day in ("monday", "tuesday", "wednesday", "thursday", "friday")
    ]
    + [{"day": day, "closed": True} for day in ("saturday", "sunday")],
}


# ---------------------------------------------------------------------------
# A fake Google, with a calendar
# ---------------------------------------------------------------------------


class FakeGoogle:
    def __init__(self) -> None:
        self.busy: list[tuple[datetime, datetime]] = []
        self.events: dict[str, dict[str, Any]] = {}
        self.create_status: int | None = None
        self.creates = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3599,
                    "scope": "https://www.googleapis.com/auth/calendar.events",
                },
            )
        if path == "/v1/userinfo":
            return httpx.Response(200, json={"email": "owner@example.com"})
        if path == "/revoke":
            return httpx.Response(200)
        if path == "/calendar/v3/freeBusy":
            busy = [
                {"start": start.astimezone(UTC).isoformat(), "end": end.astimezone(UTC).isoformat()}
                for start, end in self.busy
            ]
            return httpx.Response(200, json={"calendars": {"primary": {"busy": busy}}})
        if path == "/calendar/v3/calendars/primary/events" and request.method == "POST":
            self.creates += 1
            if self.create_status is not None:
                return httpx.Response(self.create_status, json={"error": "boom"})
            body = json.loads(request.content)
            event_id = body.get("id") or f"evt{len(self.events)}"
            if event_id in self.events:
                return httpx.Response(409, json={"error": "duplicate"})
            self.events[event_id] = body
            return httpx.Response(200, json={**body, "id": event_id})
        if path.startswith("/calendar/v3/calendars/primary/events/") and request.method == "DELETE":
            self.events.pop(path.rsplit("/", 1)[-1], None)
            return httpx.Response(204)
        return httpx.Response(404, json={"error": f"unexpected {request.method} {request.url}"})


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
async def booking_app(migrated_database: str, google: FakeGoogle) -> AsyncIterator[FastAPI]:
    from app.db.session import create_engine
    from tests.worker_fixtures import truncate_all

    settings = build_settings(
        database_url=migrated_database,
        twilio_account_sid="ACtest",
        twilio_auth_token="test-token",
        elevenlabs_webhook_secret="test-webhook-secret",
        integration_encryption_key=ENCRYPTION_KEY,
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret="google-client-secret",
        public_app_url=APP_URL,
        public_api_url=API_URL,
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        application.state.integration_transport = httpx.MockTransport(google.handler)
        try:
            yield application
        finally:
            engine = create_engine(settings)
            try:
                await truncate_all(engine)
            finally:
                await engine.dispose()


@pytest.fixture
async def client(booking_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=booking_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _business(client: AsyncClient, app: FastAPI) -> Owner:
    """A live business in New York, open weekdays nine to five."""
    owner = await live_owner(client, app)
    async with app.state.session_factory() as session:
        tenant = await session.get(Tenant, owner.tenant_id)
        assert tenant is not None
        tenant.timezone = TIMEZONE
        profile = (
            await session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == owner.tenant_id)
            )
        ).scalar_one()
        profile.hours_json = WEEKDAY_HOURS
        await session.commit()
    return owner


async def _connect(client: AsyncClient, owner: Owner) -> None:
    started = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/connect",
        headers=owner.headers,
    )
    assert started.status_code == 200, started.text
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    landed = await client.get(
        "/api/v1/integrations/oauth/google_calendar/callback",
        params={"code": "code", "state": state},
        headers={"Cookie": f"{COOKIE}={owner.token}"},
    )
    assert landed.status_code == 303
    assert "connected=google_calendar" in landed.headers["location"]


async def _agent(app: FastAPI, tenant_id: uuid.UUID) -> Agent:
    async with app.state.session_factory() as session:
        return (
            await session.execute(
                select(Agent).where(
                    Agent.tenant_id == tenant_id, Agent.status == AgentStatus.ACTIVE
                )
            )
        ).scalar_one()


def _vendor_agent(app: FastAPI, agent: Agent) -> Any:
    assert agent.elevenlabs_agent_id is not None
    return app.state.providers.elevenlabs.agents[agent.elevenlabs_agent_id]


async def _tools(app: FastAPI, tenant_id: uuid.UUID) -> dict[str, WebhookTool]:
    vendor = app.state.providers.elevenlabs
    stored = _vendor_agent(app, await _agent(app, tenant_id))
    return {vendor.tools[tool_id].name: vendor.tools[tool_id] for tool_id in stored.tool_ids}


async def _call(
    client: AsyncClient, tool: WebhookTool, body: dict[str, Any], **headers: str
) -> httpx.Response:
    """Call a tool the way the vendor would: its own URL, its own headers."""
    path = urlparse(tool.url).path
    return await client.post(path, json=body, headers=headers or tool.headers)


def _weekday(weekday: int, weeks: int = 1) -> date:
    """A day of the given weekday at least ``weeks`` weeks from today, in New York."""
    day = datetime.now(ZONE).date() + timedelta(days=7 * weeks)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE)


async def _appointments(app: FastAPI, tenant_id: uuid.UUID) -> list[Appointment]:
    async with app.state.session_factory() as session:
        return list(
            (await session.execute(select(Appointment).where(Appointment.tenant_id == tenant_id)))
            .scalars()
            .all()
        )


@pytest.fixture
async def connected(
    client: AsyncClient, booking_app: FastAPI
) -> tuple[Owner, dict[str, WebhookTool]]:
    owner = await _business(client, booking_app)
    await _connect(client, owner)
    return owner, await _tools(booking_app, owner.tenant_id)


# ---------------------------------------------------------------------------
# The agent is given the tools, and told how to use them
# ---------------------------------------------------------------------------


async def test_connecting_a_calendar_gives_the_agent_its_own_booking_tools(
    connected: tuple[Owner, dict[str, WebhookTool]], booking_app: FastAPI
) -> None:
    owner, tools = connected
    assert set(tools) == {CHECK_AVAILABILITY, BOOK_APPOINTMENT}
    settings = booking_app.state.settings
    for tool in tools.values():
        assert tool.url.startswith(f"{API_URL}/api/v1/voice-tools/{owner.tenant_id}/calendar/")
        assert tool.headers == {TOKEN_HEADER: tool_token(settings, owner.tenant_id)}
        # The call's identity comes from the vendor, never from the model.
        conversation = next(p for p in tool.parameters if p.name == "conversation_id")
        assert conversation.dynamic_variable == "system__conversation_id"

    stored = _vendor_agent(booking_app, await _agent(booking_app, owner.tenant_id))
    assert "## Booking appointments" in stored.system_prompt
    agent = await _agent(booking_app, owner.tenant_id)
    assert agent.vendor_tools is not None
    assert set(agent.vendor_tools["ids"]) == {CHECK_AVAILABILITY, BOOK_APPOINTMENT}


async def test_the_credential_is_not_in_the_tools_repr(
    connected: tuple[Owner, dict[str, WebhookTool]],
) -> None:
    _, tools = connected
    tool = tools[BOOK_APPOINTMENT]
    assert tool.headers[TOKEN_HEADER] not in repr(tool)


async def test_disconnecting_takes_the_tools_and_the_instructions_away(
    connected: tuple[Owner, dict[str, WebhookTool]], client: AsyncClient, booking_app: FastAPI
) -> None:
    owner, _ = connected
    response = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/disconnect",
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text

    agent = await _agent(booking_app, owner.tenant_id)
    stored = _vendor_agent(booking_app, agent)
    assert stored.tool_ids == ()
    assert "## Booking appointments" not in stored.system_prompt
    assert booking_app.state.providers.elevenlabs.tools == {}
    assert agent.vendor_tools is None


async def test_an_unchanged_push_does_not_rewrite_the_tools(
    connected: tuple[Owner, dict[str, WebhookTool]], booking_app: FastAPI
) -> None:
    from app.services.config_publishing import resync_tenant_agent

    owner, before = connected
    vendor = booking_app.state.providers.elevenlabs
    writes = vendor.behaviour.count("upsert_webhook_tool")
    async with booking_app.state.session_factory() as session:
        tenant = await session.get(Tenant, owner.tenant_id)
        assert tenant is not None
        assert await resync_tenant_agent(
            session, settings=booking_app.state.settings, voice=vendor, tenant=tenant
        )
        await session.commit()
    assert vendor.behaviour.count("upsert_webhook_tool") == writes
    assert await _tools(booking_app, owner.tenant_id) == before


async def test_without_a_calendar_the_agent_has_no_booking_tools(
    client: AsyncClient, booking_app: FastAPI
) -> None:
    owner = await _business(client, booking_app)
    stored = _vendor_agent(booking_app, await _agent(booking_app, owner.tenant_id))
    assert stored.tool_ids == ()
    assert "## Booking appointments" not in stored.system_prompt


# ---------------------------------------------------------------------------
# Only the right business
# ---------------------------------------------------------------------------


async def test_a_tool_call_needs_this_businesses_own_credential(
    connected: tuple[Owner, dict[str, WebhookTool]], client: AsyncClient, booking_app: FastAPI
) -> None:
    owner, tools = connected
    tool = tools[CHECK_AVAILABILITY]
    body = {"date": _weekday(2).isoformat()}

    assert (await _call(client, tool, body, **{TOKEN_HEADER: "wrong"})).status_code == 401
    assert (await _call(client, tool, body, **{"X-Other": "1"})).status_code == 401

    # Another business's genuine credential opens nothing here.
    other_id, _ = await other_tenant(booking_app)
    stolen = {TOKEN_HEADER: tool_token(booking_app.state.settings, other_id)}
    assert (await _call(client, tool, body, **stolen)).status_code == 401

    # And this one's credential cannot be pointed at the other business.
    response = await client.post(
        f"/api/v1/voice-tools/{other_id}/calendar/book",
        json={"date": body["date"], "time": "10:00", "caller_name": "Mallory"},
        headers=tool.headers,
    )
    assert response.status_code == 401
    assert await _appointments(booking_app, other_id) == []
    assert await _appointments(booking_app, owner.tenant_id) == []


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


async def test_availability_offers_only_free_times_inside_opening_hours(
    connected: tuple[Owner, dict[str, WebhookTool]], client: AsyncClient, google: FakeGoogle
) -> None:
    _, tools = connected
    day = _weekday(2)  # a Wednesday
    google.busy = [(_at(day, 10), _at(day, 11))]

    response = await _call(
        client, tools[CHECK_AVAILABILITY], {"date": day.isoformat(), "time": "10:00"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "available"
    assert body["requested_time_available"] is False
    assert body["timezone"] == TIMEZONE
    offered = [slot["time"] for slot in body["slots"]]
    assert "10:00" not in offered and "10:30" not in offered
    assert offered == sorted(offered)
    assert all("09:00" <= slot <= "16:30" for slot in offered)
    assert {"09:00", "09:30", "11:00", "11:30"} <= set(offered)


async def test_a_free_requested_time_is_confirmed(
    connected: tuple[Owner, dict[str, WebhookTool]], client: AsyncClient
) -> None:
    _, tools = connected
    body = (
        await _call(
            client, tools[CHECK_AVAILABILITY], {"date": _weekday(2).isoformat(), "time": "3pm"}
        )
    ).json()
    assert body["result"] == "available"
    assert body["requested_time_available"] is True


async def test_a_closed_day_points_to_the_next_opening(
    connected: tuple[Owner, dict[str, WebhookTool]], client: AsyncClient
) -> None:
    _, tools = connected
    saturday = _weekday(5)
    body = (await _call(client, tools[CHECK_AVAILABILITY], {"date": saturday.isoformat()})).json()
    assert body["result"] == "closed"
    assert body["next_available"]
    assert body["next_available"][0]["date"] == (saturday + timedelta(days=2)).isoformat()


@pytest.mark.parametrize(
    ("body", "result"),
    [
        ({"date": "next tuesday"}, "invalid_request"),
        ({"date": "2001-01-01"}, "invalid_request"),
        ({"date": "2999-01-01"}, "too_far"),
        ({}, "invalid_request"),
        ({"date": ""}, "invalid_request"),
    ],
)
async def test_unusable_dates_are_explained(
    connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    body: dict[str, Any],
    result: str,
) -> None:
    _, tools = connected
    response = await _call(client, tools[CHECK_AVAILABILITY], body)
    assert response.status_code == 200
    assert response.json()["result"] == result
    assert response.json()["message"]


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


def _booking(day: date, at: str = "14:00", **overrides: Any) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "time": at,
        "caller_name": "Jane Caller",
        "reason": "Haircut",
        "caller_id": "+15555550123",
        "conversation_id": "conv_1",
        **overrides,
    }


async def test_booking_creates_the_event_and_records_it(
    connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    google: FakeGoogle,
) -> None:
    owner, tools = connected
    day = _weekday(2)
    body = (await _call(client, tools[BOOK_APPOINTMENT], _booking(day))).json()

    assert body["result"] == "booked", body
    assert body["appointment"]["date"] == day.isoformat()
    assert body["appointment"]["time"] == "14:00"
    assert body["appointment"]["spoken"].endswith("at 2:00 PM")

    [event] = google.events.values()
    assert event["start"]["dateTime"] == _at(day, 14).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert event["end"]["dateTime"] == _at(day, 14, 30).astimezone(UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert "Jane Caller" in event["summary"]
    assert "+15555550123" in event["description"]

    [appointment] = await _appointments(booking_app, owner.tenant_id)
    assert appointment.status is AppointmentStatus.BOOKED
    assert appointment.external_event_id == event["id"]
    assert appointment.starts_at == _at(day, 14)
    assert appointment.caller_phone == "+15555550123"
    assert appointment.conversation_id == "conv_1"


async def test_booking_the_same_slot_twice_in_one_call_is_one_booking(
    connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    google: FakeGoogle,
) -> None:
    owner, tools = connected
    day = _weekday(2)
    first = (await _call(client, tools[BOOK_APPOINTMENT], _booking(day))).json()
    # The model re-spells the name; it is still the same booking.
    again = (
        await _call(client, tools[BOOK_APPOINTMENT], _booking(day, caller_name="Jayne Caller"))
    ).json()

    assert first["result"] == "booked"
    assert again["result"] == "already_booked"
    assert again["appointment"]["time"] == "14:00"
    assert len(google.events) == 1
    assert len(await _appointments(booking_app, owner.tenant_id)) == 1


async def test_a_busy_slot_is_refused_with_alternatives(
    connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    google: FakeGoogle,
) -> None:
    owner, tools = connected
    day = _weekday(2)
    google.busy = [(_at(day, 13, 45), _at(day, 14, 15))]
    body = (await _call(client, tools[BOOK_APPOINTMENT], _booking(day))).json()

    assert body["result"] == "slot_taken"
    assert body["alternatives"]
    assert all(slot["date"] == day.isoformat() for slot in body["alternatives"])
    assert google.creates == 0
    assert await _appointments(booking_app, owner.tenant_id) == []


async def test_two_callers_racing_for_one_slot_cannot_both_get_it(
    connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    google: FakeGoogle,
) -> None:
    """The fake calendar never shows the first booking as busy, so only our own
    lock and our own table can stop the second."""
    owner, tools = connected
    day = _weekday(2)
    results = await asyncio.gather(
        _call(client, tools[BOOK_APPOINTMENT], _booking(day, conversation_id="conv_a")),
        _call(
            client,
            tools[BOOK_APPOINTMENT],
            _booking(day, conversation_id="conv_b", caller_name="Other Caller"),
        ),
    )
    outcomes = sorted(response.json()["result"] for response in results)
    assert outcomes == ["booked", "slot_taken"]
    assert len(google.events) == 1
    assert len(await _appointments(booking_app, owner.tenant_id)) == 1


@pytest.mark.parametrize(
    ("overrides", "result"),
    [
        ({"caller_name": ""}, "invalid_request"),
        ({"time": "half past never"}, "invalid_request"),
        ({"time": "08:00"}, "outside_hours"),
        ({"time": "16:45"}, "outside_hours"),
    ],
)
async def test_bookings_that_break_the_rules_are_refused(
    connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    google: FakeGoogle,
    overrides: dict[str, Any],
    result: str,
) -> None:
    owner, tools = connected
    body = (await _call(client, tools[BOOK_APPOINTMENT], _booking(_weekday(2), **overrides))).json()
    assert body["result"] == result, body
    assert body["message"]
    assert google.creates == 0
    assert await _appointments(booking_app, owner.tenant_id) == []


async def test_a_missing_name_says_what_to_ask(
    connected: tuple[Owner, dict[str, WebhookTool]], client: AsyncClient
) -> None:
    _, tools = connected
    request = _booking(_weekday(2))
    del request["caller_name"]
    body = (await _call(client, tools[BOOK_APPOINTMENT], request)).json()
    assert body["result"] == "invalid_request"
    assert body["fields"] == ["caller_name"]
    assert "name" in body["message"]


async def test_a_closed_day_and_a_too_soon_time_are_refused(
    connected: tuple[Owner, dict[str, WebhookTool]], client: AsyncClient, google: FakeGoogle
) -> None:
    _, tools = connected
    saturday = (await _call(client, tools[BOOK_APPOINTMENT], _booking(_weekday(5)))).json()
    assert saturday["result"] == "closed"

    today = datetime.now(ZONE).date()
    past = (await _call(client, tools[BOOK_APPOINTMENT], _booking(today, "00:00"))).json()
    assert past["result"] in {"too_soon", "closed", "outside_hours"}
    assert google.creates == 0


async def test_a_calendar_failure_is_reported_as_not_booked(
    connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    google: FakeGoogle,
) -> None:
    owner, tools = connected
    google.create_status = 500
    body = (await _call(client, tools[BOOK_APPOINTMENT], _booking(_weekday(2)))).json()
    assert body["result"] == "calendar_unavailable"
    assert "Don't tell the caller they are booked" in body["message"]
    assert await _appointments(booking_app, owner.tenant_id) == []


async def test_after_disconnecting_a_stale_tool_cannot_book(
    connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    google: FakeGoogle,
) -> None:
    """A copy of the tool that outlived its agent still authenticates, and still
    books nothing without a connected calendar."""
    owner, tools = connected
    await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/disconnect",
        headers=owner.headers,
    )
    body = (await _call(client, tools[BOOK_APPOINTMENT], _booking(_weekday(2)))).json()
    assert body["result"] == "calendar_unavailable"
    assert google.creates == 0


# ---------------------------------------------------------------------------
# What reaches the vendors
# ---------------------------------------------------------------------------


def test_the_elevenlabs_tool_payload_keeps_vendor_fields_out_of_the_models_hands() -> None:
    tool = WebhookTool(
        name="book_appointment",
        description="Book it.",
        url="https://api.example.com/x",
        headers={TOKEN_HEADER: "secret"},
        parameters=(
            ToolParameter(name="date", description="The day.", required=True),
            ToolParameter(name="conversation_id", dynamic_variable="system__conversation_id"),
        ),
    )
    config = ElevenLabsRestProvider._tool_config(tool)
    schema = config["api_schema"]
    assert config["type"] == "webhook"
    assert schema["method"] == "POST"
    assert schema["request_headers"] == {TOKEN_HEADER: "secret"}
    body = schema["request_body_schema"]
    assert body["required"] == ["date"]
    assert body["properties"]["date"] == {"type": "string", "description": "The day."}
    assert body["properties"]["conversation_id"] == {
        "type": "string",
        "dynamic_variable": "system__conversation_id",
    }


async def test_google_treats_a_duplicate_event_id_as_the_earlier_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["id"] == "abc123def"
        return httpx.Response(409, json={"error": {"code": 409}})

    adapter = GoogleCalendar(build_settings(), httpx.MockTransport(handler))
    event = CalendarEvent(
        title="t",
        start=datetime(2030, 1, 1, 10, tzinfo=UTC),
        end=datetime(2030, 1, 1, 11, tzinfo=UTC),
        idempotency_key="abc123def",
    )
    assert await adapter.create_event(access_token="token", event=event) == "abc123def"
