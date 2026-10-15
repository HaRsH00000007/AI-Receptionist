"""The name a business gives its own receptionist.

A label the owner reads, never a value the agent speaks: it is not rendered
into the prompt and never reaches a vendor. Nullable with no default, so every
existing profile keeps meaning "no name given" and the UI goes on showing the
business name, which is what it showed before this column existed.

Revision ID: a7f2c81d4e93
Revises: c41d7e9a2b60
Created: 2026-10-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7f2c81d4e93"
down_revision: str | None = "c41d7e9a2b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "business_profiles",
        sa.Column("agent_name", sa.String(length=80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("business_profiles", "agent_name")
