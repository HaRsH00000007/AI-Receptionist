"""The raw signup form and its normalized form."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import GreetingStyle, enum_column

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class BusinessProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """What the business told us, kept alongside what we made of it.

    The ``*_raw`` columns are never discarded. When a prompt template improves we
    re-run normalization from the original text rather than from a previous
    interpretation of it — and when a tenant complains that the AI got their
    hours wrong, the raw string is the evidence.

    ``hours_json`` and ``escalation_json`` are validated by
    :class:`app.schemas.business.BusinessHours` and
    :class:`app.schemas.business.EscalationPolicy` before they are written; the
    column type is JSONB because Postgres cannot enforce those shapes.
    """

    __tablename__ = "business_profiles"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        # One profile per tenant. Its absence is what let a duplicated
        # spreadsheet column quietly change the meaning of every later step.
        unique=True,
    )

    raw_form_json: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    services: Mapped[list[str]] = mapped_column(nullable=False, default=list)

    hours_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    hours_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    #: Selects the voice deterministically; never inferred by a model.
    greeting_style: Mapped[GreetingStyle] = mapped_column(
        enum_column(GreetingStyle, "greeting_style"),
        nullable=False,
        default=GreetingStyle.PROFESSIONAL,
    )

    #: The opening line the owner chose or wrote, used verbatim.
    #:
    #: ``None`` means "write one for me" — the behaviour every tenant had before
    #: this column existed — and the generated greeting is used instead. The
    #: style above still picks the voice either way, so a custom line is spoken
    #: in the register the owner asked for.
    greeting_custom: Mapped[str | None] = mapped_column(Text, nullable=True)

    escalation_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalation_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    #: Bumped whenever the profile changes, so a cached config keyed by version
    #: becomes unreachable rather than stale.
    config_version: Mapped[int] = mapped_column(nullable=False, default=1)

    tenant: Mapped[Tenant] = relationship(back_populates="business_profile")
