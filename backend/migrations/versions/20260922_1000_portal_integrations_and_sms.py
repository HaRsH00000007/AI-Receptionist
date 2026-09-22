"""Customer portal: integrations and SMS registration.

Two new tables, nothing altered, so this is safe to run against a live database
with traffic on it:

- ``tenant_integrations`` holds a tenant's connection to a third-party service.
  Its credentials column is ciphertext only — encrypted with
  ``INTEGRATION_ENCRYPTION_KEY`` before it is written — so there is no
  plaintext secret for this migration, a dump or a backup to expose.
- ``sms_registrations`` holds a tenant's A2P 10DLC brand and campaign details
  and where their review stands. No row means "not started".

Both cascade with their tenant, like every other tenant-owned table.


Revision ID: f3a9c6d21b84
Revises: d8f4a26c1e90
Created: 2026-09-22 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a9c6d21b84"
down_revision: str | None = "d8f4a26c1e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=48)


def _timestamps() -> list[sa.Column[object]]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "tenant_integrations",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "provider",
            _enum(
                "integration_provider",
                "google_calendar",
                "microsoft_outlook",
                "zapier",
                "clio",
                "acuity_scheduling",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("integration_status", "connected", "error", "disconnected"),
            nullable=False,
        ),
        sa.Column("external_account_id", sa.String(length=320), nullable=True),
        sa.Column("credentials_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("connected_by_user_id", sa.UUID(), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("settings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_integrations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connected_by_user_id"],
            ["users.id"],
            name=op.f("fk_tenant_integrations_connected_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_integrations")),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_tenant_integrations_tenant_provider"),
    )

    op.create_table(
        "sms_registrations",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "sms_registration_status",
                "draft",
                "submitted",
                "under_review",
                "approved",
                "rejected",
                "enabled",
            ),
            nullable=False,
        ),
        sa.Column(
            "brand_type",
            _enum("sms_brand_type", "standard", "sole_proprietor"),
            nullable=False,
        ),
        sa.Column("legal_business_name", sa.String(length=255), nullable=True),
        sa.Column("tax_id", sa.String(length=32), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("address_line1", sa.String(length=255), nullable=True),
        sa.Column("address_line2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("region", sa.String(length=100), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("contact_name", sa.String(length=200), nullable=True),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column("contact_phone", sa.String(length=32), nullable=True),
        sa.Column(
            "use_case",
            _enum(
                "sms_use_case",
                "customer_care",
                "account_notification",
                "appointment_reminders",
                "marketing",
                "mixed",
            ),
            nullable=True,
        ),
        sa.Column("campaign_description", sa.Text(), nullable=True),
        sa.Column("sample_messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("opt_in_description", sa.Text(), nullable=True),
        sa.Column("twilio_customer_profile_sid", sa.String(length=64), nullable=True),
        sa.Column("twilio_brand_sid", sa.String(length=64), nullable=True),
        sa.Column("twilio_campaign_sid", sa.String(length=64), nullable=True),
        sa.Column("twilio_messaging_service_sid", sa.String(length=64), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("submitted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_sms_registrations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["users.id"],
            name=op.f("fk_sms_registrations_submitted_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sms_registrations")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_sms_registrations_tenant_id")),
    )


def downgrade() -> None:
    # Dropping tenant_integrations discards every stored credential. That is
    # the right outcome for a rollback — they are ciphertext for a feature that
    # no longer exists — and customers reconnect after a re-upgrade.
    op.drop_table("sms_registrations")
    op.drop_table("tenant_integrations")
