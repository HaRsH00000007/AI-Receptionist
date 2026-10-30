"""The raw signup form and its normalized form."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import GreetingStyle, Responsiveness, enum_column

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

    #: What the receptionist says as it signs off, used verbatim.
    #:
    #: ``None`` means "no sign-off of your own", and the agent ends the call the
    #: way its prompt otherwise tells it to.
    #:
    #: Unlike the opening line, this is **not** a field the vendor has: there is
    #: no "last message" on an ElevenLabs agent, so it is rendered into the
    #: system prompt as an instruction to close with these words
    #: (``render_system_prompt``). A model following an instruction is a weaker
    #: promise than a first message it is given to read, and the UI says so
    #: rather than implying the two behave the same.
    goodbye_custom: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: How fast the receptionist talks, as a multiplier.
    #:
    #: The vendor's own field (``tts.speed``) and its own range: it refuses
    #: anything outside 0.7 to 1.2, which was checked against the live API
    #: rather than read off a doc. Null means "don't send one", which is the
    #: vendor's 1.0 — and is what every receptionist had before this column.
    speaking_speed: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: How quickly it decides the caller has stopped talking.
    #:
    #: Null is the same as ``AUTO``: the vendor's recommended setting. Stored as
    #: our own three-value enum rather than the vendor's words, so the column
    #: still means something if the vendor renames them.
    responsiveness: Mapped[Responsiveness | None] = mapped_column(
        enum_column(Responsiveness, "responsiveness"), nullable=True
    )

    escalation_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalation_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    #: Bumped whenever the profile changes, so a cached config keyed by version
    #: becomes unreachable rather than stale.
    config_version: Mapped[int] = mapped_column(nullable=False, default=1)

    # ---- website-learned material (null for a manually set-up business) ----
    #: Knowledge items the customer kept from their website, each with its
    #: source page. Feeds the v2 config prompt and survives a resync, so the
    #: agent does not forget what the website taught it when it is republished.
    knowledge_json: Mapped[list[dict[str, Any]] | None] = mapped_column(nullable=True)
    #: What the receptionist should collect from callers, in order.
    caller_questions_json: Mapped[list[dict[str, Any]] | None] = mapped_column(nullable=True)
    #: Draft SMS bodies the agent may offer to send. Configuration only: there
    #: is no sending path yet, and the UI says so rather than implying one.
    sms_templates_json: Mapped[list[dict[str, Any]] | None] = mapped_column(nullable=True)
    #: An explicit voice choice, overriding the greeting-style default in
    #: ``select_voice_id``. Null means "whatever the style maps to".
    voice_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: What the owner calls their receptionist, for their own reference.
    #:
    #: A label, not configuration: it is never spoken, never rendered into the
    #: prompt and never sent to a vendor, so changing it cannot change what a
    #: caller hears. Null means "no name given" and the UI falls back to the
    #: business name.
    #:
    #: It lives here rather than on ``agents`` because an ``Agent`` row is the
    #: vendor's projection of a config, rebuilt on resync, while this is the
    #: customer's own word for the thing. When one tenant can have several
    #: agents (docs/00_DECISIONS.md s2) this column moves with that migration.
    agent_name: Mapped[str | None] = mapped_column(String(80), nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="business_profile")
