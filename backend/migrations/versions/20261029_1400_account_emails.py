"""Account emails: a tenant-free notification, and single-use auth tokens.

Two changes, both needed before any account email can be delivered.

**``notifications.tenant_id`` becomes nullable.** It was ``NOT NULL``, and the
magic-link route had nothing truthful to put in it — an unexchanged sign-in
link has no active tenant, and a freshly registered account has no tenant at
all. It inserted ``NULL``, the constraint rejected it, and because the insert
was flushed by the request's unit of work *after* the response had been sent,
every sign-in link request answered "a link is on its way" and queued nothing.
A welcome email and a password reset have the same shape: they belong to a
person, not a business.

The downgrade can only restore the constraint by deleting the rows that
required it — account-level notifications have no tenant to fall back to.
Deleting them is correct and is the only option: they are a record of mail we
owed, the mail has either been sent or failed by then, and the alternative is a
downgrade that cannot run.

**``auth_tokens`` is new.** Password resets and email verifications are
single-use proofs of inbox control. They are deliberately *not* rows in
``sessions`` — see :mod:`app.models.auth_token`: a reset token stored there
would be one boolean check away from being accepted as a bearer token.

Revision ID: b7e4c1f29a53
Revises: a4d19b63e8c7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7e4c1f29a53"
down_revision = "a4d19b63e8c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "notifications",
        "tenant_id",
        existing_type=sa.UUID(),
        nullable=True,
    )

    op.create_table(
        "auth_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "password_reset",
                "email_verification",
                name="auth_token_purpose",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Unique, so a collision cannot make one person's token resolve to
        # another's account.
        sa.UniqueConstraint("token_hash", name="uq_auth_tokens_token_hash"),
    )
    op.create_index(
        "ix_auth_tokens_user_id_purpose", "auth_tokens", ["user_id", "purpose"], unique=False
    )
    op.create_index("ix_auth_tokens_expires_at", "auth_tokens", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_auth_tokens_expires_at", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_user_id_purpose", table_name="auth_tokens")
    op.drop_table("auth_tokens")

    # The account-level rows cannot satisfy the restored constraint: there is no
    # tenant to attribute a password reset to. See the module docstring.
    op.execute(sa.text("DELETE FROM notifications WHERE tenant_id IS NULL"))
    op.alter_column(
        "notifications",
        "tenant_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
