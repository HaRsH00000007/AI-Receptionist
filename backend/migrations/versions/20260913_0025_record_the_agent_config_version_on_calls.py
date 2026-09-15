"""Record which agent config version served each call.

M8. One nullable column, no default, no backfill -- the "expand" half of an
expand/contract change, so an older process keeps running against the new
schema and a rollback is a column drop with nothing depending on it.

Nullable rather than defaulted because the honest value for a call that
predates this column is *unknown*, and inventing a version for it would make
the audit trail confidently wrong. Calls recorded from here on carry the
version that was live at the moment they arrived.

Revision ID: 666b40397396
Revises: f7b775b60439
Created: 2026-09-13 00:25:41.930897
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "666b40397396"
down_revision: str | None = "f7b775b60439"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("agent_config_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "agent_config_version")
