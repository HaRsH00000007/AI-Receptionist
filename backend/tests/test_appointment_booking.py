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
4. The agent has the tools exactly while a business has *chosen* a calendar to
   book into, and is told how to use them.

Everything here books into a **secondary** calendar, not the account's default,
because booking into the wrong calendar is silent: the caller is told a time is
free, the owner never sees the appointment, and nothing errors. So the fake
records which calendar every query and every create named, and the tests assert
it.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.providers.base import CalendarEvent
from app.integrations.providers.google_calendar import GoogleCalendar
from app.integrations.service import IntegrationService
from app.main import create_app
from app.models import Agent, Appointment, BusinessProfile, Tenant
from app.models.enums import AgentStatus, AppointmentStatus, IntegrationProvider
from app.providers.models import ToolParameter, WebhookTool
from app.providers.real.voice import ElevenLabsRestProvider
from app.schemas.business import BusinessHours
from app.services.appointment_booking import AppointmentBooking, _local, _Window, _window
from app.services.calendar_tools import (
    BOOK_APPOINTMENT,
    CHECK_AVAILABILITY,
    TOKEN_HEADER,
    booking_supported,
    tool_token,
)
from tests.portal_support import COOKIE, Owner, add_member, live_owner, other_tenant
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


#: The account's default calendar, and the separate one a business would
#: realistically keep its appointments in.
DEFAULT_CALENDAR = "owner@example.com"
BOOKINGS_CALENDAR = "bookings@group.calendar.google.com"
SHARED_CALENDAR = "roster@group.calendar.google.com"


class FakeGoogle:
    def __init__(self) -> None:
        self.busy: list[tuple[datetime, datetime]] = []
        self.events: dict[str, dict[str, Any]] = {}
        self.create_status: int | None = None
        self.creates = 0
        #: 403 when the grant predates the calendar-list scope.
        self.list_status: int | None = None
        #: Which calendar each call named, so selection can be asserted rather
        #: than assumed from a passing booking.
        self.freebusy_for: list[str] = []
        self.created_in: list[str] = []
        self.deleted_from: list[tuple[str, str]] = []
        self.calendars: list[dict[str, Any]] = [
            {
                "id": DEFAULT_CALENDAR,
                "summary": "Owner",
                "primary": True,
                "accessRole": "owner",
            },
            {"id": BOOKINGS_CALENDAR, "summary": "Bookings", "accessRole": "writer"},
            # Shared read-only: listed, never a valid choice.
            {"id": SHARED_CALENDAR, "summary": "Shared roster", "accessRole": "reader"},
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
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
        if path == "/calendar/v3/users/me/calendarList":
            if self.list_status is not None:
                return httpx.Response(self.list_status, json={"error": "insufficient scope"})
            return httpx.Response(200, json={"items": self.calendars})
        if path == "/calendar/v3/freeBusy":
            asked = json.loads(request.content)["items"][0]["id"]
            self.freebusy_for.append(asked)
            busy = [
                {"start": start.astimezone(UTC).isoformat(), "end": end.astimezone(UTC).isoformat()}
                for start, end in self.busy
            ]
            return httpx.Response(200, json={"calendars": {asked: {"busy": busy}}})
        if path.startswith("/calendar/v3/calendars/") and "/events" in path:
            calendar_id, _, tail = path[len("/calendar/v3/calendars/") :].partition("/events")
            if request.method == "POST" and not tail:
                self.creates += 1
                self.created_in.append(calendar_id)
                if self.create_status is not None:
                    return httpx.Response(self.create_status, json={"error": "boom"})
                body = json.loads(request.content)
                event_id = body.get("id") or f"evt{len(self.events)}"
                if event_id in self.events:
                    return httpx.Response(409, json={"error": "duplicate"})
                self.events[event_id] = body
                return httpx.Response(200, json={**body, "id": event_id})
            if request.method == "DELETE" and tail.startswith("/"):
                event_id = tail.lstrip("/")
                self.deleted_from.append((calendar_id, event_id))
                self.events.pop(event_id, None)
                return httpx.Response(204)
        return httpx.Response(404, json={"error": f"unexpected {request.method} {request.url}"})


class FakeOutlook:
    """Microsoft Graph, enough of it to connect a calendar and book into one."""

    #: Graph calendar ids are long base64url strings; the padding is what makes
    #: percent-encoding the path matter.
    DEFAULT = "AAMkAGI2TAAA-default="
    BOOKINGS = "AAMkAGI2TAAA-bookings=="

    def __init__(self) -> None:
        self.busy: list[tuple[datetime, datetime]] = []
        self.events: dict[str, dict[str, Any]] = {}
        self.create_status: int | None = None
        self.creates = 0
        self.view_for: list[str] = []
        self.created_in: list[str] = []
        self.deleted: list[str] = []
        #: Graph's idempotency: the same transactionId returns the first event.
        self.by_transaction: dict[str, str] = {}
        self.calendars: list[dict[str, Any]] = [
            {"id": self.DEFAULT, "name": "Calendar", "isDefaultCalendar": True, "canEdit": True},
            {"id": self.BOOKINGS, "name": "Bookings", "isDefaultCalendar": False, "canEdit": True},
            {"id": "AAMkAGI2TAAA-shared", "name": "Team (read only)", "canEdit": False},
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        if path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "graph-access",
                    "refresh_token": "graph-refresh",
                    "expires_in": 3599,
                    "scope": "offline_access Calendars.ReadWrite",
                },
            )
        if path == "/v1.0/me":
            return httpx.Response(200, json={"userPrincipalName": "owner@outlook.test"})
        if path == "/v1.0/me/calendars":
            return httpx.Response(200, json={"value": self.calendars})
        if path.startswith("/v1.0/me/calendars/") and path.endswith("/calendarView"):
            calendar_id = path[len("/v1.0/me/calendars/") : -len("/calendarView")]
            self.view_for.append(calendar_id)
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "showAs": "busy",
                            "isCancelled": False,
                            "start": {"dateTime": _graph_stamp(start), "timeZone": "UTC"},
                            "end": {"dateTime": _graph_stamp(end), "timeZone": "UTC"},
                        }
                        for start, end in self.busy
                    ]
                },
            )
        if path.startswith("/v1.0/me/calendars/") and "/events" in path:
            calendar_id, _, tail = path[len("/v1.0/me/calendars/") :].partition("/events")
            if request.method == "POST" and not tail:
                self.creates += 1
                self.created_in.append(calendar_id)
                if self.create_status is not None:
                    return httpx.Response(self.create_status, json={"error": "boom"})
                body = json.loads(request.content)
                transaction = body.get("transactionId")
                if transaction and transaction in self.by_transaction:
                    first = self.by_transaction[transaction]
                    return httpx.Response(201, json={**self.events[first], "id": first})
                event_id = f"evt{len(self.events)}"
                self.events[event_id] = body
                if transaction:
                    self.by_transaction[transaction] = event_id
                return httpx.Response(201, json={**body, "id": event_id})
            if request.method == "DELETE" and tail.startswith("/"):
                self.deleted.append(tail.lstrip("/"))
                self.events.pop(tail.lstrip("/"), None)
                return httpx.Response(204)
        return httpx.Response(404, json={"error": f"unexpected {request.method} {request.url}"})


def _graph_stamp(moment: datetime) -> str:
    """Graph's own shape: no offset, seven fractional digits."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.0000000")


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def outlook() -> FakeOutlook:
    return FakeOutlook()


@pytest.fixture
async def booking_app(
    migrated_database: str, google: FakeGoogle, outlook: FakeOutlook
) -> AsyncIterator[FastAPI]:
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
        microsoft_oauth_client_id="microsoft-client-id",
        microsoft_oauth_client_secret="microsoft-client-secret",
        public_app_url=APP_URL,
        public_api_url=API_URL,
        # Off by default, so every test that expects booking says so.
        calendar_booking_enabled=True,
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):

        def route(request: httpx.Request) -> httpx.Response:
            microsoft = request.url.host in {"graph.microsoft.com", "login.microsoftonline.com"}
            return (outlook if microsoft else google).handler(request)

        application.state.integration_transport = httpx.MockTransport(route)
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


async def _connect(client: AsyncClient, owner: Owner, provider: str = "google_calendar") -> None:
    started = await client.post(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/{provider}/connect",
        headers=owner.headers,
    )
    assert started.status_code == 200, started.text
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    landed = await client.get(
        f"/api/v1/integrations/oauth/{provider}/callback",
        params={"code": "code", "state": state},
        headers={"Cookie": f"{COOKIE}={owner.token}"},
    )
    assert landed.status_code == 303
    assert f"connected={provider}" in landed.headers["location"]


async def _choose(
    client: AsyncClient,
    owner: Owner,
    calendar_id: str,
    provider: str = "google_calendar",
) -> httpx.Response:
    """Pick the calendar to book into, the way the dashboard does."""
    return await client.put(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/{provider}/calendar",
        json={"calendar_id": calendar_id},
        headers=owner.headers,
    )


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
    """A business that has connected Google *and* chosen a calendar.

    The chosen one is the secondary "Bookings" calendar, so every test that
    follows proves the business's own choice is used rather than the default.
    """
    owner = await _business(client, booking_app)
    await _connect(client, owner)
    chosen = await _choose(client, owner, BOOKINGS_CALENDAR)
    assert chosen.status_code == 200, chosen.text
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


# ---------------------------------------------------------------------------
# Which calendar, and whether booking is on at all
# ---------------------------------------------------------------------------


async def test_connecting_a_calendar_does_not_by_itself_arm_booking(
    client: AsyncClient, booking_app: FastAPI
) -> None:
    """Connecting means "see when I am busy". Writing to a calendar is a
    second, separate decision, and an upgrade must not make it on the owner's
    behalf — least of all on the next agent push after they edit a greeting."""
    owner = await _business(client, booking_app)
    await _connect(client, owner)

    assert await _tools(booking_app, owner.tenant_id) == {}
    stored = _vendor_agent(booking_app, await _agent(booking_app, owner.tenant_id))
    assert not stored.tool_ids
    assert "book_appointment" not in stored.system_prompt


async def test_choosing_a_calendar_is_what_gives_the_agent_its_tools(
    client: AsyncClient, booking_app: FastAPI
) -> None:
    owner = await _business(client, booking_app)
    await _connect(client, owner)
    chosen = await _choose(client, owner, BOOKINGS_CALENDAR)

    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["calendar_name"] == "Bookings"
    assert chosen.json()["bookable"] is True
    assert set(await _tools(booking_app, owner.tenant_id)) == {
        CHECK_AVAILABILITY,
        BOOK_APPOINTMENT,
    }


async def test_availability_and_booking_use_the_chosen_calendar(
    connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    google: FakeGoogle,
) -> None:
    """The bug this guards: booking into the account's default calendar instead
    of the one the business keeps its appointments in. Nothing errors — the
    caller is told a time is free and the owner never sees the appointment."""
    _, tools = connected
    day = _weekday(2)
    await _call(client, tools[CHECK_AVAILABILITY], {"date": day.isoformat()})
    booked = (await _call(client, tools[BOOK_APPOINTMENT], _booking(day))).json()

    assert booked["result"] == "booked", booked
    assert google.freebusy_for, "no free/busy query was made"
    assert set(google.freebusy_for) == {BOOKINGS_CALENDAR}
    assert google.created_in == [BOOKINGS_CALENDAR]
    assert DEFAULT_CALENDAR not in google.freebusy_for + google.created_in


async def test_a_calendar_outside_the_account_is_refused(
    client: AsyncClient, booking_app: FastAPI
) -> None:
    """The id is checked against the account's own list, not trusted. Storing
    one that is not there would fail on every booking instead of on the save."""
    owner = await _business(client, booking_app)
    await _connect(client, owner)

    refused = await _choose(client, owner, "someone-elses@group.calendar.google.com")

    assert refused.status_code == 404
    assert await _tools(booking_app, owner.tenant_id) == {}


async def test_a_read_only_calendar_cannot_be_chosen(
    client: AsyncClient, booking_app: FastAPI
) -> None:
    """It can be read for availability but never written to, so offering it as
    a booking target would promise appointments that cannot be made."""
    owner = await _business(client, booking_app)
    await _connect(client, owner)

    refused = await _choose(client, owner, SHARED_CALENDAR)

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "calendar_not_writable"
    assert await _tools(booking_app, owner.tenant_id) == {}


async def test_a_grant_without_the_list_scope_asks_for_a_reconnect(
    client: AsyncClient, booking_app: FastAPI, google: FakeGoogle
) -> None:
    """Connections made before the calendar-list scope was asked for cannot
    enumerate calendars, and only the owner can fix that."""
    owner = await _business(client, booking_app)
    await _connect(client, owner)
    google.list_status = 403

    listed = await client.get(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/calendars",
        headers=owner.headers,
    )

    assert listed.status_code == 409
    assert listed.json()["error"]["code"] == "integration_scope_missing"


async def test_the_calendar_list_names_the_default_first(
    client: AsyncClient, booking_app: FastAPI
) -> None:
    owner = await _business(client, booking_app)
    await _connect(client, owner)

    listed = await client.get(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/calendars",
        headers=owner.headers,
    )

    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["selected_id"] is None
    assert next(entry["id"] for entry in body["calendars"]) == DEFAULT_CALENDAR
    assert body["calendars"][0]["primary"] is True
    assert {entry["id"]: entry["writable"] for entry in body["calendars"]}[SHARED_CALENDAR] is False


async def test_only_an_admin_can_choose_the_calendar(
    client: AsyncClient, booking_app: FastAPI
) -> None:
    owner = await _business(client, booking_app)
    await _connect(client, owner)
    member = await add_member(booking_app, owner.tenant_id)

    refused = await client.put(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/calendar",
        json={"calendar_id": BOOKINGS_CALENDAR},
        headers=member,
    )

    assert refused.status_code == 403
    assert await _tools(booking_app, owner.tenant_id) == {}


async def test_another_business_cannot_choose_this_ones_calendar(
    client: AsyncClient, booking_app: FastAPI
) -> None:
    owner = await _business(client, booking_app)
    await _connect(client, owner)
    _, intruder = await other_tenant(booking_app)

    refused = await client.put(
        f"/api/v1/tenants/{owner.tenant_id}/integrations/google_calendar/calendar",
        json={"calendar_id": BOOKINGS_CALENDAR},
        headers=intruder,
    )

    assert refused.status_code in {403, 404}
    assert await _tools(booking_app, owner.tenant_id) == {}


def test_booking_is_off_until_a_deployment_turns_it_on() -> None:
    """The default that decides whether an upgrade changes what every agent does."""
    assert build_settings().calendar_booking_enabled is False


def test_a_deployment_with_booking_off_carries_no_tools() -> None:
    agent = Agent(tenant_id=uuid.uuid4(), status=AgentStatus.ACTIVE)
    settings = build_settings(
        calendar_booking_enabled=False, public_api_url=API_URL, auth_secret_key="x" * 32
    )

    assert booking_supported(settings, agent) is False
    assert booking_supported(settings.model_copy(update={"calendar_booking_enabled": True}), agent)


# ---------------------------------------------------------------------------
# When the clocks change
#
# The slot arithmetic is pure, so these assert it directly against fixed dates
# rather than through a booking — a transition that is inside the booking
# horizon today is outside it next month, and a test that silently stops
# exercising its subject is worse than no test.
# ---------------------------------------------------------------------------

#: America/New_York, 2027: 02:00 becomes 03:00, so 02:00 to 02:59 never happens.
SPRING_FORWARD = date(2027, 3, 14)
#: And 2026: 02:00 becomes 01:00, so 01:00 to 01:59 happens twice.
FALL_BACK = date(2026, 11, 1)


def _booking_engine(**overrides: Any) -> AppointmentBooking:
    """The service with no database behind it. Enough for the slot arithmetic,
    which is the only part of booking that DST can get wrong."""
    return AppointmentBooking(
        cast(AsyncSession, None), build_settings(**overrides), cast(IntegrationService, None)
    )


def _all_day(day: date) -> _Window:
    """Local midnight to local midnight — 23, 24 or 25 hours depending on the day."""
    return _Window(
        opens=datetime.combine(day, time(0, 0), tzinfo=ZONE),
        closes=datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=ZONE),
    )


def test_a_time_the_clocks_skip_does_not_exist() -> None:
    """02:30 on the spring-forward day is not a time. Booking it would store an
    instant an hour from the one everybody said out loud."""
    assert _local(SPRING_FORWARD, time(2, 30), ZONE) is None
    assert _local(SPRING_FORWARD, time(1, 30), ZONE) is not None
    assert _local(SPRING_FORWARD, time(3, 30), ZONE) is not None


def test_a_time_the_clocks_repeat_is_the_first_pass() -> None:
    """01:30 happens twice on the fall-back day. The earlier one is taken, which
    is what a caller saying "half one" means and what the owner's calendar shows."""
    moment = _local(FALL_BACK, time(1, 30), ZONE)

    assert moment is not None
    # Still daylight time: UTC-4, so 05:30Z rather than 06:30Z.
    assert moment.utcoffset() == timedelta(hours=-4)
    assert moment.astimezone(UTC).strftime("%H:%M") == "05:30"


def test_the_slots_on_a_day_the_clocks_change_are_real_distinct_times() -> None:
    """Stepping in local time would invent an hour in March and repeat one in
    November. Stepping in UTC gives a 23-hour and a 25-hour day, which is what
    those days are."""
    engine = _booking_engine()
    earliest = datetime(2020, 1, 1, tzinfo=UTC)

    short = engine._free(_all_day(SPRING_FORWARD), [], earliest)
    long = engine._free(_all_day(FALL_BACK), [], earliest)
    ordinary = engine._free(_all_day(date(2026, 11, 4)), [], earliest)

    assert len(ordinary) == 48
    assert len(short) == 46
    assert len(long) == 50
    for slots in (short, long, ordinary):
        assert len({slot.astimezone(UTC) for slot in slots}) == len(slots)
    # The hour that does not exist is offered on neither side of midnight.
    assert not [slot for slot in short if slot.strftime("%H:%M") == "02:30"]


def test_opening_hours_stay_on_the_wall_clock_across_a_transition() -> None:
    """Nine o'clock is nine o'clock on both sides of the change; it is the UTC
    offset that moves, which is exactly what a business means by its hours."""
    hours = BusinessHours.model_validate(
        {
            "timezone": TIMEZONE,
            "days": [
                {"day": day, "opens_at": "09:00", "closes_at": "17:00"}
                for day in (
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                )
            ],
        }
    )

    before = _window(hours, FALL_BACK - timedelta(days=1), ZONE)
    after = _window(hours, FALL_BACK + timedelta(days=1), ZONE)

    assert before is not None and after is not None
    assert before.opens.strftime("%H:%M") == after.opens.strftime("%H:%M") == "09:00"
    assert before.opens.utcoffset() == timedelta(hours=-4)
    assert after.opens.utcoffset() == timedelta(hours=-5)


# ---------------------------------------------------------------------------
# The event exists but we could not record it
# ---------------------------------------------------------------------------


async def test_an_event_we_cannot_record_is_taken_back_off_the_calendar(
    connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    google: FakeGoogle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window between a created event and the row that remembers it.

    Left alone, the owner's calendar holds an appointment this product has no
    record of: it will not be shown, not reconciled, and counted as free the
    next time somebody asks. So the event is cancelled and the caller is told
    the booking is not confirmed.
    """
    owner, tools = connected
    day = _weekday(2)

    original = AsyncSession.commit
    failures = {"left": 1}

    async def flaky(self: AsyncSession) -> None:
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("the database went away mid-booking")
        await original(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky)
    body = (await _call(client, tools[BOOK_APPOINTMENT], _booking(day))).json()
    monkeypatch.undo()

    assert body["result"] == "calendar_unavailable"
    # The agent is told not to promise, because the appointment does not exist.
    assert body["message"].startswith("Don't promise a time")
    # Created, then taken back off the chosen calendar.
    assert google.creates == 1
    assert [calendar for calendar, _ in google.deleted_from] == [BOOKINGS_CALENDAR]
    assert google.events == {}
    assert await _appointments(booking_app, owner.tenant_id) == []


async def test_a_booking_the_calendar_rejects_leaves_no_row(
    connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    google: FakeGoogle,
) -> None:
    owner, tools = connected
    google.create_status = 500

    body = (await _call(client, tools[BOOK_APPOINTMENT], _booking(_weekday(2)))).json()

    assert body["result"] == "calendar_unavailable"
    assert google.events == {}
    assert await _appointments(booking_app, owner.tenant_id) == []


# ---------------------------------------------------------------------------
# Outlook
#
# The same properties as Google, against Microsoft Graph: the chosen calendar is
# used, a repeat is one booking, and a refusal is never reported as booked.
# ---------------------------------------------------------------------------


@pytest.fixture
async def outlook_connected(
    client: AsyncClient, booking_app: FastAPI
) -> tuple[Owner, dict[str, WebhookTool]]:
    """A business booking into a secondary Outlook calendar, Google unconnected."""
    owner = await _business(client, booking_app)
    await _connect(client, owner, "microsoft_outlook")
    chosen = await _choose(client, owner, FakeOutlook.BOOKINGS, "microsoft_outlook")
    assert chosen.status_code == 200, chosen.text
    return owner, await _tools(booking_app, owner.tenant_id)


async def test_outlook_books_into_the_chosen_calendar(
    outlook_connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    outlook: FakeOutlook,
) -> None:
    owner, tools = outlook_connected
    day = _weekday(2)

    body = (await _call(client, tools[BOOK_APPOINTMENT], _booking(day))).json()

    assert body["result"] == "booked", body
    assert body["appointment"]["time"] == "14:00"
    assert outlook.created_in == [FakeOutlook.BOOKINGS]
    assert outlook.view_for and set(outlook.view_for) == {FakeOutlook.BOOKINGS}
    assert FakeOutlook.DEFAULT not in outlook.created_in + outlook.view_for

    [appointment] = await _appointments(booking_app, owner.tenant_id)
    assert appointment.provider is IntegrationProvider.MICROSOFT_OUTLOOK
    assert appointment.starts_at == _at(day, 14)
    assert appointment.external_event_id in outlook.events


async def test_outlook_carries_graphs_own_idempotency_key(
    outlook_connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    outlook: FakeOutlook,
) -> None:
    """``transactionId`` is what makes a create that timed out after succeeding
    find the first event rather than add a second."""
    _, tools = outlook_connected

    await _call(client, tools[BOOK_APPOINTMENT], _booking(_weekday(2)))

    [event] = outlook.events.values()
    assert event["transactionId"]
    assert list(outlook.by_transaction) == [event["transactionId"]]


async def test_outlook_books_a_repeated_request_once(
    outlook_connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    outlook: FakeOutlook,
) -> None:
    owner, tools = outlook_connected
    day = _weekday(2)

    first = (await _call(client, tools[BOOK_APPOINTMENT], _booking(day))).json()
    again = (await _call(client, tools[BOOK_APPOINTMENT], _booking(day))).json()

    assert first["result"] == "booked"
    assert again["result"] == "already_booked"
    assert len(outlook.events) == 1
    assert len(await _appointments(booking_app, owner.tenant_id)) == 1


async def test_outlook_refuses_a_slot_its_calendar_says_is_busy(
    outlook_connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    outlook: FakeOutlook,
) -> None:
    owner, tools = outlook_connected
    day = _weekday(2)
    outlook.busy = [(_at(day, 13, 45), _at(day, 14, 15))]

    body = (await _call(client, tools[BOOK_APPOINTMENT], _booking(day))).json()

    assert body["result"] == "slot_taken"
    assert outlook.creates == 0
    assert await _appointments(booking_app, owner.tenant_id) == []


async def test_an_outlook_failure_is_never_reported_as_booked(
    outlook_connected: tuple[Owner, dict[str, WebhookTool]],
    client: AsyncClient,
    booking_app: FastAPI,
    outlook: FakeOutlook,
) -> None:
    owner, tools = outlook_connected
    outlook.create_status = 503

    body = (await _call(client, tools[BOOK_APPOINTMENT], _booking(_weekday(2)))).json()

    assert body["result"] == "calendar_unavailable"
    assert "Don't tell the caller" in body["message"]
    assert outlook.events == {}
    assert await _appointments(booking_app, owner.tenant_id) == []
