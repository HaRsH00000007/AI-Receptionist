"""Onboarding drafts: the setup form, saved as it is filled in.

Accounts are now created before businesses, so a person can have an account and
an unfinished setup form. One table, one row per user, holding that form so it
can be resumed on any device. Nothing else reads it.

Additive only: a new table, nothing altered.


Revision ID: 7c2e5b9a4d10
Revises: f3a9c6d21b84
Created: 2026-09-22 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c2e5b9a4d10"
down_revision: str | None = "f3a9c6d21b84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "onboarding_drafts",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_onboarding_drafts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_onboarding_drafts")),
        sa.UniqueConstraint("user_id", name=op.f("uq_onboarding_drafts_user_id")),
    )


def downgrade() -> None:
    # Discards unfinished setup forms. Nobody loses an account or a business;
    # someone mid-setup starts the form again.
    op.drop_table("onboarding_drafts")
