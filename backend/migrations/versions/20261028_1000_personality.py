"""How fast the receptionist talks, and how quickly it answers.

Two nullable columns, both carrying the vendor's own settings:

* ``speaking_speed`` is ElevenLabs' ``tts.speed``. The CHECK is the range the
  vendor itself enforces — it refuses anything outside 0.7 to 1.2, which was
  checked against the live API on a throwaway agent rather than read off a doc.
  A value it would reject is a 422 on every publish from then on, so it is
  worth refusing here too.
* ``responsiveness`` is our three-value name for ``turn.turn_eagerness``, which
  accepts ``patient``, ``normal`` or ``eager`` and nothing else.

Null means "send nothing and take the vendor's default", which is exactly how
every receptionist behaved before these existed — so there is nothing to
backfill and no default to invent.

Revision ID: f3c82a51d7be
Revises: d1a95f3c7b08
Created: 2026-10-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3c82a51d7be"
down_revision: str | None = "d1a95f3c7b08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("business_profiles", sa.Column("speaking_speed", sa.Float(), nullable=True))
    op.add_column(
        "business_profiles", sa.Column("responsiveness", sa.String(length=48), nullable=True)
    )
    op.create_check_constraint(
        "ck_business_profiles_speaking_speed",
        "business_profiles",
        "speaking_speed IS NULL OR (speaking_speed >= 0.7 AND speaking_speed <= 1.2)",
    )
    op.create_check_constraint(
        "ck_business_profiles_responsiveness",
        "business_profiles",
        "responsiveness IS NULL OR responsiveness IN ('auto', 'low', 'high')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_business_profiles_responsiveness", "business_profiles", type_="check")
    op.drop_constraint("ck_business_profiles_speaking_speed", "business_profiles", type_="check")
    op.drop_column("business_profiles", "responsiveness")
    op.drop_column("business_profiles", "speaking_speed")
