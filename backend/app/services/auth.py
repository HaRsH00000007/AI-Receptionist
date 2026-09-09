"""Authentication: magic links, sessions, and the checks that guard a request.

Passwordless by design. There is no password column anywhere in the schema, so
there is no credential to steal from a dump, reuse across sites, or leak in a
log. The tradeoff is an explicit one: account access becomes email access, which
is already true of any system with a password-reset flow.

The flow:

    request_magic_link(email)  -> a short-lived, single-use token, emailed
            |
    exchange_magic_link(token) -> that token is consumed, a session is issued
            |
    authenticate(token)        -> every subsequent request

Both stages are rows in ``sessions``, because a magic link and a session cookie
are the same object at different ages — a bearer token with an expiry that can
be revoked — and splitting them would duplicate the expiry, revocation and
replay logic that must be identical for both.

Enumeration is treated as a real leak throughout. ``request_magic_link`` behaves
identically for an address with an account and one without: same response, same
timing class, no distinguishing error. Otherwise the login form becomes a free
oracle for "is this business a customer of yours?".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.errors import AuthenticationError, AuthorizationError
from app.core.logging import get_logger
from app.models.enums import (
    ActorType,
    AuditAction,
    MembershipRole,
    SessionStatus,
    UserStatus,
    role_at_least,
)
from app.models.identity import Membership, Session, User
from app.services.audit import AuditService
from app.services.tokens import generate_token, hash_token

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A token and its row. The plain value exists only here, once."""

    token: str
    session: Session


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making this request, and in which tenant.

    Resolved once per request and passed down. Handlers receive this rather than
    a user id so that a handler cannot accidentally trust a tenant id that came
    from the URL — the POC's exact vulnerability, where the tenant UUID in the
    path *was* the credential.
    """

    user: User
    session: Session
    #: The tenant this request acts in, and the role held there. Both None for a
    #: platform admin operating outside any tenant.
    tenant_id: uuid.UUID | None
    role: MembershipRole | None

    @property
    def is_impersonating(self) -> bool:
        return self.session.impersonated_by_user_id is not None

    @property
    def actor_type(self) -> ActorType:
        """How this actor is recorded in the audit log.

        Impersonation outranks the other labels: an action taken while
        impersonating must never be indistinguishable from one the customer
        took themselves.
        """
        if self.is_impersonating:
            return ActorType.IMPERSONATION
        if self.user.is_platform_admin:
            return ActorType.ADMIN
        return ActorType.USER

    def require_role(self, required: MembershipRole) -> None:
        """Raise unless this principal holds at least ``required`` in its tenant.

        Platform admins are *not* exempt. An admin who needs to act inside a
        tenant does so through impersonation, which is audited as such — a
        silent bypass here would make the audit log lie about who did what.
        """
        if self.role is None or not role_at_least(self.role, required):
            raise AuthorizationError(
                "insufficient permissions for this action",
                details={"required_role": required.value},
            )


class AuthService:
    """Issues, exchanges and validates credentials."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.audit = AuditService(session)

    # ---------------------------------------------------------------------
    # Issuing
    # ---------------------------------------------------------------------

    async def request_magic_link(
        self,
        email: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedToken | None:
        """Issue a single-use login link, or return ``None`` if there is no account.

        The ``None`` is for the *caller's* benefit — it decides whether to send
        an email — and must never reach the client. The endpoint returns the
        same 202 either way. A caller that leaks this distinction reintroduces
        account enumeration.
        """
        normalized = email.strip().lower()
        user = await self._user_by_email(normalized)

        if user is None or user.status is not UserStatus.ACTIVE:
            # Audited even though nothing was issued: repeated requests for an
            # address with no account are what a login-enumeration attempt looks
            # like, and the log is where that becomes visible.
            self.audit.record(
                AuditAction.MAGIC_LINK_REQUESTED,
                actor_type=ActorType.SYSTEM,
                actor_label=normalized,
                ip_address=ip_address,
                user_agent=user_agent,
                meta={"issued": False, "reason": "no_active_account"},
            )
            return None

        issued = self._issue(
            user,
            ttl_s=self.settings.magic_link_ttl_s,
            is_magic_link=True,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.audit.record(
            AuditAction.MAGIC_LINK_REQUESTED,
            actor_type=ActorType.SYSTEM,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
            meta={"issued": True},
        )
        return issued

    async def exchange_magic_link(
        self,
        token: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedToken:
        """Consume a magic link and issue a session.

        Single-use is enforced by ``consumed_at``, set inside the same
        transaction that creates the session. Without it a link sitting in an
        inbox stays a working credential until expiry — one forwarded email away
        from an account takeover.
        """
        now = datetime.now(UTC)
        link = await self._session_by_token(token)

        if link is None or not link.is_magic_link or not link.is_usable_at(now):
            self.audit.record(
                AuditAction.LOGIN_FAILED,
                actor_type=ActorType.SYSTEM,
                actor_label=None,
                ip_address=ip_address,
                user_agent=user_agent,
                meta={"reason": "invalid_or_expired_magic_link"},
            )
            # Uniform message: distinguishing "already used" from "expired" from
            # "never existed" tells an attacker which half to keep working on.
            raise AuthenticationError("invalid or expired login link")

        user = await self.session.get(User, link.user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            raise AuthenticationError("invalid or expired login link")

        link.consumed_at = now
        link.status = SessionStatus.REVOKED
        link.revoked_at = now

        issued = self._issue(
            user,
            ttl_s=self.settings.session_ttl_s,
            is_magic_link=False,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        # Default the session into the user's only tenant, so a single-tenant
        # customer never has to choose one. Ambiguous for a multi-tenant user,
        # who selects explicitly.
        memberships = await self._accepted_memberships(user.id)
        if len(memberships) == 1:
            issued.session.active_tenant_id = memberships[0].tenant_id

        user.last_login_at = now
        if user.email_verified_at is None:
            # Following a link proves control of the inbox. This is the only
            # place verification happens, because it is the only place it is
            # actually demonstrated.
            user.email_verified_at = now

        self.audit.record(
            AuditAction.LOGIN_SUCCEEDED,
            actor_type=ActorType.USER,
            actor=user,
            tenant_id=issued.session.active_tenant_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return issued

    def _issue(
        self,
        user: User,
        *,
        ttl_s: int,
        is_magic_link: bool,
        ip_address: str | None,
        user_agent: str | None,
    ) -> IssuedToken:
        """Create a token row. The plain token is returned and never stored."""
        token = generate_token()
        row = Session(
            user_id=user.id,
            token_hash=hash_token(token),
            is_magic_link=is_magic_link,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
            ip_address=ip_address[:45] if ip_address else None,
            user_agent=user_agent[:1024] if user_agent else None,
        )
        self.session.add(row)
        return IssuedToken(token=token, session=row)

    # ---------------------------------------------------------------------
    # Validating
    # ---------------------------------------------------------------------

    async def authenticate(
        self,
        token: str,
        *,
        tenant_id: uuid.UUID | None = None,
    ) -> Principal:
        """Resolve a session token to a :class:`Principal`, or refuse.

        ``tenant_id`` is the tenant the request is *asking* to act in — from the
        URL, typically. It is verified against an accepted membership here; it
        never grants anything by itself. That inversion is the fix for the POC,
        where the tenant id in the path was accepted as proof of access.
        """
        now = datetime.now(UTC)
        row = await self._session_by_token(token)

        if row is None or row.is_magic_link or not row.is_usable_at(now):
            raise AuthenticationError("invalid or expired session")

        user = await self.session.get(User, row.user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            raise AuthenticationError("invalid or expired session")

        # Cheap liveness signal for "sign out my other devices". Not part of
        # validation: writing on every request would make each read a write.
        row.last_seen_at = now

        requested = tenant_id or row.active_tenant_id
        if requested is None:
            return Principal(user=user, session=row, tenant_id=None, role=None)

        membership = await self._membership(user.id, requested)
        if membership is None or membership.accepted_at is None:
            # NotFound-shaped, not Forbidden: telling a caller that a tenant
            # exists but is not theirs is an enumeration oracle. The API layer
            # renders this as a 404 for exactly that reason.
            raise AuthorizationError(
                "no access to this organization",
                details={"tenant_id": str(requested)},
            )

        return Principal(
            user=user,
            session=row,
            tenant_id=membership.tenant_id,
            role=membership.role,
        )

    async def logout(self, session_row: Session) -> None:
        """Revoke one session. Idempotent — logging out twice is not an error."""
        if session_row.status is SessionStatus.ACTIVE:
            session_row.status = SessionStatus.REVOKED
            session_row.revoked_at = datetime.now(UTC)
        self.audit.record(
            AuditAction.LOGOUT,
            actor_type=ActorType.USER,
            actor_label=None,
            tenant_id=session_row.active_tenant_id,
            entity_type="Session",
            entity_id=session_row.id,
        )

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        """Revoke every live session for a user. Returns how many were revoked.

        The "sign out everywhere" action, and the thing to run when an account
        is suspected compromised.
        """
        now = datetime.now(UTC)
        result = await self.session.execute(
            select(Session).where(
                Session.user_id == user_id,
                Session.status == SessionStatus.ACTIVE,
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = SessionStatus.REVOKED
            row.revoked_at = now
        return len(rows)

    # ---------------------------------------------------------------------
    # Lookups
    # ---------------------------------------------------------------------

    async def _user_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email).limit(1))
        return result.scalar_one_or_none()

    async def _session_by_token(self, token: str) -> Session | None:
        """Look up by hash.

        The presented token is hashed and matched by index; the stored hash is
        never compared byte-by-byte against a candidate in Python, so there is
        no timing channel to exploit here.
        """
        result = await self.session.execute(
            select(Session).where(Session.token_hash == hash_token(token)).limit(1)
        )
        return result.scalar_one_or_none()

    async def _membership(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> Membership | None:
        result = await self.session.execute(
            select(Membership)
            .where(Membership.user_id == user_id, Membership.tenant_id == tenant_id)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _accepted_memberships(self, user_id: uuid.UUID) -> list[Membership]:
        result = await self.session.execute(
            select(Membership)
            .where(Membership.user_id == user_id, Membership.accepted_at.is_not(None))
            .options(selectinload(Membership.tenant))
        )
        return list(result.scalars().all())
