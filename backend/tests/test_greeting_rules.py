"""Greetings that replace the usual one at certain times, or on certain dates.

The feature's whole difficulty is that its correct value changes with the clock
and with nothing else, so these tests are mostly about *when* a rule is in force
and about who is holding the right line at that moment:

* the resolution itself, as a pure function against fixed instants — including
  the two cases that are easy to get wrong, a window that crosses midnight and a
  one-off date landing on top of a standing weekly rule;
* the published configuration keeps the business's *default* opening line, so a
  year of lunch hours does not turn the version history into noise;
* the voice agent is told the line that is in force now, and the worker's sweep
  is what moves it when the clock does — not a save, because nobody saves
  anything at noon;
* a rule belonging to another business is a 404, and a member who can read the
  dashboard cannot change what callers hear.

The fake ElevenLabs provider records the ``first_message`` it was pushed, which
is what lets all of that be asserted on the thing that actually matters: the
words the next caller would hear.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Agent, AgentConfig
from app.models.enums import (
    AgentMode,
    AgentStatus,
    GreetingScheduleKind,
    MembershipRole,
    TenantStatus,
)
from app.services.greeting_rules import MAX_RULES
from app.services.greeting_schedule import (
    PARAMS_KEY,
    ScheduledGreeting,
    active_greeting,
    schedule_of_config,
)
from app.services.greeting_sync import sweep_scheduled_greetings
from tests.portal_support import add_member, live_owner

LUNCH = "Thanks for calling. We're closed for lunch until one; leave a message and we'll call back."
HOLIDAY = "Happy holidays! We're closed until the 27th. Leave a message and we'll call you back."

#: A date range that contains today whatever timezone the tenant is read in.
TODAY = datetime.now(UTC).date()
AROUND_TODAY = {
    "start_date": (TODAY - timedelta(days=1)).isoformat(),
    "end_date": (TODAY + timedelta(days=1)).isoformat(),
}
#: Long over, in every timezone.
LONG_PAST = {"start_date": "2020-01-01", "end_date": "2020-01-02"}


def _weekly(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "weekly",
        "message": LUNCH,
        "weekdays": [0, 1, 2, 3, 4],
        "start_minute": 12 * 60,
        "end_minute": 13 * 60,
    }
    payload.update(overrides)
    return payload


def _dated(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": "dated", "message": HOLIDAY, **LONG_PAST}
    payload.update(overrides)
    return payload


def _url(owner: Any, rule_id: Any = None) -> str:
    base = f"/api/v1/tenants/{owner.tenant_id}/agent/greeting-rules"
    return base if rule_id is None else f"{base}/{rule_id}"


async def _add(client: AsyncClient, owner: Any, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(_url(owner), json=payload, headers=owner.headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _pushed_greeting(api_app: FastAPI, tenant_id: uuid.UUID) -> str:
    """The opening line the voice agent currently holds."""
    async with api_app.state.session_factory() as session:
        agent = (
            await session.execute(select(Agent).where(Agent.tenant_id == tenant_id))
        ).scalar_one()
        agent_id = agent.elevenlabs_agent_id
    assert agent_id
    return api_app.state.providers.elevenlabs.agents[agent_id].first_message


async def _live_config(api_app: FastAPI, tenant_id: uuid.UUID) -> AgentConfig:
    async with api_app.state.session_factory() as session:
        return (
            await session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == tenant_id)
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one()


async def _sweep(api_app: FastAPI, moment: datetime) -> int:
    async with api_app.state.session_factory() as session:
        return await sweep_scheduled_greetings(
            session, api_app.state.settings, api_app.state.providers, moment=moment
        )


async def _timezone(api_app: FastAPI, tenant_id: uuid.UUID) -> str:
    from app.models import Tenant

    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        return tenant.timezone


def _at(timezone: str, when: datetime) -> datetime:
    """A local wall-clock moment, as the instant it actually is."""
    return when.replace(tzinfo=ZoneInfo(timezone)).astimezone(UTC)


# ---------------------------------------------------------------------------
# Deciding which line is in force — no database, fixed instants
# ---------------------------------------------------------------------------


def test_no_rules_means_the_usual_greeting() -> None:
    message, rule = active_greeting(
        (), moment=datetime(2026, 10, 23, 12, 30, tzinfo=UTC), timezone="UTC", default="Hello."
    )
    assert (message, rule) == ("Hello.", None)


def test_a_weekly_window_is_read_in_the_business_timezone() -> None:
    """Noon in New York, not noon in UTC.

    The bug this guards against is the one every scheduling feature ships with
    once: the window is stored as local minutes, so comparing it against a UTC
    clock puts a lunch-hour greeting on the phones at seven in the morning.
    """
    lunch = ScheduledGreeting(
        kind=GreetingScheduleKind.WEEKLY,
        message=LUNCH,
        weekdays=(0, 1, 2, 3, 4),
        start_minute=12 * 60,
        end_minute=13 * 60,
    )
    # Friday 2026-10-23, 16:30 UTC — which is 12:30 in New York.
    during = datetime(2026, 10, 23, 16, 30, tzinfo=UTC)
    assert active_greeting(
        (lunch,), moment=during, timezone="America/New_York", default="Hello."
    ) == (LUNCH, lunch)
    # The same instant in Los Angeles is 09:30, well before lunch.
    assert active_greeting(
        (lunch,), moment=during, timezone="America/Los_Angeles", default="Hello."
    ) == ("Hello.", None)


def test_the_end_of_a_window_belongs_to_the_next_rule() -> None:
    """11-to-12 then 12-to-1 must not both claim noon."""
    morning = ScheduledGreeting(
        kind=GreetingScheduleKind.WEEKLY, message="Morning.", start_minute=11 * 60, end_minute=720
    )
    assert active_greeting(
        (morning,), moment=datetime(2026, 10, 23, 12, 0, tzinfo=UTC), timezone="UTC", default="Hi."
    ) == ("Hi.", None)
    assert active_greeting(
        (morning,), moment=datetime(2026, 10, 23, 11, 59, tzinfo=UTC), timezone="UTC", default="Hi."
    ) == ("Morning.", morning)


def test_a_window_that_crosses_midnight_runs_into_the_next_morning() -> None:
    """Friday's 22:00-to-06:00 rule is still Friday's at one on Saturday.

    Stored against the day it *starts*, so a business that says "Friday night"
    gets Friday night — not Friday evening plus Friday's own small hours.
    """
    overnight = ScheduledGreeting(
        kind=GreetingScheduleKind.WEEKLY,
        message="We're closed for the night.",
        weekdays=(4,),  # Friday
        start_minute=22 * 60,
        end_minute=6 * 60,
    )
    late_friday = datetime(2026, 10, 23, 23, 30, tzinfo=UTC)  # a Friday
    early_saturday = datetime(2026, 10, 24, 1, 0, tzinfo=UTC)
    early_friday = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)

    assert active_greeting((overnight,), moment=late_friday, timezone="UTC", default="Hi.")[1]
    assert active_greeting((overnight,), moment=early_saturday, timezone="UTC", default="Hi.")[1]
    assert (
        active_greeting((overnight,), moment=early_friday, timezone="UTC", default="Hi.")[1] is None
    ), "Friday's small hours belong to Thursday's rule, which this business does not have"


def test_a_one_off_date_beats_a_standing_weekly_rule() -> None:
    """Closed for Christmas outranks "we always shut for lunch"."""
    lunch = ScheduledGreeting(
        kind=GreetingScheduleKind.WEEKLY, message=LUNCH, start_minute=12 * 60, end_minute=13 * 60
    )
    holiday = ScheduledGreeting(
        kind=GreetingScheduleKind.DATED,
        message=HOLIDAY,
        start_date=date(2026, 12, 24),
        end_date=date(2026, 12, 26),
    )
    noon_on_christmas = datetime(2026, 12, 25, 12, 30, tzinfo=UTC)
    # Listed weekly-first on purpose: precedence is by kind, not by position.
    assert active_greeting(
        (lunch, holiday), moment=noon_on_christmas, timezone="UTC", default="Hello."
    ) == (HOLIDAY, holiday)


def test_an_unreadable_schedule_is_skipped_rather_than_raised() -> None:
    """This parses JSONB on the audible path; one bad entry is not a dropped call."""
    schedule = schedule_of_config(
        {
            PARAMS_KEY: [
                {"kind": "weekly", "start_minute": 0, "end_minute": 60},  # no message
                {"kind": "nonsense", "message": "Hi."},
                "not a rule at all",
                {"kind": "dated", "message": HOLIDAY, "start_date": "2026-12-25"},
            ]
        }
    )
    assert [entry.message for entry in schedule] == [HOLIDAY]


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


async def test_a_new_receptionist_has_no_scheduled_greetings(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The empty state is real: every call gets the one opening line."""
    owner = await live_owner(api_client, api_app)
    response = await api_client.get(_url(owner), headers=owner.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rules"] == []
    assert body["max_rules"] == MAX_RULES
    assert body["timezone"] == await _timezone(api_app, owner.tenant_id)
    live = await _live_config(api_app, owner.tenant_id)
    assert body["default_greeting"] == live.first_message
    assert body["active_message"] == live.first_message, "with no rules, now is the usual line"


async def test_the_schedule_needs_a_session(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    assert (await api_client.get(_url(owner))).status_code == 401


# ---------------------------------------------------------------------------
# Adding
# ---------------------------------------------------------------------------


async def test_adding_a_rule_publishes_the_schedule_without_changing_the_usual_line(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The version records the schedule and keeps the default opening line.

    Publishing the currently-active exception instead would make the history
    unreadable — "version 41, lunch; version 42, not lunch", every day — and a
    rollback would restore whatever happened to be true when it was published.
    """
    owner = await live_owner(api_client, api_app)
    before = await _live_config(api_app, owner.tenant_id)

    body = await _add(api_client, owner, _weekly())

    assert len(body["rules"]) == 1
    rule = body["rules"][0]
    assert rule["kind"] == "weekly"
    assert rule["weekdays"] == [0, 1, 2, 3, 4]
    assert (rule["start_minute"], rule["end_minute"]) == (720, 780)
    assert rule["enabled"] is True
    assert body["config_version"] == before.version + 1
    assert body["synced"] is True

    after = await _live_config(api_app, owner.tenant_id)
    assert after.first_message == before.first_message, "the default line is untouched"
    assert after.system_prompt == before.system_prompt, "no word of the prompt changes"
    assert [entry.message for entry in schedule_of_config(after.model_params_json)] == [LUNCH]


async def test_a_rule_that_is_not_running_leaves_callers_on_the_usual_greeting(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    live = await _live_config(api_app, owner.tenant_id)

    body = await _add(api_client, owner, _dated())  # dated in 2020

    assert body["active_message"] == live.first_message
    assert await _pushed_greeting(api_app, owner.tenant_id) == live.first_message


async def test_a_rule_that_is_running_is_pushed_to_the_voice_agent_as_it_is_saved(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Saving a rule that applies right now is audible immediately.

    The sweep would get there within a tick, but a customer who types today's
    closure and puts the phone down expects the next caller to hear it.
    """
    owner = await live_owner(api_client, api_app)
    default = (await _live_config(api_app, owner.tenant_id)).first_message

    body = await _add(api_client, owner, _dated(**AROUND_TODAY))

    assert body["active_message"] == HOLIDAY
    assert await _pushed_greeting(api_app, owner.tenant_id) == HOLIDAY
    assert default != HOLIDAY


async def test_a_weekly_rule_must_say_what_time(api_client: AsyncClient, api_app: FastAPI) -> None:
    """A weekly rule with no window would claim every hour of its days."""
    owner = await live_owner(api_client, api_app)
    response = await api_client.post(
        _url(owner),
        json=_weekly(start_minute=None, end_minute=None),
        headers=owner.headers,
    )
    assert response.status_code == 422, response.text


async def test_a_rule_cannot_carry_the_other_kind_of_calendar(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """ "Every Tuesday in December" is refused rather than half-stored."""
    owner = await live_owner(api_client, api_app)
    weekly_with_dates = await api_client.post(
        _url(owner), json=_weekly(**LONG_PAST), headers=owner.headers
    )
    dated_with_weekdays = await api_client.post(
        _url(owner), json=_dated(weekdays=[1]), headers=owner.headers
    )
    assert weekly_with_dates.status_code == 422, weekly_with_dates.text
    assert dated_with_weekdays.status_code == 422, dated_with_weekdays.text


async def test_a_greeting_of_nothing_but_spaces_is_refused(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """`min_length` is satisfied by three spaces, and a window that matched
    would have the receptionist answer with silence."""
    owner = await live_owner(api_client, api_app)
    response = await api_client.post(
        _url(owner), json=_weekly(message="   "), headers=owner.headers
    )
    assert response.status_code == 422, response.text


async def test_the_last_date_cannot_be_before_the_first(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    response = await api_client.post(
        _url(owner),
        json=_dated(start_date="2026-12-26", end_date="2026-12-24"),
        headers=owner.headers,
    )
    assert response.status_code == 422, response.text


async def test_the_number_of_scheduled_greetings_is_capped(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    for index in range(MAX_RULES):
        await _add(api_client, owner, _weekly(message=f"Closed, number {index}."))

    response = await api_client.post(_url(owner), json=_weekly(), headers=owner.headers)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["max_rules"] == MAX_RULES


# ---------------------------------------------------------------------------
# Editing and removing
# ---------------------------------------------------------------------------


async def test_switching_a_rule_off_takes_it_out_of_the_schedule(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Off means not matched, and the business keeps the wording on file."""
    owner = await live_owner(api_client, api_app)
    default = (await _live_config(api_app, owner.tenant_id)).first_message
    created = await _add(api_client, owner, _dated(**AROUND_TODAY))
    rule_id = created["rules"][0]["id"]
    assert await _pushed_greeting(api_app, owner.tenant_id) == HOLIDAY

    response = await api_client.patch(
        _url(owner, rule_id), json={"enabled": False}, headers=owner.headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rules"][0]["enabled"] is False
    assert body["rules"][0]["message"] == HOLIDAY, "still on file"
    assert body["active_message"] == default
    assert await _pushed_greeting(api_app, owner.tenant_id) == default
    live = await _live_config(api_app, owner.tenant_id)
    assert schedule_of_config(live.model_params_json) == ()


async def test_a_weekly_rule_cannot_be_edited_into_a_dated_one(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Changing which calendar a rule is read against changes every field on it,
    so "edit" would quietly mean "replace"."""
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner, _weekly())
    rule_id = created["rules"][0]["id"]

    response = await api_client.patch(
        _url(owner, rule_id), json={"start_date": "2026-12-25"}, headers=owner.headers
    )
    assert response.status_code == 422, response.text


async def test_half_a_window_is_never_an_edit(api_client: AsyncClient, api_app: FastAPI) -> None:
    """The pair is stored and read together; a new start with the old end would
    leave a window nobody asked for."""
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner, _weekly())
    rule_id = created["rules"][0]["id"]

    response = await api_client.patch(
        _url(owner, rule_id), json={"start_minute": 11 * 60}, headers=owner.headers
    )
    assert response.status_code == 422, response.text


async def test_a_dated_greeting_can_be_put_back_to_all_day(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Two explicit nulls mean "remove the window", not "I did not mention it".

    This is the checkbox in the form. Reading them as "leave it alone" would
    make switching the window off appear to work and change nothing.
    """
    owner = await live_owner(api_client, api_app)
    created = await _add(
        api_client, owner, _dated(**AROUND_TODAY, start_minute=9 * 60, end_minute=17 * 60)
    )
    rule_id = created["rules"][0]["id"]

    response = await api_client.patch(
        _url(owner, rule_id),
        json={"start_minute": None, "end_minute": None},
        headers=owner.headers,
    )

    assert response.status_code == 200, response.text
    rule = response.json()["rules"][0]
    assert (rule["start_minute"], rule["end_minute"]) == (None, None)


async def test_a_weekly_greeting_cannot_have_its_window_removed(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Without one it would claim every hour of its days."""
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner, _weekly())
    rule_id = created["rules"][0]["id"]

    response = await api_client.patch(
        _url(owner, rule_id),
        json={"start_minute": None, "end_minute": None},
        headers=owner.headers,
    )
    assert response.status_code == 422, response.text


async def test_an_edit_that_changes_nothing_publishes_nothing(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner, _weekly())
    rule_id = created["rules"][0]["id"]
    version = (await _live_config(api_app, owner.tenant_id)).version

    response = await api_client.patch(
        _url(owner, rule_id), json={"message": LUNCH}, headers=owner.headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["config_version"] == version, "no version burnt on a no-op"


async def test_deleting_the_rule_puts_the_usual_greeting_back(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    default = (await _live_config(api_app, owner.tenant_id)).first_message
    created = await _add(api_client, owner, _dated(**AROUND_TODAY))
    rule_id = created["rules"][0]["id"]

    response = await api_client.delete(_url(owner, rule_id), headers=owner.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rules"] == []
    assert body["active_message"] == default
    assert await _pushed_greeting(api_app, owner.tenant_id) == default


# ---------------------------------------------------------------------------
# The sweep: what moves the greeting when nobody does
# ---------------------------------------------------------------------------


async def test_the_worker_switches_the_greeting_when_the_clock_enters_a_window(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Nobody saves anything at noon, so something has to move it.

    On the native call path ElevenLabs owns the number and answers from the
    agent's own opening line; there is no request of ours in the middle to
    substitute one. This sweep is the whole mechanism.
    """
    owner = await live_owner(api_client, api_app)
    default = (await _live_config(api_app, owner.tenant_id)).first_message
    timezone = await _timezone(api_app, owner.tenant_id)
    await _add(api_client, owner, _weekly())  # weekdays, 12:00-13:00 local

    # A Friday, in the business's own time.
    before_lunch = _at(timezone, datetime(2026, 10, 23, 11, 30))
    during_lunch = _at(timezone, datetime(2026, 10, 23, 12, 30))
    after_lunch = _at(timezone, datetime(2026, 10, 23, 14, 0))

    assert await _sweep(api_app, before_lunch) == 0, "already correct"
    assert await _pushed_greeting(api_app, owner.tenant_id) == default

    assert await _sweep(api_app, during_lunch) == 1
    assert await _pushed_greeting(api_app, owner.tenant_id) == LUNCH

    assert await _sweep(api_app, during_lunch) == 0, "idempotent: no second vendor call"

    assert await _sweep(api_app, after_lunch) == 1
    assert await _pushed_greeting(api_app, owner.tenant_id) == default


async def test_the_sweep_ignores_a_business_with_no_rules(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The bound that makes it cheap enough to run every tick."""
    owner = await live_owner(api_client, api_app)
    assert await _sweep(api_app, datetime.now(UTC)) == 0
    assert (
        await _pushed_greeting(api_app, owner.tenant_id)
        == (await _live_config(api_app, owner.tenant_id)).first_message
    )


async def test_editing_the_receptionist_mid_window_keeps_the_scheduled_greeting(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A save that pushed ``first_message`` verbatim would cancel the exception.

    Every push resolves the schedule, which is why this holds for an edit that
    has nothing to do with greetings at all.
    """
    owner = await live_owner(api_client, api_app)
    await _add(api_client, owner, _dated(**AROUND_TODAY))
    assert await _pushed_greeting(api_app, owner.tenant_id) == HOLIDAY

    response = await api_client.put(
        f"/api/v1/tenants/{owner.tenant_id}/agent/settings",
        json={
            "services": "cuts, color, styling",
            "operating_hours": "Mon-Fri 9-6",
            "greeting_style": "friendly",
            "custom_greeting": "",
            "escalation_rules": "",
        },
        headers=owner.headers,
    )

    assert response.status_code == 200, response.text
    assert await _pushed_greeting(api_app, owner.tenant_id) == HOLIDAY


# ---------------------------------------------------------------------------
# Who may change what callers hear
# ---------------------------------------------------------------------------


async def test_a_member_cannot_change_the_greeting(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Reading the dashboard is not permission to speak for the business."""
    owner = await live_owner(api_client, api_app)
    member = await add_member(api_app, owner.tenant_id, MembershipRole.MEMBER)

    assert (await api_client.get(_url(owner), headers=member)).status_code == 200
    created = await api_client.post(_url(owner), json=_weekly(), headers=member)
    assert created.status_code == 403, created.text


async def test_another_businesss_rule_is_simply_not_found(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    stranger = await live_owner(api_client, api_app)
    created = await _add(api_client, stranger, _weekly())
    rule_id = created["rules"][0]["id"]

    edited = await api_client.patch(
        _url(owner, rule_id), json={"enabled": False}, headers=owner.headers
    )
    removed = await api_client.delete(_url(owner, rule_id), headers=owner.headers)

    assert edited.status_code == 404, edited.text
    assert removed.status_code == 404, removed.text
    # And the stranger's rule is still theirs, untouched.
    theirs = await api_client.get(_url(stranger), headers=stranger.headers)
    assert theirs.json()["rules"][0]["id"] == rule_id


# ---------------------------------------------------------------------------
# The other call path: the greeting as a per-call dynamic variable
# ---------------------------------------------------------------------------


async def test_the_twiml_path_resolves_the_schedule_without_a_second_query(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Where a shared agent asks us who it is, it is told the right greeting.

    On this path nothing is pushed to the vendor at all — the agent is generic
    until ``/voice/init`` answers, and the schedule rides on the configuration
    the answer is built from. So the resolution happens in process, on the
    audible path, with no query of its own.
    """
    from app.models.enums import PhoneNumberStatus
    from tests.factories import make_agent, make_agent_config, make_phone_number, make_tenant

    async with api_app.state.session_factory() as session:
        tenant = make_tenant(
            name="Careful Legal",
            status=TenantStatus.ACTIVE,
            agent_mode=AgentMode.SHARED_VERTICAL,
            timezone="America/New_York",
        )
        session.add(tenant)
        await session.flush()
        config = make_agent_config(
            tenant,
            is_live=True,
            first_message="Thanks for calling Careful Legal.",
            model_params_json={
                PARAMS_KEY: [
                    {
                        "kind": "dated",
                        "message": HOLIDAY,
                        "weekdays": [],
                        "start_minute": None,
                        "end_minute": None,
                        **AROUND_TODAY,
                    }
                ]
            },
        )
        session.add(config)
        await session.flush()
        # Active, or the number directory does not resolve it — which is the
        # same refusal a released number gets on a real call.
        number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
        session.add_all(
            [
                number,
                make_agent(
                    tenant,
                    config,
                    status=AgentStatus.ACTIVE,
                    elevenlabs_agent_id="agent_one_for_all",
                    is_shared=True,
                ),
            ]
        )
        await session.commit()
        dialled = number.e164

    response = await api_client.post("/api/v1/voice/init", json={"agent_number": dialled})

    assert response.status_code == 200, response.text
    assert response.json()["dynamic_variables"]["greeting"] == HOLIDAY
