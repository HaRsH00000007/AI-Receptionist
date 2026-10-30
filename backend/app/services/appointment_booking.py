"""Checking a business's calendar and booking into it, for a caller on the line.

What the voice agent's two tools do (:mod:`app.services.calendar_tools`). The
agent is a language model talking to a stranger, so everything it sends is
treated as a claim to check, and everything sent back is written to be acted on
without interpretation:

* **Every answer is a result code and a sentence.** ``booked``, ``slot_taken``,
  ``calendar_unavailable``... plus a ``message`` that tells the agent what to do
  next. A model reading free-form errors improvises; one reading "do not promise
  a time, offer to take a message" mostly does that.
* **Only the calendar decides what is free.** Times are offered from the
  calendar's busy blocks, inside the business's opening hours, no sooner than
  the minimum notice and no further out than the booking horizon. A time the
  model proposes on its own is checked the same way before it is booked.
* **Booking re-checks, under a lock.** Bookings for one business are serialized
  with a transaction-scoped advisory lock, and inside it both the calendar and
  our own ``appointments`` are checked again immediately before the event is
  created. Two callers asking for the same slot cannot both get it through us.
* **A repeated request is the same booking.** The idempotency key is derived
  from the conversation and the slot, so a tool call the vendor retries, or a
  model that books twice, gets ``already_booked`` and the original event — and
  the calendar adapters carry the same key, so even a create that timed out
  after succeeding is found rather than duplicated.

Times are the business's local time throughout, because that is what the caller
and the owner both mean by "3pm on Tuesday".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.integrations.providers.base import BusyInterval, CalendarEvent
from app.integrations.service import IntegrationService, OpenCalendar
from app.models import Appointment, BusinessProfile, Tenant
from app.models.enums import AppointmentStatus
from app.schemas.business import WEEK_ORDER, BusinessHours, DaySchedule, Weekday

logger = get_logger(__name__)

#: How many times one answer offers. Enough to choose from; few enough to say.
MAX_OFFERED = 6
#: How many days past a full or closed day are searched for the next opening.
LOOKAHEAD_DAYS = 7


class Result(StrEnum):
    AVAILABLE = "available"
    #: Open that day, but nothing is free.
    UNAVAILABLE = "unavailable"
    CLOSED = "closed"
    BOOKED = "booked"
    ALREADY_BOOKED = "already_booked"
    SLOT_TAKEN = "slot_taken"
    OUTSIDE_HOURS = "outside_hours"
    TOO_SOON = "too_soon"
    TOO_FAR = "too_far"
    INVALID_REQUEST = "invalid_request"
    CALENDAR_UNAVAILABLE = "calendar_unavailable"


_FALLBACK_MESSAGE = (
    "Don't promise a time. Tell the caller you can't book it right now and offer to "
    "take a message so the team can call back to arrange it."
)


# ---------------------------------------------------------------------------
# Requests, as the agent sends them
# ---------------------------------------------------------------------------


class _ToolRequest(BaseModel):
    """Lenient on purpose: an unknown field is ignored, not a refusal.

    Empty strings — what a model sends for "I have nothing here" — and a dynamic
    variable the vendor could not resolve are both read as absent.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _blank_is_absent(cls, value: Any) -> Any:
        if isinstance(value, str) and (not value.strip() or value.strip().startswith("{{")):
            return None
        if isinstance(value, int | float) and not isinstance(value, bool):
            return str(value)
        return value


class AvailabilityRequest(_ToolRequest):
    date: str = Field(max_length=40)
    time: str | None = Field(default=None, max_length=20)
    conversation_id: str | None = Field(default=None, max_length=128)


class BookingRequest(_ToolRequest):
    date: str = Field(max_length=40)
    time: str = Field(max_length=20)
    caller_name: str = Field(min_length=1, max_length=120)
    caller_phone: str | None = Field(default=None, max_length=32)
    caller_email: str | None = Field(default=None, max_length=320)
    reason: str | None = Field(default=None, max_length=500)
    #: The number the call came from, filled by the vendor.
    caller_id: str | None = Field(default=None, max_length=32)
    conversation_id: str | None = Field(default=None, max_length=128)


#: What to tell the agent when a field it was asked for is missing or unusable.
_FIELD_HINTS = {
    "date": "Ask the caller which day they want.",
    "time": "Ask the caller what time they want.",
    "caller_name": "Ask the caller for their full name.",
}


def invalid_request(error: ValidationError) -> dict[str, Any]:
    """A validation failure, phrased as what to ask the caller next."""
    fields = sorted({str(issue["loc"][0]) for issue in error.errors() if issue.get("loc")})
    hints = [_FIELD_HINTS[name] for name in fields if name in _FIELD_HINTS]
    return {
        "result": Result.INVALID_REQUEST.value,
        "fields": fields,
        "message": " ".join(hints) or "Some details were not usable. Ask the caller again.",
    }


# ---------------------------------------------------------------------------
# Parsing and formatting
# ---------------------------------------------------------------------------

_TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H")


def parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_time(value: str) -> time | None:
    cleaned = re.sub(r"\s+", " ", value.strip().upper().replace(".", ""))
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).time().replace(second=0, microsecond=0)
        except ValueError:
            continue
    return None


def spoken_time(moment: datetime) -> str:
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def spoken_day(day: date) -> str:
    # Built by hand rather than with %-d, which Windows' strftime does not know.
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B')}"


def _slot(moment: datetime, *, with_day: bool) -> dict[str, str]:
    said = spoken_time(moment)
    return {
        "date": moment.date().isoformat(),
        "time": moment.strftime("%H:%M"),
        "spoken": f"{spoken_day(moment.date())} at {said}" if with_day else said,
    }


def _zone(tenant: Tenant) -> ZoneInfo:
    try:
        return ZoneInfo(tenant.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "tenant has an unknown timezone; booking in UTC", extra={"tenant_id": str(tenant.id)}
        )
        return ZoneInfo("UTC")


def _local(day: date, at: time, zone: ZoneInfo) -> datetime | None:
    """``day`` at ``at`` on the business's wall clock, or ``None`` if that time
    does not exist there (the hour skipped when the clocks go forward)."""
    moment = datetime.combine(day, at, tzinfo=zone)
    round_trip = moment.astimezone(UTC).astimezone(zone)
    if round_trip.replace(tzinfo=None) != moment.replace(tzinfo=None):
        return None
    return moment


def _overlaps(start: datetime, end: datetime, busy: list[BusyInterval]) -> bool:
    return any(block.start < end and start < block.end for block in busy)


def _spread(slots: list[datetime], limit: int) -> list[datetime]:
    """At most ``limit`` slots, spread across the day rather than the first few."""
    if len(slots) <= limit:
        return slots
    step = len(slots) / limit
    return [slots[int(index * step)] for index in range(limit)]


def _nearest(slots: list[datetime], target: datetime, limit: int) -> list[datetime]:
    closest = sorted(slots, key=lambda slot: abs((slot - target).total_seconds()))[:limit]
    return sorted(closest)


# ---------------------------------------------------------------------------
# Opening hours
# ---------------------------------------------------------------------------

#: When a business never told us its hours: the same assumption the config
#: generator falls back to, a weekday business.
_DEFAULT_DAYS = [
    DaySchedule(day=day, opens_at="09:00", closes_at="17:00")
    if day not in (Weekday.SATURDAY, Weekday.SUNDAY)
    else DaySchedule(day=day, closed=True)
    for day in WEEK_ORDER
]


def opening_hours(profile: BusinessProfile | None, timezone: str) -> BusinessHours:
    if profile is not None and profile.hours_json:
        try:
            return BusinessHours.model_validate(profile.hours_json)
        except ValidationError:
            logger.warning("stored hours failed validation; assuming weekday hours")
    return BusinessHours(timezone=timezone, days=_DEFAULT_DAYS)


@dataclass(frozen=True, slots=True)
class _Window:
    opens: datetime
    closes: datetime


def _window(hours: BusinessHours, day: date, zone: ZoneInfo) -> _Window | None:
    schedule = hours.by_day().get(WEEK_ORDER[day.weekday()])
    if schedule is None or schedule.closed or not schedule.opens_at or not schedule.closes_at:
        return None
    opens = datetime.combine(day, time.fromisoformat(schedule.opens_at), tzinfo=zone)
    closes = datetime.combine(day, time.fromisoformat(schedule.closes_at), tzinfo=zone)
    return _Window(opens=opens, closes=closes)


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class AppointmentBooking:
    def __init__(
        self, session: AsyncSession, settings: Settings, integrations: IntegrationService
    ) -> None:
        self.session = session
        self.settings = settings
        self.integrations = integrations
        self.duration = timedelta(minutes=settings.booking_slot_minutes)

    # ---- availability ----------------------------------------------------
    async def check_availability(
        self, tenant: Tenant, request: AvailabilityRequest
    ) -> dict[str, Any]:
        zone = _zone(tenant)
        now = datetime.now(UTC)
        today = now.astimezone(zone).date()
        base = self._base(today, zone)

        day = parse_date(request.date)
        if day is None:
            return {
                **base,
                "result": Result.INVALID_REQUEST.value,
                "message": "That date was not understood. Send it as YYYY-MM-DD.",
            }
        if day < today:
            return {
                **base,
                "result": Result.INVALID_REQUEST.value,
                "message": f"That date has passed; today is {spoken_day(today)}. "
                "Ask the caller which day they meant.",
            }
        last_day = today + timedelta(days=self.settings.booking_horizon_days)
        if day > last_day:
            return {
                **base,
                "result": Result.TOO_FAR.value,
                "message": "That is further ahead than bookings can be made. Offer a "
                f"day before {spoken_day(last_day)}.",
            }
        wanted_time = parse_time(request.time) if request.time else None
        if request.time and wanted_time is None:
            return {
                **base,
                "result": Result.INVALID_REQUEST.value,
                "message": "That time was not understood. Send it as HH:MM, 24-hour.",
            }

        calendar = await self._open(tenant)
        if isinstance(calendar, dict):
            return {**base, **calendar}

        hours = opening_hours(await self._profile(tenant), tenant.timezone)
        last_searched = min(day + timedelta(days=LOOKAHEAD_DAYS), last_day)
        busy = await self._busy(
            tenant,
            calendar,
            start=datetime.combine(day, time.min, tzinfo=zone),
            end=datetime.combine(last_searched + timedelta(days=1), time.min, tzinfo=zone),
        )
        if isinstance(busy, dict):
            return {**base, **busy}

        earliest = now + timedelta(minutes=self.settings.booking_min_notice_minutes)
        window = _window(hours, day, zone)
        free = self._free(window, busy, earliest)
        answer: dict[str, Any] = {**base, "date": day.isoformat(), "day": spoken_day(day)}

        requested: datetime | None = None
        if wanted_time is not None:
            requested = _local(day, wanted_time, zone)
            answer["requested_time_available"] = requested is not None and self._bookable(
                requested, window, busy, earliest
            )

        if free:
            offered = (
                _nearest(free, requested, MAX_OFFERED) if requested else _spread(free, MAX_OFFERED)
            )
            answer["slots"] = [_slot(slot, with_day=False) for slot in offered]
            answer["result"] = Result.AVAILABLE.value
            if answer.get("requested_time_available"):
                answer["message"] = (
                    "The requested time is free. Confirm it with the caller, then book it."
                )
            elif requested is not None:
                answer["message"] = (
                    "The requested time is not available. Offer the nearest times in slots."
                )
            else:
                answer["message"] = "Offer the caller a few of these times."
            return answer

        answer["result"] = (Result.CLOSED if window is None else Result.UNAVAILABLE).value
        following = self._next_opening(hours, day, last_searched, zone, busy, earliest)
        answer["next_available"] = [_slot(slot, with_day=True) for slot in following]
        closed = "The business is closed that day" if window is None else "Nothing is free that day"
        answer["message"] = (
            f"{closed}. Offer the times in next_available instead."
            if following
            else f"{closed}, and nothing is free in the following week either. "
            "Offer to take a message so the team can call back."
        )
        return answer

    # ---- booking ---------------------------------------------------------
    async def book(self, tenant: Tenant, request: BookingRequest) -> dict[str, Any]:
        zone = _zone(tenant)
        now = datetime.now(UTC)
        today = now.astimezone(zone).date()
        base = self._base(today, zone)

        day = parse_date(request.date)
        at = parse_time(request.time)
        if day is None or at is None:
            return {
                **base,
                "result": Result.INVALID_REQUEST.value,
                "message": "The date or time was not understood. Send them as YYYY-MM-DD "
                "and HH:MM, 24-hour.",
            }
        start = _local(day, at, zone)
        if start is None:
            return {
                **base,
                "result": Result.INVALID_REQUEST.value,
                "message": "That time does not exist that day because the clocks change. "
                "Offer another time.",
            }
        end = start + self.duration

        hours = opening_hours(await self._profile(tenant), tenant.timezone)
        window = _window(hours, day, zone)
        if window is None:
            return {
                **base,
                "result": Result.CLOSED.value,
                "message": "The business is closed that day. Call check_availability for "
                "another day.",
            }
        if start < window.opens or end > window.closes:
            return {
                **base,
                "result": Result.OUTSIDE_HOURS.value,
                "message": f"That is outside opening hours ({spoken_time(window.opens)} to "
                f"{spoken_time(window.closes)}). Call check_availability and offer a time "
                "from it.",
            }
        if start < now + timedelta(minutes=self.settings.booking_min_notice_minutes):
            return {
                **base,
                "result": Result.TOO_SOON.value,
                "message": "That is too soon to book. Call check_availability and offer a "
                "later time.",
            }
        if day > today + timedelta(days=self.settings.booking_horizon_days):
            return {
                **base,
                "result": Result.TOO_FAR.value,
                "message": "That is further ahead than bookings can be made.",
            }

        name = request.caller_name.strip()
        phone = request.caller_phone or request.caller_id
        key = self._key(request.conversation_id, start, name, phone)

        # Cheap and lock-free: the common duplicate is the same request again.
        existing = await self._by_key(tenant, key)
        if existing is not None:
            return self._already_booked(base, existing, zone)

        calendar = await self._open(tenant)
        if isinstance(calendar, dict):
            return {**base, **calendar}

        # Everything from here to the commit is serialized per business. Taken
        # after the calendar is open, because opening it may commit a refreshed
        # token, and a commit would release this lock.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"appointments:{tenant.id}"},
        )
        existing = await self._by_key(tenant, key)
        if existing is not None:
            return self._already_booked(base, existing, zone)

        busy = await self._busy(tenant, calendar, start=window.opens, end=window.closes)
        if isinstance(busy, dict):
            return {**base, **busy}
        if _overlaps(start, end, busy):
            earliest = now + timedelta(minutes=self.settings.booking_min_notice_minutes)
            alternatives = _nearest(self._free(window, busy, earliest), start, 3)
            return {
                **base,
                "result": Result.SLOT_TAKEN.value,
                "alternatives": [_slot(slot, with_day=True) for slot in alternatives],
                "message": (
                    "That time was just taken. Apologize and offer the alternatives."
                    if alternatives
                    else "That time was just taken and nothing else is free that day. "
                    "Call check_availability for another day."
                ),
            }

        event = CalendarEvent(
            title=f"{name}: {request.reason}" if request.reason else f"Appointment: {name}",
            start=start,
            end=end,
            description=self._describe(name, phone, request),
            idempotency_key=key,
        )
        try:
            event_id = await calendar.create_event(event)
        except AppError as exc:
            logger.warning(
                "the calendar refused or failed a booking",
                extra={
                    "tenant_id": str(tenant.id),
                    "provider": calendar.provider.value,
                    "code": exc.code,
                },
            )
            # A timeout may have created the event after all, so this cannot
            # say "not booked" either: the team confirms with the caller.
            return {
                **base,
                "result": Result.CALENDAR_UNAVAILABLE.value,
                "message": "The booking could not be confirmed. Don't tell the caller "
                "they are booked. Take their details and say the team will call to "
                "confirm the appointment.",
            }

        appointment = Appointment(
            tenant_id=tenant.id,
            provider=calendar.provider,
            external_event_id=event_id,
            status=AppointmentStatus.BOOKED,
            starts_at=start,
            ends_at=end,
            timezone=zone.key,
            caller_name=name,
            caller_phone=phone,
            caller_email=request.caller_email,
            reason=request.reason,
            conversation_id=request.conversation_id,
            idempotency_key=key,
        )
        self.session.add(appointment)
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            logger.exception(
                "booked an event but could not record it; removing the event",
                extra={"tenant_id": str(tenant.id), "provider": calendar.provider.value},
            )
            try:
                await calendar.cancel_event(event_id)
            except Exception:
                logger.exception(
                    "could not remove an unrecorded event; the owner's calendar holds it",
                    extra={"tenant_id": str(tenant.id), "provider": calendar.provider.value},
                )
            return {
                **base,
                "result": Result.CALENDAR_UNAVAILABLE.value,
                "message": _FALLBACK_MESSAGE,
            }

        logger.info(
            "appointment booked",
            extra={
                "tenant_id": str(tenant.id),
                "appointment_id": str(appointment.id),
                "provider": calendar.provider.value,
            },
        )
        local_start = start.astimezone(zone)
        return {
            **base,
            "result": Result.BOOKED.value,
            "appointment": {
                **_slot(local_start, with_day=True),
                "duration_minutes": self.settings.booking_slot_minutes,
            },
            "message": f"Booked for {spoken_day(local_start.date())} at "
            f"{spoken_time(local_start)}. Confirm the day and time back to the caller.",
        }

    # ---- internals -------------------------------------------------------
    def _base(self, today: date, zone: ZoneInfo) -> dict[str, Any]:
        return {"today": f"{spoken_day(today)} {today.year}", "timezone": zone.key}

    async def _profile(self, tenant: Tenant) -> BusinessProfile | None:
        return (
            await self.session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == tenant.id)
            )
        ).scalar_one_or_none()

    async def _open(self, tenant: Tenant) -> OpenCalendar | dict[str, Any]:
        """The connected calendar, or the result to return instead of using one."""
        provider = await self.integrations.booking_calendar(tenant)
        if provider is None:
            return {"result": Result.CALENDAR_UNAVAILABLE.value, "message": _FALLBACK_MESSAGE}
        try:
            return await self.integrations.open_calendar(tenant, provider)
        except AppError as exc:
            logger.warning(
                "the booking calendar could not be opened",
                extra={"tenant_id": str(tenant.id), "provider": provider.value, "code": exc.code},
            )
            return {"result": Result.CALENDAR_UNAVAILABLE.value, "message": _FALLBACK_MESSAGE}

    async def _busy(
        self, tenant: Tenant, calendar: OpenCalendar, *, start: datetime, end: datetime
    ) -> list[BusyInterval] | dict[str, Any]:
        """The calendar's busy time plus our own bookings, or the result to return."""
        try:
            busy = await calendar.busy_intervals(start=start, end=end)
        except AppError as exc:
            logger.warning(
                "the booking calendar could not be read",
                extra={
                    "tenant_id": str(tenant.id),
                    "provider": calendar.provider.value,
                    "code": exc.code,
                },
            )
            return {"result": Result.CALENDAR_UNAVAILABLE.value, "message": _FALLBACK_MESSAGE}
        ours = (
            await self.session.execute(
                select(Appointment.starts_at, Appointment.ends_at).where(
                    Appointment.tenant_id == tenant.id,
                    Appointment.status == AppointmentStatus.BOOKED,
                    Appointment.starts_at < end,
                    Appointment.ends_at > start,
                )
            )
        ).all()
        return busy + [BusyInterval(start=row.starts_at, end=row.ends_at) for row in ours]

    def _bookable(
        self, start: datetime, window: _Window | None, busy: list[BusyInterval], earliest: datetime
    ) -> bool:
        end = start + self.duration
        return (
            window is not None
            and window.opens <= start
            and end <= window.closes
            and start >= earliest
            and not _overlaps(start, end, busy)
        )

    def _free(
        self, window: _Window | None, busy: list[BusyInterval], earliest: datetime
    ) -> list[datetime]:
        """Every bookable slot start in ``window``, on a grid from opening time."""
        if window is None:
            return []
        # Stepped in UTC so a day the clocks change on has no gap or repeat.
        slots: list[datetime] = []
        cursor = window.opens.astimezone(UTC)
        zone = window.opens.tzinfo
        closes = window.closes.astimezone(UTC)
        while cursor + self.duration <= closes:
            local = cursor.astimezone(zone)
            if self._bookable(local, window, busy, earliest):
                slots.append(local)
            cursor += self.duration
        return slots

    def _next_opening(
        self,
        hours: BusinessHours,
        after: date,
        last: date,
        zone: ZoneInfo,
        busy: list[BusyInterval],
        earliest: datetime,
    ) -> list[datetime]:
        day = after + timedelta(days=1)
        while day <= last:
            free = self._free(_window(hours, day, zone), busy, earliest)
            if free:
                return _spread(free, 3)
            day += timedelta(days=1)
        return []

    @staticmethod
    def _key(conversation_id: str | None, start: datetime, name: str, phone: str | None) -> str:
        """Same request, same key. Hex, so it doubles as a Google event id.

        Within one conversation the slot alone identifies the booking: the model
        re-spelling the caller's name must not make a second one. Without a
        conversation id the caller's details stand in for it.
        """
        moment = start.astimezone(UTC).isoformat()
        if conversation_id:
            material = f"conversation|{conversation_id}|{moment}"
        else:
            material = f"caller|{moment}|{name.casefold()}|{phone or ''}"
        return hashlib.sha256(material.encode()).hexdigest()

    async def _by_key(self, tenant: Tenant, key: str) -> Appointment | None:
        return (
            await self.session.execute(
                select(Appointment).where(
                    Appointment.tenant_id == tenant.id,
                    Appointment.idempotency_key == key,
                    Appointment.status == AppointmentStatus.BOOKED,
                )
            )
        ).scalar_one_or_none()

    def _already_booked(
        self, base: dict[str, Any], appointment: Appointment, zone: ZoneInfo
    ) -> dict[str, Any]:
        local_start = appointment.starts_at.astimezone(zone)
        return {
            **base,
            "result": Result.ALREADY_BOOKED.value,
            "appointment": {
                **_slot(local_start, with_day=True),
                "duration_minutes": int(
                    (appointment.ends_at - appointment.starts_at).total_seconds() // 60
                ),
            },
            "message": "This appointment is already booked. Confirm the day and time back "
            "to the caller; do not book it again.",
        }

    @staticmethod
    def _describe(name: str, phone: str | None, request: BookingRequest) -> str:
        lines = ["Booked by your AI receptionist during a phone call.", "", f"Caller: {name}"]
        if phone:
            lines.append(f"Phone: {phone}")
        if request.caller_email:
            lines.append(f"Email: {request.caller_email}")
        if request.reason:
            lines.append(f"Reason: {request.reason}")
        if request.conversation_id:
            lines.append(f"Conversation: {request.conversation_id}")
        return "\n".join(lines)
