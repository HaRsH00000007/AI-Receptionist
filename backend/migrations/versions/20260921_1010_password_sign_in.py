"""Let an account hold a password.

Sign-in was magic-link only, which made account access depend on outbound email
working. A business that had just signed up therefore had no way in at all until
mail delivery was configured. A password chosen during signup removes that
dependency for the first login; the link stays as the way back for anyone who
has forgotten it.

Nullable with no backfill: ``NULL`` means "signs in by link", which is what
every existing account does. Nothing is invalidated by this migration.

Widths: 255 is comfortably above an Argon2id encoded hash (about 100 characters
at the current parameters) and leaves room for the parameters to grow without
another migration.


Revision ID: b3e7d05a91c8
Revises: a7f2c91d4b63
Created: 2026-09-21 10:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e7d05a91c8"
down_revision: str | None = "a7f2c91d4b63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    # Dropping this discards every password that has been set. The accounts
    # survive — they fall back to magic-link sign-in, which is where they
    # started — but nobody can sign in with a password until they set one again.
    op.drop_column("users", "password_hash")
