"""Users, their membership of organizations, and their sessions.

The POC had no identity at all: a tenant UUID in a URL was the only thing
standing between a caller and another business's transcripts. These three
tables are what replace that.

The shape is deliberately *user ↔ membership ↔ tenant* rather than a
``tenant_id`` on the user. One person legitimately belongs to more than one
business — an agency running reception for several clients, an owner who also
helps out at a friend's salon — and a schema that assumes otherwise has to be
migrated under load later. The join table costs one extra query and removes
that whole class of rework.

``tenants`` remains the organization. There is no separate ``organizations``
table: a second table with the same lifecycle and the same primary key would be
two names for one row, and every query would have to remember to join it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    MembershipRole,
    SessionStatus,
    UserStatus,
    enum_column,
)

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One person. Not one business — see :class:`Membership`.

    Two ways in, and a row may support either or both. ``password_hash`` is the
    one people expect and the one that works on the day they sign up; the magic
    link in :class:`Session` needs no stored credential at all and remains the
    way back in for someone who has forgotten the password.

    Passwords were deliberately absent at first: a credential that is never
    stored cannot be stolen from a dump or reused across sites. What changed the
    decision is that a magic link cannot be sent before outbound email works,
    which left a new customer with an account they could not reach. The column
    is Argon2id, never the password (see :mod:`app.services.passwords`), and it
    is nullable — an account created before this, or invited without one, has no
    password and signs in by link.

    ``email`` is stored lower-cased and is globally unique: it is the identifier
    both a magic link and a password sign-in resolve through, so two rows
    differing only in case would be two accounts for one inbox.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    #: Optional: we know the address before we know the name, and asking for a
    #: full name at signup costs conversions for something the greeting never
    #: uses.
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        enum_column(UserStatus, "user_status"),
        nullable=False,
        default=UserStatus.ACTIVE,
    )
    #: Argon2id, or NULL for an account that signs in by link only. Never
    #: written by signup for an address that already exists: letting a second
    #: submission set a password on someone else's account would be a takeover
    #: with no theft required.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Set the first time a magic link is followed. Until then the address is
    #: claimed but unproven, which matters before anything is billed to it.
    #:
    #: A password sign-in deliberately does not set it. Choosing a password at
    #: signup proves nothing about the inbox, and recording it as proof would
    #: make the column mean two different things.
    email_verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)

    #: Platform staff, not a tenant role. Gates the admin surface. A boolean
    #: rather than a role table because there are two answers and adding a
    #: table would imply a hierarchy that does not exist.
    is_platform_admin: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    # `foreign_keys` is required on both sides here, not optional style. Both
    # children carry a *second* reference back to users — `invited_by_user_id`
    # and `impersonated_by_user_id` — so without it SQLAlchemy cannot tell which
    # column means "belongs to this user" and refuses to configure the mapper.
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="Membership.user_id",
    )
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="Session.user_id",
    )

    __table_args__ = (Index("ix_users_status", "status"),)


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's role within one tenant.

    This row *is* the authorization decision. Every tenant-scoped request
    resolves to one of these or is refused, which keeps "may this person do
    this?" in one place instead of spread across endpoint handlers.

    The partial unique index on ``OWNER`` is what stops a tenant from ending up
    with two owners after a concurrent invite, or none after a removal — either
    of which leaves billing with no responsible party.
    """

    __tablename__ = "memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MembershipRole] = mapped_column(
        enum_column(MembershipRole, "membership_role"),
        nullable=False,
        default=MembershipRole.MEMBER,
    )
    #: Who invited them, for the audit trail. Nullable because the founding
    #: owner is created by signup, not invited by anyone.
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Null while an invitation is outstanding. An unaccepted membership grants
    #: nothing — authorization checks require this to be set.
    accepted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    user: Mapped[User] = relationship(back_populates="memberships", foreign_keys=[user_id])
    tenant: Mapped[Tenant] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_memberships_user_id_tenant_id"),
        Index(
            "uq_memberships_one_owner_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text(f"role = '{MembershipRole.OWNER.value}'"),
        ),
        Index("ix_memberships_tenant_id", "tenant_id"),
    )


class Session(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A logged-in browser session, and the magic link that created it.

    Both live in one table on purpose. A magic link and a session cookie are the
    same thing at different stages — a bearer token with an expiry that can be
    revoked — and splitting them would duplicate the expiry, revocation and
    replay logic that has to be identical for both.

    **Only hashes are stored.** ``token_hash`` holds a SHA-256 of the value the
    client holds, so a database dump does not hand over live sessions. The plain
    token exists exactly once, in the response that issued it.

    ``consumed_at`` is what makes a magic link single-use: it is set on first
    exchange, and a second attempt with the same link is refused. Without it, a
    link sitting in an inbox stays a working credential until it expires, which
    is a forwarded-email away from an account takeover.
    """

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: SHA-256 hex of the bearer token. Unique so a collision cannot silently
    #: hand one user another's session.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: True while this row is an unexchanged magic link rather than a session.
    is_magic_link: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )
    status: Mapped[SessionStatus] = mapped_column(
        enum_column(SessionStatus, "session_status"),
        nullable=False,
        default=SessionStatus.ACTIVE,
    )
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)

    #: Recorded for the audit trail and for "sign out my other devices". Not
    #: used to *validate* a session: IPs change legitimately mid-session on
    #: mobile networks, and binding to one would log people out on a train.
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Which tenant this session is currently acting in, for a user who belongs
    #: to several. Authorization still re-checks the membership on every
    #: request — this is a convenience, never the permission itself.
    active_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True
    )

    #: Set when a platform admin is impersonating. Its presence is what makes
    #: every action in this session auditable as impersonation rather than as
    #: the customer's own work.
    impersonated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="sessions", foreign_keys=[user_id])

    __table_args__ = (
        # The lookup on every authenticated request: hash, then status and
        # expiry. Composite so the common case is answered from the index.
        Index("ix_sessions_token_hash_status", "token_hash", "status"),
        Index("ix_sessions_user_id_status", "user_id", "status"),
        # Drives the cleanup job that removes expired rows.
        Index("ix_sessions_expires_at", "expires_at"),
    )

    def is_usable_at(self, now: datetime) -> bool:
        """Whether this token may be accepted right now.

        One predicate, used by both the magic-link exchange and the session
        check, so the two can never drift into disagreeing about what expired
        means.
        """
        return (
            self.status == SessionStatus.ACTIVE
            and self.consumed_at is None
            and self.expires_at > now
        )


def session_metadata(request_ip: str | None, user_agent: str | None) -> dict[str, Any]:
    """Normalize what we record about where a session came from.

    Truncates rather than rejects: an oversized User-Agent is a curiosity, not a
    reason to fail a login.
    """
    return {
        "ip_address": (request_ip or None) if request_ip is None else request_ip[:45],
        "user_agent": (user_agent or None) if user_agent is None else user_agent[:1024],
    }
