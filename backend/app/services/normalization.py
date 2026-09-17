"""Deterministic normalization of what the owner typed on the form.

None of this is a language task, so none of it goes near a model. Phone numbers,
emails and area codes have exact rules; opening hours have common patterns that
parse cleanly and a long tail that does not. The parser here handles the
patterns and *declines* the rest by returning ``None``, at which point the
config-generation step asks the LLM — which is the only part that genuinely
needs one.
"""

from __future__ import annotations

import re
import unicodedata

from app.core.errors import InvalidInputError
from app.data.area_codes import is_known_area_code
from app.schemas.business import BusinessHours, DaySchedule, Weekday


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def normalize_email(value: str) -> str:
    """Lower-case and trim.

    The partial unique index on ``tenants.contact_email`` compares raw values,
    so normalizing here is what makes "Owner@Salon.com" and "owner@salon.com"
    the same signup rather than two numbers bought.
    """
    return value.strip().lower()


# ---------------------------------------------------------------------------
# Greeting
# ---------------------------------------------------------------------------
_GREETING_WHITESPACE = re.compile(r"\s+")


def normalize_greeting(value: str) -> str:
    """One speakable line, or empty when the owner wants us to write it.

    A greeting is read aloud, so a line break means nothing to a caller, and an
    invisible control character can confuse a vendor's speech synthesis. Every
    run of whitespace becomes one space and control characters are dropped —
    which also stops a greeting smuggling formatting into the vendor payload.

    Whitespace is collapsed *before* control characters are removed, so
    "Hello\\nthere" stays two words rather than becoming one.
    """
    collapsed = _GREETING_WHITESPACE.sub(" ", value)
    printable = "".join(
        character for character in collapsed if not unicodedata.category(character).startswith("C")
    )
    return printable.strip()


# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------
_DIGITS = re.compile(r"\D")


def normalize_phone(value: str) -> str:
    """Return a US number in E.164, or raise.

    Scope is deliberately NANP-only: the POC buys US numbers, and accepting an
    international format we cannot provision would fail much later, in the
    purchase step, where the error is far less clear.
    """
    raw = value.strip()
    digits = _DIGITS.sub("", raw)

    if raw.startswith("+"):
        if len(digits) == 11 and digits.startswith("1"):
            return f"+{digits}"
        raise InvalidInputError(
            "only US (+1) phone numbers are supported in the POC",
            details={"field": "contact_phone"},
        )

    if len(digits) == 10:
        candidate = digits
    elif len(digits) == 11 and digits.startswith("1"):
        candidate = digits[1:]
    else:
        raise InvalidInputError(
            "phone number must have 10 digits",
            details={"field": "contact_phone"},
        )

    # NANP forbids 0 and 1 as the first digit of an area code or exchange.
    if candidate[0] in "01" or candidate[3] in "01":
        raise InvalidInputError(
            "not a valid US phone number",
            details={"field": "contact_phone"},
        )
    return f"+1{candidate}"


def area_code_of(e164: str) -> str:
    """The area code of a normalized US number."""
    return e164[2:5]


# ---------------------------------------------------------------------------
# Area code
# ---------------------------------------------------------------------------
def normalize_area_code(value: str) -> str:
    code = _DIGITS.sub("", value.strip())
    if len(code) != 3:
        raise InvalidInputError(
            "area code must be exactly 3 digits", details={"field": "area_code"}
        )
    if code[0] in "01":
        raise InvalidInputError(
            "an area code cannot start with 0 or 1", details={"field": "area_code"}
        )
    if not is_known_area_code(code):
        raise InvalidInputError(
            "unknown US area code",
            details={"field": "area_code", "area_code": code},
        )
    return code


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------
_SERVICE_SPLIT = re.compile(r"[,;\n]+")


def normalize_services(value: str | list[str]) -> list[str]:
    """Split free text into a list, preserving the owner's wording.

    Order is kept and duplicates removed case-insensitively, so the same form
    always yields the same list — which matters because the list is part of the
    prompt, and an unstable prompt means an unstable config.
    """
    parts = value if isinstance(value, list) else _SERVICE_SPLIT.split(value)
    seen: set[str] = set()
    services: list[str] = []
    for part in parts:
        cleaned = " ".join(part.split())
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            services.append(cleaned)
    return services


# ---------------------------------------------------------------------------
# Opening hours
# ---------------------------------------------------------------------------
_DAY_ALIASES: dict[str, Weekday] = {
    "mon": Weekday.MONDAY,
    "monday": Weekday.MONDAY,
    "tue": Weekday.TUESDAY,
    "tues": Weekday.TUESDAY,
    "tuesday": Weekday.TUESDAY,
    "wed": Weekday.WEDNESDAY,
    "weds": Weekday.WEDNESDAY,
    "wednesday": Weekday.WEDNESDAY,
    "thu": Weekday.THURSDAY,
    "thur": Weekday.THURSDAY,
    "thurs": Weekday.THURSDAY,
    "thursday": Weekday.THURSDAY,
    "fri": Weekday.FRIDAY,
    "friday": Weekday.FRIDAY,
    "sat": Weekday.SATURDAY,
    "saturday": Weekday.SATURDAY,
    "sun": Weekday.SUNDAY,
    "sunday": Weekday.SUNDAY,
}

_WEEK: tuple[Weekday, ...] = tuple(Weekday)

_SEGMENT_SPLIT = re.compile(r"[,;\n]+")
_DAY_TOKEN = (
    r"(mon|monday|tues?|tuesday|weds?|wednesday|thur?s?|thursday"
    r"|fri|friday|sat|saturday|sun|sunday)"
)
# \u2013 is an en dash, written escaped so it is unmistakable in source. It is
# accepted deliberately: people paste hours from documents that autocorrected
# the hyphen, and refusing those strings would be the worse bug.
_DASH = r"(?:-|\u2013|\u2014)"
_DAY_RANGE = re.compile(
    rf"{_DAY_TOKEN}\s*(?:{_DASH}|to|through|thru)\s*{_DAY_TOKEN}", re.IGNORECASE
)
_SINGLE_DAY = re.compile(_DAY_TOKEN, re.IGNORECASE)
_TIME = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
_TIME_RANGE = re.compile(rf"{_TIME}\s*(?:{_DASH}|to|till|until|til)\s*{_TIME}", re.IGNORECASE)
_CLOSING_ONLY = re.compile(rf"(?:till|until|til)\s*{_TIME}", re.IGNORECASE)


def _to_24h(hour_text: str, minute_text: str | None, meridiem: str | None) -> str:
    """Resolve an hour to 24-hour time.

    Without an am/pm marker the rule is fixed and documented rather than clever:
    7-11 is morning, 12 is noon, 1-6 is afternoon, and anything else is already
    24-hour. That reads "9-6" as 09:00-18:00, which is what a salon means.
    """
    hour = int(hour_text)
    minute = int(minute_text) if minute_text else 0
    if minute > 59:
        raise ValueError("minute out of range")

    if meridiem:
        marker = meridiem.lower()
        if hour < 1 or hour > 12:
            raise ValueError("hour out of range for a 12-hour clock")
        # 12am is midnight and 12pm is noon; every other hour just shifts.
        # Spelled out rather than nested ternaries: this is the rule people
        # get wrong, and it should be readable at a glance.
        if marker == "am":  # noqa: SIM108
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    elif 1 <= hour <= 6:
        hour += 12
    elif hour > 23:
        raise ValueError("hour out of range")

    return f"{hour:02d}:{minute:02d}"


def _days_in(segment: str) -> list[Weekday]:
    match = _DAY_RANGE.search(segment)
    if match:
        start = _DAY_ALIASES[match.group(1).lower()]
        end = _DAY_ALIASES[match.group(2).lower()]
        start_index, end_index = _WEEK.index(start), _WEEK.index(end)
        if start_index <= end_index:
            return list(_WEEK[start_index : end_index + 1])
        # Wraps the weekend, e.g. "Sat-Mon".
        return list(_WEEK[start_index:] + _WEEK[: end_index + 1])

    found = [_DAY_ALIASES[token.lower()] for token in _SINGLE_DAY.findall(segment)]
    # Keep declaration order, drop repeats.
    return list(dict.fromkeys(found))


def parse_opening_hours(raw: str, *, timezone: str) -> BusinessHours | None:
    """Best-effort parse of a free-text hours string.

    Returns ``None`` rather than guessing when the text does not match a known
    pattern; the caller then leaves normalization to the LLM. A wrong answer
    here would be worse than no answer, because it would tell callers the
    business is open when it is shut.
    """
    text = raw.strip()
    if not text:
        return None

    if re.fullmatch(r"24\s*/\s*7|24x7|always open|open 24 hours", text, re.IGNORECASE):
        return BusinessHours(
            timezone=timezone,
            days=[DaySchedule(day=day, opens_at="00:00", closes_at="23:59") for day in _WEEK],
        )

    schedules: dict[Weekday, DaySchedule] = {}
    #: "Sat till 2" has no opening time of its own; it inherits the one from the
    #: segment before it, which is how people actually write these strings.
    previous_open: str | None = None
    understood_every_segment = True

    for segment in _SEGMENT_SPLIT.split(text):
        chunk = segment.strip()
        if not chunk:
            continue

        days = _days_in(chunk)
        if not days:
            understood_every_segment = False
            continue

        if re.search(r"closed", chunk, re.IGNORECASE):
            for day in days:
                schedules[day] = DaySchedule(day=day, closed=True)
            continue

        try:
            range_match = _TIME_RANGE.search(chunk)
            if range_match:
                opens = _to_24h(range_match.group(1), range_match.group(2), range_match.group(3))
                closes = _to_24h(range_match.group(4), range_match.group(5), range_match.group(6))
            else:
                # "Sat till 2" inherits the previous segment's opening time.
                closing_match = _CLOSING_ONLY.search(chunk)
                if not closing_match or previous_open is None:
                    understood_every_segment = False
                    continue
                opens = previous_open
                closes = _to_24h(
                    closing_match.group(1), closing_match.group(2), closing_match.group(3)
                )
        except ValueError:
            understood_every_segment = False
            continue

        if opens >= closes:
            understood_every_segment = False
            continue

        for day in days:
            schedules[day] = DaySchedule(day=day, opens_at=opens, closes_at=closes)
        previous_open = opens

    if not schedules:
        return None

    # A partial parse is still useful, but only if nothing was misread.
    if not understood_every_segment:
        return None

    return BusinessHours(
        timezone=timezone, days=[schedules[day] for day in _WEEK if day in schedules]
    )
