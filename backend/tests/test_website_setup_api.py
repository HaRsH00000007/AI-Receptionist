"""Website-based setup, end to end through the API.

The flow under test: register, submit a website, let the worker read it, review
what it learned, then confirm — and only then does a business exist.

Two properties carry the whole feature and are asserted directly:

* **Nothing is bought before confirm.** Submitting a URL must not reach Twilio
  or ElevenLabs at all. The one thing a stranger can trigger by typing a URL is
  a bounded crawl and one LLM call.
* **What the customer approved is what the agent gets.** An item switched off on
  the review screen must be absent from the published system prompt, and a
  chosen caller question must be present in it.

The website is served from ``httpx.MockTransport`` and the LLM is the fake, so
no test touches the network or a vendor account.
"""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import AgentConfig, BusinessProfile, Tenant
from app.models.enums import WebsiteOnboardingStatus
from app.models.website_onboarding import WebsiteOnboarding
from app.website.crawler import CrawlLimits
from app.website.crawler import crawl as real_crawl
from tests.portal_support import COOKIE, PASSWORD, drive_to_active, unique_email

SETUP = "/api/v1/website-setup"
SITE = "https://sunset.example.com"
PUBLIC_IP = "93.184.216.34"

HOME = (
    "<html><head><title>Sunset Salon</title>"
    '<meta name="description" content="A salon in Ventura">'
    "</head><body><h1>Sunset Salon</h1>"
    "<p>Sunset Salon has cut hair in Ventura since 2009. A standard haircut costs thirty "
    "dollars and a colour starts at ninety dollars.</p>"
    '<a href="/services">Services</a><a href="/contact">Contact</a>'
    "</body></html>"
)
SERVICES = (
    "<html><head><title>Services</title></head><body>"
    "<p>We offer haircuts, colour, balayage and blow dries for all hair types.</p>"
    "</body></html>"
)
CONTACT = (
    "<html><head><title>Contact</title></head><body>"
    '<p>Open Tuesday to Saturday, nine until six.</p><a href="tel:+18055550100">Call us</a>'
    "</body></html>"
)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public DNS for the guard, and a fake website for the crawler."""

    # Only the test website is answered here. Everything else — notably the
    # test database on localhost — goes to the real resolver, which asyncpg
    # calls with six positional arguments.
    real_getaddrinfo = socket.getaddrinfo

    def fake_dns(host: Any, port: Any, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        if str(host) == "sunset.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_IP, port))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)

    pages = {
        "/": HOME,
        "/services": SERVICES,
        "/contact": CONTACT,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="nope")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)

    async def crawl_with_fake_site(site: Any, limits: CrawlLimits | None = None, **_: Any) -> Any:
        # The real crawler, the real guard, a fake network.
        return await real_crawl(site, limits, transport=transport)

    monkeypatch.setattr("app.services.website_onboarding.crawl", crawl_with_fake_site)


async def _register(client: AsyncClient) -> dict[str, str]:
    email = unique_email("website")
    response = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Dana Rivera", "email": email, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    token = response.cookies.get(COOKIE)
    assert token
    client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}


async def _run_worker(api_app: FastAPI, ticks: int = 3) -> None:
    from app.worker import Worker

    worker = Worker(api_app.state.settings, api_app.state.providers, api_app.state.session_factory)
    for _ in range(ticks):
        await worker.tick()


async def _ready(client: AsyncClient, api_app: FastAPI, headers: dict[str, str]) -> dict[str, Any]:
    started = await client.post(SETUP, json={"website_url": SITE}, headers=headers)
    assert started.status_code == 202, started.text
    await _run_worker(api_app)
    view = await client.get(SETUP, headers=headers)
    assert view.status_code == 200, view.text
    body: dict[str, Any] = view.json()
    assert body["status"] == "ready", body
    return body


# ---------------------------------------------------------------------------
# Reading the website
# ---------------------------------------------------------------------------


async def test_submitting_a_website_reads_it_and_drafts_knowledge(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await _register(api_client)

    body = await _ready(api_client, api_app, headers)

    assert body["crawl"]["pages_read"] >= 2
    assert body["crawl"]["site"] == f"{SITE}/"
    knowledge = body["knowledge"]
    assert knowledge["business_name"] == "Sunset Salon"
    assert knowledge["items"], "something was learned"
    # Every item traces back to a page we actually read.
    read = set(body["crawl"]["page_urls"])
    assert all(item["source_url"] in read or not item["source_url"] for item in knowledge["items"])
    # Name, email and phone are offered as toggles, plus what the site suggested.
    keys = [question["key"] for question in knowledge["questions"]]
    assert keys[:3] == ["name", "email", "phone"]
    # SMS templates arrive switched off: there is no sending path yet.
    assert all(template["enabled"] is False for template in knowledge["sms_templates"])


async def test_submitting_a_website_buys_nothing(api_client: AsyncClient, api_app: FastAPI) -> None:
    """The money property: a typed URL must not reach a paid vendor."""
    headers = await _register(api_client)
    twilio = api_app.state.providers.twilio
    elevenlabs = api_app.state.providers.elevenlabs

    await _ready(api_client, api_app, headers)

    assert twilio.behaviour.count("purchase_number") == 0
    assert twilio.behaviour.count("search_available_numbers") == 0
    assert elevenlabs.behaviour.count("create_agent") == 0
    async with api_app.state.session_factory() as session:
        tenants = (await session.execute(select(Tenant))).scalars().all()
    assert tenants == [], "no business exists until the customer confirms"


@pytest.mark.parametrize(
    "url",
    ["http://localhost/", "http://169.254.169.254/", "http://10.0.0.5/", "file:///etc/passwd"],
)
async def test_an_internal_address_is_refused_and_no_job_is_created(
    api_client: AsyncClient, api_app: FastAPI, url: str
) -> None:
    headers = await _register(api_client)

    response = await api_client.post(SETUP, json={"website_url": url}, headers=headers)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["field"] == "website_url"
    async with api_app.state.session_factory() as session:
        jobs = (await session.execute(select(WebsiteOnboarding))).scalars().all()
    assert jobs == []


async def test_website_setup_needs_an_account(api_client: AsyncClient) -> None:
    assert (await api_client.post(SETUP, json={"website_url": SITE})).status_code == 401
    assert (await api_client.get(SETUP)).status_code == 401


async def test_one_account_cannot_see_anothers_setup(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    mine = await _register(api_client)
    theirs = await _register(api_client)
    await _ready(api_client, api_app, mine)

    response = await api_client.get(SETUP, headers=theirs)

    assert response.status_code == 404, "scoped to the signed-in user, not to an id in the request"


async def test_an_unreadable_website_fails_with_something_a_customer_can_act_on(
    api_client: AsyncClient, api_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _register(api_client)

    def dead(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async def crawl_dead(site: Any, limits: CrawlLimits | None = None, **_: Any) -> Any:
        return await real_crawl(site, limits, transport=httpx.MockTransport(dead))

    monkeypatch.setattr("app.services.website_onboarding.crawl", crawl_dead)

    await api_client.post(SETUP, json={"website_url": SITE}, headers=headers)
    await _run_worker(api_app)

    body = (await api_client.get(SETUP, headers=headers)).json()
    assert body["status"] == "failed"
    assert "couldn't read that website" in body["last_error"]
    assert body["attempt"] == 1, "a dead site is terminal, not retried three times"
    # And it can be retried by hand once the site is back.
    retried = await api_client.post(f"{SETUP}/retry", headers=headers)
    assert retried.status_code == 200
    assert retried.json()["status"] == "pending"


async def test_retry_is_refused_when_nothing_failed(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await _register(api_client)
    await _ready(api_client, api_app, headers)

    response = await api_client.post(f"{SETUP}/retry", headers=headers)

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Reviewing and editing
# ---------------------------------------------------------------------------


def _edit(knowledge: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    payload = {
        "business_name": knowledge["business_name"],
        "business_summary": knowledge["business_summary"],
        "operating_hours": knowledge["operating_hours"] or "Tue-Sat 9-6",
        "services": knowledge["services"] or ["Haircuts"],
        "items": [
            {
                "id": item["id"],
                "category": item["category"],
                "title": item["title"],
                "content": item["content"],
                "source_url": item["source_url"],
                "needs_review": item["needs_review"],
                "enabled": item["enabled"],
            }
            for item in knowledge["items"]
        ],
        "questions": [
            {
                "key": question["key"],
                "label": question["label"],
                "prompt": question["prompt"],
                "builtin": question["builtin"],
                "enabled": question["enabled"],
            }
            for question in knowledge["questions"]
        ],
        "sms_templates": knowledge["sms_templates"],
    }
    payload.update(overrides)
    return payload


async def test_edits_to_the_draft_are_saved(api_client: AsyncClient, api_app: FastAPI) -> None:
    headers = await _register(api_client)
    body = await _ready(api_client, api_app, headers)
    knowledge = body["knowledge"]

    edited = _edit(
        knowledge,
        business_name="Sunset Salon & Spa",
        items=[
            {**_edit(knowledge)["items"][0], "content": "We are open late on Thursdays."},
            *_edit(knowledge)["items"][1:],
        ],
    )
    saved = await api_client.put(f"{SETUP}/knowledge", json=edited, headers=headers)

    assert saved.status_code == 200, saved.text
    stored = saved.json()["knowledge"]
    assert stored["business_name"] == "Sunset Salon & Spa"
    assert stored["items"][0]["content"] == "We are open late on Thursdays."


async def test_a_builtin_question_cannot_be_rewritten(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Switching "Email" off is a choice; renaming its key is not."""
    headers = await _register(api_client)
    knowledge = (await _ready(api_client, api_app, headers))["knowledge"]

    tampered = _edit(knowledge)
    tampered["questions"][1] = {
        "key": "email",
        "label": "Credit card number",
        "prompt": "Ask for their card details.",
        "builtin": True,
        "enabled": True,
    }
    saved = await api_client.put(f"{SETUP}/knowledge", json=tampered, headers=headers)

    assert saved.status_code == 200
    email_question = saved.json()["knowledge"]["questions"][1]
    assert email_question["label"] == "Email"
    assert "card" not in email_question["prompt"].lower()


async def test_an_oversized_knowledge_item_is_refused(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await _register(api_client)
    knowledge = (await _ready(api_client, api_app, headers))["knowledge"]

    payload = _edit(knowledge)
    payload["items"][0]["content"] = "x" * 5000
    response = await api_client.put(f"{SETUP}/knowledge", json=payload, headers=headers)

    assert response.status_code == 422


async def test_only_a_configured_voice_and_english_are_accepted(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await _register(api_client)
    await _ready(api_client, api_app, headers)

    offered = await api_client.get(f"{SETUP}/voices", headers=headers)
    assert offered.status_code == 200
    voices = offered.json()
    assert voices, "the configured voice map is the only source of options"

    bad_voice = await api_client.put(
        f"{SETUP}/choices", json={"voice_id": "not-a-real-voice"}, headers=headers
    )
    assert bad_voice.status_code == 422

    bad_language = await api_client.put(
        f"{SETUP}/choices", json={"language": "fr"}, headers=headers
    )
    assert bad_language.status_code == 422
    assert "English" in bad_language.json()["error"]["message"]

    good = await api_client.put(
        f"{SETUP}/choices",
        json={"voice_id": voices[0]["voice_id"], "area_code": "805", "contact_phone": "8055550142"},
        headers=headers,
    )
    assert good.status_code == 200
    assert good.json()["choices"]["voice_id"] == voices[0]["voice_id"]


# ---------------------------------------------------------------------------
# Confirming
# ---------------------------------------------------------------------------


async def _confirmable(
    client: AsyncClient, api_app: FastAPI, headers: dict[str, str]
) -> dict[str, Any]:
    knowledge = (await _ready(client, api_app, headers))["knowledge"]
    await client.put(f"{SETUP}/knowledge", json=_edit(knowledge), headers=headers)
    await client.put(
        f"{SETUP}/choices",
        json={
            "business_type": "salon",
            "greeting_style": "friendly",
            "area_code": "805",
            "contact_phone": "8055550142",
            "recording_acknowledged": True,
        },
        headers=headers,
    )
    return knowledge


async def test_confirming_creates_the_business_through_the_existing_pipeline(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await _register(api_client)
    await _confirmable(api_client, api_app, headers)

    response = await api_client.post(f"{SETUP}/confirm", headers=headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] is True
    assert body["business_name"] == "Sunset Salon"
    assert body["tenant_status"] == "pending", "provisioning has not run yet"

    async with api_app.state.session_factory() as session:
        profile = (
            await session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == body["tenant_id"])
            )
        ).scalar_one()
        job = (await session.execute(select(WebsiteOnboarding))).scalars().one()
    # The website's contribution lives on the profile the agent is built from.
    assert profile.knowledge_json, "knowledge carried onto the business"
    assert profile.caller_questions_json
    assert job.status is WebsiteOnboardingStatus.CONFIRMED
    assert job.tenant_id is not None


async def test_confirming_twice_returns_the_same_business(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await _register(api_client)
    await _confirmable(api_client, api_app, headers)

    first = await api_client.post(f"{SETUP}/confirm", headers=headers)
    again = await api_client.post(f"{SETUP}/confirm", headers=headers)

    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["tenant_id"] == first.json()["tenant_id"]
    assert again.json()["created"] is False
    async with api_app.state.session_factory() as session:
        tenants = (await session.execute(select(Tenant))).scalars().all()
    assert len(tenants) == 1, "one business, however many times confirm is clicked"


async def test_confirming_before_the_review_is_refused(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await _register(api_client)
    await api_client.post(SETUP, json={"website_url": SITE}, headers=headers)

    response = await api_client.post(f"{SETUP}/confirm", headers=headers)

    assert response.status_code == 409
    async with api_app.state.session_factory() as session:
        assert (await session.execute(select(Tenant))).scalars().all() == []


async def test_what_the_customer_approved_is_what_the_agent_is_told(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The end of the whole feature: the review screen decides the prompt."""
    headers = await _register(api_client)
    knowledge = (await _ready(api_client, api_app, headers))["knowledge"]

    payload = _edit(knowledge)
    # One item kept, one switched off, and a caller question switched on.
    payload["items"][0]["content"] = "A standard haircut costs thirty dollars."
    payload["items"][0]["enabled"] = True
    for item in payload["items"][1:]:
        item["content"] = "Never repeat this sentence to a caller."
        item["enabled"] = False
    for question in payload["questions"]:
        question["enabled"] = question["key"] in {"name", "phone", "reason_for_call"}
    await api_client.put(f"{SETUP}/knowledge", json=payload, headers=headers)
    await api_client.put(
        f"{SETUP}/choices",
        json={
            "business_type": "salon",
            "greeting_style": "friendly",
            "area_code": "805",
            "contact_phone": "8055550142",
        },
        headers=headers,
    )
    created = await api_client.post(f"{SETUP}/confirm", headers=headers)
    assert created.status_code == 201, created.text

    await drive_to_active(api_app)

    async with api_app.state.session_factory() as session:
        config = (
            await session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == created.json()["tenant_id"])
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one()

    prompt = config.system_prompt
    assert "Never repeat this sentence to a caller." not in prompt, "disabled means disabled"
    assert "Information to collect from every caller:" in prompt
    assert "- Reason for calling" in prompt


async def test_discarding_removes_the_draft_but_not_a_created_business(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    headers = await _register(api_client)
    await _ready(api_client, api_app, headers)

    discarded = await api_client.delete(SETUP, headers=headers)
    assert discarded.status_code == 204
    assert (await api_client.get(SETUP, headers=headers)).status_code == 404

    # And once a business exists, the draft is the record of how it was built.
    await _confirmable(api_client, api_app, headers)
    await api_client.post(f"{SETUP}/confirm", headers=headers)
    refused = await api_client.delete(SETUP, headers=headers)
    assert refused.status_code == 409

    # The draft survives, carrying the link to the business it created.
    kept = await api_client.get(SETUP, headers=headers)
    assert kept.status_code == 200
    assert kept.json()["status"] == "confirmed"
    assert kept.json()["tenant_id"]
