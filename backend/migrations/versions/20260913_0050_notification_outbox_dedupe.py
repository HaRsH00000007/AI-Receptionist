"""Notification outbox deduplication.

M13. One nullable column and one partial unique index, so that two independent
attempts to announce the same event -- a redelivered post-call webhook, a
retried provisioning activity -- collide in the database instead of both
sending. Without it the customer receives the same call summary twice, which is
the failure webhook deduplication exists to prevent, reintroduced one layer
further down.

Partial on `dedupe_key IS NOT NULL`, because a one-off notification that nothing
else will ever try to send needs no key and several of those must coexist.


Revision ID: 4e3e4f2d0eda
Revises: 3e7d2e6f5552
Created: 2026-09-13 00:50:43.199284
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4e3e4f2d0eda"
down_revision: str | None = "3e7d2e6f5552"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("dedupe_key", sa.String(length=200), nullable=True))
    op.create_index(
        "uq_notifications_dedupe_key",
        "notifications",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_notifications_dedupe_key",
        table_name="notifications",
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )
    op.drop_column("notifications", "dedupe_key")
