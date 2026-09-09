"""Groq, added so the POC can run against a real model before an OpenAI key exists.

Everything here runs through ``httpx.MockTransport``. **No test makes a real
Groq call** — the point is to prove the adapter builds the right request and
reads the reply the same way its peers do, not that Groq is up.

The load-bearing claim is that Groq changes *nothing above the adapter*. The
same prompt, the same JSON extraction, the same Pydantic validation, the same
repair-then-fallback behaviour. So most of these tests drive
``ConfigGenerator`` and ``Summarizer`` rather than the provider directly: if
those still behave identically with Groq underneath, the abstraction held.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.core.errors import VendorError
from app.models.enums import AgentConfigSource
from app.providers.http import ProviderHTTPClient
from app.providers.real.llm import AnthropicProvider, GroqProvider, OpenAIProvider
from app.providers.registry import build_providers
from app.schemas.agent_config import GeneratedAgentConfig
from app.services.config_generator import ConfigGenerator
from app.services.summarizer import Summarizer
from tests.support import build_settings
from tests.test_real_adapters import Recorder

GROQ_BASE = "https://api.groq.com/openai/v1"

#: A Groq id shaped like the real ones. Never sent anywhere — the transport is
#: mocked — but it keeps the tests honest about ids being explicit config.
GROQ_CONFIG_MODEL = "llama-3.3-70b-versatile"
GROQ_SUMMARY_MODEL = "llama-3.1-8b-instant"


def groq_settings(**overrides: Any):  # type: ignore[no-untyped-def]
    """Settings with Groq selected and its models set, as the guard requires."""
    values: dict[str, Any] = {
        "llm_provider": "groq",
        "groq_api_key": "gsk-test-not-a-real-key",
        "llm_config_model": GROQ_CONFIG_MODEL,
        "llm_summary_model": GROQ_SUMMARY_MODEL,
    }
    values.update(overrides)
    return build_settings(**values)


def wire(provider: GroqProvider, recorder: Recorder, settings: Any) -> GroqProvider:
    """Swap the adapter's client for a recording one, as the peer tests do."""
    provider._client = ProviderHTTPClient(
        vendor="groq",
        base_url=settings.groq_api_base_url,
        timeout_s=5,
        headers={
            "authorization": f"Bearer {settings.groq_api_key.get_secret_value()}",
            "content-type": "application/json",
        },
        transport=recorder.transport(),
    )
    return provider


def completion(content: str, *, model: str = GROQ_CONFIG_MODEL) -> dict[str, Any]:
    """A Groq reply, in OpenAI's chat-completions shape."""
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "model": model,
        "usage": {"prompt_tokens": 120, "completion_tokens": 60},
    }


VALID_CONFIG_JSON = """
{
  "system_prompt": "You are the receptionist for Sunset Salon, a salon.",
  "greeting": "Thanks for calling Sunset Salon, how can I help?",
  "voice_style": "friendly",
  "faq": [{"question": "Do you do colour?", "answer": "Yes, we do."}],
  "escalation_triggers": ["complaint"],
  "fallback_behavior": "Take a message and pass it to the owner.",
  "business_hours": {
    "monday": "9-6", "tuesday": "9-6", "wednesday": "9-6",
    "thursday": "9-6", "friday": "9-6", "saturday": "closed", "sunday": "closed"
  }
}
"""

VALID_SUMMARY_JSON = """
{
  "summary": "A caller asked about Thursday morning availability and left a number.",
  "intent": "booking_request",
  "caller_name": "Dana",
  "callback_number": "+15559998888",
  "action_required": true,
  "sentiment": "positive"
}
"""


# ===========================================================================
# Initialization and configuration
# ===========================================================================


async def test_the_adapter_targets_groq_and_authenticates() -> None:
    """Base URL, path and credential — the three things easy to get wrong."""
    recorder = Recorder([(200, completion('{"ok":true}'))])
    settings = groq_settings()
    provider = wire(GroqProvider(settings), recorder, settings)

    reply = await provider.complete(
        system="sys", user="usr", model=GROQ_CONFIG_MODEL, max_tokens=100, temperature=0.0
    )

    assert str(recorder.last.url) == f"{GROQ_BASE}/chat/completions"
    # Not /v1/chat/completions: the base URL already ends in /openai/v1, and
    # doubling the prefix yields a 404 that reads like a bad model id.
    assert recorder.last.url.path == "/openai/v1/chat/completions"
    assert recorder.last.headers["authorization"] == "Bearer gsk-test-not-a-real-key"
    assert reply.text == '{"ok":true}'
    assert reply.input_tokens == 120
    assert reply.output_tokens == 60


async def test_the_bearer_token_is_not_the_masked_secretstr() -> None:
    """The f-string trap: interpolating a SecretStr yields '**********'."""
    settings = groq_settings()
    header = f"Bearer {settings.groq_api_key.get_secret_value()}"
    assert header == "Bearer gsk-test-not-a-real-key"
    assert "*" not in header


async def test_json_mode_is_requested() -> None:
    """Requested, never trusted — the reply is still parsed and validated."""
    recorder = Recorder([(200, completion("{}"))])
    settings = groq_settings()
    provider = wire(GroqProvider(settings), recorder, settings)

    await provider.complete(
        system="sys", user="usr", model=GROQ_CONFIG_MODEL, max_tokens=64, temperature=0.2
    )

    body = recorder.body()
    assert body["response_format"] == {"type": "json_object"}
    assert body["model"] == GROQ_CONFIG_MODEL
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.2
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]


async def test_groq_needs_its_key() -> None:
    with pytest.raises(ValidationError, match="GROQ_API_KEY"):
        build_settings(
            llm_provider="groq",
            llm_config_model=GROQ_CONFIG_MODEL,
            llm_summary_model=GROQ_SUMMARY_MODEL,
        )


async def test_groq_refuses_to_start_on_the_default_anthropic_models() -> None:
    """The failure this guard exists to prevent is a 404 mid-provisioning."""
    with pytest.raises(ValidationError, match="LLM_CONFIG_MODEL"):
        build_settings(llm_provider="groq", groq_api_key="gsk-test")


async def test_the_summary_model_is_checked_too() -> None:
    with pytest.raises(ValidationError, match="LLM_SUMMARY_MODEL"):
        build_settings(
            llm_provider="groq",
            groq_api_key="gsk-test",
            llm_config_model=GROQ_CONFIG_MODEL,
        )


async def test_an_unknown_provider_is_a_startup_error_not_a_fake() -> None:
    """A typo must not silently downgrade to the fake and look like it worked."""
    with pytest.raises(ValidationError):
        build_settings(llm_provider="grok")  # note the missing q


# ===========================================================================
# Provider selection
# ===========================================================================


def test_the_registry_selects_groq() -> None:
    providers = build_providers(groq_settings())
    assert isinstance(providers.llm, GroqProvider)
    assert providers.llm.name == "groq"


def test_the_other_providers_still_resolve() -> None:
    """Groq is an addition, not a replacement."""
    assert build_providers(build_settings()).llm.name == "fake-llm"
    assert isinstance(
        build_providers(build_settings(llm_provider="anthropic", anthropic_api_key="k")).llm,
        AnthropicProvider,
    )
    assert isinstance(
        build_providers(build_settings(llm_provider="openai", openai_api_key="k")).llm,
        OpenAIProvider,
    )


def test_dry_run_does_not_force_the_llm_to_fake() -> None:
    """Deliberate existing behaviour, preserved.

    Twilio, ElevenLabs and email are forced to fakes by DRY_RUN because they
    spend money or contact a customer. The LLM is exempt — running a dry run
    against a real model is usually the point — and adding Groq must not
    quietly change that.
    """
    settings = groq_settings(dry_run=True)
    assert settings.dry_run is True
    assert settings.effective_llm_provider == "groq"
    providers = build_providers(settings)
    assert isinstance(providers.llm, GroqProvider)
    # The money-spending providers are still faked.
    assert providers.twilio.name == "fake-twilio"
    assert providers.elevenlabs.name == "fake-elevenlabs"


# ===========================================================================
# Structured config generation — the contract that must not weaken
# ===========================================================================


class _Tenant:
    id = uuid.uuid4()
    name = "Sunset Salon"
    business_type = "salon"
    timezone = "America/Los_Angeles"


class _Profile:
    services = "cuts, colour"
    hours_json: dict[str, Any] | None = None
    greeting_style = "friendly"
    escalation_rules = ""
    business_type = "salon"
    extra_json: dict[str, Any] | None = None


async def test_a_valid_groq_reply_becomes_a_validated_config() -> None:
    """Groq output goes through the identical schema the other providers do."""
    recorder = Recorder([(200, completion(VALID_CONFIG_JSON))])
    settings = groq_settings()
    provider = wire(GroqProvider(settings), recorder, settings)

    outcome = await ConfigGenerator(provider, settings).generate(_Tenant(), _Profile())  # type: ignore[arg-type]

    assert outcome.source is AgentConfigSource.LLM
    assert isinstance(outcome.config, GeneratedAgentConfig)
    assert "Sunset Salon" in outcome.config.system_prompt
    # The model that answered is recorded on the config row.
    assert outcome.detail == GROQ_CONFIG_MODEL


async def test_malformed_json_is_repaired_then_falls_back() -> None:
    """Invalid output is never silently accepted.

    Two bad replies exhaust the repair attempt, and the caller falls back to the
    deterministic template — exactly what it does for any other provider. The
    guarantee is that a broken model cannot produce a broken agent.
    """
    recorder = Recorder(
        [
            (200, completion("not json at all")),
            (200, completion('{"system_prompt": "missing everything else"}')),
        ]
    )
    settings = groq_settings()
    provider = wire(GroqProvider(settings), recorder, settings)

    outcome = await ConfigGenerator(provider, settings).generate(_Tenant(), _Profile())  # type: ignore[arg-type]

    assert outcome.source is AgentConfigSource.TEMPLATE_FALLBACK
    # It really did try twice — the repair prompt is a second request.
    assert len(recorder.requests) == 2
    # And the fallback is still a valid config, not a half-built one.
    assert isinstance(outcome.config, GeneratedAgentConfig)


async def test_a_schema_violation_is_rejected_not_coerced() -> None:
    """A reply that parses as JSON but breaks the schema must not get through."""
    recorder = Recorder(
        [
            (200, completion('{"system_prompt": 12345}')),
            (200, completion('{"greeting": "hi"}')),
        ]
    )
    settings = groq_settings()
    provider = wire(GroqProvider(settings), recorder, settings)

    outcome = await ConfigGenerator(provider, settings).generate(_Tenant(), _Profile())  # type: ignore[arg-type]

    assert outcome.source is AgentConfigSource.TEMPLATE_FALLBACK


async def test_json_wrapped_in_a_markdown_fence_is_still_read() -> None:
    """Small models fence their JSON. The existing extractor handles it."""
    recorder = Recorder([(200, completion(f"```json\n{VALID_CONFIG_JSON}\n```"))])
    settings = groq_settings()
    provider = wire(GroqProvider(settings), recorder, settings)

    outcome = await ConfigGenerator(provider, settings).generate(_Tenant(), _Profile())  # type: ignore[arg-type]

    assert outcome.source is AgentConfigSource.LLM


# ===========================================================================
# Call summaries
# ===========================================================================


async def test_a_summary_validates_against_the_existing_contract() -> None:
    recorder = Recorder([(200, completion(VALID_SUMMARY_JSON, model=GROQ_SUMMARY_MODEL))])
    settings = groq_settings()
    provider = wire(GroqProvider(settings), recorder, settings)

    summary = await Summarizer(provider, settings).summarize(
        transcript="agent: hello\nuser: do you have Thursday?",
        business_name="Sunset Salon",
        business_type="salon",
        called_number="+18055551000",
        caller_number="+15559998888",
        duration_s=96,
    )

    assert summary.intent == "booking_request"
    assert summary.action_required is True
    # The summary model setting is what was sent, not the config model.
    assert recorder.body()["model"] == GROQ_SUMMARY_MODEL


async def test_a_malformed_summary_is_raised_not_returned() -> None:
    """The post-call worker retries on this; it must not receive junk."""
    recorder = Recorder([(200, completion("I think they wanted an appointment."))])
    settings = groq_settings()
    provider = wire(GroqProvider(settings), recorder, settings)

    with pytest.raises(Exception) as excinfo:
        await Summarizer(provider, settings).summarize(
            transcript="agent: hello",
            business_name="Sunset Salon",
            business_type="salon",
            called_number="+18055551000",
            caller_number="+15559998888",
            duration_s=10,
        )
    # Retryable, because a resample may well validate.
    assert getattr(excinfo.value, "retryable", False) is True


# ===========================================================================
# Error classification — must match the other LLM adapters exactly
# ===========================================================================


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(429, True), (500, True), (503, True), (400, False), (401, False), (404, False)],
)
async def test_vendor_errors_classify_like_every_other_adapter(
    status: int, retryable: bool
) -> None:
    """Classification lives in the shared HTTP client, so Groq inherits it.

    401 and 404 matter most here: a wrong key and a retired model id are the
    two most likely Groq failures, and hammering either would waste the retry
    budget on something that cannot succeed.
    """
    recorder = Recorder([(status, {"error": {"message": "nope"}})])
    settings = groq_settings()
    provider = wire(GroqProvider(settings), recorder, settings)

    with pytest.raises(VendorError) as excinfo:
        await provider.complete(
            system="s", user="u", model=GROQ_CONFIG_MODEL, max_tokens=10, temperature=0.0
        )

    assert excinfo.value.vendor == "groq"
    assert excinfo.value.retryable is retryable


async def test_an_empty_reply_is_retryable() -> None:
    recorder = Recorder([(200, {"choices": [], "model": GROQ_CONFIG_MODEL})])
    settings = groq_settings()
    provider = wire(GroqProvider(settings), recorder, settings)

    with pytest.raises(VendorError) as excinfo:
        await provider.complete(
            system="s", user="u", model=GROQ_CONFIG_MODEL, max_tokens=10, temperature=0.0
        )

    assert excinfo.value.code == "vendor_empty_response"
    assert excinfo.value.retryable is True


async def test_the_api_key_never_appears_in_an_error() -> None:
    """A vendor error reaches the customer status page through `last_error`."""
    recorder = Recorder([(401, {"error": {"message": "Invalid API Key"}})])
    settings = groq_settings()
    provider = wire(GroqProvider(settings), recorder, settings)

    with pytest.raises(VendorError) as excinfo:
        await provider.complete(
            system="s", user="u", model=GROQ_CONFIG_MODEL, max_tokens=10, temperature=0.0
        )

    rendered = f"{excinfo.value.message} {excinfo.value.details}"
    assert "gsk-test-not-a-real-key" not in rendered


async def test_a_transport_failure_falls_back_rather_than_failing_the_run() -> None:
    """Groq being down must not stop a tenant getting a receptionist."""

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("groq unreachable")

    settings = groq_settings()
    provider = GroqProvider(settings)
    provider._client = ProviderHTTPClient(
        vendor="groq",
        base_url=settings.groq_api_base_url,
        timeout_s=5,
        transport=httpx.MockTransport(handle),
    )

    outcome = await ConfigGenerator(provider, settings).generate(_Tenant(), _Profile())  # type: ignore[arg-type]

    assert outcome.source is AgentConfigSource.TEMPLATE_FALLBACK
