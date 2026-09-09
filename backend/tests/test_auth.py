"""Authentication: the credential lifecycle, and the ways it must refuse.

Most of these tests assert on a *refusal*. That is the point — an auth system is
judged by what it declines, and every case here is one an attacker actually
tries: a replayed magic link, an expired session, a revoked cookie, another
tenant's id in the URL, an address probed to see whether it has an account.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, AuthorizationError, ConfigurationError
from app.models.enums import (
    AuditAction,
    MembershipRole,
    SessionStatus,
    UserStatus,
    role_at_least,
)
from app.models.identity import Session, User
from app.services.auth import AuthService
from app.services.status_tokens import issue_status_token, verify_status_token
from app.services.tokens import generate_token, hash_token, tokens_match
from tests.factories import make_membership, make_session, make_tenant, make_user
from tests.support import build_settings

# ---------------------------------------------------------------------------
# Token primitives
# ---------------------------------------------------------------------------


def test_tokens_are_unique_and_unguessable() -> None:
    tokens = {generate_token() for _ in range(500)}
    assert len(tokens) == 500
    # 32 random bytes -> 43 URL-safe characters.
    assert all(len(token) >= 43 for token in tokens)


def test_only_the_hash_is_ever_stored() -> None:
    """A database dump must not contain anything usable as a credential."""
    token = generate_token()
    stored = hash_token(token)
    assert stored != token
    assert token not in stored
    assert len(stored) == 64


def test_token_matching_is_by_hash() -> None:
    token = generate_token()
    assert tokens_match(token, hash_token(token))
    assert not tokens_match(generate_token(), hash_token(token))


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


def test_role_ranking_is_not_alphabetical() -> None:
    """The bug this guards: comparing StrEnum members compares their strings.

    Alphabetically "admin" < "owner", so a naive comparison would make an admin
    outrank an owner. The explicit rank mapping is what prevents that.
    """
    assert role_at_least(MembershipRole.OWNER, MembershipRole.ADMIN)
    assert not role_at_least(MembershipRole.ADMIN, MembershipRole.OWNER)
    assert role_at_least(MembershipRole.ADMIN, MembershipRole.MEMBER)
    assert not role_at_least(MembershipRole.MEMBER, MembershipRole.ADMIN)
    assert role_at_least(MembershipRole.MEMBER, MembershipRole.MEMBER)


# ---------------------------------------------------------------------------
# Magic links
# ---------------------------------------------------------------------------


async def test_a_magic_link_is_issued_for_a_known_address(db_session: AsyncSession) -> None:
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    issued = await AuthService(db_session, build_settings()).request_magic_link(user.email)

    assert issued is not None
    assert issued.session.is_magic_link is True
    # The row stores the hash; the plain token is returned once and never kept.
    assert issued.session.token_hash == hash_token(issued.token)


async def test_no_link_is_issued_for_an_unknown_address(db_session: AsyncSession) -> None:
    issued = await AuthService(db_session, build_settings()).request_magic_link(
        "nobody@example.com"
    )
    assert issued is None


async def test_a_disabled_user_gets_no_link(db_session: AsyncSession) -> None:
    user = make_user(status=UserStatus.DISABLED)
    db_session.add(user)
    await db_session.flush()

    assert await AuthService(db_session, build_settings()).request_magic_link(user.email) is None


async def test_the_address_is_matched_case_insensitively(db_session: AsyncSession) -> None:
    """Two rows differing only in case would be two accounts for one inbox."""
    user = make_user(email="owner@example.com")
    db_session.add(user)
    await db_session.flush()

    issued = await AuthService(db_session, build_settings()).request_magic_link(
        "  OWNER@Example.COM  "
    )
    assert issued is not None


async def test_exchanging_a_link_issues_a_session(db_session: AsyncSession) -> None:
    service = AuthService(db_session, build_settings())
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    link = await service.request_magic_link(user.email)
    assert link is not None
    await db_session.flush()

    session = await service.exchange_magic_link(link.token)

    assert session.session.is_magic_link is False
    assert session.token != link.token
    assert user.last_login_at is not None
    # Following a link is the only proof of inbox control, so it is the only
    # place verification is recorded.
    assert user.email_verified_at is not None


async def test_a_magic_link_cannot_be_used_twice(db_session: AsyncSession) -> None:
    """The forwarded-email attack.

    Without single-use, a link sitting in an inbox stays a working credential
    until it expires.
    """
    service = AuthService(db_session, build_settings())
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    link = await service.request_magic_link(user.email)
    assert link is not None
    await db_session.flush()

    await service.exchange_magic_link(link.token)
    await db_session.flush()

    with pytest.raises(AuthenticationError):
        await service.exchange_magic_link(link.token)


async def test_an_expired_magic_link_is_refused(db_session: AsyncSession) -> None:
    service = AuthService(db_session, build_settings(magic_link_ttl_s=60))
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    link = await service.request_magic_link(user.email)
    assert link is not None
    link.session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    with pytest.raises(AuthenticationError):
        await service.exchange_magic_link(link.token)


async def test_a_session_token_cannot_be_used_as_a_magic_link(db_session: AsyncSession) -> None:
    """The two token kinds live in one table, so the flag must be enforced."""
    service = AuthService(db_session, build_settings())
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    link = await service.request_magic_link(user.email)
    assert link is not None
    await db_session.flush()
    session = await service.exchange_magic_link(link.token)
    await db_session.flush()

    with pytest.raises(AuthenticationError):
        await service.exchange_magic_link(session.token)


async def test_exchange_defaults_into_a_sole_tenant(db_session: AsyncSession) -> None:
    """A single-tenant customer should never have to pick their only option."""
    service = AuthService(db_session, build_settings())
    user, tenant = make_user(), make_tenant()
    db_session.add_all([user, tenant])
    await db_session.flush()
    db_session.add(make_membership(user, tenant))
    await db_session.flush()

    link = await service.request_magic_link(user.email)
    assert link is not None
    await db_session.flush()

    session = await service.exchange_magic_link(link.token)
    assert session.session.active_tenant_id == tenant.id


async def test_exchange_does_not_guess_between_several_tenants(db_session: AsyncSession) -> None:
    service = AuthService(db_session, build_settings())
    user = make_user()
    first, second = make_tenant(), make_tenant()
    db_session.add_all([user, first, second])
    await db_session.flush()
    db_session.add_all([make_membership(user, first), make_membership(user, second)])
    await db_session.flush()

    link = await service.request_magic_link(user.email)
    assert link is not None
    await db_session.flush()

    session = await service.exchange_magic_link(link.token)
    assert session.session.active_tenant_id is None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


async def test_a_valid_session_resolves_to_its_user(db_session: AsyncSession) -> None:
    service = AuthService(db_session, build_settings())
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    link = await service.request_magic_link(user.email)
    assert link is not None
    await db_session.flush()
    session = await service.exchange_magic_link(link.token)
    await db_session.flush()

    principal = await service.authenticate(session.token)
    assert principal.user.id == user.id


async def test_an_unknown_token_is_refused(db_session: AsyncSession) -> None:
    with pytest.raises(AuthenticationError):
        await AuthService(db_session, build_settings()).authenticate(generate_token())


async def test_an_expired_session_is_refused(db_session: AsyncSession) -> None:
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    token = generate_token()
    db_session.add(
        make_session(
            user,
            token_hash=hash_token(token),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    await db_session.flush()

    with pytest.raises(AuthenticationError):
        await AuthService(db_session, build_settings()).authenticate(token)


async def test_a_revoked_session_is_refused(db_session: AsyncSession) -> None:
    service = AuthService(db_session, build_settings())
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    token = generate_token()
    row = make_session(user, token_hash=hash_token(token))
    db_session.add(row)
    await db_session.flush()

    await service.logout(row)
    await db_session.flush()

    with pytest.raises(AuthenticationError):
        await service.authenticate(token)


async def test_logout_is_idempotent(db_session: AsyncSession) -> None:
    service = AuthService(db_session, build_settings())
    user = make_user()
    db_session.add(user)
    await db_session.flush()
    row = make_session(user)
    db_session.add(row)
    await db_session.flush()

    await service.logout(row)
    first_revoked_at = row.revoked_at
    await service.logout(row)

    assert row.status is SessionStatus.REVOKED
    # The second call must not move the timestamp — that would misreport when
    # access actually ended.
    assert row.revoked_at == first_revoked_at


async def test_revoking_all_sessions_signs_out_every_device(db_session: AsyncSession) -> None:
    service = AuthService(db_session, build_settings())
    user = make_user()
    db_session.add(user)
    await db_session.flush()
    db_session.add_all([make_session(user) for _ in range(3)])
    await db_session.flush()

    assert await service.revoke_all_for_user(user.id) == 3

    result = await db_session.execute(
        select(Session).where(Session.user_id == user.id, Session.status == SessionStatus.ACTIVE)
    )
    assert result.scalars().all() == []


async def test_a_disabled_user_loses_their_live_sessions(db_session: AsyncSession) -> None:
    """Disabling an account must take effect on the next request, not at expiry."""
    service = AuthService(db_session, build_settings())
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    token = generate_token()
    db_session.add(make_session(user, token_hash=hash_token(token)))
    await db_session.flush()

    assert (await service.authenticate(token)).user.id == user.id

    user.status = UserStatus.DISABLED
    await db_session.flush()

    with pytest.raises(AuthenticationError):
        await service.authenticate(token)


# ---------------------------------------------------------------------------
# Tenant access — the POC's vulnerability
# ---------------------------------------------------------------------------


async def test_a_tenant_id_alone_grants_nothing(db_session: AsyncSession) -> None:
    """The whole point of M3.

    In the POC, knowing a tenant UUID was sufficient to read that tenant's
    calls and transcripts. Here the id is a *request*, checked against a
    membership, and a valid session with no membership is refused.
    """
    service = AuthService(db_session, build_settings())
    user, theirs = make_user(), make_tenant()
    db_session.add_all([user, theirs])
    await db_session.flush()

    token = generate_token()
    db_session.add(make_session(user, token_hash=hash_token(token)))
    await db_session.flush()

    with pytest.raises(AuthorizationError):
        await service.authenticate(token, tenant_id=theirs.id)


async def test_membership_grants_access_to_its_own_tenant(db_session: AsyncSession) -> None:
    service = AuthService(db_session, build_settings())
    user, tenant = make_user(), make_tenant()
    db_session.add_all([user, tenant])
    await db_session.flush()
    db_session.add(make_membership(user, tenant, role=MembershipRole.ADMIN))
    token = generate_token()
    db_session.add(make_session(user, token_hash=hash_token(token)))
    await db_session.flush()

    principal = await service.authenticate(token, tenant_id=tenant.id)
    assert principal.tenant_id == tenant.id
    assert principal.role is MembershipRole.ADMIN


async def test_an_unaccepted_invitation_grants_nothing(db_session: AsyncSession) -> None:
    service = AuthService(db_session, build_settings())
    user, tenant = make_user(), make_tenant()
    db_session.add_all([user, tenant])
    await db_session.flush()
    db_session.add(make_membership(user, tenant, accepted_at=None))
    token = generate_token()
    db_session.add(make_session(user, token_hash=hash_token(token)))
    await db_session.flush()

    with pytest.raises(AuthorizationError):
        await service.authenticate(token, tenant_id=tenant.id)


async def test_a_platform_admin_does_not_bypass_membership(db_session: AsyncSession) -> None:
    """No silent superuser.

    An operator who needs to act inside a tenant does so via impersonation,
    which is audited as impersonation. A quiet bypass here would make the audit
    log misattribute the action to the customer.
    """
    service = AuthService(db_session, build_settings())
    admin, theirs = make_user(is_platform_admin=True), make_tenant()
    db_session.add_all([admin, theirs])
    await db_session.flush()
    token = generate_token()
    db_session.add(make_session(admin, token_hash=hash_token(token)))
    await db_session.flush()

    with pytest.raises(AuthorizationError):
        await service.authenticate(token, tenant_id=theirs.id)


async def test_require_role_enforces_the_hierarchy(db_session: AsyncSession) -> None:
    service = AuthService(db_session, build_settings())
    user, tenant = make_user(), make_tenant()
    db_session.add_all([user, tenant])
    await db_session.flush()
    db_session.add(make_membership(user, tenant, role=MembershipRole.MEMBER))
    token = generate_token()
    db_session.add(make_session(user, token_hash=hash_token(token)))
    await db_session.flush()

    principal = await service.authenticate(token, tenant_id=tenant.id)

    principal.require_role(MembershipRole.MEMBER)
    with pytest.raises(AuthorizationError):
        principal.require_role(MembershipRole.ADMIN)
    with pytest.raises(AuthorizationError):
        principal.require_role(MembershipRole.OWNER)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


async def test_a_successful_login_is_audited(db_session: AsyncSession) -> None:
    from app.models.audit import AuditLog

    service = AuthService(db_session, build_settings())
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    link = await service.request_magic_link(user.email)
    assert link is not None
    await db_session.flush()
    await service.exchange_magic_link(link.token)
    await db_session.flush()

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_SUCCEEDED)
    )
    entries = list(result.scalars().all())
    assert len(entries) == 1
    assert entries[0].actor_user_id == user.id


async def test_a_failed_login_is_audited(db_session: AsyncSession) -> None:
    """Repeated failures are what an attack looks like; the log is where it shows."""
    from app.models.audit import AuditLog

    service = AuthService(db_session, build_settings())
    with pytest.raises(AuthenticationError):
        await service.exchange_magic_link(generate_token())
    await db_session.flush()

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_FAILED)
    )
    assert len(list(result.scalars().all())) == 1


async def test_audit_metadata_never_stores_a_credential(db_session: AsyncSession) -> None:
    from app.models.enums import ActorType
    from app.services.audit import AuditService

    entry = AuditService(db_session).record(
        AuditAction.ADMIN_ACTION,
        actor_type=ActorType.ADMIN,
        meta={
            "authorization": "Bearer sk-live-should-never-persist",
            "nested": {"api_key": "also-not-this", "kept": "this is fine"},
            "harmless": "value",
        },
    )

    assert entry.meta_json["authorization"] == "[redacted]"
    assert entry.meta_json["nested"]["api_key"] == "[redacted]"
    assert entry.meta_json["nested"]["kept"] == "this is fine"
    assert entry.meta_json["harmless"] == "value"


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


async def _login(api_app: FastAPI, api_client: AsyncClient, email: str) -> str:
    """Drive the real endpoints to obtain a session token."""
    factory = api_app.state.session_factory
    async with factory() as session:
        service = AuthService(session, api_app.state.settings)
        issued = await service.request_magic_link(email)
        assert issued is not None
        await session.commit()
        token = issued.token

    response = await api_client.post("/api/v1/auth/session", json={"token": token})
    assert response.status_code == 200
    session_token: str = response.json()["token"]
    return session_token


async def test_magic_link_endpoint_is_uninformative(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """The account-enumeration defence, asserted end to end.

    An unknown address and a known one must be indistinguishable from the
    outside: same status, same body.
    """
    factory = api_app.state.session_factory
    async with factory() as session:
        session.add(make_user(email="known@example.com"))
        await session.commit()

    known = await api_client.post("/api/v1/auth/magic-link", json={"email": "known@example.com"})
    unknown = await api_client.post(
        "/api/v1/auth/magic-link", json={"email": "unknown@example.com"}
    )

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()


async def test_me_requires_authentication(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_a_bad_bearer_token_is_refused(api_client: AsyncClient) -> None:
    response = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {generate_token()}"}
    )
    assert response.status_code == 401


async def test_a_malformed_authorization_header_is_refused(api_client: AsyncClient) -> None:
    for header in ("Bearer", "Basic abc", "token123", ""):
        response = await api_client.get("/api/v1/auth/me", headers={"Authorization": header})
        assert response.status_code == 401, header


async def test_the_full_login_flow_over_http(api_app: FastAPI, api_client: AsyncClient) -> None:
    factory = api_app.state.session_factory
    async with factory() as session:
        session.add(make_user(email="owner@example.com", full_name="Sam Owner"))
        await session.commit()

    token = await _login(api_app, api_client, "owner@example.com")

    me = await api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "owner@example.com"
    assert me.json()["full_name"] == "Sam Owner"


async def test_the_session_cookie_is_httponly_and_scoped(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """An XSS bug must not be able to read the session."""
    factory = api_app.state.session_factory
    async with factory() as session:
        session.add(make_user(email="cookie@example.com"))
        await session.commit()
        service = AuthService(session, api_app.state.settings)

    async with factory() as session:
        service = AuthService(session, api_app.state.settings)
        issued = await service.request_magic_link("cookie@example.com")
        assert issued is not None
        await session.commit()
        link_token = issued.token

    response = await api_client.post("/api/v1/auth/session", json={"token": link_token})
    header = response.headers["set-cookie"].lower()

    assert api_app.state.settings.session_cookie_name in header
    assert "httponly" in header
    assert "path=/" in header
    assert "samesite=lax" in header


async def test_logging_out_invalidates_the_token(api_app: FastAPI, api_client: AsyncClient) -> None:
    factory = api_app.state.session_factory
    async with factory() as session:
        session.add(make_user(email="bye@example.com"))
        await session.commit()

    token = await _login(api_app, api_client, "bye@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    assert (await api_client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    assert (await api_client.delete("/api/v1/auth/session", headers=headers)).status_code == 204
    assert (await api_client.get("/api/v1/auth/me", headers=headers)).status_code == 401


async def test_login_attempts_are_rate_limited(api_app: FastAPI, api_client: AsyncClient) -> None:
    """A magic-link form is both a spam relay and a brute-force surface."""
    api_app.state.login_limiter.reset()
    limit = api_app.state.settings.login_rate_limit

    for _ in range(limit):
        response = await api_client.post(
            "/api/v1/auth/magic-link", json={"email": "flood@example.com"}
        )
        assert response.status_code == 202

    blocked = await api_client.post("/api/v1/auth/magic-link", json={"email": "flood@example.com"})
    assert blocked.status_code == 429


async def test_rate_limiting_does_not_lock_out_a_different_account(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """Keying on the address alone would hand out a free denial of service.

    An attacker could burn a victim's budget and lock them out of their own
    account, so the key pairs address with source.
    """
    api_app.state.login_limiter.reset()
    for _ in range(api_app.state.settings.login_rate_limit):
        await api_client.post("/api/v1/auth/magic-link", json={"email": "victim@example.com"})

    other = await api_client.post(
        "/api/v1/auth/magic-link", json={"email": "bystander@example.com"}
    )
    assert other.status_code == 202


async def test_an_unknown_tenant_id_is_not_confirmed(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """404, never 403 — a 403 would confirm the tenant exists."""
    factory = api_app.state.session_factory
    async with factory() as session:
        user = make_user(email="probe@example.com")
        theirs = make_tenant()
        session.add_all([user, theirs])
        await session.commit()
        their_id = theirs.id

    token = await _login(api_app, api_client, "probe@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    existing = await api_client.get(f"/api/v1/tenants/{their_id}", headers=headers)
    absent = await api_client.get(f"/api/v1/tenants/{uuid.uuid4()}", headers=headers)

    # Identical, and identically unhelpful. A 403 for the first would confirm
    # that tenant exists, which is exactly the enumeration oracle being closed.
    assert existing.status_code == absent.status_code == 404


# ---------------------------------------------------------------------------
# Configuration coupling
# ---------------------------------------------------------------------------


def test_the_cookie_name_matches_the_dependency_alias() -> None:
    """The one place a literal duplicates configuration.

    ``get_principal`` declares the cookie alias as a literal because FastAPI
    reads dependency signatures at import time, before Settings exists. If the
    configured name ever changes, the cookie would be written under one name and
    read under another — login would silently never stick. This test is what
    stops that.
    """
    from typing import get_type_hints

    from app.api.auth_deps import get_principal

    # get_type_hints, not inspect.signature: `from __future__ import
    # annotations` leaves annotations as strings, so the Cookie(...) metadata is
    # only reachable once they are resolved with include_extras.
    hints = get_type_hints(get_principal, include_extras=True)
    alias = hints["session_cookie"].__metadata__[0].alias
    assert alias == build_settings().session_cookie_name


def test_a_user_model_carries_no_password_column() -> None:
    """Passwordless is structural, not a convention.

    There is no credential to steal from a dump, reuse across sites, or leak in
    a log — and a future change that adds one should have to delete this test.
    """
    columns = set(User.__table__.columns.keys())
    assert not {"password", "password_hash", "hashed_password"} & columns


# ---------------------------------------------------------------------------
# Status grants — the replacement for "the tenant UUID is the credential"
# ---------------------------------------------------------------------------


def test_a_status_token_round_trips() -> None:
    settings = build_settings()
    tenant_id = uuid.uuid4()
    token = issue_status_token(settings, tenant_id)
    assert verify_status_token(settings, token) == tenant_id


def test_a_status_token_expires() -> None:
    settings = build_settings()
    token = issue_status_token(settings, uuid.uuid4(), ttl_s=60, now=1_000.0)

    assert verify_status_token(settings, token, now=1_030.0)
    with pytest.raises(AuthenticationError):
        verify_status_token(settings, token, now=1_061.0)


def test_a_status_token_cannot_be_edited() -> None:
    """The signature covers both the tenant and the expiry."""
    settings = build_settings()
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    token = issue_status_token(settings, mine)
    _, _, expiry, signature = token.split(".")

    # Swap in another tenant, keep the signature.
    with pytest.raises(AuthenticationError):
        verify_status_token(settings, f"v1.{theirs}.{expiry}.{signature}")

    # Push the expiry out, keep the signature.
    with pytest.raises(AuthenticationError):
        verify_status_token(settings, f"v1.{mine}.{int(expiry) + 10_000}.{signature}")


def test_a_status_token_from_another_secret_is_refused() -> None:
    issued = issue_status_token(
        build_settings(auth_secret_key="secret-one-aaaaaaaaaaaaaaaaaaaa"), uuid.uuid4()
    )
    with pytest.raises(AuthenticationError):
        verify_status_token(
            build_settings(auth_secret_key="secret-two-bbbbbbbbbbbbbbbbbbbb"), issued
        )


@pytest.mark.parametrize(
    "malformed",
    ["", "garbage", "v1.not-a-uuid.999.sig", "v2." + str(uuid.uuid4()) + ".999.sig", "a.b.c"],
)
def test_a_malformed_status_token_is_refused(malformed: str) -> None:
    with pytest.raises(AuthenticationError):
        verify_status_token(build_settings(), malformed)


def test_signing_without_a_secret_is_refused_rather_than_defaulted() -> None:
    """A signing key that silently falls back to a constant is forgeable."""
    with pytest.raises(ConfigurationError):
        issue_status_token(build_settings(auth_secret_key=""), uuid.uuid4())


async def test_a_bare_tenant_uuid_no_longer_grants_access(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """The POC's vulnerability, asserted closed at the HTTP boundary."""
    factory = api_app.state.session_factory
    async with factory() as session:
        tenant = make_tenant()
        session.add(tenant)
        await session.commit()
        tenant_id = tenant.id

    response = await api_client.get(f"/api/v1/tenants/{tenant_id}")
    assert response.status_code == 401


async def test_a_status_grant_opens_the_status_page(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    factory = api_app.state.session_factory
    async with factory() as session:
        tenant = make_tenant()
        session.add(tenant)
        await session.commit()
        tenant_id = tenant.id

    token = issue_status_token(api_app.state.settings, tenant_id)
    response = await api_client.get(f"/api/v1/tenants/{tenant_id}", params={"status_token": token})
    assert response.status_code == 200
    assert response.json()["id"] == str(tenant_id)


async def test_a_status_grant_does_not_open_another_tenant(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    factory = api_app.state.session_factory
    async with factory() as session:
        mine, theirs = make_tenant(), make_tenant()
        session.add_all([mine, theirs])
        await session.commit()
        mine_id, theirs_id = mine.id, theirs.id

    token = issue_status_token(api_app.state.settings, mine_id)
    response = await api_client.get(f"/api/v1/tenants/{theirs_id}", params={"status_token": token})
    assert response.status_code == 404


async def test_signup_returns_a_usable_status_grant(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """End to end: the flow the status page actually takes."""
    signup = await api_client.post(
        "/api/v1/signups",
        json={
            "business_name": "Sunset Salon",
            "business_type": "salon",
            "services": ["cuts", "color"],
            "operating_hours": "Mon-Fri 9-6",
            "greeting_style": "professional",
            "notification_email": "owner@sunset.example.com",
            "area_code": "805",
            "contact_phone": "+15551234567",
        },
    )
    assert signup.status_code == 201
    body = signup.json()

    status_page = await api_client.get(
        f"/api/v1/tenants/{body['tenant_id']}",
        params={"status_token": body["status_token"]},
    )
    assert status_page.status_code == 200
    assert status_page.json()["name"] == "Sunset Salon"
