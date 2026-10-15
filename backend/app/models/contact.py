"""A person a business keeps on file.

Until now "contacts" were derived from the call history — a read-only view
grouped by caller number. That was honest but could not be edited, and a
business needs to correct a name, write a note, or block a nuisance caller.

So a contact is now a record of its own, with one rule that keeps it consistent
with the calls it came from: **the number is the identity**. The unique
constraint on ``(tenant_id, phone_e164)`` is what makes capturing a contact from
a finished call safe to repeat — a redelivered webhook collides rather than
inserting a second row — and what stops a CSV import quietly duplicating a
customer that already exists.

Call statistics are deliberately *not* stored here. Call count, first call and
last call are read from ``calls`` when the list is served, so the two can never
disagree, and deleting a contact never deletes history.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ContactSource, ContactStatus, enum_column


class Contact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One caller a tenant keeps on file."""

    __tablename__ = "contacts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    #: E.164, normalized before it is written. The identity of the contact.
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)

    #: Null when nobody has given a name yet. A caller whose name we never
    #: learned is still worth keeping: the number is the useful part, and the
    #: UI shows "Unknown caller" rather than inventing one.
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: Whatever the business wants to remember. Theirs, never ours: nothing
    #: automatic writes here, so a note is never overwritten by a later call.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ContactStatus] = mapped_column(
        enum_column(ContactStatus, "contact_status"),
        nullable=False,
        default=ContactStatus.ACTIVE,
    )

    #: How the contact first arrived. Kept because it decides what may be
    #: overwritten automatically: a name typed by a person outranks a name a
    #: caller claimed on the phone.
    source: Mapped[ContactSource] = mapped_column(
        enum_column(ContactSource, "contact_source"),
        nullable=False,
        default=ContactSource.MANUAL,
    )

    blocked_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        # One row per number per tenant. This is the duplicate guard for the
        # call-capture path and the CSV import both: concurrent inserts lose to
        # the constraint rather than to a check-then-insert race.
        UniqueConstraint("tenant_id", "phone_e164", name="uq_contacts_tenant_phone"),
        # The list query: a tenant's contacts, filtered by status, newest first.
        Index("ix_contacts_tenant_status_created", "tenant_id", "status", "created_at"),
    )
