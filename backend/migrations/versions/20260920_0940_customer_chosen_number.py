"""The number the customer chose at signup.

The form now shows numbers that are actually available and lets the business
pick one, so the purchase step needs to know which one they saw. A preference
rather than a reservation: nothing is held at the vendor between the search and
the purchase, and the step falls back to its own search if the number is gone.

Nullable with no backfill: ``NULL`` means "choose one for me", which is exactly
what every existing tenant did.


Revision ID: a7f2c91d4b63
Revises: c5a81f3b62d7
Created: 2026-09-20 09:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7f2c91d4b63"
down_revision: str | None = "c5a81f3b62d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("requested_number", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "requested_number")
