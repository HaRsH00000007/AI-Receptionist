"""The emails that belong to a person: welcome, sign-in link, reset, verify.

These exist because the whole set was broken in a way a green test suite did not
notice. The magic-link endpoint returned 202 "a sign-in link is on its way",
staged a row that violated a ``NOT NULL`` constraint, and lost it in the commit
that ran *after* the response was sent — for every user, always. The only test
covering it asserted the status code and the body, both of which were correct.

So almost every test here asserts on the **delivered message**, not on the
response. A 202 proves nothing: it was the 202 that hid this for months. The
chain each test walks is the real one — request, durable row, worker tick, fake
provider's outbox — and where a token is involved it is extracted from the email
body and used, because a link nobody can follow is the same as no link.

The other recurring theme is what these endpoints must *not* reveal. A reset
form that answers differently for a registered address is the same enumeration
oracle the login form is careful not to be, so the uniform-answer property is
asserted for real rather than assumed from the shared response model.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.errors import AuthenticationError
from app.models import AuthToken, Notification, User
from app.models.enums import (
    AuthTokenPurpose,
    NotificationKind,
    NotificationStatus,
    SessionStatus,
    UserStatus,
)
from app.models.identity import Session
from app.providers.fakes.mail import FakeEmailProvider
from app.services.auth import AuthService
from app.services.tokens import hash_token
from app.worker import Worker
from tests.factories import make_user
from tests.support import build_settings

REGISTRATION = {
    "full_name": "Priya Raman",
    "email": "priya@harshtabletennis.example.com",
    "password": "a-long-enough-password",
}

#: Identity tables the shared `api_app` teardown does not truncate — the rest of
#: the suite avoids collisions by generating a unique address per test instead.
#: These tests are about one named account going through a flow several times, so
#: they read far better with a fixed address and a clean slate.
_IDENTITY_TABLES = "users, sessions, auth_tokens, memberships, audit_logs"


@pytest.fixture(autouse=True)
async def _fresh_identity(migrated_database: str) -> AsyncIterator[None]:
    """Clear the identity tables after each test in this module."""
    yield
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {_IDENTITY_TABLES} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mail(api_app: FastAPI) -> FakeEmailProvider:
    provider = api_app.state.providers.email
    assert isinstance(provider, FakeEmailProvider)
    return provider


async def _deliver_everything(api_app: FastAPI) -> None:
    """Run the worker until the outbox is empty, as production would.

    Delivery is deliberately not on the request path, so a test that stops at
    the response has tested nothing about whether the email exists.
    """
    worker = Worker(api_app.state.settings, api_app.state.providers, api_app.state.session_factory)
    await worker.tick()


def _token_in(body: str) -> str:
    """The token from a link in an email body.

    Pulled out of the message rather than read from the database on purpose: it
    asserts that the value the customer receives is the one that works. A test
    that takes the token from the row would pass even if the template built a
    broken URL.
    """
    match = re.search(r"[?&]token=([A-Za-z0-9_\-]+)", body)
    assert match is not None, f"no token in: {body[:400]}"
    return match.group(1)


async def _notifications(api_app: FastAPI, kind: NotificationKind) -> list[Notification]:
    async with api_app.state.session_factory() as session:
        rows = (
            await session.execute(
                select(Notification)
                .where(Notification.kind == kind)
                .order_by(Notification.created_at)
            )
        ).scalars()
        return list(rows)


async def _register(api_client: AsyncClient, **overrides: str) -> dict[str, object]:
    payload = {**REGISTRATION, **overrides}
    response = await api_client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    result: dict[str, object] = response.json()
    return result


# ---------------------------------------------------------------------------
# Registration: the welcome email
# ---------------------------------------------------------------------------


async def test_registering_sends_a_welcome_email(api_app: FastAPI, api_client: AsyncClient) -> None:
    """There was no welcome email at all. This is the one that was missing."""
    await _register(api_client)
    await _deliver_everything(api_app)

    message = _mail(api_app).last_to(REGISTRATION["email"])
    assert message is not None
    assert message.subject == "Welcome to AI Receptionist"
    # Addressed to the person, so it reads as sent to them and not to a list.
    assert "Priya" in message.text


async def test_the_welcome_email_carries_a_working_verification_link(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """One email, both jobs. The link has to actually verify the address."""
    await _register(api_client)
    await _deliver_everything(api_app)

    message = _mail(api_app).last_to(REGISTRATION["email"])
    assert message is not None
    token = _token_in(message.text)

    confirmed = await api_client.post("/api/v1/auth/verify-email", json={"token": token})
    assert confirmed.status_code == 204

    async with api_app.state.session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == REGISTRATION["email"]))
        ).scalar_one()
        assert user.email_verified_at is not None


async def test_the_welcome_email_is_queued_before_it_is_delivered(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """The durable obligation exists the moment the account does.

    Asserted without running the worker: if the row only appeared at delivery
    time, a crash in between would lose it silently — which is the failure the
    outbox exists to prevent.
    """
    await _register(api_client)

    rows = await _notifications(api_app, NotificationKind.WELCOME)
    assert len(rows) == 1
    assert rows[0].status is NotificationStatus.PENDING
    assert rows[0].recipient == REGISTRATION["email"]


async def test_a_welcome_email_carries_no_tenant(api_app: FastAPI, api_client: AsyncClient) -> None:
    """The constraint that broke every account email.

    A new account belongs to no business, so there is no tenant to attribute
    this to and the column must accept that rather than reject the insert.
    """
    await _register(api_client)

    rows = await _notifications(api_app, NotificationKind.WELCOME)
    assert [row.tenant_id for row in rows] == [None]


async def test_only_one_welcome_email_per_account(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """Deduped on the account, so a retried registration cannot send twice."""
    await _register(api_client)
    # A second registration for the same address is refused, so the only way to
    # reach the dedupe key is to stage it again directly — which is exactly what
    # a retried request would do.
    from app.services.account_notifications import stage_welcome

    async with api_app.state.session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == REGISTRATION["email"]))
        ).scalar_one()
        again = await stage_welcome(
            session, api_app.state.settings, user=user, verification_token="irrelevant"
        )
        await session.commit()

    assert again is None, "a second welcome was queued"
    assert len(await _notifications(api_app, NotificationKind.WELCOME)) == 1


async def test_a_second_registration_is_refused_and_sends_nothing(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    await _register(api_client)
    repeat = await api_client.post("/api/v1/auth/register", json=REGISTRATION)
    assert repeat.status_code == 409

    assert len(await _notifications(api_app, NotificationKind.WELCOME)) == 1


# ---------------------------------------------------------------------------
# The sign-in link
# ---------------------------------------------------------------------------


async def test_a_sign_in_link_is_actually_delivered(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """The regression test for the whole investigation.

    Before the fix this endpoint returned 202, wrote nothing, and the customer
    waited for an email that did not exist.
    """
    await _register(api_client)
    _mail(api_app).outbox.clear()

    asked = await api_client.post("/api/v1/auth/magic-link", json={"email": REGISTRATION["email"]})
    assert asked.status_code == 202

    await _deliver_everything(api_app)
    message = _mail(api_app).last_to(REGISTRATION["email"])
    assert message is not None, "no sign-in link was delivered"
    assert message.subject == "Your sign-in link"


async def test_the_link_in_the_email_signs_you_in(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """The second half of the same bug.

    The failed insert rolled back the magic-link *session row* with it, so even
    a hand-delivered link would have been invalid. Following the token proves
    both halves are fixed.
    """
    await _register(api_client)
    _mail(api_app).outbox.clear()

    await api_client.post("/api/v1/auth/magic-link", json={"email": REGISTRATION["email"]})
    await _deliver_everything(api_app)

    message = _mail(api_app).last_to(REGISTRATION["email"])
    assert message is not None
    token = _token_in(message.text)

    signed_in = await api_client.post("/api/v1/auth/session", json={"token": token})
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["email"] == REGISTRATION["email"]


async def test_a_sign_in_link_works_for_a_user_with_no_tenant(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """A registered account with no business is the commonest state there is.

    It is also the state the old code could never serve: it read the session's
    active tenant, which is null until a business exists *and* has been chosen.
    """
    async with api_app.state.session_factory() as session:
        session.add(make_user(email="tenantless@example.com"))
        await session.commit()

    await api_client.post("/api/v1/auth/magic-link", json={"email": "tenantless@example.com"})
    await _deliver_everything(api_app)

    assert _mail(api_app).last_to("tenantless@example.com") is not None


async def test_asking_twice_sends_two_links(api_app: FastAPI, api_client: AsyncClient) -> None:
    """No dedupe key, deliberately.

    Somebody who asks again is somebody whose first email did not arrive.
    Collapsing the second request would withhold the only message they are
    waiting for.
    """
    await _register(api_client)
    _mail(api_app).outbox.clear()

    for _ in range(2):
        await api_client.post("/api/v1/auth/magic-link", json={"email": REGISTRATION["email"]})
    await _deliver_everything(api_app)

    links = [m for m in _mail(api_app).outbox if m.subject == "Your sign-in link"]
    assert len(links) == 2
    # Different tokens, so the second email is a usable credential rather than a
    # copy of a link that may already have been consumed.
    assert _token_in(links[0].text) != _token_in(links[1].text)


async def test_an_unknown_address_is_answered_the_same_and_emails_nobody(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """Uniform to the caller, and silent to the inbox.

    The status-and-body half of this was already covered. What was not is that
    no message is queued — an email to an address with no account would leak the
    same fact out of band.
    """
    await _register(api_client)
    _mail(api_app).outbox.clear()

    known = await api_client.post("/api/v1/auth/magic-link", json={"email": REGISTRATION["email"]})
    unknown = await api_client.post("/api/v1/auth/magic-link", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()

    await _deliver_everything(api_app)
    assert _mail(api_app).last_to("nobody@example.com") is None


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


async def test_a_reset_link_is_delivered_and_sets_a_new_password(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """The flow that did not exist, end to end.

    Without it, forgetting a password meant permanent lockout: the only other
    way back in was a sign-in link, and that was broken too.
    """
    await _register(api_client)
    _mail(api_app).outbox.clear()

    asked = await api_client.post(
        "/api/v1/auth/password-reset", json={"email": REGISTRATION["email"]}
    )
    assert asked.status_code == 202

    await _deliver_everything(api_app)
    message = _mail(api_app).last_to(REGISTRATION["email"])
    assert message is not None
    assert message.subject == "Reset your password"

    done = await api_client.post(
        "/api/v1/auth/password-reset/complete",
        json={"token": _token_in(message.text), "new_password": "a-brand-new-password"},
    )
    assert done.status_code == 204

    signed_in = await api_client.post(
        "/api/v1/auth/password-session",
        json={"email": REGISTRATION["email"], "password": "a-brand-new-password"},
    )
    assert signed_in.status_code == 200


async def test_the_old_password_stops_working(api_app: FastAPI, api_client: AsyncClient) -> None:
    await _register(api_client)
    _mail(api_app).outbox.clear()
    await api_client.post("/api/v1/auth/password-reset", json={"email": REGISTRATION["email"]})
    await _deliver_everything(api_app)
    message = _mail(api_app).last_to(REGISTRATION["email"])
    assert message is not None

    await api_client.post(
        "/api/v1/auth/password-reset/complete",
        json={"token": _token_in(message.text), "new_password": "a-brand-new-password"},
    )

    refused = await api_client.post(
        "/api/v1/auth/password-session",
        json={"email": REGISTRATION["email"], "password": REGISTRATION["password"]},
    )
    assert refused.status_code == 401


async def test_a_reset_link_is_single_use(api_app: FastAPI, api_client: AsyncClient) -> None:
    """A reset email sitting in a mailbox must stop being a credential.

    One forwarded message is otherwise an account takeover whenever the first
    reader is finished with it.
    """
    await _register(api_client)
    _mail(api_app).outbox.clear()
    await api_client.post("/api/v1/auth/password-reset", json={"email": REGISTRATION["email"]})
    await _deliver_everything(api_app)
    message = _mail(api_app).last_to(REGISTRATION["email"])
    assert message is not None
    token = _token_in(message.text)

    first = await api_client.post(
        "/api/v1/auth/password-reset/complete",
        json={"token": token, "new_password": "the-first-new-password"},
    )
    second = await api_client.post(
        "/api/v1/auth/password-reset/complete",
        json={"token": token, "new_password": "a-second-attempt-password"},
    )
    assert first.status_code == 204
    assert second.status_code == 401

    # The second attempt changed nothing.
    assert (
        await api_client.post(
            "/api/v1/auth/password-session",
            json={"email": REGISTRATION["email"], "password": "the-first-new-password"},
        )
    ).status_code == 200


async def test_asking_again_retires_the_previous_reset_link(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """Only the newest link works.

    Two live reset links means an older one — possibly in a mailbox somebody
    else now reads — still works after the person has used the new one.
    """
    await _register(api_client)
    _mail(api_app).outbox.clear()

    for _ in range(2):
        await api_client.post("/api/v1/auth/password-reset", json={"email": REGISTRATION["email"]})
    await _deliver_everything(api_app)

    resets = [m for m in _mail(api_app).outbox if m.subject == "Reset your password"]
    assert len(resets) == 2
    stale, fresh = _token_in(resets[0].text), _token_in(resets[1].text)

    refused = await api_client.post(
        "/api/v1/auth/password-reset/complete",
        json={"token": stale, "new_password": "using-the-stale-link"},
    )
    assert refused.status_code == 401

    accepted = await api_client.post(
        "/api/v1/auth/password-reset/complete",
        json={"token": fresh, "new_password": "using-the-fresh-link"},
    )
    assert accepted.status_code == 204


async def test_an_expired_reset_link_is_refused(api_app: FastAPI, api_client: AsyncClient) -> None:
    await _register(api_client)
    _mail(api_app).outbox.clear()
    await api_client.post("/api/v1/auth/password-reset", json={"email": REGISTRATION["email"]})
    await _deliver_everything(api_app)
    message = _mail(api_app).last_to(REGISTRATION["email"])
    assert message is not None
    token = _token_in(message.text)

    async with api_app.state.session_factory() as session:
        row = (
            await session.execute(
                select(AuthToken).where(AuthToken.token_hash == hash_token(token))
            )
        ).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    refused = await api_client.post(
        "/api/v1/auth/password-reset/complete",
        json={"token": token, "new_password": "too-late-for-this-one"},
    )
    assert refused.status_code == 401


async def test_resetting_signs_every_device_out(api_app: FastAPI, api_client: AsyncClient) -> None:
    """A reset is what someone does when they think another person is in their
    account. Leaving that person signed in would make it decorative."""
    registered = await _register(api_client)
    existing_token = registered["token"]
    assert isinstance(existing_token, str)

    _mail(api_app).outbox.clear()
    await api_client.post("/api/v1/auth/password-reset", json={"email": REGISTRATION["email"]})
    await _deliver_everything(api_app)
    message = _mail(api_app).last_to(REGISTRATION["email"])
    assert message is not None

    await api_client.post(
        "/api/v1/auth/password-reset/complete",
        json={"token": _token_in(message.text), "new_password": "a-brand-new-password"},
    )

    still_in = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {existing_token}"}
    )
    assert still_in.status_code == 401


async def test_an_unknown_address_cannot_be_detected_through_the_reset_form(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """The enumeration property, asserted rather than assumed."""
    await _register(api_client)
    _mail(api_app).outbox.clear()

    known = await api_client.post(
        "/api/v1/auth/password-reset", json={"email": REGISTRATION["email"]}
    )
    unknown = await api_client.post(
        "/api/v1/auth/password-reset", json={"email": "nobody@example.com"}
    )

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()

    await _deliver_everything(api_app)
    assert _mail(api_app).last_to("nobody@example.com") is None


async def test_a_disabled_account_gets_no_reset_link(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    async with api_app.state.session_factory() as session:
        session.add(make_user(email="disabled@example.com", status=UserStatus.DISABLED))
        await session.commit()

    answered = await api_client.post(
        "/api/v1/auth/password-reset", json={"email": "disabled@example.com"}
    )
    assert answered.status_code == 202

    await _deliver_everything(api_app)
    assert _mail(api_app).last_to("disabled@example.com") is None


async def test_a_reset_notification_carries_no_tenant(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    await _register(api_client)
    await api_client.post("/api/v1/auth/password-reset", json={"email": REGISTRATION["email"]})

    rows = await _notifications(api_app, NotificationKind.PASSWORD_RESET)
    assert [row.tenant_id for row in rows] == [None]


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------


async def test_verification_can_be_sent_again(api_app: FastAPI, api_client: AsyncClient) -> None:
    """For somebody whose welcome email never arrived."""
    registered = await _register(api_client)
    token = registered["token"]
    assert isinstance(token, str)
    _mail(api_app).outbox.clear()

    again = await api_client.post(
        "/api/v1/auth/verify-email/resend", headers={"Authorization": f"Bearer {token}"}
    )
    assert again.status_code == 204

    await _deliver_everything(api_app)
    message = _mail(api_app).last_to(REGISTRATION["email"])
    assert message is not None
    assert message.subject == "Confirm your email address"

    confirmed = await api_client.post(
        "/api/v1/auth/verify-email", json={"token": _token_in(message.text)}
    )
    assert confirmed.status_code == 204


async def test_resending_requires_a_session(api_client: AsyncClient) -> None:
    """Authenticated, which makes it enumeration-safe by construction: there is
    no address to probe because the caller already holds the account."""
    refused = await api_client.post("/api/v1/auth/verify-email/resend")
    assert refused.status_code == 401


async def test_resending_to_a_verified_account_does_nothing(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """The caller asked for a state that is already true. Not an error."""
    registered = await _register(api_client)
    token = registered["token"]
    assert isinstance(token, str)

    async with api_app.state.session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == REGISTRATION["email"]))
        ).scalar_one()
        user.email_verified_at = datetime.now(UTC)
        await session.commit()

    _mail(api_app).outbox.clear()
    response = await api_client.post(
        "/api/v1/auth/verify-email/resend", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 204

    await _deliver_everything(api_app)
    # The welcome email queued at registration still delivers on this tick, so
    # the assertion is about the verification email specifically rather than
    # about the inbox being empty.
    assert not [m for m in _mail(api_app).outbox if m.subject == "Confirm your email address"]
    assert not await _notifications(api_app, NotificationKind.EMAIL_VERIFICATION)


async def test_a_verification_link_is_single_use(api_app: FastAPI, api_client: AsyncClient) -> None:
    await _register(api_client)
    await _deliver_everything(api_app)
    message = _mail(api_app).last_to(REGISTRATION["email"])
    assert message is not None
    token = _token_in(message.text)

    assert (
        await api_client.post("/api/v1/auth/verify-email", json={"token": token})
    ).status_code == 204
    assert (
        await api_client.post("/api/v1/auth/verify-email", json={"token": token})
    ).status_code == 401


async def test_an_unknown_verification_token_is_refused(api_client: AsyncClient) -> None:
    from app.services.tokens import generate_token

    refused = await api_client.post("/api/v1/auth/verify-email", json={"token": generate_token()})
    assert refused.status_code == 401


async def test_verification_is_not_enforced_anywhere(
    api_app: FastAPI, api_client: AsyncClient
) -> None:
    """Explicitly asserted, because it is a product decision and not an oversight.

    Verification is recorded but gates nothing until that is approved. If
    somebody later makes it a gate, this test fails and says so.
    """
    registered = await _register(api_client)
    token = registered["token"]
    assert isinstance(token, str)

    async with api_app.state.session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == REGISTRATION["email"]))
        ).scalar_one()
        assert user.email_verified_at is None, "registration must not self-verify"

    # Unverified, and the session works for everything a verified one would.
    assert (
        await api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 200
    assert (
        await api_client.post(
            "/api/v1/auth/password-session",
            json={"email": REGISTRATION["email"], "password": REGISTRATION["password"]},
        )
    ).status_code == 200


# ---------------------------------------------------------------------------
# The token primitives, at the service layer
# ---------------------------------------------------------------------------


async def test_a_verification_token_cannot_be_used_as_a_reset_token(
    db_session: AsyncSession,
) -> None:
    """Purpose is matched as well as the hash.

    Without it the weakest token in the set sets the strength of all of them: a
    day-long verification link would become a day-long password-reset link.
    """
    settings = build_settings()
    service = AuthService(db_session, settings)
    user = make_user(email="crossuse@example.com")
    db_session.add(user)
    await db_session.flush()

    issued = await service.issue_email_verification(user)
    await db_session.flush()

    with pytest.raises(AuthenticationError):
        await service.reset_password(issued.token, "a-perfectly-fine-password")


async def test_a_reset_token_is_not_a_session(db_session: AsyncSession) -> None:
    """It lives in its own table, so it cannot authenticate anything.

    This is why ``auth_tokens`` exists rather than another flag on ``sessions``:
    a reset token stored there would be one boolean check away from being
    accepted as a bearer token, and that check is the kind that gets dropped.
    """
    settings = build_settings()
    service = AuthService(db_session, settings)
    user = make_user(email="notasession@example.com")
    db_session.add(user)
    await db_session.flush()

    issued = await service.request_password_reset("notasession@example.com")
    assert issued is not None
    await db_session.flush()

    # Nothing in `sessions` matches it, so `authenticate` has nothing to find.
    assert (
        await db_session.execute(
            select(Session).where(Session.token_hash == hash_token(issued.token))
        )
    ).scalar_one_or_none() is None
    with pytest.raises(AuthenticationError):
        await service.authenticate(issued.token)


async def test_only_the_hash_of_a_token_is_stored(db_session: AsyncSession) -> None:
    settings = build_settings()
    service = AuthService(db_session, settings)
    user = make_user(email="hashed@example.com")
    db_session.add(user)
    await db_session.flush()

    issued = await service.request_password_reset("hashed@example.com")
    assert issued is not None
    await db_session.flush()

    assert issued.row.token_hash == hash_token(issued.token)
    assert issued.token not in issued.row.token_hash


async def test_a_token_for_a_deleted_user_goes_with_them(
    db_session: AsyncSession,
) -> None:
    """``ON DELETE CASCADE``, asserted. A token whose user is gone must not
    linger as a credential pointing at a missing row."""
    settings = build_settings()
    service = AuthService(db_session, settings)
    user = make_user(email="goneaway@example.com")
    db_session.add(user)
    await db_session.flush()
    user_id = user.id

    await service.issue_email_verification(user)
    await db_session.flush()

    await db_session.delete(user)
    await db_session.flush()

    remaining = (
        (await db_session.execute(select(AuthToken).where(AuthToken.user_id == user_id)))
        .scalars()
        .all()
    )
    assert list(remaining) == []


async def test_superseding_marks_tokens_used_rather_than_deleting_them(
    db_session: AsyncSession,
) -> None:
    """So "this link had already been replaced" stays answerable afterwards."""
    settings = build_settings()
    service = AuthService(db_session, settings)
    user = make_user(email="superseded@example.com")
    db_session.add(user)
    await db_session.flush()

    first = await service.request_password_reset("superseded@example.com")
    await db_session.flush()
    second = await service.request_password_reset("superseded@example.com")
    await db_session.flush()
    assert first is not None and second is not None

    rows = (
        (
            await db_session.execute(
                select(AuthToken).where(
                    AuthToken.user_id == user.id,
                    AuthToken.purpose == AuthTokenPurpose.PASSWORD_RESET,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2, "the old row was deleted rather than retired"
    assert sum(1 for row in rows if row.consumed_at is None) == 1


async def test_a_reset_revokes_sessions_at_the_service_layer(
    db_session: AsyncSession,
) -> None:
    """Covers the sessions of *other* devices, which the API test cannot see."""
    settings = build_settings()
    service = AuthService(db_session, settings)
    user = make_user(email="manydevices@example.com")
    db_session.add(user)
    await db_session.flush()

    for _ in range(3):
        db_session.add(
            Session(
                user_id=user.id,
                token_hash=hash_token(str(uuid.uuid4())),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
    await db_session.flush()

    issued = await service.request_password_reset("manydevices@example.com")
    assert issued is not None
    await db_session.flush()
    await service.reset_password(issued.token, "a-perfectly-fine-password")
    await db_session.flush()

    active = (
        (
            await db_session.execute(
                select(Session).where(
                    Session.user_id == user.id,
                    Session.status == SessionStatus.ACTIVE,
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(active) == []


async def test_resetting_proves_the_inbox_and_so_verifies_the_address(
    db_session: AsyncSession,
) -> None:
    """Following a link to an inbox is the same proof a magic link gives."""
    settings = build_settings()
    service = AuthService(db_session, settings)
    user = make_user(email="provenbyreset@example.com")
    db_session.add(user)
    await db_session.flush()
    assert user.email_verified_at is None

    issued = await service.request_password_reset("provenbyreset@example.com")
    assert issued is not None
    await db_session.flush()
    await service.reset_password(issued.token, "a-perfectly-fine-password")

    assert user.email_verified_at is not None
