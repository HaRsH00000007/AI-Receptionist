"""Labels a business puts on its calls, and which calls got them.

Two tables, both additive. A tenant with no tags behaves exactly as before: the
classifier is skipped entirely, so no extra model call is made for anybody who
does not use the feature.

Three constraints are the design, not decoration:

* ``uq_call_tags_tenant_name`` — two tags with one name are two things neither
  the model nor the owner can tell apart. In the database because the service's
  count-then-insert cannot see a concurrent insert.
* the assignment's primary key is ``(call_id, tag_id)`` — a retried
  classification re-asserts the rows it already wrote instead of duplicating
  them, so idempotency is structural rather than a check somebody could drop.
* both foreign keys cascade — deleting a tag removes it from every call it ever
  marked, which is the honest reading of a delete button, and deleting a call or
  a whole receptionist takes its labels with it.

Revision ID: a4d19b63e8c7
Revises: f3c82a51d7be
Created: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4d19b63e8c7"
down_revision: str | None = "f3c82a51d7be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "call_tags",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("color", sa.String(length=48), nullable=False),
        # No server defaults: the table is new and empty, so there is nothing to
        # backfill, and a default the models do not declare is schema drift that
        # `test_migrations_produce_the_models_schema` would (rightly) fail.
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_call_tags")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_call_tags_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_call_tags_tenant_name"),
        sa.CheckConstraint(
            "color IN ('green', 'amber', 'red', 'blue', 'violet', 'teal', 'indigo', 'slate')",
            name=op.f("ck_call_tags_color"),
        ),
    )
    op.create_index(
        "ix_call_tags_tenant_position", "call_tags", ["tenant_id", "position", "created_at"]
    )

    op.create_table(
        "call_tag_assignments",
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.PrimaryKeyConstraint("call_id", "tag_id", name=op.f("pk_call_tag_assignments")),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["calls.id"],
            name=op.f("fk_call_tag_assignments_call_id_calls"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["call_tags.id"],
            name=op.f("fk_call_tag_assignments_tag_id_call_tags"),
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_call_tag_assignments_tag", "call_tag_assignments", ["tag_id"])


def downgrade() -> None:
    op.drop_index("ix_call_tag_assignments_tag", table_name="call_tag_assignments")
    op.drop_table("call_tag_assignments")
    op.drop_index("ix_call_tags_tenant_position", table_name="call_tags")
    op.drop_table("call_tags")
