"""When the receptionist should stop talking and fetch a person.

One row is one rule: *if the caller mentions this, ring that number*. Rules are
a table rather than a column on the profile for three reasons that all showed
up in the first sketch of this feature:

* a business has several of them (billing goes to the office, emergencies go to
  the owner's mobile), and a JSON list would have to be rewritten whole to edit
  one of them;
* each one is edited, disabled and deleted on its own, and the audit trail
  should say *which* rule changed;
* the destination is a phone number — the one piece of configuration in this
  product that can cost money and reach a real person's pocket — so it gets a
  row, a tenant scope and a foreign key, not a position in an array.

``condition`` is the whole mechanism, not decoration. It is sent to ElevenLabs
as the condition on the transfer destination, and the model decides from it
whether this call is one of the ones that should be handed over. It is never
rendered into the system prompt: a prompt that recited the destinations would
let a caller talk the receptionist into reading a private mobile number out.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import TransferType, enum_column


class TransferRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One "transfer this kind of call to this number" rule."""

    __tablename__ = "transfer_rules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    #: What the business calls this rule — "Front desk", "Dr. Patel". Shown in
    #: their own list and nowhere else; the caller never hears it.
    label: Mapped[str | None] = mapped_column(String(80), nullable=True)

    #: Where the call goes, E.164, normalized before it is written.
    #:
    #: Not unique per tenant on purpose: one number legitimately serves several
    #: conditions ("billing" and "after hours" both ring the office).
    destination_e164: Mapped[str] = mapped_column(String(20), nullable=False)

    #: When to use this rule, in the business's own words. Read by the model at
    #: call time; see the module docstring for why it never reaches the prompt.
    condition: Mapped[str] = mapped_column(Text, nullable=False)

    transfer_type: Mapped[TransferType] = mapped_column(
        enum_column(TransferType, "transfer_type"),
        nullable=False,
        default=TransferType.WARM,
    )

    #: A rule that is off stays on file but is not sent to the vendor, so a
    #: business can stop forwarding over a holiday without retyping a number.
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    #: Display order, assigned as "one past the last" when a rule is added.
    #: Not unique: two rules sharing a position sort by ``created_at`` after it,
    #: which is a cosmetic tie, and a UNIQUE constraint here would turn a
    #: reorder into a dance around it.
    position: Mapped[int] = mapped_column(nullable=False, default=0)

    __table_args__ = (
        # The list query, and the only query this table has: a tenant's rules
        # in display order.
        Index("ix_transfer_rules_tenant_position", "tenant_id", "position", "created_at"),
    )
