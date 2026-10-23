"""Which opening line is in force right now.

A pure function over a published schedule and a moment in time. No database, no
vendor, no clock of its own — the moment is always passed in, because this is
asserted against fixed instants in the tests and read on the audible call path,
and a function that reads the clock itself can be neither.

**Why the schedule travels on the configuration version.** The rules table is
the truth, but a call is answered from the live config: the TwiML path reads it
out of the version-keyed cache and the vendor holds a projection of it. Copying
the enabled rules onto the version when they change means a call can always
answer "which greeting did this version say, and why" without a second query on
the audible path — the same reason the transfer destinations travel there
(:mod:`app.services.transfer_targets`).

**Why a dated rule beats a weekly one.** A business that closes at noon every
weekday and is also shut for Christmas means the Christmas message on Christmas
Day. The specific statement wins over the standing one; within a kind, the
first rule the owner listed wins.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.logging import get_logger
from app.models.enums import GreetingScheduleKind

logger = get_logger(__name__)

#: Where the schedule lives inside ``agent_configs.model_params_json``.
PARAMS_KEY = "greeting_schedule"

#: Minutes in a day. A window is stored as two offsets from local midnight.
MINUTES_IN_DAY = 24 * 60


@dataclass(frozen=True, slots=True)
class ScheduledGreeting:
    """One rule, flattened to what deciding needs.

    Deliberately not the ORM row: this is read from a published configuration,
    which may have been written by an older version of the rules table, so it
    describes a rule rather than pointing at one.
    """

    kind: GreetingScheduleKind
    message: str
    #: ``date.weekday()`` numbers — Monday is 0. Empty means every day.
    weekdays: tuple[int, ...] = ()
    start_minute: int | None = None
    end_minute: int | None = None
    start_date: date | None = None
    end_date: date | None = None

    @property
    def all_day(self) -> bool:
        return self.start_minute is None or self.end_minute is None

    @property
    def crosses_midnight(self) -> bool:
        """A window whose end is at or before its start runs into the next day.

        Equal ends included on purpose: "22:00 to 22:00" is nobody's idea of a
        zero-length window, and reading it as "all day from 22:00" is the only
        interpretation that is not silently useless.
        """
        return (
            not self.all_day
            and self.end_minute is not None
            and self.start_minute is not None
            and self.end_minute <= self.start_minute
        )


def schedule_for(rules: Iterable[Any]) -> tuple[ScheduledGreeting, ...]:
    """The enabled rules, in the order they were given.

    Disabled rules are dropped here rather than filtered in SQL so that the
    caller's list — the one the owner sees, enabled and disabled together —
    stays the single query this feature makes.
    """
    schedule: list[ScheduledGreeting] = []
    for rule in rules:
        if not rule.enabled:
            continue
        schedule.append(
            ScheduledGreeting(
                kind=rule.kind,
                message=rule.message,
                weekdays=tuple(rule.weekdays_json or ()),
                start_minute=rule.start_minute,
                end_minute=rule.end_minute,
                start_date=rule.start_date,
                end_date=rule.end_date,
            )
        )
    return tuple(schedule)


def dump_schedule(schedule: Sequence[ScheduledGreeting]) -> list[dict[str, Any]]:
    """JSON for ``model_params``. Dates as ISO strings; JSONB has no date type."""
    return [
        {
            "kind": entry.kind.value,
            "message": entry.message,
            "weekdays": list(entry.weekdays),
            "start_minute": entry.start_minute,
            "end_minute": entry.end_minute,
            "start_date": entry.start_date.isoformat() if entry.start_date else None,
            "end_date": entry.end_date.isoformat() if entry.end_date else None,
        }
        for entry in schedule
    ]


def load_schedule(raw: Any) -> tuple[ScheduledGreeting, ...]:
    """Read a published schedule back, skipping anything unreadable.

    Defensive for the same reason the transfer targets are: this parses a JSONB
    column that may have been written by an older release, and it runs on the
    audible path. A malformed entry costs that one exception to the greeting,
    not the call.
    """
    if not isinstance(raw, list):
        return ()
    schedule: list[ScheduledGreeting] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            kind = GreetingScheduleKind(str(item.get("kind")))
            message = str(item["message"])
            if not message:
                continue
            schedule.append(
                ScheduledGreeting(
                    kind=kind,
                    message=message,
                    weekdays=tuple(int(day) for day in item.get("weekdays") or ()),
                    start_minute=_as_minute(item.get("start_minute")),
                    end_minute=_as_minute(item.get("end_minute")),
                    start_date=_as_date(item.get("start_date")),
                    end_date=_as_date(item.get("end_date")),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("skipping an unreadable scheduled greeting")
    return tuple(schedule)


def schedule_of_config(model_params: dict[str, Any] | None) -> tuple[ScheduledGreeting, ...]:
    """The schedule a published configuration carries."""
    return load_schedule((model_params or {}).get(PARAMS_KEY))


def active_greeting(
    schedule: Sequence[ScheduledGreeting],
    *,
    moment: datetime,
    timezone: str,
    default: str,
) -> tuple[str, ScheduledGreeting | None]:
    """The opening line for ``moment``, and the rule that chose it.

    ``default`` is returned with ``None`` when no rule matches, which is the
    normal case for almost every call: a business with no exceptions has an
    empty schedule and this is one comparison.
    """
    if not schedule:
        return default, None

    local = _in_zone(moment, timezone)
    # Dated first: a one-off closure is the more specific statement.
    for kind in (GreetingScheduleKind.DATED, GreetingScheduleKind.WEEKLY):
        for entry in schedule:
            if entry.kind is kind and _matches(entry, local):
                return entry.message, entry
    return default, None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def _matches(entry: ScheduledGreeting, local: datetime) -> bool:
    if entry.kind is GreetingScheduleKind.DATED:
        return _dated_matches(entry, local)
    return _weekly_matches(entry, local)


def _dated_matches(entry: ScheduledGreeting, local: datetime) -> bool:
    if entry.start_date is None:
        return False
    end = entry.end_date or entry.start_date
    if entry.all_day:
        return entry.start_date <= local.date() <= end
    # With a window, the range says which days it applies to and the window
    # says when on those days — including a window that runs past midnight into
    # the morning after the last date.
    for day, minute in _candidate_days(entry, local):
        if entry.start_date <= day <= end and _within(entry, minute):
            return True
    return False


def _weekly_matches(entry: ScheduledGreeting, local: datetime) -> bool:
    for day, minute in _candidate_days(entry, local):
        if entry.weekdays and day.weekday() not in entry.weekdays:
            continue
        if _within(entry, minute):
            return True
    return False


def _candidate_days(entry: ScheduledGreeting, local: datetime) -> list[tuple[date, int]]:
    """Which day a window has to be read against, and the minute within it.

    Normally today. For a window that crosses midnight, *yesterday* has to be
    considered too: at 01:00 on Saturday it is Friday's 22:00-to-06:00 rule that
    is still running, and Friday is the weekday the owner selected.
    """
    minute = local.hour * 60 + local.minute
    days = [(local.date(), minute)]
    if entry.crosses_midnight:
        days.append((local.date() - timedelta(days=1), minute + MINUTES_IN_DAY))
    return days


def _within(entry: ScheduledGreeting, minute: int) -> bool:
    """Is ``minute`` inside the window, start inclusive and end exclusive?

    Exclusive at the end so that back-to-back rules — "11:00 to 12:00" then
    "12:00 to 13:00" — do not both claim noon.
    """
    if entry.all_day:
        return True
    assert entry.start_minute is not None and entry.end_minute is not None
    end = entry.end_minute + MINUTES_IN_DAY if entry.crosses_midnight else entry.end_minute
    return entry.start_minute <= minute < end


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def _in_zone(moment: datetime, timezone: str) -> datetime:
    """``moment`` as the business's own wall clock.

    An unknown zone falls back to UTC rather than raising: this runs on the
    audible path, and a greeting read against the wrong clock is a far smaller
    failure than a dropped call. It is logged, because a tenant whose timezone
    this deployment's zone data has never heard of is a real bug somewhere else.
    """
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown tenant timezone; reading the greeting schedule in UTC")
        return moment
    return moment.astimezone(zone)


def _as_minute(value: Any) -> int | None:
    if value is None:
        return None
    minute = int(value)
    if not 0 <= minute < MINUTES_IN_DAY:
        raise ValueError("a minute of the day is 0 to 1439")
    return minute


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(str(value))
