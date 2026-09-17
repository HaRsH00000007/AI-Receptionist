"""Custom greetings.

The line a caller hears first is ``agent_configs.first_message``, written by the
LLM or by the deterministic template. This column holds the line the owner
picked or typed instead, so the greeting previewed at signup is the greeting
that gets spoken.

Nullable with no backfill: ``NULL`` means "write one for me", which is exactly
what every existing tenant already does, so this migration changes no live
behaviour on its own.


Revision ID: c5a81f3b62d7
Revises: 4e3e4f2d0eda
Created: 2026-09-17 09:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5a81f3b62d7"
down_revision: str | None = "4e3e4f2d0eda"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("business_profiles", sa.Column("greeting_custom", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("business_profiles", "greeting_custom")
