"""When the receptionist should answer with something other than its usual line.

One row is one exception: *between these hours, or on these dates, say this
instead*. Lunch hours and public holidays are the two things every business
asked for, and they are different enough to be two kinds rather than one:

* a weekly rule repeats forever and is described in weekdays and clock times;
* a dated rule is a one-off and is described in calendar dates.

Both are read in the **tenant's own timezone**, the one on ``tenants.timezone``
that the opening hours are already stored in. A greeting schedule in UTC would
switch over at the wrong moment for every business in the country, and storing
a second timezone here would let the two disagree.

The message is spoken to a caller verbatim, so it is the business's own words
and never a template. It is *not* part of the system prompt: it replaces the
agent's opening line, which is why it can be swapped by the worker without
regenerating anything (:mod:`app.services.greeting_schedule`).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Date, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import GreetingScheduleKind, enum_column


class GreetingRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One "say this instead, at these times" rule."""

    __tablename__ = "greeting_rules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[GreetingScheduleKind] = mapped_column(
        enum_column(GreetingScheduleKind, "greeting_schedule_kind"), nullable=False
    )

    #: What the caller hears. Verbatim, like the default opening line.
    message: Mapped[str] = mapped_column(Text, nullable=False)

    #: Which days a weekly rule applies to, as ``date.weekday()`` numbers —
    #: Monday is 0. A list rather than seven booleans so the common "weekdays"
    #: case is one value, and JSONB rather than an array column because every
    #: other structured column in this schema is JSONB.
    #:
    #: Null for a dated rule.
    #:
    #: The column type is spelled out because ``list[int]`` is not in the
    #: shared annotation map, and widening that map for one column would make
    #: every future ``list[int]`` silently a JSONB column. ``none_as_null``
    #: because a JSONB column stores ``None`` as the JSON value ``null`` by
    #: default, which is not SQL NULL — and the CHECK constraint that keeps a
    #: dated rule free of weekdays tests for SQL NULL.
    weekdays_json: Mapped[list[int] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    #: The window, in minutes since local midnight, 0 to 1439.
    #:
    #: ``end_minute`` at or before ``start_minute`` means the window crosses
    #: midnight — 22:00 to 06:00 is a real thing a business says, and refusing
    #: it would push them into two rules that behave subtly differently. The
    #: weekday of a crossing window is the day it *starts*.
    #:
    #: Both null on a dated rule means the whole of each date.
    start_minute: Mapped[int | None] = mapped_column(nullable=True)
    end_minute: Mapped[int | None] = mapped_column(nullable=True)

    #: A dated rule's range, inclusive at both ends. A single day sets both to
    #: the same date rather than leaving one null, so every stored range reads
    #: the same way.
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: A rule that is off stays on file but is never matched, so a business can
    #: keep last year's holiday hours without them being spoken.
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    #: Display order within its kind, assigned as "one past the last". Also the
    #: tie-break when two rules of the same kind match the same moment: the
    #: first one wins, which is the order the owner sees them in.
    position: Mapped[int] = mapped_column(nullable=False, default=0)

    __table_args__ = (
        # The only query this table has: a tenant's rules in display order.
        Index("ix_greeting_rules_tenant_position", "tenant_id", "position", "created_at"),
    )

    def as_dict(self) -> dict[str, Any]:
        """The shape the schedule is published and evaluated in.

        Here rather than in the service so that the row and the published copy
        cannot drift into two different ideas of what a rule is.
        """
        return {
            "kind": self.kind.value,
            "message": self.message,
            "weekdays": list(self.weekdays_json or ()),
            "start_minute": self.start_minute,
            "end_minute": self.end_minute,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
        }
