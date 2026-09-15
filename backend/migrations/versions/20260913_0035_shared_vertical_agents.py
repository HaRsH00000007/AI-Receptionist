"""Shared vertical agents.

M7. Two changes, both about one fact: with shared agents, many tenants point at
the same ElevenLabs agent id.

  agents.is_shared              -- recorded on the row rather than inferred
                                   from the tenant's current mode, because
                                   compensation deletes a failed tenant's agent
                                   and doing that to a shared one would take
                                   down every tenant on that vertical. A tenant
                                   migrated between modes must not be able to
                                   make that inference wrong.
  uq_agents_elevenlabs_agent_id -- replaced by a partial unique index covering
                                   dedicated agents only. It still catches the
                                   failure it was written for (two tenants
                                   adopting one private agent) while allowing
                                   the sharing this module exists for.

The replacement index is strictly narrower than the constraint it replaces, so
an older process keeps working against the new schema: it never creates the
rows the old constraint forbade.


Revision ID: 3e7d2e6f5552
Revises: 666b40397396
Created: 2026-09-13 00:35:10.018692
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3e7d2e6f5552"
down_revision: str | None = "666b40397396"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("is_shared", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.drop_constraint(op.f("uq_agents_elevenlabs_agent_id"), "agents", type_="unique")
    op.create_index(
        "uq_agents_dedicated_elevenlabs_agent_id",
        "agents",
        ["elevenlabs_agent_id"],
        unique=True,
        postgresql_where=sa.text("NOT is_shared AND elevenlabs_agent_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Reinstating the global constraint requires that no shared agents exist;
    # if any tenant has been migrated to a shared agent this will fail, which is
    # the correct outcome — silently dropping those rows would unroute calls.
    op.drop_index(
        "uq_agents_dedicated_elevenlabs_agent_id",
        table_name="agents",
        postgresql_where=sa.text("NOT is_shared AND elevenlabs_agent_id IS NOT NULL"),
    )
    op.create_unique_constraint(
        op.f("uq_agents_elevenlabs_agent_id"), "agents", ["elevenlabs_agent_id"]
    )
    op.drop_column("agents", "is_shared")
