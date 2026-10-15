"""Contacts as records a business can edit, not a view over call history.

The unique constraint on (tenant_id, phone_e164) is the point of this table.
It is what makes capturing a contact from a finished call safe to repeat — a
redelivered webhook collides instead of inserting a second row — and what stops
a CSV import duplicating a customer who already exists. Doing that check in
application code instead would lose the race between two concurrent imports.

Call counts are not stored here. They are read from `calls` when the list is
served, so the two cannot drift, and deleting a contact never touches history.

Revision ID: c41d7e9a2b60
Revises: 9b4e1c73af52
Created: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c41d7e9a2b60"
down_revision: str | None = "9b4e1c73af52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("source", sa.String(length=48), nullable=False),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_contacts_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "phone_e164", name="uq_contacts_tenant_phone"),
        sa.CheckConstraint(
            "status IN ('active', 'blocked')", name=op.f("ck_contacts_contact_status")
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'call', 'import')", name=op.f("ck_contacts_contact_source")
        ),
    )
    op.create_index(
        "ix_contacts_tenant_status_created", "contacts", ["tenant_id", "status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_contacts_tenant_status_created", table_name="contacts")
    op.drop_table("contacts")
