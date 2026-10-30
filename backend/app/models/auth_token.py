"""Single-use credentials that are *not* sessions.

A password reset and an email verification are both "prove you read this inbox,
once, soon". A magic link is the same sentence with a different ending — it
becomes a session — which is why that one lives in ``sessions`` alongside the
thing it turns into, and these two do not.

Keeping them out of ``sessions`` is a security property rather than tidiness.
``AuthService.authenticate`` resolves a bearer token by looking ``sessions`` up
by hash; a reset token stored there would be one ``is_magic_link`` check away
from being accepted as a session, and that check is exactly the kind that gets
dropped in a refactor. A reset token in its own table cannot authenticate
anything, whatever anyone forgets.

The same two rules as every other credential here (see
:mod:`app.services.tokens`): only the SHA-256 is stored, and single use is
enforced by ``consumed_at`` inside the transaction that acts on it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AuthTokenPurpose, enum_column


class AuthToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One short-lived, single-use proof of inbox control."""

    __tablename__ = "auth_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[AuthTokenPurpose] = mapped_column(
        enum_column(AuthTokenPurpose, "auth_token_purpose"), nullable=False
    )
    #: SHA-256 hex of the value in the email. Unique, so a collision cannot let
    #: one person's token resolve to another's account.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    #: Set on use. What makes the token single-use, and what makes a forwarded
    #: email stop working the moment the first reader has acted on it.
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    #: For the audit trail only, never for validation — the person who clicks
    #: the link in the email is routinely on a different network from the one
    #: that asked for it.
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Superseding a user's outstanding tokens when a new one is issued, and
        # the retention sweep.
        Index("ix_auth_tokens_user_id_purpose", "user_id", "purpose"),
        Index("ix_auth_tokens_expires_at", "expires_at"),
    )

    def is_usable_at(self, moment: datetime | None = None) -> bool:
        """Unconsumed and unexpired. The only definition of "valid" here."""
        now = moment or datetime.now(UTC)
        return self.consumed_at is None and self.expires_at > now
