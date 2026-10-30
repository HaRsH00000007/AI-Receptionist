"""Appointment booking: what the receptionist booked, and the tools that do it.

Both changes are additive, so nothing existing changes until a business
connects a calendar.

**``appointments`` is new.** One row per event the receptionist created in a
business's connected calendar. It exists for idempotency (a unique key per
conversation and slot, so a repeated tool call cannot book twice), for an
overlap check of our own that does not wait for the calendar to catch up, and
for the dashboard. See :mod:`app.models.appointment`.

**``agents.vendor_tools`` is new and nullable.** ElevenLabs keeps webhook tools
as workspace objects that an agent references by id. These ids are what lets a
later push update or delete the tools it made, rather than leaving a new pair
behind every time. Null — every existing agent — means "has none".

Revision ID: c6e2f9a14b73
Revises: b7e4c1f29a53
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c6e2f9a14b73"
down_revision = "b7e4c1f29a53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("vendor_tools", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.create_table(
        "appointments",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "google_calendar",
                "microsoft_outlook",
                "zapier",
                "clio",
                "acuity_scheduling",
                name="appointment_provider",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("external_event_id", sa.String(length=1024), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "booked",
                "cancelled",
                name="appointment_status",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("caller_name", sa.String(length=120), nullable=False),
        sa.Column("caller_phone", sa.String(length=32), nullable=True),
        sa.Column("caller_email", sa.String(length=320), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("conversation_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_appointments_tenant_key"),
    )
    op.create_index(
        "ix_appointments_tenant_starts_at",
        "appointments",
        ["tenant_id", "starts_at"],
        unique=False,
    )
    op.create_index(
        "ix_appointments_conversation", "appointments", ["conversation_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_appointments_conversation", table_name="appointments")
    op.drop_index("ix_appointments_tenant_starts_at", table_name="appointments")
    op.drop_table("appointments")
    op.drop_column("agents", "vendor_tools")
