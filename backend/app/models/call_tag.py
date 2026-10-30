"""Labels a business puts on its calls, and which calls got them.

Two tables, because a tag and the act of applying one have different lifetimes
and different owners:

* :class:`CallTag` is configuration. The business writes it, it belongs to the
  tenant, and its ``description`` is the whole mechanism — that sentence is what
  the model reads to decide whether a call is one of these.
* :class:`CallTagAssignment` is what the classifier concluded about one call. It
  is a join row and nothing more: no scores, no reasoning, no second copy of the
  tag's name. The name lives in one place so renaming a tag renames it
  everywhere, including on calls from last month.

**Deleting a tag deletes its history.** The assignment's foreign key cascades,
so removing "Pricing" removes it from every call it was ever applied to. That is
the honest reading of a delete button and it is why *disabling* exists beside
it: a disabled tag stays on the calls it already marked and is never applied
again. Both behaviours are tested, because the difference is the kind of thing a
customer discovers at the worst moment.

**The assignment's primary key is the pair.** A retried classification writes
the same (call, tag) rows it wrote before, so the retry is idempotent by
construction rather than by a check the next person could forget.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import TagColor, enum_column


class CallTag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One label a business wants its calls sorted by."""

    __tablename__ = "call_tags"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    #: What the business calls it: "Pricing", "Urgent". Shown on the call.
    name: Mapped[str] = mapped_column(String(40), nullable=False)

    #: When the receptionist should apply it, in the business's own words.
    #:
    #: Read by the model at classification time and by nobody else — it is never
    #: rendered into the agent's system prompt, so a tag cannot change what the
    #: receptionist says during a call. A tag describes a call after the fact.
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    color: Mapped[TagColor] = mapped_column(
        enum_column(TagColor, "tag_color"), nullable=False, default=TagColor.SLATE
    )

    #: A tag that is off keeps the calls it already marked and is not offered to
    #: the classifier again, so a business can retire a label without losing the
    #: history it explains.
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    #: Display order, assigned as "one past the last".
    position: Mapped[int] = mapped_column(nullable=False, default=0)

    __table_args__ = (
        # Two tags with one name are two things the model cannot tell apart, and
        # a list the owner cannot either. Enforced in the database because the
        # service's check cannot see a concurrent insert.
        UniqueConstraint("tenant_id", "name", name="uq_call_tags_tenant_name"),
        # The list query, and the only query this table has.
        Index("ix_call_tags_tenant_position", "tenant_id", "position", "created_at"),
    )


class CallTagAssignment(TimestampMixin, Base):
    """One tag the classifier put on one call.

    No surrogate key: the pair *is* the identity, which is what makes a retry
    harmless. See the module docstring.
    """

    __tablename__ = "call_tag_assignments"

    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("call_tags.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (
        # "Which calls have this tag", for the filter on the calls list.
        Index("ix_call_tag_assignments_tag", "tag_id"),
    )
