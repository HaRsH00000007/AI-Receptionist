"""allow a failed purchase to have no twilio sid

``ck_phone_numbers_twilio_sid_required_once_purchased`` originally exempted only
PENDING. That made the compensation path unable to record its own outcome: a run
that fails *before* Twilio is ever called leaves a row that never received a
SID, and marking it FAILED violated the constraint.

FAILED therefore joins PENDING in the exemption. The guarantee that matters is
unchanged: an ACTIVE or RELEASED number must still reference a real Twilio
resource, so a crash mid-purchase can never leave a row that looks provisioned
but points at nothing.

Found by tests/test_provisioning.py::test_a_terminal_error_is_never_retried.

Revision ID: 6505a4a93e92
Revises: 6c1f0a7b91d2
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "6505a4a93e92"
down_revision: str | None = "6c1f0a7b91d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "twilio_sid_required_once_purchased"
_OLD = "status = 'pending' OR twilio_sid IS NOT NULL"
_NEW = "status IN ('pending', 'failed') OR twilio_sid IS NOT NULL"


def upgrade() -> None:
    op.drop_constraint(_NAME, "phone_numbers", type_="check")
    op.create_check_constraint(_NAME, "phone_numbers", _NEW)


def downgrade() -> None:
    # A FAILED row with no SID would block the old constraint. Such a row
    # records a purchase that never happened, so retiring it loses nothing real.
    op.execute("DELETE FROM phone_numbers WHERE status = 'failed' AND twilio_sid IS NULL")
    op.drop_constraint(_NAME, "phone_numbers", type_="check")
    op.create_check_constraint(_NAME, "phone_numbers", _OLD)
