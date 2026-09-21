"""Signing in with a password, and the account signup now creates.

Two things are under test and they are easy to conflate. The first is that a
business which has just signed up *has an account at all* — before this, signup
produced a tenant nobody could sign in to. The second is that the password path
refuses in exactly the same way for every kind of wrong, because a login form
that answers differently for "no such address" is a directory of your customers.

The takeover test is the one to read first. Signup is anonymous, so anything
that writes a password onto an address that already exists is a way to seize a
stranger's account by typing their email into a form.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError
from app.models.enums import MembershipRole, UserStatus
from app.models.identity import Membership, User
from app.schemas.signup import SignupRequest
from app.services.auth import AuthService
from app.services.passwords import MIN_LENGTH, hash_password, verify
from app.services.signup import SignupService
from tests.factories import make_tenant, make_user
from tests.support import build_settings

PASSWORD = "correct horse battery"
OTHER_PASSWORD = "a different password"

SIGNUP: dict[str, Any] = {
    "business_name": "Sunset Salon",
    "business_type": "salon",
    "services": "cuts, color",
    "operating_hours": "Mon-Fri 9-6",
    "greeting_style": "friendly",
    "escalation_rules": "Text the owner for emergencies",
    # Unique to this module. The shared fixture address is used by other test
    # files, and a live tenant left behind by one of them would make signup
    # return that tenant rather than create the account under test.
    "notification_email": "owner@password-auth.example.com",
    "area_code": "212",
    "plan": "starter",
    "contact_phone": "8055550142",
    "password": PASSWORD,
}


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------
def test_the_password_is_not_recoverable_from_its_hash() -> None:
    stored = hash_password(PASSWORD)
    assert PASSWORD not in stored
    assert stored.startswith("$argon2id$")


def test_the_same_password_hashes_differently_every_time() -> None:
    """Salted, so two customers with the same password do not share a hash.

    An unsalted scheme lets one cracked hash unlock every account that chose
    the same password, and makes that fact visible in a dump before any
    cracking starts.
    """
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_verification_accepts_the_password_and_rejects_the_rest() -> None:
    stored = hash_password(PASSWORD)
    assert verify(PASSWORD, stored)
    assert not verify(OTHER_PASSWORD, stored)
    assert not verify("", stored)


def test_an_account_with_no_password_cannot_be_signed_into() -> None:
    """A null hash must refuse everything, including the empty string.

    The failure this prevents: a magic-link-only account being openable by
    anyone who submits a blank password, because "no hash" compared equal to
    "no input".
    """
    assert not verify("", None)
    assert not verify(PASSWORD, None)


# ---------------------------------------------------------------------------
# The signup contract
# ---------------------------------------------------------------------------
def test_a_password_is_optional_on_the_request() -> None:
    """Not every signup has a form behind it — the Tally webhook has no field."""
    request = SignupRequest.model_validate({k: v for k, v in SIGNUP.items() if k != "password"})
    assert request.password.get_secret_value() == ""


def test_a_short_password_is_refused() -> None:
    with pytest.raises(ValidationError):
        SignupRequest.model_validate({**SIGNUP, "password": "a" * (MIN_LENGTH - 1)})


def test_the_password_never_reaches_the_stored_form_snapshot() -> None:
    """``raw_form_json`` keeps the submitted form verbatim, and is dumped often.

    A plain password in that column would land in every backup and in every
    debug view of a business profile. ``exclude=True`` on the field is what
    keeps it out, and this is the test that notices if it is ever removed.
    """
    request = SignupRequest.model_validate(SIGNUP)
    dumped = request.model_dump(mode="json")

    assert "password" not in dumped
    assert PASSWORD not in str(dumped)


def test_the_password_is_not_in_the_model_repr() -> None:
    """Models get repr'd into tracebacks and log lines."""
    assert PASSWORD not in repr(SignupRequest.model_validate(SIGNUP))


# ---------------------------------------------------------------------------
# Signup creates an account (skips without PostgreSQL)
# ---------------------------------------------------------------------------
async def _submit(session: AsyncSession, **overrides: Any) -> Any:
    request = SignupRequest.model_validate({**SIGNUP, **overrides})
    return await SignupService(session, build_settings()).submit(
        request, correlation_id="test-correlation"
    )


async def _owner_of(session: AsyncSession, email: str) -> User | None:
    return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def test_signup_creates_an_account_for_the_owner(db_session: AsyncSession) -> None:
    """The gap this module closes: a tenant nobody could sign in to."""
    result = await _submit(db_session)

    owner = await _owner_of(db_session, SIGNUP["notification_email"])
    assert owner is not None
    assert owner.status is UserStatus.ACTIVE
    assert result.password_set is True


async def test_the_owner_can_act_in_the_tenant_immediately(db_session: AsyncSession) -> None:
    """An unaccepted membership grants nothing, so the founder's must be accepted.

    Leaving ``accepted_at`` null would create the business and lock its owner
    out of it — with no invitation pending that anyone could accept.
    """
    result = await _submit(db_session)
    await db_session.flush()

    membership = (
        await db_session.execute(select(Membership).where(Membership.tenant_id == result.tenant.id))
    ).scalar_one()

    assert membership.role is MembershipRole.OWNER
    assert membership.accepted_at is not None


async def test_the_stored_hash_verifies_the_chosen_password(db_session: AsyncSession) -> None:
    await _submit(db_session)

    owner = await _owner_of(db_session, SIGNUP["notification_email"])
    assert owner is not None
    assert owner.password_hash is not None
    assert verify(PASSWORD, owner.password_hash)
    assert not verify(OTHER_PASSWORD, owner.password_hash)


async def test_a_signup_without_a_password_still_creates_the_account(
    db_session: AsyncSession,
) -> None:
    """A Tally signup gets an account too — it signs in by link instead."""
    result = await _submit(db_session, password="")

    owner = await _owner_of(db_session, SIGNUP["notification_email"])
    assert owner is not None
    assert owner.password_hash is None
    assert result.password_set is False


async def test_signup_does_not_overwrite_an_existing_password(
    db_session: AsyncSession,
) -> None:
    """The account takeover this prevents.

    Signup is anonymous: anyone may submit anyone's address. If a second signup
    could set a password on an address that already has an account, typing a
    stranger's email into the form would hand over their account — no
    interception, no access to their inbox, nothing to steal.
    """
    existing = make_user(email=SIGNUP["notification_email"], password_hash=hash_password(PASSWORD))
    db_session.add(existing)
    await db_session.flush()

    result = await _submit(db_session, password=OTHER_PASSWORD)
    await db_session.flush()
    await db_session.refresh(existing)

    assert verify(PASSWORD, existing.password_hash)
    assert not verify(OTHER_PASSWORD, existing.password_hash)
    assert result.password_set is False


async def test_a_second_business_reuses_one_account(db_session: AsyncSession) -> None:
    """One person, two businesses — the membership table is built for this."""
    existing = make_user(email=SIGNUP["notification_email"])
    db_session.add(existing)
    await db_session.flush()

    result = await _submit(db_session)
    await db_session.flush()

    owners = (
        (await db_session.execute(select(User).where(User.email == SIGNUP["notification_email"])))
        .scalars()
        .all()
    )
    membership = (
        await db_session.execute(select(Membership).where(Membership.tenant_id == result.tenant.id))
    ).scalar_one()

    assert len(owners) == 1
    assert membership.user_id == existing.id


# ---------------------------------------------------------------------------
# Signing in (skips without PostgreSQL)
# ---------------------------------------------------------------------------
async def _account(db_session: AsyncSession, **overrides: Any) -> User:
    user = make_user(password_hash=hash_password(PASSWORD), **overrides)
    db_session.add(user)
    await db_session.flush()
    return user


async def test_the_right_password_starts_a_session(db_session: AsyncSession) -> None:
    user = await _account(db_session)

    issued = await AuthService(db_session, build_settings()).sign_in_with_password(
        user.email, PASSWORD
    )

    assert issued.session.user_id == user.id
    # A session, not a link: a row that is still a magic link authenticates
    # nothing, and one that was born a session must never be exchangeable.
    assert issued.session.is_magic_link is False
    assert issued.token


async def test_the_session_defaults_into_the_only_tenant(db_session: AsyncSession) -> None:
    """A single-business customer should never be asked to choose one."""
    from tests.factories import make_membership

    user = await _account(db_session)
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(make_membership(user, tenant))
    await db_session.flush()

    issued = await AuthService(db_session, build_settings()).sign_in_with_password(
        user.email, PASSWORD
    )

    assert issued.session.active_tenant_id == tenant.id


@pytest.mark.parametrize("password", [OTHER_PASSWORD, "", " " + PASSWORD, PASSWORD.upper()])
async def test_a_wrong_password_is_refused(db_session: AsyncSession, password: str) -> None:
    user = await _account(db_session)

    with pytest.raises(AuthenticationError):
        await AuthService(db_session, build_settings()).sign_in_with_password(user.email, password)


async def test_an_unknown_address_is_refused_the_same_way(db_session: AsyncSession) -> None:
    """Identical to a wrong password, deliberately.

    A different error — or a different status, or a different message — turns
    the login form into a free lookup for "is this business a customer?".
    """
    user = await _account(db_session)
    service = AuthService(db_session, build_settings())

    with pytest.raises(AuthenticationError) as unknown:
        await service.sign_in_with_password("nobody@example.com", PASSWORD)
    with pytest.raises(AuthenticationError) as wrong:
        await service.sign_in_with_password(user.email, OTHER_PASSWORD)

    assert str(unknown.value) == str(wrong.value)


async def test_a_link_only_account_cannot_be_opened_with_a_blank_password(
    db_session: AsyncSession,
) -> None:
    user = make_user(password_hash=None)
    db_session.add(user)
    await db_session.flush()

    with pytest.raises(AuthenticationError):
        await AuthService(db_session, build_settings()).sign_in_with_password(user.email, "")


async def test_a_disabled_account_cannot_sign_in(db_session: AsyncSession) -> None:
    """Disabled rather than deleted, so the audit trail stays readable — but the
    password that still hashes correctly must not open anything."""
    user = await _account(db_session, status=UserStatus.DISABLED)

    with pytest.raises(AuthenticationError):
        await AuthService(db_session, build_settings()).sign_in_with_password(user.email, PASSWORD)


async def test_the_address_is_matched_case_insensitively(db_session: AsyncSession) -> None:
    """Emails are stored lower-cased; a customer typing their own address in
    title case is not a failed login."""
    user = await _account(db_session)

    issued = await AuthService(db_session, build_settings()).sign_in_with_password(
        user.email.upper(), PASSWORD
    )

    assert issued.session.user_id == user.id


async def test_a_password_sign_in_does_not_verify_the_email(db_session: AsyncSession) -> None:
    """Choosing a password proves nothing about who reads that inbox.

    ``email_verified_at`` means "someone followed a link we sent there". Setting
    it here would make the column mean two different things, and the second one
    would be false.
    """
    user = await _account(db_session)
    assert user.email_verified_at is None

    await AuthService(db_session, build_settings()).sign_in_with_password(user.email, PASSWORD)

    assert user.email_verified_at is None
    assert user.last_login_at is not None


# ---------------------------------------------------------------------------
# The endpoint (skips without PostgreSQL)
# ---------------------------------------------------------------------------
async def test_signing_up_then_signing_in_works_end_to_end(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The whole point of the module, in one test.

    No email is sent, received or configured anywhere in this flow — which is
    exactly the situation a new deployment is in on its first day.
    """
    signup = await api_client.post("/api/v1/signups", json=SIGNUP)
    assert signup.status_code == 201

    response = await api_client.post(
        "/api/v1/auth/password-session",
        json={"email": SIGNUP["notification_email"], "password": PASSWORD},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == SIGNUP["notification_email"]
    assert [m["role"] for m in body["memberships"]] == ["owner"]


async def test_the_session_cookie_is_set_and_protected(api_client: AsyncClient) -> None:
    await api_client.post("/api/v1/signups", json=SIGNUP)

    response = await api_client.post(
        "/api/v1/auth/password-session",
        json={"email": SIGNUP["notification_email"], "password": PASSWORD},
    )

    cookie = response.headers.get("set-cookie", "")
    assert cookie
    # HttpOnly is what stops an XSS bug from reading the session out of
    # document.cookie and posting it somewhere else.
    assert "httponly" in cookie.lower()


async def test_the_cookie_alone_reaches_the_dashboard(api_app: FastAPI) -> None:
    """A signed-in browser sends nothing but the cookie.

    Over ``https`` deliberately. The session cookie is marked Secure, so a
    client on plain http discards it on arrival — which is the behaviour that
    protects it in production, and means the round trip can only be exercised
    against a URL the cookie is willing to travel to.
    """
    transport = ASGITransport(app=api_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        await client.post("/api/v1/signups", json=SIGNUP)
        await client.post(
            "/api/v1/auth/password-session",
            json={"email": SIGNUP["notification_email"], "password": PASSWORD},
        )

        me = await client.get("/api/v1/auth/me")

    assert me.status_code == 200
    assert me.json()["email"] == SIGNUP["notification_email"]


async def test_the_endpoint_refuses_identically_for_every_kind_of_wrong(
    api_client: AsyncClient,
) -> None:
    """Same status, same body — for a wrong password and an unknown address."""
    await api_client.post("/api/v1/signups", json=SIGNUP)

    wrong = await api_client.post(
        "/api/v1/auth/password-session",
        json={"email": SIGNUP["notification_email"], "password": OTHER_PASSWORD},
    )
    unknown = await api_client.post(
        "/api/v1/auth/password-session",
        json={"email": "stranger@example.com", "password": OTHER_PASSWORD},
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"]


async def test_a_refused_sign_in_sets_no_cookie(api_client: AsyncClient) -> None:
    await api_client.post("/api/v1/signups", json=SIGNUP)

    response = await api_client.post(
        "/api/v1/auth/password-session",
        json={"email": SIGNUP["notification_email"], "password": OTHER_PASSWORD},
    )

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
