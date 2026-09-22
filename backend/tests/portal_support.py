"""Helpers for the customer-portal tests.

The portal is reached the way a customer reaches it: sign up through the API
(which now signs the owner in), drive provisioning with the real worker against
fake vendors, and call the endpoints with the session that signup issued. Other
members and other tenants are seeded directly, because what those tests need is
*someone else's* data to try to reach.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from app.models import Membership, Tenant, User
from app.models.enums import BusinessType, MembershipRole, TenantStatus
from app.models.identity import Session
from app.services.tokens import generate_token, hash_token

PASSWORD = "correct horse battery staple"
COOKIE = "ai_receptionist_session"

_counter = itertools.count(1)


def unique_email(prefix: str = "owner") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}-{next(_counter)}@portal.example.com"


def signup_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "business_name": "Sunset Salon",
        "business_type": "salon",
        "services": "cuts, color",
        "operating_hours": "Mon-Fri 9-6",
        "greeting_style": "friendly",
        "escalation_rules": "",
        "notification_email": unique_email(),
        "area_code": "805",
        "plan": "starter",
        "contact_phone": "8055550142",
        "password": PASSWORD,
    }
    payload.update(overrides)
    return payload


@dataclass
class Owner:
    tenant_id: uuid.UUID
    email: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


async def sign_up(client: AsyncClient, **overrides: Any) -> Owner:
    """Sign up a new business and return its signed-in owner."""
    payload = signup_payload(**overrides)
    response = await client.post("/api/v1/signups", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["signed_in"] is True, body
    token = response.cookies.get(COOKIE)
    assert token, "signup should have set the session cookie"
    # The server prefers the cookie to a bearer header. Left in the jar, it
    # would ride along with every later request — including the ones that are
    # meant to be made as *someone else* — and those tests would pass as the
    # owner. Every request states its credential explicitly instead.
    client.cookies.clear()
    return Owner(
        tenant_id=uuid.UUID(body["tenant_id"]), email=payload["notification_email"], token=token
    )


async def drive_to_active(api_app: FastAPI, ticks: int = 14) -> None:
    from app.worker import Worker

    worker = Worker(api_app.state.settings, api_app.state.providers, api_app.state.session_factory)
    for _ in range(ticks):
        await worker.tick()


async def live_owner(client: AsyncClient, api_app: FastAPI, **overrides: Any) -> Owner:
    owner = await sign_up(client, **overrides)
    await drive_to_active(api_app)
    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, owner.tenant_id)
        assert tenant is not None
        assert tenant.status is TenantStatus.ACTIVE, "provisioning should have finished"
    return owner


async def add_member(
    api_app: FastAPI, tenant_id: uuid.UUID, role: MembershipRole = MembershipRole.MEMBER
) -> dict[str, str]:
    """Another person in the same business, signed in. Returns auth headers."""
    token = generate_token()
    async with api_app.state.session_factory() as session:
        user = User(email=unique_email("member"), full_name="Pat Member")
        session.add(user)
        await session.flush()
        session.add(
            Membership(
                user_id=user.id,
                tenant_id=tenant_id,
                role=role,
                accepted_at=datetime.now(UTC),
            )
        )
        session.add(
            Session(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=datetime.now(UTC) + timedelta(days=1),
                active_tenant_id=tenant_id,
            )
        )
        await session.commit()
    return {"Authorization": f"Bearer {token}"}


async def other_tenant(
    api_app: FastAPI, *, business_type: BusinessType = BusinessType.SALON
) -> tuple[uuid.UUID, dict[str, str]]:
    """A second, unrelated business with its own signed-in owner."""
    token = generate_token()
    async with api_app.state.session_factory() as session:
        tenant = Tenant(
            name="Someone Else",
            business_type=business_type,
            contact_email=unique_email("other"),
            contact_phone="+18055550199",
            area_code="805",
            status=TenantStatus.ACTIVE,
        )
        user = User(email=unique_email("other-owner"))
        session.add_all([tenant, user])
        await session.flush()
        session.add(
            Membership(
                user_id=user.id,
                tenant_id=tenant.id,
                role=MembershipRole.OWNER,
                accepted_at=datetime.now(UTC),
            )
        )
        session.add(
            Session(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=datetime.now(UTC) + timedelta(days=1),
                active_tenant_id=tenant.id,
            )
        )
        await session.commit()
        return tenant.id, {"Authorization": f"Bearer {token}"}
