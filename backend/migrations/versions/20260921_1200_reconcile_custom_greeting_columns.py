"""Reconcile the two custom-greeting columns.

The custom greeting was built twice, on two machines, and both versions reached
a database before anyone noticed:

- ``b2c9d41f07ae`` added ``business_profiles.custom_greeting``. It was never
  pushed, but the database it ran against was later moved to staging, so
  staging is at that revision.
- ``c5a81f3b62d7`` added ``business_profiles.greeting_custom``, which is the
  column the application uses.

Both branch from ``4e3e4f2d0eda``. This merge joins them so there is one head
again, and folds the unused column into the live one. A database at
``b2c9d41f07ae`` reaches here by running the main branch (whose columns it does
not have); a database at ``b3e7d05a91c8`` reaches here by adding and then
dropping ``custom_greeting``, which changes nothing. Either way nothing is
added twice and no greeting is lost.

The downgrade restores ``custom_greeting`` with the values copied back, and
leaves ``greeting_custom`` alone: that column belongs to ``c5a81f3b62d7``, which
is still applied on the other side of this merge, and its own downgrade is the
one that drops it.


Revision ID: d8f4a26c1e90
Revises: b3e7d05a91c8, b2c9d41f07ae
Created: 2026-09-21 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f4a26c1e90"
down_revision: str | tuple[str, ...] | None = ("b3e7d05a91c8", "b2c9d41f07ae")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Only fill a gap: a greeting already in the live column is the newer
    # choice and wins.
    op.execute(
        sa.text(
            "UPDATE business_profiles "
            "SET greeting_custom = custom_greeting "
            "WHERE custom_greeting IS NOT NULL AND greeting_custom IS NULL"
        )
    )
    op.drop_column("business_profiles", "custom_greeting")


def downgrade() -> None:
    op.add_column("business_profiles", sa.Column("custom_greeting", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE business_profiles "
            "SET custom_greeting = greeting_custom "
            "WHERE greeting_custom IS NOT NULL"
        )
    )
