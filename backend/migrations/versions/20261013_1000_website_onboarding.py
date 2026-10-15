"""Website-based setup: the job table, and where its output lands.

Two parts, for two different lifetimes.

``website_onboardings`` is scratch space: one row per account, holding a crawl's
findings and the customer's edits while they work through the review screens.
It is keyed on the user because it exists *before* a tenant does.

The four new ``business_profiles`` columns are the permanent half. Once a
customer confirms, what their website taught us has to live on the thing the
agent is rebuilt from — otherwise the next republish would quietly forget it and
the receptionist would lose half of what it knew. They are nullable because a
manually set-up business has none of this, and that path is untouched.

Revision ID: 9b4e1c73af52
Revises: 7c2e5b9a4d10
Created: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9b4e1c73af52"
down_revision: str | None = "7c2e5b9a4d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "website_onboardings",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("website_url", sa.String(length=2048), nullable=False),
        # No server defaults: the model sets both in Python, and a default the
        # model does not declare is schema drift the migration test catches.
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("crawl_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("knowledge_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("choices_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_website_onboardings")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_website_onboardings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_website_onboardings_tenant_id_tenants"),
            ondelete="SET NULL",
        ),
        # One website setup per account: starting another replaces it.
        sa.UniqueConstraint("user_id", name=op.f("uq_website_onboardings_user_id")),
        # The enum is a VARCHAR + CHECK (see enums.enum_column), so adding a
        # status later is an ordinary constraint change, not an ALTER TYPE.
        sa.CheckConstraint(
            "status IN ('pending', 'crawling', 'generating', 'ready', 'failed', 'confirmed')",
            name=op.f("ck_website_onboardings_website_onboarding_status"),
        ),
    )
    op.create_index(
        "ix_website_onboardings_status_next_attempt_at",
        "website_onboardings",
        ["status", "next_attempt_at"],
    )

    op.add_column(
        "business_profiles",
        sa.Column("knowledge_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "business_profiles",
        sa.Column("caller_questions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "business_profiles",
        sa.Column("sms_templates_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("business_profiles", sa.Column("voice_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("business_profiles", "voice_id")
    op.drop_column("business_profiles", "sms_templates_json")
    op.drop_column("business_profiles", "caller_questions_json")
    op.drop_column("business_profiles", "knowledge_json")
    op.drop_index("ix_website_onboardings_status_next_attempt_at", table_name="website_onboardings")
    op.drop_table("website_onboardings")
