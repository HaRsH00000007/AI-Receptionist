"""Production identity, billing, usage, audit and operations tables.

Module 2 of the production migration. Additive only -- every operation here
creates something new, and the single ALTER adds a column with a server
default. Nothing is dropped, renamed or narrowed, so this is the "expand"
half of an expand/contract migration: an older application process keeps
running unchanged against the new schema, which is what makes a rolling
deploy and a rollback safe.

What lands here:
  users / memberships / sessions   -- identity, replacing "a tenant UUID in
                                      the URL is the credential"
  subscriptions / billing_events   -- entitlement, owned by us and not by
                                      Stripe
  usage_events / usage_daily       -- the metering ledger and its rollup
  audit_logs                       -- append-only record of who did what
  idempotency_keys                 -- durable, so a restart or a second
                                      replica cannot replay a paid action
  notifications / _attempts        -- a durable obligation to tell someone
  recordings                       -- metadata; the audio lives in S3/MinIO
  data_deletion_requests           -- the receipt for an erasure
  tenants.agent_mode               -- per-tenant flag for the shared-agent
                                      migration (M7)

Revision ID: f7b775b60439
Revises: 6505a4a93e92
Created: 2026-09-09 03:14:20.570094
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f7b775b60439"
down_revision: str | None = "6505a4a93e92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "disabled", name="user_status", native_enum=False, length=48),
            nullable=False,
        ),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_platform_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index("ix_users_status", "users", ["status"], unique=False)
    op.create_table(
        "audit_logs",
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column(
            "actor_type",
            sa.Enum(
                "user",
                "system",
                "admin",
                "impersonation",
                name="actor_type",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_label", sa.String(length=320), nullable=True),
        sa.Column("impersonated_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "action",
            sa.Enum(
                "login_succeeded",
                "login_failed",
                "logout",
                "magic_link_requested",
                "config_created",
                "config_activated",
                "config_rolled_back",
                "number_purchased",
                "number_released",
                "agent_created",
                "agent_resynced",
                "provisioning_started",
                "provisioning_retried",
                "provisioning_abandoned",
                "subscription_changed",
                "member_invited",
                "member_role_changed",
                "member_removed",
                "admin_action",
                "impersonation_started",
                "impersonation_ended",
                "data_export_requested",
                "data_deletion_requested",
                "data_deletion_completed",
                name="audit_action",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("meta_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["impersonated_by_user_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_impersonated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_audit_logs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_action_occurred_at", "audit_logs", ["action", "occurred_at"], unique=False
    )
    op.create_index(
        "ix_audit_logs_actor_user_id_occurred_at",
        "audit_logs",
        ["actor_user_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_entity_type_entity_id",
        "audit_logs",
        ["entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_tenant_id_occurred_at",
        "audit_logs",
        ["tenant_id", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "data_deletion_requests",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "in_progress",
                "completed",
                "rejected",
                name="deletion_request_status",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("deleted_counts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "status <> 'completed' OR completed_at IS NOT NULL",
            name=op.f("ck_data_deletion_requests_completed_at_required_once_completed"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_data_deletion_requests_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_data_deletion_requests_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_deletion_requests")),
    )
    op.create_index(
        "ix_data_deletion_requests_status", "data_deletion_requests", ["status"], unique=False
    )
    op.create_index(
        "ix_data_deletion_requests_tenant_id_created_at",
        "data_deletion_requests",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_idempotency_keys_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint(
            "endpoint", "idempotency_key", name="uq_idempotency_keys_endpoint_idempotency_key"
        ),
    )
    op.create_index(
        "ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"], unique=False
    )
    op.create_table(
        "memberships",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "owner", "admin", "member", name="membership_role", native_enum=False, length=48
            ),
            nullable=False,
        ),
        sa.Column("invited_by_user_id", sa.UUID(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
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
            ["invited_by_user_id"],
            ["users.id"],
            name=op.f("fk_memberships_invited_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_memberships_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memberships_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_memberships_user_id_tenant_id"),
    )
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"], unique=False)
    op.create_index(
        "uq_memberships_one_owner_per_tenant",
        "memberships",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )
    op.create_table(
        "sessions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("is_magic_link", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "revoked", name="session_status", native_enum=False, length=48),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("active_tenant_id", sa.UUID(), nullable=True),
        sa.Column("impersonated_by_user_id", sa.UUID(), nullable=True),
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
            ["active_tenant_id"],
            ["tenants.id"],
            name=op.f("fk_sessions_active_tenant_id_tenants"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["impersonated_by_user_id"],
            ["users.id"],
            name=op.f("fk_sessions_impersonated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"], unique=False)
    op.create_index(
        "ix_sessions_token_hash_status", "sessions", ["token_hash", "status"], unique=False
    )
    op.create_index("ix_sessions_user_id_status", "sessions", ["user_id", "status"], unique=False)
    op.create_table(
        "subscriptions",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "plan",
            sa.Enum(
                "trial",
                "starter",
                "pro",
                "enterprise",
                name="tenant_plan",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "trialing",
                "active",
                "past_due",
                "canceled",
                "expired",
                name="subscription_status",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("stripe_customer_id", sa.String(length=64), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True),
        sa.Column("stripe_price_id", sa.String(length=64), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_at_period_end", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "has_payment_method", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
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
        sa.CheckConstraint(
            "status <> 'trialing' OR trial_ends_at IS NOT NULL",
            name=op.f("ck_subscriptions_trial_requires_an_end_date"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_subscriptions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        sa.UniqueConstraint(
            "stripe_subscription_id", name=op.f("uq_subscriptions_stripe_subscription_id")
        ),
    )
    op.create_index("ix_subscriptions_status", "subscriptions", ["status"], unique=False)
    op.create_index(
        "ix_subscriptions_stripe_customer_id", "subscriptions", ["stripe_customer_id"], unique=False
    )
    op.create_index(
        "uq_subscriptions_live_per_tenant",
        "subscriptions",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'past_due', 'trialing')"),
    )
    op.create_table(
        "usage_daily",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "call_minutes",
                "llm_tokens",
                "tts_characters",
                "phone_number_month",
                name="usage_kind",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("cost_cents", sa.Integer(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_usage_daily_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_daily")),
        sa.UniqueConstraint(
            "tenant_id", "usage_date", "kind", name="uq_usage_daily_tenant_id_usage_date_kind"
        ),
    )
    op.create_index("ix_usage_daily_usage_date", "usage_daily", ["usage_date"], unique=False)
    op.create_table(
        "billing_events",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("subscription_id", sa.UUID(), nullable=True),
        sa.Column(
            "event_type",
            sa.Enum(
                "subscription_created",
                "subscription_updated",
                "subscription_canceled",
                "payment_succeeded",
                "payment_failed",
                "trial_granted",
                "trial_expired",
                name="billing_event_type",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("provider_event_id", sa.String(length=128), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
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
            ["subscription_id"],
            ["subscriptions.id"],
            name=op.f("fk_billing_events_subscription_id_subscriptions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_billing_events_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_events")),
        sa.UniqueConstraint("provider_event_id", name=op.f("uq_billing_events_provider_event_id")),
    )
    op.create_index("ix_billing_events_event_type", "billing_events", ["event_type"], unique=False)
    op.create_index(
        "ix_billing_events_tenant_id_created_at",
        "billing_events",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "notifications",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("call_id", sa.UUID(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "welcome",
                "provisioning_complete",
                "provisioning_failed",
                "missed_call",
                "call_summary",
                "billing",
                "magic_link",
                "usage_warning",
                name="notification_kind",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "sent",
                "failed",
                name="notification_status",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("template_vars_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            ["call_id"],
            ["calls.id"],
            name=op.f("fk_notifications_call_id_calls"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_notifications_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(
        "ix_notifications_status_next_attempt_at",
        "notifications",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_tenant_id_created_at",
        "notifications",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "recordings",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("call_id", sa.UUID(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("storage_provider", sa.String(length=32), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column(
            "consent_announced", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0", name=op.f("ck_recordings_size_not_negative")
        ),
        sa.ForeignKeyConstraint(
            ["call_id"], ["calls.id"], name=op.f("fk_recordings_call_id_calls"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_recordings_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recordings")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_recordings_storage_key")),
    )
    op.create_index(
        "ix_recordings_delete_after",
        "recordings",
        ["delete_after"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_recordings_tenant_id_created_at",
        "recordings",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "usage_events",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("call_id", sa.UUID(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "call_minutes",
                "llm_tokens",
                "tts_characters",
                "phone_number_month",
                name="usage_kind",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "measured",
                "provider_reported",
                "reconciled",
                "manual_adjustment",
                name="usage_source",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("cost_cents", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_reference", sa.String(length=128), nullable=True),
        sa.Column("meta_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.CheckConstraint(
            "cost_cents IS NULL OR cost_cents >= 0", name=op.f("ck_usage_events_cost_not_negative")
        ),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["calls.id"],
            name=op.f("fk_usage_events_call_id_calls"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_usage_events_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
        sa.UniqueConstraint(
            "provider", "provider_reference", name="uq_usage_events_provider_provider_reference"
        ),
    )
    op.create_index(
        "ix_usage_events_kind_occurred_at", "usage_events", ["kind", "occurred_at"], unique=False
    )
    op.create_index(
        "ix_usage_events_tenant_id_occurred_at",
        "usage_events",
        ["tenant_id", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "notification_attempts",
        sa.Column("notification_id", sa.UUID(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
            ["notification_id"],
            ["notifications.id"],
            name=op.f("fk_notification_attempts_notification_id_notifications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_attempts")),
        sa.UniqueConstraint(
            "notification_id", "attempt", name="uq_notification_attempts_notification_id_attempt"
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "agent_mode",
            sa.Enum(
                "per_tenant", "shared_vertical", name="agent_mode", native_enum=False, length=48
            ),
            server_default="per_tenant",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "agent_mode")
    op.drop_table("notification_attempts")
    op.drop_index("ix_usage_events_tenant_id_occurred_at", table_name="usage_events")
    op.drop_index("ix_usage_events_kind_occurred_at", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_index("ix_recordings_tenant_id_created_at", table_name="recordings")
    op.drop_index(
        "ix_recordings_delete_after",
        table_name="recordings",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("recordings")
    op.drop_index("ix_notifications_tenant_id_created_at", table_name="notifications")
    op.drop_index("ix_notifications_status_next_attempt_at", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_billing_events_tenant_id_created_at", table_name="billing_events")
    op.drop_index("ix_billing_events_event_type", table_name="billing_events")
    op.drop_table("billing_events")
    op.drop_index("ix_usage_daily_usage_date", table_name="usage_daily")
    op.drop_table("usage_daily")
    op.drop_index(
        "uq_subscriptions_live_per_tenant",
        table_name="subscriptions",
        postgresql_where=sa.text("status IN ('active', 'past_due', 'trialing')"),
    )
    op.drop_index("ix_subscriptions_stripe_customer_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_status", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_sessions_user_id_status", table_name="sessions")
    op.drop_index("ix_sessions_token_hash_status", table_name="sessions")
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(
        "uq_memberships_one_owner_per_tenant",
        table_name="memberships",
        postgresql_where=sa.text("role = 'owner'"),
    )
    op.drop_index("ix_memberships_tenant_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index(
        "ix_data_deletion_requests_tenant_id_created_at", table_name="data_deletion_requests"
    )
    op.drop_index("ix_data_deletion_requests_status", table_name="data_deletion_requests")
    op.drop_table("data_deletion_requests")
    op.drop_index("ix_audit_logs_tenant_id_occurred_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_type_entity_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_user_id_occurred_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action_occurred_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_table("users")
