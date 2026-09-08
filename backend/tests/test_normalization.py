"""Deterministic normalization.

No database, no network. These are the rules that decide whether two signups are
the same business and which state a number gets bought in, so they are worth
pinning exactly.
"""

from __future__ import annotations

import pytest

from app.core.errors import InvalidInputError
from app.data.area_codes import area_codes_for_region, is_known_area_code, lookup
from app.schemas.business import Weekday
from app.services.normalization import (
    area_code_of,
    normalize_area_code,
    normalize_email,
    normalize_phone,
    normalize_services,
    parse_opening_hours,
)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Owner@Salon.COM", "owner@salon.com"),
        ("  owner@salon.com  ", "owner@salon.com"),
        ("OWNER@SALON.COM", "owner@salon.com"),
    ],
)
def test_emails_normalize_to_one_form(raw: str, expected: str) -> None:
    """Two spellings of one address must not buy two phone numbers."""
    assert normalize_email(raw) == expected


# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    ["8055550142", "805-555-0142", "(805) 555-0142", "+1 805 555 0142", "1-805-555-0142"],
)
def test_us_numbers_reach_e164(raw: str) -> None:
    assert normalize_phone(raw) == "+18055550142"


@pytest.mark.parametrize(
    "raw",
    [
        "123",  # too short
        "80555501421234",  # too long
        "+44 20 7946 0958",  # not a US number
        "0055550142",  # area code cannot start with 0
        "8051550142",  # exchange cannot start with 1
        "",
    ],
)
def test_bad_numbers_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidInputError):
        normalize_phone(raw)


def test_area_code_read_from_a_normalized_number() -> None:
    assert area_code_of("+18055550142") == "805"


# ---------------------------------------------------------------------------
# Area code
# ---------------------------------------------------------------------------
def test_area_code_accepts_a_known_code() -> None:
    assert normalize_area_code(" 805 ") == "805"


@pytest.mark.parametrize("raw", ["80", "8055", "055", "199", "999"])
def test_area_code_rejects_bad_input(raw: str) -> None:
    """999 is syntactically fine but is not an assigned code; both must fail."""
    with pytest.raises(InvalidInputError):
        normalize_area_code(raw)


def test_area_code_resolves_state_and_timezone() -> None:
    assert lookup("805").state == "CA"
    assert lookup("805").timezone == "America/Los_Angeles"
    assert lookup("212").timezone == "America/New_York"


def test_arizona_is_not_given_daylight_saving() -> None:
    """Phoenix does not observe DST; a generic Mountain zone would be wrong."""
    assert lookup("602").timezone == "America/Phoenix"


def test_unknown_area_code_falls_back_rather_than_raising() -> None:
    """A gap in a reference table must not block a valid signup."""
    info = lookup("321654")
    assert info.state is None
    assert info.timezone == "America/Los_Angeles"


def test_toll_free_codes_belong_to_no_state() -> None:
    assert lookup("833").is_toll_free is True
    assert lookup("833").state is None
    assert is_known_area_code("833")


def test_same_state_fallback_has_codes_to_offer() -> None:
    codes = area_codes_for_region("CA")
    assert "805" in codes and "415" in codes
    assert len(codes) > 10


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------
def test_services_split_and_deduplicate_preserving_order() -> None:
    assert normalize_services("cuts, color; walk-ins , Cuts") == ["cuts", "color", "walk-ins"]


def test_services_accept_an_already_split_list() -> None:
    assert normalize_services(["cuts", " color "]) == ["cuts", "color"]


def test_services_collapse_internal_whitespace() -> None:
    assert normalize_services("hair   colouring") == ["hair colouring"]


# ---------------------------------------------------------------------------
# Opening hours
# ---------------------------------------------------------------------------
def test_the_plans_own_example_parses() -> None:
    """ "Mon-Fri 9-6, Sat till 2, closed Sun" - docs/00_DECISIONS.md."""
    hours = parse_opening_hours(
        "Mon-Fri 9-6, Sat till 2, closed Sun", timezone="America/Los_Angeles"
    )
    assert hours is not None

    by_day = hours.by_day()
    assert by_day[Weekday.MONDAY].opens_at == "09:00"
    assert by_day[Weekday.MONDAY].closes_at == "18:00"
    assert by_day[Weekday.SATURDAY].closes_at == "14:00"
    assert by_day[Weekday.SUNDAY].closed is True
    assert hours.is_complete()


def test_bare_hours_resolve_to_business_time_of_day() -> None:
    """ "9-6" means 9am to 6pm, not 9am to 6am."""
    hours = parse_opening_hours("Mon 9-6", timezone="UTC")
    assert hours is not None
    assert (hours.days[0].opens_at, hours.days[0].closes_at) == ("09:00", "18:00")


@pytest.mark.parametrize(
    ("raw", "opens", "closes"),
    [
        ("Mon 9am-5pm", "09:00", "17:00"),
        ("Mon 09:30-17:45", "09:30", "17:45"),
        ("Mon 12pm-8pm", "12:00", "20:00"),
        ("Mon 8:00 to 16:00", "08:00", "16:00"),
    ],
)
def test_time_formats(raw: str, opens: str, closes: str) -> None:
    hours = parse_opening_hours(raw, timezone="UTC")
    assert hours is not None
    assert (hours.days[0].opens_at, hours.days[0].closes_at) == (opens, closes)


def test_en_dash_is_accepted() -> None:
    """Autocorrected hyphens are common in pasted text."""
    # chr() rather than a literal: the character is the point of the test,
    # and writing it inline is indistinguishable from a typo.
    en_dash = chr(0x2013)
    hours = parse_opening_hours(f"Mon{en_dash}Fri 9am{en_dash}5pm", timezone="UTC")
    assert hours is not None
    assert len(hours.days) == 5


@pytest.mark.parametrize(
    "raw",
    [
        "whenever we feel like it",
        "by appointment only",
        "Mon 6pm-9am",  # closes before it opens
        "",
        "Mon 25:00-30:00",
    ],
)
def test_unparseable_hours_decline_rather_than_guess(raw: str) -> None:
    """A wrong answer here tells callers the shop is open when it is shut.

    Returning None hands the job to the LLM, which is the part that can read
    free text.
    """
    assert parse_opening_hours(raw, timezone="UTC") is None


def test_a_partially_understood_string_is_declined_whole() -> None:
    """Half-right hours are more dangerous than no hours."""
    assert parse_opening_hours("Mon 9-5, whenever on Saturdays", timezone="UTC") is None


def test_always_open() -> None:
    hours = parse_opening_hours("24/7", timezone="UTC")
    assert hours is not None
    assert hours.is_complete()
    assert all(not day.closed for day in hours.days)
