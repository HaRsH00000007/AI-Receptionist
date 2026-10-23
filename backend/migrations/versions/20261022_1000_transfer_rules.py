"""Rules for handing a live call to a real person.

A table rather than a JSON column on the profile: each rule is edited, disabled
and deleted on its own, the audit trail has to name the one that changed, and
the destination is a phone number — so it gets a tenant scope and a foreign key
with ON DELETE CASCADE, which is what makes deleting a receptionist take its
forwarding rules with it.

Nothing existing changes. A tenant with no rows behaves exactly as before: no
transfer tool is configured on its agent, and the receptionist takes a message.

Revision ID: b5d31f6c2a84
Revises: a7f2c81d4e93
Created: 2026-10-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b5d31f6c2a84"
down_revision: str | None = "a7f2c81d4e93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transfer_rules",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("destination_e164", sa.String(length=20), nullable=False),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("transfer_type", sa.String(length=48), nullable=False),
        # No server defaults: the table is new and empty, so there is nothing
        # to backfill, and a default the models do not declare is schema drift
        # that `test_migrations_produce_the_models_schema` would (rightly) fail.
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfer_rules")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_transfer_rules_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        # Warm is a conference transfer with a briefing, cold is a blind one.
        # SIP REFER is absent because every number here reaches ElevenLabs
        # through Twilio, not a SIP trunk that permits REFER.
        sa.CheckConstraint(
            "transfer_type IN ('warm', 'cold')", name=op.f("ck_transfer_rules_transfer_type")
        ),
    )
    op.create_index(
        "ix_transfer_rules_tenant_position",
        "transfer_rules",
        ["tenant_id", "position", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_transfer_rules_tenant_position", table_name="transfer_rules")
    op.drop_table("transfer_rules")
