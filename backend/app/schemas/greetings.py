"""Read and write models for scheduled greetings.

The validation here is what stops a rule that cannot mean anything from being
stored, and each check exists because the alternative is a caller hearing the
wrong thing:

* a weekly rule must say *when*, because a weekly rule with no window would
  claim every hour of its days and silently replace the usual greeting forever;
* a dated rule must say *which dates*, for the same reason;
* a rule cannot carry the other kind's fields, so "every Tuesday in December"
  is refused rather than stored as something that looks like it was accepted;
* the message is trimmed and bounded like the default opening line, because it
  is spoken to a caller in exactly the same place.

Times are minutes since local midnight rather than clock strings. The clock the
customer types is theirs; the stored value is arithmetic, and parsing "12:00 pm"
in two places is how the two places end up disagreeing.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import GreetingScheduleKind
from app.services.greeting_schedule import MINUTES_IN_DAY

#: The same bound the default opening line has (``custom_greeting``). A caller
#: hears this in the same breath, so it gets the same ceiling.
MESSAGE_MAX_LENGTH = 300

#: Monday is 0, matching ``date.weekday()``.
LAST_WEEKDAY = 6


class GreetingRuleInput(BaseModel):
    """A new rule.

    ``kind`` decides which of the two field groups is required; see the
    validator below for why that is checked here and not in the service.
    """

    model_config = ConfigDict(extra="forbid")

    kind: GreetingScheduleKind
    message: str = Field(min_length=1, max_length=MESSAGE_MAX_LENGTH)

    #: Weekly only. Empty means every day of the week.
    weekdays: list[int] = Field(default_factory=list)
    #: Minutes since local midnight. Both or neither.
    start_minute: int | None = Field(default=None, ge=0, lt=MINUTES_IN_DAY)
    end_minute: int | None = Field(default=None, ge=0, lt=MINUTES_IN_DAY)

    #: Dated only. ``end_date`` defaults to ``start_date`` — a single day.
    start_date: date | None = None
    end_date: date | None = None

    enabled: bool = True

    @field_validator("message")
    @classmethod
    def _one_line_of_speech(cls, value: str) -> str:
        """Collapse whitespace, then insist there is something left.

        ``min_length`` is satisfied by three spaces, and an empty greeting is
        the dangerous one: the window would match and the receptionist would
        answer with silence.
        """
        collapsed = " ".join(value.split())
        if not collapsed:
            raise ValueError("write what the receptionist should say")
        return collapsed

    @field_validator("weekdays")
    @classmethod
    def _real_weekdays(cls, value: list[int]) -> list[int]:
        for day in value:
            if not 0 <= day <= LAST_WEEKDAY:
                raise ValueError("a weekday is 0 (Monday) to 6 (Sunday)")
        # Sorted and de-duplicated so two requests that mean the same thing are
        # stored the same way, and the UI can compare them.
        return sorted(set(value))

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        _check_coherence(self)
        return self


class GreetingRuleUpdate(BaseModel):
    """An edit. An omitted field is left exactly as it was.

    ``kind`` is absent on purpose: a weekly rule cannot become a dated one.
    Changing which calendar a rule is read against changes every field on it,
    and "edit" would quietly mean "replace" — so the UI deletes and re-adds,
    which is what the owner is actually doing.
    """

    model_config = ConfigDict(extra="forbid")

    message: str | None = Field(default=None, min_length=1, max_length=MESSAGE_MAX_LENGTH)
    weekdays: list[int] | None = None
    #: ``None`` leaves the window alone. Clearing it means sending both as
    #: ``null`` explicitly, which only a dated rule may do.
    start_minute: int | None = Field(default=None, ge=0, lt=MINUTES_IN_DAY)
    end_minute: int | None = Field(default=None, ge=0, lt=MINUTES_IN_DAY)
    start_date: date | None = None
    end_date: date | None = None
    enabled: bool | None = None

    @field_validator("message")
    @classmethod
    def _one_line_of_speech(cls, value: str | None) -> str | None:
        if value is None:
            return None
        collapsed = " ".join(value.split())
        if not collapsed:
            raise ValueError("write what the receptionist should say")
        return collapsed

    @field_validator("weekdays")
    @classmethod
    def _real_weekdays(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        for day in value:
            if not 0 <= day <= LAST_WEEKDAY:
                raise ValueError("a weekday is 0 (Monday) to 6 (Sunday)")
        return sorted(set(value))

    @model_validator(mode="after")
    def _window_is_whole(self) -> Self:
        """One end of a window is never an edit.

        The fields are stored as a pair and read as a pair; accepting a new
        start without a new end would leave a window that means something
        nobody asked for. The rest of the coherence check happens in the
        service, against the rule as it will be once this is applied.
        """
        if (self.start_minute is None) != (self.end_minute is None):
            raise ValueError("send both the start and the end of the window, or neither")
        if self.end_date is not None and self.start_date is None:
            raise ValueError("send the first date as well as the last one")
        return self


class GreetingRuleView(BaseModel):
    """One rule, as its business sees it."""

    id: uuid.UUID
    kind: GreetingScheduleKind
    message: str
    weekdays: list[int]
    start_minute: int | None
    end_minute: int | None
    start_date: date | None
    end_date: date | None
    enabled: bool
    position: int
    created_at: datetime


class GreetingRulesView(BaseModel):
    """The rules, with what the page needs to describe them truthfully.

    ``timezone`` because every rule is read against the business's own clock
    and the page has to say which one that is — the same zone the opening hours
    are stored in, changed on this same settings page.

    ``default_greeting`` is the line callers hear when no rule matches, which is
    almost every call; showing the schedule without it would make the exceptions
    look like the whole story. ``active_message`` is what a call arriving *now*
    would be answered with, so the page can say which rule is in force instead
    of leaving the owner to work it out from a list of windows.
    """

    rules: list[GreetingRuleView]
    max_rules: int
    timezone: str
    default_greeting: str | None
    active_message: str | None


class GreetingRuleResult(GreetingRulesView):
    """The rules after a change, and what publishing them did.

    ``synced`` is separate from success for the reason it is everywhere else
    here: the rule is in our database the moment this returns, and a vendor that
    was unreachable holds a stale opening line until the next sync — so the
    message says which greeting callers are hearing meanwhile rather than
    implying the change is already audible.
    """

    config_version: int | None
    synced: bool
    detail: str


def _check_coherence(rule: GreetingRuleInput) -> None:
    """Each kind must carry its own fields and only its own.

    In the schema rather than the service because it is a property of the
    request, not of the business: a dated rule with weekdays on it is a client
    bug, and a 422 that names the field is a better answer than a stored rule
    that quietly ignores half of what was sent.
    """
    if rule.kind is GreetingScheduleKind.WEEKLY:
        if rule.start_date is not None or rule.end_date is not None:
            raise ValueError("a weekly rule repeats; it does not take dates")
        if rule.start_minute is None or rule.end_minute is None:
            raise ValueError("say what time the weekly greeting starts and ends")
        return

    if rule.weekdays:
        raise ValueError("a rule for specific dates does not take weekdays")
    if rule.start_date is None:
        raise ValueError("say which date the greeting applies to")
    end = rule.end_date or rule.start_date
    if end < rule.start_date:
        raise ValueError("the last date cannot be before the first one")
    if (rule.start_minute is None) != (rule.end_minute is None):
        raise ValueError("send both the start and the end of the window, or neither")


def window_fields(payload: GreetingRuleInput) -> dict[str, Any]:
    """The stored shape of a new rule's calendar fields.

    ``end_date`` is filled in from ``start_date`` so that every stored range is
    a real range; a null end would otherwise have to be re-interpreted as "the
    same day" everywhere it is read.
    """
    return {
        "weekdays_json": list(payload.weekdays) or None,
        "start_minute": payload.start_minute,
        "end_minute": payload.end_minute,
        "start_date": payload.start_date,
        "end_date": (payload.end_date or payload.start_date) if payload.start_date else None,
    }
