"""The customer's way in: signed in after signup, and able to retry a stopped setup.

The dashboard decides where to send someone from their tenant's state; these
tests pin the backend half of that. A new business is signed in the moment it
signs up — so it lands on its own setup page, not a login form — and a setup
that stopped on an error can be retried by its owner without calling support.

The takeover test comes first because it is the one that must never regress:
signup is anonymous, and signing in whoever submits a known address would hand
them that account.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import AuditLog, ProvisioningRun, Tenant
from app.models.enums import (
    AuditAction,
    MembershipRole,
    ProvisioningStatus,
    StepStatus,
    TenantStatus,
)
from app.models.provisioning import ProvisioningStepRecord
from tests.portal_support import (
    COOKIE,
    add_member,
    drive_to_active,
    other_tenant,
    sign_up,
    signup_payload,
    unique_email,
)
from tests.support import grant

# ---------------------------------------------------------------------------
# Signed in after signup
# ---------------------------------------------------------------------------


async def test_signup_with_an_existing_address_does_not_sign_anyone_in(
    api_client: AsyncClient,
) -> None:
    """The takeover guard: a known address is never given a session by signup."""
    email = unique_email()
    first = await api_client.post("/api/v1/signups", json=signup_payload(notification_email=email))
    assert first.status_code == 201

    # Same inbox, a different business — a legitimate second signup, or an
    # attacker typing someone else's address. Either way: no session.
    api_client.cookies.clear()
    second = await api_client.post(
        "/api/v1/signups",
        json=signup_payload(
            notification_email=email,
            business_name="Another Business",
            password="an attacker's password",
        ),
    )
    assert second.json()["signed_in"] is False
    assert COOKIE not in second.cookies


async def test_a_new_account_is_signed_in_by_its_signup(api_client: AsyncClient) -> None:
    owner = await sign_up(api_client)

    me = await api_client.get("/api/v1/auth/me", headers=owner.headers)
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == owner.email
    assert body["has_password"] is True
    assert [m["tenant_id"] for m in body["memberships"]] == [str(owner.tenant_id)]
    # The session is scoped to the new tenant, so the dashboard opens it directly.
    assert body["active_tenant_id"] == str(owner.tenant_id)


async def test_a_signup_without_a_password_is_not_signed_in(api_client: AsyncClient) -> None:
    """The Tally path has no password field; its owner signs in by link later."""
    payload = signup_payload()
    del payload["password"]
    response = await api_client.post("/api/v1/signups", json=payload)
    assert response.status_code == 201
    assert response.json()["signed_in"] is False
    assert COOKIE not in response.cookies


async def test_the_new_owner_can_watch_setup_with_their_session(
    api_client: AsyncClient,
) -> None:
    """The signed-in setup page reads provisioning with the session, no grant."""
    owner = await sign_up(api_client)
    response = await api_client.get(
        f"/api/v1/tenants/{owner.tenant_id}/provisioning", headers=owner.headers
    )
    assert response.status_code == 200
    assert response.json()["tenant_id"] == str(owner.tenant_id)


# ---------------------------------------------------------------------------
# Retrying a stopped setup
# ---------------------------------------------------------------------------


async def _fail_the_run(api_app: FastAPI, tenant_id: uuid.UUID) -> uuid.UUID:
    """Put the tenant's run in FAILED, the way a vendor error would."""
    async with api_app.state.session_factory() as session:
        run = (
            await session.execute(
                select(ProvisioningRun).where(ProvisioningRun.tenant_id == tenant_id)
            )
        ).scalar_one()
        run.status = ProvisioningStatus.FAILED
        run.last_error = "[vendor_error] twilio returned 500"
        step = (
            (
                await session.execute(
                    select(ProvisioningStepRecord).where(ProvisioningStepRecord.run_id == run.id)
                )
            )
            .scalars()
            .first()
        )
        assert step is not None
        step.status = StepStatus.FAILED
        step.error = "twilio returned 500"
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        tenant.status = TenantStatus.FAILED
        await session.commit()
        return run.id


async def test_the_owner_can_retry_a_failed_setup(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await sign_up(api_client)
    run_id = await _fail_the_run(api_app, owner.tenant_id)

    response = await api_client.post(
        f"/api/v1/tenants/{owner.tenant_id}/provisioning/retry", headers=owner.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == str(run_id)
    assert body["steps_reset"], "the failed step should have been reset"

    async with api_app.state.session_factory() as session:
        run = await session.get(ProvisioningRun, run_id)
        tenant = await session.get(Tenant, owner.tenant_id)
        audit = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == AuditAction.PROVISIONING_RETRIED)
                )
            )
            .scalars()
            .all()
        )
    assert run is not None and run.status is ProvisioningStatus.DRAFT
    assert run.last_error is None
    assert tenant is not None and tenant.status is TenantStatus.PENDING
    assert any(entry.tenant_id == owner.tenant_id for entry in audit)

    # And it genuinely resumes: the worker takes it to the end.
    await drive_to_active(api_app)
    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, owner.tenant_id)
    assert tenant is not None and tenant.status is TenantStatus.ACTIVE


async def test_a_run_that_has_not_failed_cannot_be_retried(api_client: AsyncClient) -> None:
    owner = await sign_up(api_client)
    response = await api_client.post(
        f"/api/v1/tenants/{owner.tenant_id}/provisioning/retry", headers=owner.headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_a_plain_member_cannot_retry(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await sign_up(api_client)
    await _fail_the_run(api_app, owner.tenant_id)
    member = await add_member(api_app, owner.tenant_id, MembershipRole.MEMBER)

    response = await api_client.post(
        f"/api/v1/tenants/{owner.tenant_id}/provisioning/retry", headers=member
    )
    assert response.status_code == 403


async def test_another_tenant_cannot_retry_this_one(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await sign_up(api_client)
    await _fail_the_run(api_app, owner.tenant_id)
    _, stranger = await other_tenant(api_app)

    response = await api_client.post(
        f"/api/v1/tenants/{owner.tenant_id}/provisioning/retry", headers=stranger
    )
    # Not 403: that would confirm the tenant exists.
    assert response.status_code == 404


async def test_the_status_link_cannot_retry(api_client: AsyncClient, api_app: FastAPI) -> None:
    """The anonymous status grant reads provisioning; it never acts on it."""
    owner = await sign_up(api_client)
    await _fail_the_run(api_app, owner.tenant_id)
    api_client.cookies.clear()

    response = await api_client.post(
        f"/api/v1/tenants/{owner.tenant_id}/provisioning/retry",
        params=grant(api_app, owner.tenant_id),
    )
    assert response.status_code == 401
