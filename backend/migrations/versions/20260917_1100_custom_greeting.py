"""Customer-supplied opening line.

One nullable column. NULL keeps its original meaning for every existing row --
"nobody chose one, so the generated greeting stands" -- which is why this needs
no backfill and why the column cannot be NOT NULL with a default: an empty
string would be indistinguishable from a business that really did want silence
at the start of the call.

The greeting lives on the profile, next to `hours_raw`, because it is something
the owner told us. The config row still holds the rendered `first_message`; this
is the source it is rendered from.


Revision ID: b2c9d41f07ae
Revises: 4e3e4f2d0eda
Created: 2026-09-17 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c9d41f07ae"
down_revision: str | None = "4e3e4f2d0eda"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("business_profiles", sa.Column("custom_greeting", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("business_profiles", "custom_greeting")
