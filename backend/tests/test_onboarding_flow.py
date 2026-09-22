"""Account first, then the business: register, save the form, create the business.

The ownership test is the one to read first. The setup form has an email field
— where call summaries go — and that address may belong to somebody else's
account. The new business must belong to whoever is *signed in*, never to the
account the form's email happens to name; otherwise typing a stranger's email
would hand them a business (or hand you theirs).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import AuditLog, Membership, Tenant, User
from app.models.enums import AuditAction, TenantStatus
from app.models.onboarding import OnboardingDraft
from tests.portal_support import COOKIE, PASSWORD, drive_to_active, signup_payload, unique_email


async def _register(client: AsyncClient, email: str | None = None) -> tuple[str, dict[str, str]]:
    email = email or unique_email("account")
    response = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "  Dana   Rivera ", "email": email, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    token = response.cookies.get(COOKIE)
    assert token
    client.cookies.clear()
    return email, {"Authorization": f"Bearer {token}"}


def _business(**overrides: Any) -> dict[str, Any]:
    payload = signup_payload(**overrides)
    del payload["password"]
    return payload


# ---------------------------------------------------------------------------
# Creating the account
# ---------------------------------------------------------------------------


async def test_registering_creates_a_signed_in_account_with_no_business(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    email, headers = await _register(api_client)

    me = (await api_client.get("/api/v1/auth/me", headers=headers)).json()
    assert me["email"] == email
    assert me["full_name"] == "Dana Rivera"
    assert me["has_password"] is True
    # No business yet: the dashboard sends this person to the setup form.
    assert me["memberships"] == []

    async with api_app.state.session_factory() as session:
        audit = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == AuditAction.ACCOUNT_REGISTERED)
                )
            )
            .scalars()
            .all()
        )
    assert audit


async def test_an_address_can_only_register_once(api_client: AsyncClient) -> None:
    email, _ = await _register(api_client)
    again = await api_client.post(
        "/api/v1/auth/register",
        json={"full_name": "Someone Else", "email": email.upper(), "password": "another password"},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "account_exists"
    # And the existing password was not touched.
    signin = await api_client.post(
        "/api/v1/auth/password-session", json={"email": email, "password": PASSWORD}
    )
    assert signin.status_code == 200


async def test_a_short_password_is_refused_at_registration(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/auth/register",
        json={"full_name": "Dana", "email": unique_email(), "password": "short"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# The draft
# ---------------------------------------------------------------------------


async def test_the_form_is_saved_and_resumed(api_client: AsyncClient) -> None:
    _, headers = await _register(api_client)

    empty = (await api_client.get("/api/v1/onboarding/draft", headers=headers)).json()
    assert empty == {"data": None, "updated_at": None}

    saved = await api_client.put(
        "/api/v1/onboarding/draft",
        json={"data": {"step": 1, "values": {"business_name": "Sunset"}, "password": "nope"}},
        headers=headers,
    )
    assert saved.status_code == 200
    resumed = (await api_client.get("/api/v1/onboarding/draft", headers=headers)).json()
    assert resumed["data"] == {"step": 1, "values": {"business_name": "Sunset"}}
    assert resumed["updated_at"]


async def test_a_draft_belongs_to_its_account(api_client: AsyncClient) -> None:
    _, mine = await _register(api_client)
    _, theirs = await _register(api_client)
    await api_client.put(
        "/api/v1/onboarding/draft", json={"data": {"secret": "mine"}}, headers=mine
    )
    other = (await api_client.get("/api/v1/onboarding/draft", headers=theirs)).json()
    assert other["data"] is None


async def test_an_oversized_draft_is_refused(api_client: AsyncClient) -> None:
    _, headers = await _register(api_client)
    response = await api_client.put(
        "/api/v1/onboarding/draft", json={"data": {"blob": "x" * 25_000}}, headers=headers
    )
    assert response.status_code == 422


async def test_the_draft_needs_a_session(api_client: AsyncClient) -> None:
    assert (await api_client.get("/api/v1/onboarding/draft")).status_code == 401


# ---------------------------------------------------------------------------
# Creating the business
# ---------------------------------------------------------------------------


async def test_the_business_belongs_to_the_signed_in_account(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Even when the form's email is another account's address."""
    someone_else, _ = await _register(api_client)
    _, headers = await _register(api_client)
    await api_client.put("/api/v1/onboarding/draft", json={"data": {"step": 3}}, headers=headers)

    response = await api_client.post(
        "/api/v1/onboarding",
        json=_business(notification_email=someone_else),
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["signed_in"] is True
    tenant_id = body["tenant_id"]

    me = (await api_client.get("/api/v1/auth/me", headers=headers)).json()
    assert [m["tenant_id"] for m in me["memberships"]] == [tenant_id]
    assert me["memberships"][0]["role"] == "owner"
    assert me["active_tenant_id"] == tenant_id

    async with api_app.state.session_factory() as session:
        other = (await session.execute(select(User).where(User.email == someone_else))).scalar_one()
        their_memberships = (
            (await session.execute(select(Membership).where(Membership.user_id == other.id)))
            .scalars()
            .all()
        )
        drafts = (await session.execute(select(OnboardingDraft))).scalars().all()
    # The account the email named was not given anything.
    assert their_memberships == []
    # The submitted form's draft is gone.
    assert all(draft.data != {"step": 3} for draft in drafts)


async def test_submitting_twice_returns_the_same_business(api_client: AsyncClient) -> None:
    _, headers = await _register(api_client)
    payload = _business()
    first = await api_client.post("/api/v1/onboarding", json=payload, headers=headers)
    again = await api_client.post("/api/v1/onboarding", json=payload, headers=headers)
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["tenant_id"] == first.json()["tenant_id"]


async def test_another_businesss_contact_email_is_refused(api_client: AsyncClient) -> None:
    _, first_owner = await _register(api_client)
    shared = unique_email("front-desk")
    assert (
        await api_client.post(
            "/api/v1/onboarding", json=_business(notification_email=shared), headers=first_owner
        )
    ).status_code == 201

    _, second_owner = await _register(api_client)
    response = await api_client.post(
        "/api/v1/onboarding", json=_business(notification_email=shared), headers=second_owner
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "contact_email_in_use"


async def test_the_setup_form_does_not_take_a_password(api_client: AsyncClient) -> None:
    _, headers = await _register(api_client)
    response = await api_client.post(
        "/api/v1/onboarding", json={**_business(), "password": PASSWORD}, headers=headers
    )
    assert response.status_code == 422


async def test_creating_a_business_needs_an_account(api_client: AsyncClient) -> None:
    response = await api_client.post("/api/v1/onboarding", json=_business())
    assert response.status_code == 401


async def test_the_whole_flow_ends_on_a_live_dashboard(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Register, create the business, provision, and read it back as the owner."""
    _, headers = await _register(api_client)
    created = await api_client.post("/api/v1/onboarding", json=_business(), headers=headers)
    tenant_id = created.json()["tenant_id"]

    await drive_to_active(api_app)

    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, tenant_id)
    assert tenant is not None and tenant.status is TenantStatus.ACTIVE
    summary = await api_client.get(f"/api/v1/tenants/{tenant_id}/dashboard", headers=headers)
    assert summary.status_code == 200
    agent = await api_client.get(f"/api/v1/tenants/{tenant_id}/agent", headers=headers)
    assert agent.status_code == 200
    assert agent.json()["status"] == "active"


async def test_creating_a_business_logs_without_crashing(
    api_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The suite logs at WARNING, so an INFO line is never built — which hid a
    log call whose ``extra`` reused a reserved LogRecord name ("created") and
    turned every successful signup into a 500. Built at INFO here, on purpose."""
    caplog.set_level(logging.INFO)
    _, headers = await _register(api_client)
    response = await api_client.post("/api/v1/onboarding", json=_business(), headers=headers)
    assert response.status_code == 201, response.text
    assert any("business created from onboarding" in record.message for record in caplog.records)
