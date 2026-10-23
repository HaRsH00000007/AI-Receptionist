"""The sign-off a receptionist ends a call with.

One nullable column. Null is the honest value for every business that existed
before this — it means "no sign-off of your own", which is exactly how they
behaved — so there is nothing to backfill and no default to invent.

It lives on the profile rather than on the configuration because it is the
business's own words, like the opening line beside it: the configuration is
regenerated from the profile, and a column on the version would be rewritten by
every republish.

Revision ID: d1a95f3c7b08
Revises: c8e41a9b7d26
Created: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1a95f3c7b08"
down_revision: str | None = "c8e41a9b7d26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("business_profiles", sa.Column("goodbye_custom", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("business_profiles", "goodbye_custom")
