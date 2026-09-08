"""ElevenLabs agents — the vendor-side projection of an :class:`AgentConfig`."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AgentStatus, enum_column

if TYPE_CHECKING:
    from app.models.agent_config import AgentConfig
    from app.models.tenant import Tenant


class Agent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One ElevenLabs conversational agent belonging to a tenant.

    One agent per tenant is the POC shape, kept because it matches what exists
    today — but it is explicitly technical debt (docs/00_DECISIONS.md s2). The
    ``agent_config_id`` link and ``synced_at`` are what make it survivable: they
    record which of *our* configs the vendor object currently reflects, so a
    resync is a comparison rather than a guess, and the eventual move to shared
    agents is a migration rather than an archaeology exercise.
    """

    __tablename__ = "agents"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_config_id: Mapped[uuid.UUID] = mapped_column(
        # Configs are append-only history; deleting one out from under the agent
        # that references it would destroy the record of what was live.
        ForeignKey("agent_configs.id", ondelete="RESTRICT"),
        nullable=False,
    )

    #: Null while PENDING, set once ElevenLabs returns an id.
    elevenlabs_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    status: Mapped[AgentStatus] = mapped_column(
        enum_column(AgentStatus, "agent_status"),
        nullable=False,
        default=AgentStatus.PENDING,
    )
    #: When the vendor object was last confirmed to match ``agent_config_id``.
    synced_at: Mapped[datetime | None] = mapped_column(nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="agents")
    agent_config: Mapped[AgentConfig] = relationship(back_populates="agents")

    __table_args__ = (
        Index(
            "uq_agents_active_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text(f"status = '{AgentStatus.ACTIVE.value}'"),
        ),
    )
