"""Account settings: your name, and setting or changing your password.

The password rules are the ones that matter. A session left open on a shared
computer must not be enough to lock the owner out (the current password is
required), a change must sign out everyone else (it is what you do when you
think someone has your password), and support staff impersonating a customer
must not be able to leave with a password of their choosing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import AuditLog, User
from app.models.enums import AuditAction, SessionStatus
from app.models.identity import Session
from app.services.tokens import generate_token, hash_token
from tests.portal_support import PASSWORD, sign_up, signup_payload

NEW_PASSWORD = "a brand new passphrase"


async def _sign_in(client: AsyncClient, email: str, password: str) -> int:
    response = await client.post(
        "/api/v1/auth/password-session", json={"email": email, "password": password}
    )
    client.cookies.clear()
    return response.status_code


async def test_the_owner_can_change_their_name(api_client: AsyncClient) -> None:
    owner = await sign_up(api_client)
    response = await api_client.patch(
        "/api/v1/auth/me", json={"full_name": "  Dana   Rivera "}, headers=owner.headers
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Dana Rivera"


async def test_changing_the_password_needs_the_current_one(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await sign_up(api_client)
    wrong = await api_client.post(
        "/api/v1/auth/password",
        json={"current_password": "not it at all", "new_password": NEW_PASSWORD},
        headers=owner.headers,
    )
    # 422 rather than 401: a typo must not sign the dashboard out.
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "invalid_current_password"

    missing = await api_client.post(
        "/api/v1/auth/password", json={"new_password": NEW_PASSWORD}, headers=owner.headers
    )
    assert missing.status_code == 422

    assert await _sign_in(api_client, owner.email, PASSWORD) == 200


async def test_a_password_change_signs_out_every_other_session(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await sign_up(api_client)
    # A second device, signed in with the old password.
    elsewhere = await api_client.post(
        "/api/v1/auth/password-session", json={"email": owner.email, "password": PASSWORD}
    )
    other_token = elsewhere.cookies["ai_receptionist_session"]
    api_client.cookies.clear()

    response = await api_client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=owner.headers,
    )
    assert response.status_code == 204

    # This session keeps working; the other one does not.
    assert (await api_client.get("/api/v1/auth/me", headers=owner.headers)).status_code == 200
    stale = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert stale.status_code == 401

    assert await _sign_in(api_client, owner.email, PASSWORD) == 401
    assert await _sign_in(api_client, owner.email, NEW_PASSWORD) == 200

    async with api_app.state.session_factory() as session:
        audit = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == AuditAction.PASSWORD_CHANGED)
                )
            )
            .scalars()
            .all()
        )
    assert audit and audit[-1].meta_json["sessions_revoked"] >= 1
    # The password is nowhere in the log.
    assert NEW_PASSWORD not in str(audit[-1].meta_json)


async def test_a_link_only_account_can_set_a_first_password(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """No current password exists to confirm; the link already proved the inbox."""
    payload = signup_payload()
    del payload["password"]
    assert (await api_client.post("/api/v1/signups", json=payload)).status_code == 201

    token = generate_token()
    async with api_app.state.session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == payload["notification_email"]))
        ).scalar_one()
        assert user.password_hash is None
        session.add(
            Session(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await session.commit()
    headers = {"Authorization": f"Bearer {token}"}

    assert (await api_client.get("/api/v1/auth/me", headers=headers)).json()[
        "has_password"
    ] is False
    response = await api_client.post(
        "/api/v1/auth/password", json={"new_password": NEW_PASSWORD}, headers=headers
    )
    assert response.status_code == 204
    assert (await api_client.get("/api/v1/auth/me", headers=headers)).json()["has_password"] is True
    assert await _sign_in(api_client, payload["notification_email"], NEW_PASSWORD) == 200


async def test_a_short_new_password_is_refused(api_client: AsyncClient) -> None:
    owner = await sign_up(api_client)
    response = await api_client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=owner.headers,
    )
    assert response.status_code == 422


async def test_support_staff_cannot_change_a_password_while_impersonating(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await sign_up(api_client)
    token = generate_token()
    async with api_app.state.session_factory() as session:
        user = (await session.execute(select(User).where(User.email == owner.email))).scalar_one()
        operator = User(
            email=f"operator-{generate_token()[:8]}@example.com", is_platform_admin=True
        )
        session.add(operator)
        await session.flush()
        session.add(
            Session(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                impersonated_by_user_id=operator.id,
                status=SessionStatus.ACTIVE,
            )
        )
        await session.commit()
    headers = {"Authorization": f"Bearer {token}"}

    for method, path, body in (
        (
            "POST",
            "/api/v1/auth/password",
            {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        ),
        ("PATCH", "/api/v1/auth/me", {"full_name": "Someone Else"}),
    ):
        response = await api_client.request(method, path, json=body, headers=headers)
        assert response.status_code == 403, path
    assert await _sign_in(api_client, owner.email, PASSWORD) == 200
