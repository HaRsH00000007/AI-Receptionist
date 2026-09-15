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
    #: The vendor's agent id.
    #:
    #: Not globally unique any more, and that is the whole point of shared
    #: agents: every salon tenant points at the *same* vertical agent. Uniqueness
    #: is instead enforced by a partial index over dedicated agents only, which
    #: still catches the failure it was written for — two tenants accidentally
    #: adopting one private agent.
    elevenlabs_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Whether this row points at an agent shared with other tenants.
    #:
    #: The most consequential flag in the schema. Compensation deletes a failed
    #: tenant's agent at the vendor; doing that to a shared vertical agent would
    #: take down every other tenant using it. So the row records what it *is*
    #: rather than letting compensation infer it from the tenant's current mode —
    #: a tenant migrated between modes would otherwise make history lie, and the
    #: inference would be wrong exactly once, catastrophically.
    is_shared: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

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
        # A *dedicated* agent belongs to exactly one tenant. Shared agents are
        # excluded, because many tenants legitimately point at one of those.
        Index(
            "uq_agents_dedicated_elevenlabs_agent_id",
            "elevenlabs_agent_id",
            unique=True,
            postgresql_where=text("NOT is_shared AND elevenlabs_agent_id IS NOT NULL"),
        ),
    )
