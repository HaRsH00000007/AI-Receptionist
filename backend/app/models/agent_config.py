"""The rendered agent configuration — our copy, not the vendor's."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AgentConfigSource, enum_column

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.tenant import Tenant


class AgentConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One version of a tenant's prompt and voice settings.

    Configs are immutable and append-only: a change writes a new row with the
    next ``version`` and moves the ``is_live`` flag. That makes ElevenLabs a
    *projection* of this table rather than the source of truth, gives an exact
    answer to "what prompt was live when this call happened?", and turns rollback
    into moving a flag rather than calling a vendor (docs/00_DECISIONS.md s2).

    ``version`` counts this tenant's configs. ``template_version`` records which
    versioned prompt template in the repository produced it, so that a template
    improvement can be fanned out over exactly the tenants still on the old one.
    """

    __tablename__ = "agent_configs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)

    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    first_message: Mapped[str] = mapped_column(Text, nullable=False)
    voice_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_params_json: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    generated_by: Mapped[AgentConfigSource] = mapped_column(
        enum_column(AgentConfigSource, "agent_config_source"), nullable=False
    )
    #: The model or renderer that produced it, e.g. "claude-opus-5". Free text
    #: because it records a vendor's identifier, not one of ours.
    generator_detail: Mapped[str | None] = mapped_column(String(128), nullable=True)
    template_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    is_live: Mapped[bool] = mapped_column(nullable=False, default=False)

    tenant: Mapped[Tenant] = relationship(back_populates="agent_configs")
    agents: Mapped[list[Agent]] = relationship(back_populates="agent_config")

    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_agent_configs_tenant_id_version"),
        # At most one live config per tenant. Without this, a half-failed resync
        # leaves two rows claiming to be live and nothing can say which prompt
        # the vendor actually holds.
        Index(
            "uq_agent_configs_live_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_live"),
        ),
    )
