"""Anthropic, OpenAI and Groq adapters.

Both are thin: one request, one text response. Everything interesting —
prompting, schema validation, the repair retry, the deterministic fallback —
lives above this layer in :mod:`app.services.config_generator`, so switching
vendors cannot change how a bad response is handled.

Groq serves OpenAI's chat-completions API, so it shares the request and
response handling rather than reimplementing it — the only differences are the
host, the credential and the path prefix. That sharing is the point: a reply is
read the same way whoever produced it.

**Not tested against the live APIs.** CI runs the fake provider, and the
adapters here are exercised through ``httpx.MockTransport``. Set
``LLM_PROVIDER=anthropic`` (or ``openai``/``groq``) with a key to use a real one.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.errors import VendorError
from app.providers.http import ProviderHTTPClient
from app.providers.models import LLMResponse

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        self._client = ProviderHTTPClient(
            vendor="anthropic",
            base_url="https://api.anthropic.com",
            timeout_s=settings.provider_timeout_s,
            headers={
                "x-api-key": settings.anthropic_api_key.get_secret_value(),
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        payload = await self._client.post(
            "/v1/messages",
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        text = _anthropic_text(payload)
        usage: dict[str, Any] = (payload or {}).get("usage", {}) or {}
        return LLMResponse(
            text=text,
            model=(payload or {}).get("model", model),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )


def _anthropic_text(payload: dict[str, Any] | None) -> str:
    blocks = (payload or {}).get("content", [])
    parts = [block.get("text", "") for block in blocks if block.get("type") == "text"]
    text = "".join(parts).strip()
    if not text:
        raise VendorError(
            "anthropic returned no text content",
            vendor="anthropic",
            retryable=True,
            code="vendor_empty_response",
        )
    return text


def _chat_completion_text(payload: dict[str, Any] | None, *, vendor: str) -> str:
    """Read the assistant message out of an OpenAI-shaped completion.

    Shared by every vendor speaking that API. An empty reply is retryable: the
    request was fine and a resample may well succeed.
    """
    choices = (payload or {}).get("choices", [])
    text = (choices[0]["message"]["content"] if choices else "").strip()
    if not text:
        raise VendorError(
            f"{vendor} returned no content",
            vendor=vendor,
            retryable=True,
            code="vendor_empty_response",
        )
    return text


def _chat_completion_body(
    *, system: str, user: str, model: str, max_tokens: int, temperature: float
) -> dict[str, Any]:
    """The request body every OpenAI-compatible vendor accepts.

    ``response_format`` is what makes the caller's ``extract_json`` reliable
    rather than hopeful. It is requested, never trusted: the reply still goes
    through the same parse-and-validate path as any other provider's.
    """
    return {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }


def _chat_completion_response(
    payload: dict[str, Any] | None, *, vendor: str, model: str
) -> LLMResponse:
    usage: dict[str, Any] = (payload or {}).get("usage", {}) or {}
    return LLMResponse(
        text=_chat_completion_text(payload, vendor=vendor),
        model=(payload or {}).get("model", model),
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
    )


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self._client = ProviderHTTPClient(
            vendor="openai",
            base_url="https://api.openai.com",
            timeout_s=settings.provider_timeout_s,
            headers={
                "authorization": f"Bearer {settings.openai_api_key.get_secret_value()}",
                "content-type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        payload = await self._client.post(
            "/v1/chat/completions",
            json=_chat_completion_body(
                system=system,
                user=user,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            ),
        )
        return _chat_completion_response(payload, vendor=self.name, model=model)


class GroqProvider:
    """Groq, through its OpenAI-compatible endpoint.

    Added so the POC can run against a real model before the client's OpenAI
    key arrives. It is a peer of the other two, not a replacement: selecting it
    is one environment variable, and switching back is the same.

    The configured base URL already ends in ``/openai/v1``, so the path here is
    ``/chat/completions`` — not ``/v1/chat/completions`` as for OpenAI itself.
    Getting that wrong yields a 404 that looks like a bad model id.
    """

    name = "groq"

    def __init__(self, settings: Settings) -> None:
        self._client = ProviderHTTPClient(
            vendor="groq",
            base_url=settings.groq_api_base_url,
            timeout_s=settings.provider_timeout_s,
            headers={
                # `get_secret_value()` is required: interpolating the SecretStr
                # itself yields '**********' and a 401 nobody can explain.
                "authorization": f"Bearer {settings.groq_api_key.get_secret_value()}",
                "content-type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        payload = await self._client.post(
            "/chat/completions",
            json=_chat_completion_body(
                system=system,
                user=user,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            ),
        )
        return _chat_completion_response(payload, vendor=self.name, model=model)
