"""Typed shapes for the JSONB columns on ``business_profiles``.

Postgres cannot enforce the shape of a JSONB document, so these Pydantic models
are the enforcement. They exist in Module 2 rather than alongside their first
caller because three later modules write these columns — the signup normalizer,
the LLM config generator and the config editor — and the failure this project
exists to prevent is exactly three writers quietly disagreeing about a shape
(docs/00_DECISIONS.md section 10).

The transcript column deliberately has no schema here: it holds a vendor payload
we do not yet own, and inventing a shape for it would be a guess.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_TIME_OF_DAY = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Weekday(StrEnum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


WEEK_ORDER: tuple[Weekday, ...] = tuple(Weekday)


class StrictModel(BaseModel):
    """Reject unknown keys.

    An LLM that invents a field, or a form that grows one, should fail loudly
    here rather than have it silently dropped on the way into the database.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class DaySchedule(StrictModel):
    """Opening hours for one day. ``closed`` days carry no times."""

    day: Weekday
    closed: bool = False
    #: 24-hour "HH:MM", local to the tenant's timezone.
    opens_at: str | None = Field(default=None, pattern=_TIME_OF_DAY.pattern)
    closes_at: str | None = Field(default=None, pattern=_TIME_OF_DAY.pattern)

    @model_validator(mode="after")
    def _check_times(self) -> Self:
        if self.closed:
            if self.opens_at or self.closes_at:
                raise ValueError("a closed day must not have opening times")
            return self
        if not self.opens_at or not self.closes_at:
            raise ValueError("an open day needs both opens_at and closes_at")
        if self.opens_at >= self.closes_at:
            # Overnight hours are a real thing but not a POC thing; failing is
            # better than silently telling a caller the salon shuts at 09:00.
            raise ValueError("closes_at must be later than opens_at")
        return self


class BusinessHours(StrictModel):
    """The normalized form of ``hours_raw``.

    Stored in ``business_profiles.hours_json`` and rendered back into prose by
    deterministic code for the prompt — the LLM normalizes once, at config time,
    and never at call time.
    """

    timezone: str = Field(min_length=1, max_length=64)
    days: list[DaySchedule]

    @model_validator(mode="after")
    def _one_entry_per_day(self) -> Self:
        seen = [day.day for day in self.days]
        duplicates = {day for day in seen if seen.count(day) > 1}
        if duplicates:
            raise ValueError(f"duplicate days: {sorted(duplicates)}")
        return self

    def by_day(self) -> dict[Weekday, DaySchedule]:
        return {day.day: day for day in self.days}

    def is_complete(self) -> bool:
        """Whether every weekday was accounted for."""
        return set(self.by_day()) == set(WEEK_ORDER)


class EscalationMode(StrEnum):
    """What to do when a caller needs more than the AI can give."""

    TAKE_MESSAGE = "take_message"
    NOTIFY_OWNER = "notify_owner"
    TRANSFER = "transfer"


class EscalationRule(StrictModel):
    """One compiled rule.

    The LLM turns free text into these once, at config time; the runtime then
    evaluates them deterministically. Never let a model make the same decision
    ten thousand times when it can make it once
    (docs/00_DECISIONS.md section 3, item 5).
    """

    #: A short phrase matched against the caller's stated intent.
    when: str = Field(min_length=1, max_length=200)
    mode: EscalationMode


class EscalationPolicy(StrictModel):
    """The normalized form of ``escalation_raw``."""

    default_mode: EscalationMode = EscalationMode.TAKE_MESSAGE
    rules: list[EscalationRule] = Field(default_factory=list)
    notify_email: str | None = Field(default=None, max_length=320)
    notify_phone: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _notification_target_present(self) -> Self:
        needs_target = self.default_mode is EscalationMode.NOTIFY_OWNER or any(
            rule.mode is EscalationMode.NOTIFY_OWNER for rule in self.rules
        )
        if needs_target and not (self.notify_email or self.notify_phone):
            raise ValueError("notify_owner requires notify_email or notify_phone")
        return self


def dump_json_column(model: BaseModel) -> dict[str, Any]:
    """Serialize a schema for storage in a JSONB column.

    ``mode="json"`` so enums land as their string values and the round trip
    through the database is lossless.
    """
    return model.model_dump(mode="json")
