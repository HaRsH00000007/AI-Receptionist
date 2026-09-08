"""The Pydantic shapes guarding the JSONB columns."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.business import (
    BusinessHours,
    DaySchedule,
    EscalationMode,
    EscalationPolicy,
    EscalationRule,
    Weekday,
    dump_json_column,
)


def _day(day: Weekday = Weekday.MONDAY, **overrides: object) -> DaySchedule:
    values: dict[str, object] = {"day": day, "opens_at": "09:00", "closes_at": "18:00"}
    values.update(overrides)
    return DaySchedule(**values)


def test_open_day_needs_both_times() -> None:
    with pytest.raises(ValidationError):
        DaySchedule(day=Weekday.MONDAY, opens_at="09:00")


def test_closing_must_be_after_opening() -> None:
    """Otherwise a caller is told the salon shuts before it opens."""
    with pytest.raises(ValidationError):
        _day(opens_at="18:00", closes_at="09:00")


def test_closed_day_carries_no_times() -> None:
    with pytest.raises(ValidationError):
        DaySchedule(day=Weekday.SUNDAY, closed=True, opens_at="09:00", closes_at="12:00")


def test_closed_day_is_valid_on_its_own() -> None:
    assert DaySchedule(day=Weekday.SUNDAY, closed=True).closed is True


@pytest.mark.parametrize("bad", ["9:00", "25:00", "09:60", "0900", "morning", ""])
def test_times_must_be_24_hour(bad: str) -> None:
    with pytest.raises(ValidationError):
        _day(opens_at=bad)


def test_unknown_fields_are_rejected() -> None:
    """An LLM inventing a field should fail loudly, not have it dropped."""
    with pytest.raises(ValidationError):
        DaySchedule(day=Weekday.MONDAY, closed=True, note="by appointment")  # type: ignore[call-arg]


def test_duplicate_days_are_rejected() -> None:
    with pytest.raises(ValidationError):
        BusinessHours(
            timezone="America/Los_Angeles",
            days=[_day(Weekday.MONDAY), _day(Weekday.MONDAY, opens_at="10:00")],
        )


def test_hours_report_completeness() -> None:
    partial = BusinessHours(timezone="America/Los_Angeles", days=[_day(Weekday.MONDAY)])
    assert partial.is_complete() is False

    full = BusinessHours(
        timezone="America/Los_Angeles",
        days=[_day(weekday) for weekday in Weekday],
    )
    assert full.is_complete() is True
    assert set(full.by_day()) == set(Weekday)


def test_notify_owner_requires_somewhere_to_notify() -> None:
    """A rule that escalates nowhere is worse than no rule."""
    with pytest.raises(ValidationError):
        EscalationPolicy(default_mode=EscalationMode.NOTIFY_OWNER)

    with pytest.raises(ValidationError):
        EscalationPolicy(rules=[EscalationRule(when="emergency", mode=EscalationMode.NOTIFY_OWNER)])


def test_notify_owner_accepts_either_channel() -> None:
    policy = EscalationPolicy(
        default_mode=EscalationMode.NOTIFY_OWNER, notify_email="owner@salon.example.com"
    )
    assert policy.notify_email == "owner@salon.example.com"


def test_take_message_needs_no_target() -> None:
    assert EscalationPolicy().default_mode is EscalationMode.TAKE_MESSAGE


def test_dump_is_json_safe_and_round_trips() -> None:
    """What goes into JSONB must come back out as the same object."""
    hours = BusinessHours(
        timezone="America/Los_Angeles",
        days=[_day(Weekday.MONDAY), DaySchedule(day=Weekday.SUNDAY, closed=True)],
    )
    payload = dump_json_column(hours)

    assert payload["days"][0]["day"] == "monday"
    assert isinstance(payload["days"][0]["day"], str)
    assert BusinessHours.model_validate(payload) == hours


def test_policy_round_trips() -> None:
    policy = EscalationPolicy(
        default_mode=EscalationMode.TAKE_MESSAGE,
        rules=[EscalationRule(when="burst pipe", mode=EscalationMode.TRANSFER)],
        notify_phone="+15551234567",
    )
    assert EscalationPolicy.model_validate(dump_json_column(policy)) == policy


def test_schemas_are_immutable() -> None:
    """A config that can be mutated after validation is a config nobody trusts.

    mypy rejects the assignment below too, which is the point; the ignore keeps
    the runtime half of the guarantee tested.
    """
    day = _day()
    with pytest.raises(ValidationError):
        day.opens_at = "10:00"  # type: ignore[misc]
