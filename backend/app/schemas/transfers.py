"""Read and write models for the Transfer calls section.

Everything a customer sends about a transfer rule passes through here, and two
of these validators are the reason the feature cannot be used to dial anything
the product does not mean to dial:

* the destination is normalized by the same NANP normalizer the rest of the
  product uses, so a rule can only ever hold a US number in E.164 — not an
  extension, not a premium-rate string, not a SIP URI;
* the condition is trimmed to one bounded paragraph, because it is the text a
  model reads at call time.

No vendor identifier appears in any shape here. A customer never sees, sets or
learns the ElevenLabs tool configuration their rules produce.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import TransferType

#: Long enough to say "anything about billing, invoices or payments", short
#: enough that it cannot become a second system prompt smuggled in through a
#: field the agent reads.
CONDITION_MAX_LENGTH = 300


def _normalized_number(value: str) -> str:
    """A US number in E.164, or a 422 naming the field."""
    from app.core.errors import InvalidInputError
    from app.services.normalization import normalize_phone

    try:
        return normalize_phone(value)
    except InvalidInputError as exc:
        raise ValueError(exc.message) from exc


class TransferRuleInput(BaseModel):
    """A new rule."""

    model_config = ConfigDict(extra="forbid")

    #: Any shape a person types; stored in E.164.
    destination_number: str = Field(min_length=1, max_length=32)
    condition: str = Field(min_length=1, max_length=CONDITION_MAX_LENGTH)
    label: str = Field(default="", max_length=80)
    transfer_type: TransferType = TransferType.WARM
    enabled: bool = True

    @field_validator("destination_number")
    @classmethod
    def _destination_is_a_us_number(cls, value: str) -> str:
        return _normalized_number(value)

    @field_validator("condition", "label")
    @classmethod
    def _one_line_of_text(cls, value: str) -> str:
        """Collapse whitespace.

        The condition is sent to the vendor as a single condition string and
        the label is shown in a list; a newline in either is at best noise and
        at worst an attempt to add a second instruction.
        """
        return " ".join(value.split())

    @field_validator("condition")
    @classmethod
    def _condition_says_something(cls, value: str) -> str:
        """Checked *after* the whitespace is collapsed.

        ``min_length`` is satisfied by three spaces, and a condition that
        collapses to nothing is the dangerous one: the vendor's model would be
        given a destination with no rule for when to use it, and every call
        becomes a candidate for forwarding.
        """
        if not value:
            raise ValueError("say when this rule should transfer a call")
        return value


class TransferRuleUpdate(BaseModel):
    """An edit. An omitted field is left exactly as it was."""

    model_config = ConfigDict(extra="forbid")

    destination_number: str | None = Field(default=None, min_length=1, max_length=32)
    condition: str | None = Field(default=None, min_length=1, max_length=CONDITION_MAX_LENGTH)
    #: ``""`` clears the label; ``None`` leaves it alone.
    label: str | None = Field(default=None, max_length=80)
    transfer_type: TransferType | None = None
    enabled: bool | None = None

    @field_validator("destination_number")
    @classmethod
    def _destination_is_a_us_number(cls, value: str | None) -> str | None:
        return None if value is None else _normalized_number(value)

    @field_validator("condition", "label")
    @classmethod
    def _one_line_of_text(cls, value: str | None) -> str | None:
        return None if value is None else " ".join(value.split())

    @field_validator("condition")
    @classmethod
    def _condition_says_something(cls, value: str | None) -> str | None:
        """Same reason as on the create shape: blanking a condition would leave
        a destination with no rule for when to use it."""
        if value is not None and not value:
            raise ValueError("say when this rule should transfer a call")
        return value


class TransferRuleView(BaseModel):
    """One rule, as its business sees it."""

    id: uuid.UUID
    label: str | None
    destination_e164: str
    condition: str
    transfer_type: TransferType
    enabled: bool
    position: int
    created_at: datetime


class TransferRulesView(BaseModel):
    """The rules, with the two facts the page needs to tell the truth.

    ``max_rules`` so the UI can stop offering "add" at the limit instead of
    letting the API refuse, and ``cold_transfer_available`` because a blind
    transfer needs the number to have been imported into ElevenLabs from Twilio
    natively. On a deployment whose calls are bridged through our own TwiML
    instead, only warm transfers work, and offering the other would be claiming
    a capability the call path does not have.
    """

    rules: list[TransferRuleView]
    max_rules: int
    cold_transfer_available: bool


class TransferRuleResult(TransferRulesView):
    """The rules after a change, and what publishing them did.

    ``synced`` is reported separately from success for the same reason it is on
    every other write in this product: the rule is saved in our database the
    moment this returns, and a vendor that was unreachable leaves its copy
    stale rather than undoing the change. Until it syncs, callers are handled by
    the previous configuration — which for a *deleted* rule means they may still
    be transferred, so the message says so rather than implying otherwise.
    """

    #: The version the change published, or ``None`` when there was nothing to
    #: publish to yet (a receptionist whose setup has not finished).
    config_version: int | None
    synced: bool
    detail: str
