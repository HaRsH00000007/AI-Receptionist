"""Both ways to create a receptionist, deleting one, and what happens next.

Twenty smoke tests over the two pipelines end to end:

* the manual form, from signup to a live receptionist,
* the website flow, from a URL to the same live receptionist,
* deleting one either way,
* and — the part that was broken — what the account can do afterwards.

The bug these were written for: deleting a receptionist left its website draft
behind, so signing in reopened the review screen of a business that no longer
existed, and starting a new website setup was refused forever because one
account may hold only one draft.

No network and no vendor: the website is served from ``httpx.MockTransport``,
DNS is stubbed for the one test host, and every provider is the fake.
"""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Agent, BusinessProfile, PhoneNumber, Tenant
from app.models.enums import AgentStatus, PhoneNumberStatus, TenantStatus
from app.models.website_onboarding import WebsiteOnboarding
from app.website.crawler import CrawlLimits
from app.website.crawler import crawl as real_crawl
from tests.portal_support import COOKIE, PASSWORD, drive_to_active, signup_payload, unique_email

SETUP = "/api/v1/website-setup"
SITE = "https://sunset.example.com"
PUBLIC_IP = "93.184.216.34"

PAGES = {
    "/": (
        "<html><head><title>Sunset Salon</title></head><body>"
        "<p>Sunset Salon has cut hair in Ventura since 2009. A haircut costs thirty dollars.</p>"
        '<a href="/services">Services</a><a href="/contact">Contact</a></body></html>'
    ),
    "/services": (
        "<html><head><title>Services</title></head><body>"
        "<p>We offer haircuts, colour and blow dries for every hair type.</p></body></html>"
    ),
    "/contact": (
        "<html><head><title>Contact</title></head><body>"
        "<p>Open Tuesday to Saturday, nine until six, on Main Street.</p></body></html>"
    ),
}


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    real_getaddrinfo = socket.getaddrinfo

    def fake_dns(host: Any, port: Any, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        if str(host) == "sunset.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_IP, port))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)

    def handler(request: httpx.Request) -> httpx.Response:
        body = PAGES.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="nope")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)

    async def crawl_fake_site(site: Any, limits: CrawlLimits | None = None, **_: Any) -> Any:
        return await real_crawl(site, limits, transport=transport)

    monkeypatch.setattr("app.services.website_onboarding.crawl", crawl_fake_site)


# ---------------------------------------------------------------------------
# Helpers: an account, and the two ways to give it a receptionist
# ---------------------------------------------------------------------------


async def register(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Dana Rivera", "email": unique_email("pipeline"), "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    token = response.cookies.get(COOKIE)
    assert token
    client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}


async def run_worker(api_app: FastAPI, ticks: int = 3) -> None:
    from app.worker import Worker

    worker = Worker(api_app.state.settings, api_app.state.providers, api_app.state.session_factory)
    for _ in range(ticks):
        await worker.tick()


async def via_form(client: AsyncClient, headers: dict[str, str]) -> str:
    """The manual pipeline: the business form, submitted."""
    payload = signup_payload()
    del payload["password"]
    response = await client.post("/api/v1/onboarding", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return str(response.json()["tenant_id"])


async def via_website(client: AsyncClient, api_app: FastAPI, headers: dict[str, str]) -> str:
    """The website pipeline: a URL, a crawl, a review, a confirm."""
    started = await client.post(SETUP, json={"website_url": SITE}, headers=headers)
    assert started.status_code == 202, started.text
    await run_worker(api_app)

    view = await client.get(SETUP, headers=headers)
    assert view.json()["status"] == "ready", view.text
    knowledge = view.json()["knowledge"]

    saved = await client.put(
        f"{SETUP}/knowledge",
        json={
            "business_name": knowledge["business_name"] or "Sunset Salon",
            "business_summary": knowledge["business_summary"],
            "operating_hours": knowledge["operating_hours"] or "Tue-Sat 9-6",
            "services": knowledge["services"] or ["Haircuts"],
            "items": knowledge["items"],
            "questions": knowledge["questions"],
            "sms_templates": knowledge["sms_templates"],
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text

    await client.put(
        f"{SETUP}/choices",
        json={
            "business_type": "salon",
            "greeting_style": "friendly",
            "area_code": "805",
            "contact_phone": "8055550142",
        },
        headers=headers,
    )
    created = await client.post(f"{SETUP}/confirm", headers=headers)
    assert created.status_code == 201, created.text
    return str(created.json()["tenant_id"])


async def delete_receptionist(
    client: AsyncClient, tenant_id: str, headers: dict[str, str], *, release: bool = False
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/tenants/{tenant_id}/receptionist/delete",
        json={"release_number": release},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def state(
    api_app: FastAPI, tenant_id: str
) -> tuple[Tenant, Agent | None, PhoneNumber | None]:
    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        agent = (
            await session.execute(select(Agent).where(Agent.tenant_id == tenant_id))
        ).scalar_one_or_none()
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one_or_none()
        return tenant, agent, number


# ---------------------------------------------------------------------------
# 1-5: the manual pipeline still works
# ---------------------------------------------------------------------------


async def test_the_form_creates_a_business(api_client: AsyncClient, api_app: FastAPI) -> None:
    headers = await register(api_client)
    tenant_id = await via_form(api_client, headers)
    tenant, _, _ = await state(api_app, tenant_id)
    assert tenant.status is TenantStatus.PENDING


async def test_the_form_provisions_a_live_receptionist(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await register(api_client)
    tenant_id = await via_form(api_client, headers)

    await drive_to_active(api_app)

    tenant, agent, number = await state(api_app, tenant_id)
    assert tenant.status is TenantStatus.ACTIVE
    assert agent is not None and agent.status is AgentStatus.ACTIVE
    assert number is not None and number.status is PhoneNumberStatus.ACTIVE


async def test_the_form_leaves_no_website_draft(api_client: AsyncClient, api_app: FastAPI) -> None:
    """The manual path must not touch the website feature at all."""
    headers = await register(api_client)
    await via_form(api_client, headers)
    assert (await api_client.get(SETUP, headers=headers)).status_code == 404


async def test_the_form_builds_its_agent_from_the_v1_prompt(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    from app.models import AgentConfig

    headers = await register(api_client)
    tenant_id = await via_form(api_client, headers)
    await drive_to_active(api_app)

    async with api_app.state.session_factory() as session:
        config = (
            await session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == tenant_id)
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one()
    assert config.template_version == "agent_config.v1", "manual setup is untouched by v2"
    assert "Information to collect from every caller" not in config.system_prompt


async def test_the_form_can_be_submitted_twice_without_making_two_businesses(
    api_client: AsyncClient,
) -> None:
    headers = await register(api_client)
    payload = signup_payload()
    del payload["password"]
    first = await api_client.post("/api/v1/onboarding", json=payload, headers=headers)
    again = await api_client.post("/api/v1/onboarding", json=payload, headers=headers)
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["tenant_id"] == first.json()["tenant_id"]


# ---------------------------------------------------------------------------
# 6-11: the website pipeline
# ---------------------------------------------------------------------------


async def test_a_website_becomes_a_reviewable_draft(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await register(api_client)
    assert (
        await api_client.post(SETUP, json={"website_url": SITE}, headers=headers)
    ).status_code == 202

    await run_worker(api_app)

    body = (await api_client.get(SETUP, headers=headers)).json()
    assert body["status"] == "ready"
    assert body["crawl"]["pages_read"] >= 2
    assert body["knowledge"]["items"]


async def test_the_website_flow_creates_a_business(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await register(api_client)
    tenant_id = await via_website(api_client, api_app, headers)
    tenant, _, _ = await state(api_app, tenant_id)
    assert tenant.name == "Sunset Salon"
    assert tenant.status is TenantStatus.PENDING


async def test_the_website_flow_provisions_the_same_live_receptionist(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Different front door, same pipeline: one agent, one number, one run."""
    headers = await register(api_client)
    tenant_id = await via_website(api_client, api_app, headers)

    await drive_to_active(api_app)

    tenant, agent, number = await state(api_app, tenant_id)
    assert tenant.status is TenantStatus.ACTIVE
    assert agent is not None and agent.status is AgentStatus.ACTIVE
    assert number is not None and number.status is PhoneNumberStatus.ACTIVE


async def test_website_knowledge_reaches_the_live_agent(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    from app.models import AgentConfig

    headers = await register(api_client)
    tenant_id = await via_website(api_client, api_app, headers)
    await drive_to_active(api_app)

    async with api_app.state.session_factory() as session:
        profile = (
            await session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == tenant_id)
            )
        ).scalar_one()
        config = (
            await session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == tenant_id)
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one()
    assert profile.knowledge_json, "what the website taught is kept on the business"
    assert config.template_version == "agent_config.v2"


async def test_the_crawl_buys_nothing(api_client: AsyncClient, api_app: FastAPI) -> None:
    headers = await register(api_client)
    twilio = api_app.state.providers.twilio

    await api_client.post(SETUP, json={"website_url": SITE}, headers=headers)
    await run_worker(api_app)

    assert twilio.behaviour.count("purchase_number") == 0
    async with api_app.state.session_factory() as session:
        assert (await session.execute(select(Tenant))).scalars().all() == []


async def test_a_private_address_is_refused(api_client: AsyncClient) -> None:
    headers = await register(api_client)
    response = await api_client.post(
        SETUP, json={"website_url": "http://169.254.169.254/"}, headers=headers
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 12-16: deleting, from either pipeline
# ---------------------------------------------------------------------------


async def test_deleting_a_form_built_receptionist(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await register(api_client)
    tenant_id = await via_form(api_client, headers)
    await drive_to_active(api_app)

    outcome = await delete_receptionist(api_client, tenant_id, headers)

    assert outcome["complete"] is True
    tenant, agent, _ = await state(api_app, tenant_id)
    assert tenant.status is TenantStatus.CANCELLED
    assert agent is not None and agent.status is AgentStatus.DELETED


async def test_deleting_a_website_built_receptionist(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await register(api_client)
    tenant_id = await via_website(api_client, api_app, headers)
    await drive_to_active(api_app)

    outcome = await delete_receptionist(api_client, tenant_id, headers, release=True)

    assert outcome["complete"] is True
    assert outcome["number_released"] is True
    tenant, _, number = await state(api_app, tenant_id)
    assert tenant.status is TenantStatus.CANCELLED
    assert number is not None and number.status is PhoneNumberStatus.RELEASED


async def test_deleting_throws_the_website_draft_away(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The bug: the draft outlived the business and reopened on sign-in."""
    headers = await register(api_client)
    tenant_id = await via_website(api_client, api_app, headers)
    await drive_to_active(api_app)

    await delete_receptionist(api_client, tenant_id, headers)

    assert (await api_client.get(SETUP, headers=headers)).status_code == 404
    async with api_app.state.session_factory() as session:
        drafts = (await session.execute(select(WebsiteOnboarding))).scalars().all()
    assert drafts == []


async def test_deleting_keeps_the_history_the_website_built(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The draft goes; what it taught the business stays."""
    headers = await register(api_client)
    tenant_id = await via_website(api_client, api_app, headers)
    await drive_to_active(api_app)

    await delete_receptionist(api_client, tenant_id, headers)

    async with api_app.state.session_factory() as session:
        profile = (
            await session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == tenant_id)
            )
        ).scalar_one()
    assert profile.knowledge_json


async def test_deleting_is_still_owner_only(api_client: AsyncClient, api_app: FastAPI) -> None:
    from app.models.enums import MembershipRole
    from tests.portal_support import add_member

    headers = await register(api_client)
    tenant_id = await via_website(api_client, api_app, headers)
    await drive_to_active(api_app)
    admin = await add_member(api_app, tenant_id, MembershipRole.ADMIN)  # type: ignore[arg-type]

    response = await api_client.post(
        f"/api/v1/tenants/{tenant_id}/receptionist/delete",
        json={"release_number": True},
        headers=admin,
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 17-20: and afterwards — the account is not stuck
# ---------------------------------------------------------------------------


async def test_a_website_setup_can_be_started_again_after_a_delete(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The second half of the bug: the old draft blocked every new one."""
    headers = await register(api_client)
    tenant_id = await via_website(api_client, api_app, headers)
    await drive_to_active(api_app)
    await delete_receptionist(api_client, tenant_id, headers)

    again = await api_client.post(SETUP, json={"website_url": SITE}, headers=headers)

    assert again.status_code == 202, again.text
    assert again.json()["status"] == "pending"
    assert again.json()["tenant_id"] is None, "a fresh draft, not the deleted business"


async def test_a_second_receptionist_can_be_built_from_the_website_after_a_delete(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await register(api_client)
    first = await via_website(api_client, api_app, headers)
    await drive_to_active(api_app)
    await delete_receptionist(api_client, first, headers, release=True)

    second = await via_website(api_client, api_app, headers)

    assert second != first
    await drive_to_active(api_app)
    tenant, agent, number = await state(api_app, second)
    assert tenant.status is TenantStatus.ACTIVE
    assert agent is not None and agent.status is AgentStatus.ACTIVE
    assert number is not None and number.status is PhoneNumberStatus.ACTIVE


async def test_the_form_still_works_after_deleting_a_website_built_receptionist(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Deleting one pipeline's work must not block the other."""
    headers = await register(api_client)
    tenant_id = await via_website(api_client, api_app, headers)
    await drive_to_active(api_app)
    await delete_receptionist(api_client, tenant_id, headers)

    second = await via_form(api_client, headers)

    assert second != tenant_id
    await drive_to_active(api_app)
    tenant, _, _ = await state(api_app, second)
    assert tenant.status is TenantStatus.ACTIVE


async def test_a_live_business_still_protects_its_draft(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The guard is about dead businesses, not a licence to start over mid-flight."""
    headers = await register(api_client)
    await via_website(api_client, api_app, headers)

    response = await api_client.post(SETUP, json={"website_url": SITE}, headers=headers)

    assert response.status_code == 409
    assert (await api_client.delete(SETUP, headers=headers)).status_code == 409
