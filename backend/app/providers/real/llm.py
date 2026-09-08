"""Anthropic and OpenAI adapters.

Both are thin: one request, one text response. Everything interesting —
prompting, schema validation, the repair retry, the deterministic fallback —
lives above this layer in :mod:`app.services.config_generator`, so switching
vendors cannot change how a bad response is handled.

**Not tested against the live APIs.** CI runs the fake provider. Set
``LLM_PROVIDER=anthropic`` with a key to use the real one.
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
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        choices = (payload or {}).get("choices", [])
        text = (choices[0]["message"]["content"] if choices else "").strip()
        if not text:
            raise VendorError(
                "openai returned no content",
                vendor="openai",
                retryable=True,
                code="vendor_empty_response",
            )
        usage: dict[str, Any] = (payload or {}).get("usage", {}) or {}
        return LLMResponse(
            text=text,
            model=(payload or {}).get("model", model),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )
