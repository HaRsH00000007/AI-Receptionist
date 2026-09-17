"""Custom greetings — the owner's own opening line.

The greeting is the one piece of configuration a caller hears word for word, so
the guarantee here is narrow and strict: whatever the owner picked or typed is
what the agent says, whether the rest of the configuration came from the model
or from the deterministic fallback.

The voice is deliberately *not* part of that. It still follows the greeting
style, so a custom line is spoken in the register the owner asked for.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from app.models import AgentConfig
from app.models.enums import AgentConfigSource, BusinessType, GreetingStyle
from app.providers.fakes.llm import FakeLLMProvider
from app.schemas.agent_config import GeneratedAgentConfig
from app.schemas.signup import SignupRequest
from app.services.config_generator import ConfigGenerator
from app.services.normalization import normalize_greeting
from tests.support import build_settings, grant

GREETING = "Sunset Salon, this is Dana's studio — how can I help you today?"

SIGNUP: dict[str, Any] = {
    "business_name": "Sunset Salon",
    "business_type": "salon",
    "services": "cuts, color",
    "operating_hours": "Mon-Fri 9-6",
    "greeting_style": "friendly",
    "escalation_rules": "Text the owner for emergencies",
    "notification_email": "owner@sunsetsalon.example.com",
    "area_code": "805",
    "plan": "starter",
    "contact_phone": "8055550142",
}


def _tenant() -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Sunset Salon",
        business_type=BusinessType.SALON,
        timezone="America/Los_Angeles",
        contact_email="owner@sunsetsalon.example.com",
    )


def _profile(greeting_custom: str | None = None) -> Any:
    return SimpleNamespace(
        services=["cuts", "colour"],
        hours_raw="Tue-Sat 9-6",
        hours_json=None,
        greeting_style=GreetingStyle.FRIENDLY,
        greeting_custom=greeting_custom,
        escalation_raw=None,
        escalation_json=None,
    )


def _max_length(model: type[Any], field: str) -> int | None:
    for constraint in model.model_fields[field].metadata:
        limit = getattr(constraint, "max_length", None)
        if limit is not None:
            return int(limit)
    return None


# ---------------------------------------------------------------------------
# Normalization — what reaches the vendor
# ---------------------------------------------------------------------------
def test_a_typed_greeting_becomes_one_speakable_line() -> None:
    # A textarea invites line breaks; a caller cannot hear one.
    assert normalize_greeting("Hello there.\n  How can I help?") == "Hello there. How can I help?"


def test_control_characters_are_dropped_without_joining_words() -> None:
    assert normalize_greeting("Hi\x07 there") == "Hi there"
    assert normalize_greeting("Hello\nthere") == "Hello there"


def test_a_blank_greeting_means_write_one_for_me() -> None:
    assert normalize_greeting("   \n\t ") == ""


# ---------------------------------------------------------------------------
# The signup contract
# ---------------------------------------------------------------------------
def test_a_greeting_is_optional_and_defaults_to_empty() -> None:
    request = SignupRequest.model_validate(SIGNUP)
    assert request.custom_greeting == ""


def test_the_greeting_is_normalized_on_the_way_in() -> None:
    request = SignupRequest.model_validate({**SIGNUP, "custom_greeting": " Hi there!\n Welcome. "})
    assert request.custom_greeting == "Hi there! Welcome."


def test_the_cap_matches_the_field_the_greeting_ends_up_in() -> None:
    """A longer line would be accepted at signup and rejected at generation.

    Pinned rather than imported, because the two live in different layers; if
    one moves, this fails instead of a customer's greeting silently vanishing.
    """
    assert _max_length(SignupRequest, "custom_greeting") == _max_length(
        GeneratedAgentConfig, "greeting"
    )


def test_a_greeting_longer_than_the_cap_is_refused() -> None:
    with pytest.raises(ValidationError):
        SignupRequest.model_validate({**SIGNUP, "custom_greeting": "a" * 301})


# ---------------------------------------------------------------------------
# Generation — both paths
# ---------------------------------------------------------------------------
async def test_the_owners_greeting_replaces_the_generated_one() -> None:
    settings = build_settings()

    outcome = await ConfigGenerator(FakeLLMProvider(), settings).generate(
        _tenant(), _profile(GREETING)
    )

    assert outcome.source is AgentConfigSource.LLM
    assert outcome.config.greeting == GREETING


async def test_the_owners_greeting_survives_the_template_fallback() -> None:
    """Two unusable replies exhaust the repair attempt and force the template.

    The owner's line must not be collateral damage of a model having a bad day.
    """
    settings = build_settings()
    llm = FakeLLMProvider(scripted=["not json at all", "still not json"])

    outcome = await ConfigGenerator(llm, settings).generate(_tenant(), _profile(GREETING))

    assert outcome.source is AgentConfigSource.TEMPLATE_FALLBACK
    assert outcome.config.greeting == GREETING


async def test_without_one_the_generated_greeting_is_kept() -> None:
    settings = build_settings()

    outcome = await ConfigGenerator(FakeLLMProvider(), settings).generate(_tenant(), _profile())

    assert outcome.config.greeting
    assert outcome.config.greeting != GREETING


async def test_only_the_greeting_is_replaced() -> None:
    """The rest of the configuration is the model's work, untouched."""
    settings = build_settings()
    tenant, profile = _tenant(), _profile(GREETING)

    generated = await ConfigGenerator(FakeLLMProvider(), settings).generate(tenant, _profile())
    chosen = await ConfigGenerator(FakeLLMProvider(), settings).generate(tenant, profile)

    assert chosen.config.model_dump(exclude={"greeting"}) == generated.config.model_dump(
        exclude={"greeting"}
    )


# ---------------------------------------------------------------------------
# End to end (skips without PostgreSQL)
# ---------------------------------------------------------------------------
async def _signup(api_client: AsyncClient, **overrides: Any) -> uuid.UUID:
    response = await api_client.post("/api/v1/signups", json={**SIGNUP, **overrides})
    assert response.status_code == 201
    return uuid.UUID(response.json()["tenant_id"])


async def _drive_to_active(api_app: FastAPI, ticks: int = 12) -> None:
    from app.worker import Worker

    worker = Worker(api_app.state.settings, api_app.state.providers, api_app.state.session_factory)
    for _ in range(ticks):
        await worker.tick()


async def test_the_profile_reports_the_greeting_the_owner_chose(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    tenant_id = await _signup(api_client, custom_greeting=GREETING)

    body = (
        await api_client.get(
            f"/api/v1/tenants/{tenant_id}/profile", params=grant(api_app, tenant_id)
        )
    ).json()

    assert body["custom_greeting"] == GREETING


async def test_a_tenant_who_wants_one_written_reports_none(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    tenant_id = await _signup(api_client)

    body = (
        await api_client.get(
            f"/api/v1/tenants/{tenant_id}/profile", params=grant(api_app, tenant_id)
        )
    ).json()

    assert body["custom_greeting"] is None


async def test_the_live_config_speaks_the_owners_greeting(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The whole point, end to end: the line goes to the vendor verbatim."""
    tenant_id = await _signup(api_client, custom_greeting=GREETING)
    await _drive_to_active(api_app)

    async with api_app.state.session_factory() as session:
        config = (
            await session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == tenant_id)
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one()

    assert config.first_message == GREETING
    # The voice still comes from the style, not from the custom line.
    assert config.voice_id


async def test_the_profile_shows_both_the_choice_and_what_is_live(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    tenant_id = await _signup(api_client, custom_greeting=GREETING)
    await _drive_to_active(api_app)

    body = (
        await api_client.get(
            f"/api/v1/tenants/{tenant_id}/profile", params=grant(api_app, tenant_id)
        )
    ).json()

    assert body["custom_greeting"] == GREETING
    assert body["greeting"] == GREETING
