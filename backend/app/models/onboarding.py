"""An unfinished business-setup form, kept so it can be resumed."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OnboardingDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """What a signed-in person has typed into the setup form so far.

    Accounts are created before businesses, so there is a window in which
    someone has an account and half a form. Keeping the half form here — not in
    the browser — is what lets them close the tab, sign in on another device
    tomorrow and carry on where they stopped.

    One per user, replaced on every save and deleted when the business is
    created. It is the customer's own unsubmitted input and nothing reads it
    except the form: no provisioning step, no generator, no email. It never
    holds a password; the account already has one.
    """

    __tablename__ = "onboarding_drafts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    data: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
