"""Authentication: magic links, sessions, and the checks that guard a request.

Two ways to sign in, one session afterwards.

    request_magic_link(email)  -> a short-lived, single-use token, emailed
            |
    exchange_magic_link(token) -> that token is consumed, a session is issued
            |
    authenticate(token)        -> every subsequent request

    sign_in_with_password(email, password)  -> a session is issued directly
            |
    authenticate(token)        -> every subsequent request

This was magic-link only at first, and deliberately so: a credential that is
never stored cannot be stolen from a dump or reused across sites. What changed
it is that a magic link cannot be delivered before outbound email works, which
left a business that had just signed up holding an account it could not open.
The password is what makes the first login possible on day one; the link stays
as the way back for anyone who has forgotten it, and as the only way in for an
account that never set one.

Both paths converge on the same :class:`Session` row, because what a session is
allowed to do must not depend on how it was created. A difference there would
have to be re-checked at every call site, and one missed check would be a
privilege the weaker path was never meant to grant.

Both stages are rows in ``sessions``, because a magic link and a session cookie
are the same object at different ages — a bearer token with an expiry that can
be revoked — and splitting them would duplicate the expiry, revocation and
replay logic that must be identical for both.

Enumeration is treated as a real leak throughout, and the password path is the
easier one to get wrong. ``request_magic_link`` behaves identically for an
address with an account and one without: same response, same timing class, no
distinguishing error. ``sign_in_with_password`` does the same, including the
timing — it runs a full Argon2 verification even when there is no account, so
that "no such user" cannot be told from "wrong password" by a stopwatch.
Otherwise the login form becomes a free oracle for "is this business a customer
of yours?".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InvalidInputError,
)
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
from app.services.passwords import hash_password, needs_rehash, verify
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


def _refusal_reason(user: User | None, password_correct: bool) -> str:
    """Why a password sign-in was refused, for the audit log only.

    Never surfaced to the caller. The distinction is what an operator needs to
    tell a forgotten password from a credential-stuffing run, and exactly what
    an attacker must not be told.
    """
    if user is None:
        return "no_account"
    if user.password_hash is None:
        return "magic_link_only_account"
    if not password_correct:
        return "wrong_password"
    return "inactive_account"


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

        if user.email_verified_at is None:
            # Following a link proves control of the inbox. This is the only
            # place verification happens, because it is the only place it is
            # actually demonstrated — choosing a password at signup shows
            # nothing about who reads the mail for that address.
            user.email_verified_at = now

        return await self._begin_session(
            user,
            now=now,
            ip_address=ip_address,
            user_agent=user_agent,
            method="magic_link",
        )

    async def sign_in_with_password(
        self,
        email: str,
        password: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedToken:
        """Verify a password and issue a session, or refuse indistinguishably.

        Four different things can be wrong here — no such address, an account
        that signs in by link and has no password, the wrong password, a
        suspended account — and all four raise the identical error. Naming which
        one it was would tell an attacker whether to keep trying this address or
        move on, which is most of the work in a credential-stuffing run.

        The timing matches too. :func:`app.services.passwords.verify` is called
        even when there is no account to verify against, so the expensive Argon2
        computation happens on every path rather than only on the one where a
        user was found.
        """
        now = datetime.now(UTC)
        normalized = email.strip().lower()
        user = await self._user_by_email(normalized)

        # Always called, including for `user is None`. See the docstring.
        correct = verify(password, user.password_hash if user is not None else None)

        if user is None or not correct or user.status is not UserStatus.ACTIVE:
            self.audit.record(
                AuditAction.LOGIN_FAILED,
                actor_type=ActorType.SYSTEM,
                actor=user,
                actor_label=normalized,
                ip_address=ip_address,
                user_agent=user_agent,
                # Recorded here but never returned: the operator investigating a
                # spike of these needs to know which it was, the caller does not.
                meta={"method": "password", "reason": _refusal_reason(user, correct)},
            )
            raise AuthenticationError("invalid email or password")

        if user.password_hash is not None and needs_rehash(user.password_hash):
            # The only moment the plain password exists alongside a hash made
            # with outdated parameters. Skipping it would pin every account to
            # the cost factor it was created with, permanently.
            user.password_hash = hash_password(password)

        return await self._begin_session(
            user,
            now=now,
            ip_address=ip_address,
            user_agent=user_agent,
            method="password",
        )

    async def register(
        self,
        *,
        full_name: str,
        email: str,
        password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedToken:
        """Create an account and sign it in — the first step of getting started.

        An address that already has an account is refused with a conflict, and
        that is a deliberate, stated trade-off: a create-account form has to say
        "this email already has an account, sign in instead", which confirms the
        address is registered. The login form still reveals nothing. The route
        rate-limits this, and the alternative — email-verified registration —
        needs outbound email working before anyone can start.

        The password is never written to an existing account here: that would be
        a takeover by form. Changing a password is Settings' job, behind the
        current one.
        """
        normalized = email.strip().lower()
        name = " ".join(full_name.split())
        if await self._user_by_email(normalized) is not None:
            raise ConflictError(
                "an account with this email already exists; sign in instead",
                code="account_exists",
            )

        user = User(email=normalized, full_name=name or None, password_hash=hash_password(password))
        self.session.add(user)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # Two registrations for one address raced; the loser is told what
            # it would have been told a moment later.
            await self.session.rollback()
            raise ConflictError(
                "an account with this email already exists; sign in instead",
                code="account_exists",
            ) from exc

        self.audit.record(
            AuditAction.ACCOUNT_REGISTERED,
            actor_type=ActorType.USER,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return await self._begin_session(
            user,
            now=datetime.now(UTC),
            ip_address=ip_address,
            user_agent=user_agent,
            method="register",
        )

    async def sign_in_new_account(
        self,
        user: User,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedToken:
        """Start a session for an account that signup created moments ago.

        Only for an account created *by this request* with the password the
        caller just chose — the caller is the person who typed it, so this is
        exactly a password sign-in without the round trip. It must never be used
        for an address that already had an account: signup is anonymous, and
        issuing a session there would hand a stranger's account to whoever
        typed their email into the form. :class:`SignupResult.password_set` is
        the flag that encodes the difference, and the signup route checks it.
        """
        return await self._begin_session(
            user,
            now=datetime.now(UTC),
            ip_address=ip_address,
            user_agent=user_agent,
            method="signup",
        )

    async def _begin_session(
        self,
        user: User,
        *,
        now: datetime,
        ip_address: str | None,
        user_agent: str | None,
        method: str,
    ) -> IssuedToken:
        """Issue the session both sign-in paths end at.

        Shared rather than duplicated so that a session's capabilities cannot
        drift apart depending on how it was created.
        """
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

        self.audit.record(
            AuditAction.LOGIN_SUCCEEDED,
            actor_type=ActorType.USER,
            actor=user,
            tenant_id=issued.session.active_tenant_id,
            ip_address=ip_address,
            user_agent=user_agent,
            meta={"method": method},
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

    async def change_password(
        self,
        principal: Principal,
        *,
        current_password: str | None,
        new_password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> int:
        """Set or change the signed-in user's password. Returns sessions revoked.

        Three rules:

        * **Support staff cannot do this while impersonating.** An operator who
          could set a customer's password could keep access after the audited
          impersonation ended.
        * **The current password is required when there is one.** A session
          left open on a shared computer must not be enough to take the account
          over permanently. An account that has only ever used links has no
          current password, and the link that signed it in already proved the
          inbox — the same proof a reset would ask for.
        * **Every other session is signed out.** Changing a password is what a
          person does when they suspect someone else has it; leaving that
          someone signed in would make the change decorative.
        """
        user = principal.user
        if principal.is_impersonating:
            raise AuthorizationError("a password cannot be changed while impersonating")

        had_password = user.password_hash is not None
        if had_password and not (current_password and verify(current_password, user.password_hash)):
            self.audit.record(
                AuditAction.LOGIN_FAILED,
                actor_type=ActorType.USER,
                actor=user,
                ip_address=ip_address,
                user_agent=user_agent,
                meta={"method": "password_change", "reason": "wrong_current_password"},
            )
            # 422, not 401: the session is fine, and a 401 would sign the
            # dashboard out over a typo.
            raise InvalidInputError(
                "your current password is incorrect", code="invalid_current_password"
            )

        user.password_hash = hash_password(new_password)

        now = datetime.now(UTC)
        others = (
            await self.session.execute(
                select(Session).where(
                    Session.user_id == user.id,
                    Session.status == SessionStatus.ACTIVE,
                    Session.id != principal.session.id,
                )
            )
        ).scalars()
        revoked = 0
        for row in others:
            row.status = SessionStatus.REVOKED
            row.revoked_at = now
            revoked += 1

        self.audit.record(
            AuditAction.PASSWORD_CHANGED,
            actor_type=ActorType.USER,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
            meta={"had_password": had_password, "sessions_revoked": revoked},
        )
        return revoked

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
