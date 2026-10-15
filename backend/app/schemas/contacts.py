"""Request and response models for contacts.

Phone numbers are validated by :func:`app.services.normalization.normalize_phone`
rather than by a pattern here, so the API, the CSV import and the call-capture
path all agree on what a number is and all store the same E.164 form.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ContactSource, ContactStatus


class ContactCreate(BaseModel):
    """Adding a contact by hand."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    phone_number: str = Field(min_length=7, max_length=32)
    full_name: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)


class ContactUpdate(BaseModel):
    """Editing one. Every field is optional; absent means "leave it alone".

    A field sent as an empty string clears it, which is how a note is removed.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    phone_number: str | None = Field(default=None, min_length=7, max_length=32)
    full_name: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


class ContactView(BaseModel):
    """A contact as the dashboard shows it.

    Call statistics come from the call history rather than from stored counters,
    so they cannot drift from the Calls page.
    """

    id: uuid.UUID
    phone_e164: str
    #: Null when nobody has given a name. The UI says "Unknown caller"; it does
    #: not invent one.
    full_name: str | None
    note: str | None
    status: ContactStatus
    source: ContactSource
    created_at: datetime
    blocked_at: datetime | None
    call_count: int
    first_call_at: datetime | None
    last_call_at: datetime | None


class ContactListView(BaseModel):
    """One page, plus both tab counts so the tabs need no extra request."""

    contacts: list[ContactView]
    total: int
    active_count: int
    blocked_count: int
    limit: int
    offset: int


class ContactImportError(BaseModel):
    """One row that did not import, and why."""

    row: int
    reason: str


class ContactImportResult(BaseModel):
    """What an import did. Nothing is dropped silently."""

    imported: int
    #: Already on file, and a gap was filled in from the file.
    updated: int
    #: Already on file with nothing to add, or repeated inside the file.
    skipped_duplicate: int
    invalid: int
    errors: list[ContactImportError]
