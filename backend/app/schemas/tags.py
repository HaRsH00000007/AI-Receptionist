"""Read and write models for call tags.

The description is the one field worth thinking about. It is not documentation:
it is the sentence a model reads to decide whether a call is one of these, so it
is trimmed, bounded, and required to say *something* — a tag with an empty
description would be offered to the classifier with no rule for when to apply it,
and every call would become a candidate.

It is bounded at a paragraph for the same reason the transfer condition is: the
tags of one business are concatenated into a single prompt, and a description
long enough to be a second system prompt is a description that crowds out the
other tags.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import TagColor

#: Long enough for "caller asks about pricing, invoices, refunds or credits",
#: short enough that twenty-five of them still fit in one prompt.
DESCRIPTION_MAX_LENGTH = 300

#: A label on a call, not a sentence.
NAME_MAX_LENGTH = 40


def _one_line(value: str) -> str:
    """Collapse whitespace.

    Both fields end up in a prompt and in a list; a newline in either is at best
    noise and at worst an attempt to add a second instruction.
    """
    return " ".join(value.split())


class CallTagInput(BaseModel):
    """A new tag."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    description: str = Field(min_length=1, max_length=DESCRIPTION_MAX_LENGTH)
    color: TagColor = TagColor.SLATE
    enabled: bool = True

    @field_validator("name", "description")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return _one_line(value)

    @field_validator("name")
    @classmethod
    def _name_says_something(cls, value: str) -> str:
        """Checked after the whitespace is collapsed: ``min_length`` is
        satisfied by three spaces."""
        if not value:
            raise ValueError("give the tag a name")
        return value

    @field_validator("description")
    @classmethod
    def _description_says_when(cls, value: str) -> str:
        """The dangerous blank.

        A tag with no description is handed to the classifier with no rule for
        when it applies, and every call becomes a candidate for it.
        """
        if not value:
            raise ValueError("say when your receptionist should apply this tag")
        return value


class CallTagUpdate(BaseModel):
    """An edit. An omitted field is left exactly as it was."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    description: str | None = Field(default=None, min_length=1, max_length=DESCRIPTION_MAX_LENGTH)
    color: TagColor | None = None
    enabled: bool | None = None

    @field_validator("name", "description")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        return None if value is None else _one_line(value)

    @field_validator("name")
    @classmethod
    def _name_says_something(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("give the tag a name")
        return value

    @field_validator("description")
    @classmethod
    def _description_says_when(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("say when your receptionist should apply this tag")
        return value


class CallTagView(BaseModel):
    """One tag, as its business sees it."""

    id: uuid.UUID
    name: str
    description: str
    color: TagColor
    enabled: bool
    position: int
    created_at: datetime


class CallTagsView(BaseModel):
    """The tags, with the two facts the page needs.

    ``max_tags`` so the UI can stop offering "add" at the limit rather than
    letting the API refuse, and ``active`` so the "5 of 25 active" line counts
    the same thing the limit counts.
    """

    tags: list[CallTagView]
    max_tags: int
    active: int


class CallTagLabel(BaseModel):
    """A tag as it appears *on a call*.

    Deliberately thinner than :class:`CallTagView`: a call shows the label, not
    the rule behind it, and the description is the business's own configuration
    rather than something to repeat on every row of a list.
    """

    id: uuid.UUID
    name: str
    color: TagColor
