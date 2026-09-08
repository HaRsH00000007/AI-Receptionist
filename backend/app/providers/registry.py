"""Provider selection.

The one place that decides fake-versus-real. Steps receive a :class:`Providers`
bundle and never ask whether DRY_RUN is on — which means there is no code path
where a step could accidentally spend money because someone forgot a branch.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.fakes.llm import FakeLLMProvider
from app.providers.fakes.mail import FakeEmailProvider
from app.providers.fakes.telephony import FakeTwilioProvider
from app.providers.fakes.voice import FakeElevenLabsProvider
from app.providers.protocols import (
    ElevenLabsProvider,
    EmailProvider,
    LLMProvider,
    TwilioProvider,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Providers:
    """Everything external, resolved once and injected everywhere."""

    llm: LLMProvider
    twilio: TwilioProvider
    elevenlabs: ElevenLabsProvider
    email: EmailProvider

    async def aclose(self) -> None:
        """Close any adapter holding an HTTP connection pool."""
        for provider in (self.llm, self.twilio, self.elevenlabs, self.email):
            closer = getattr(provider, "aclose", None)
            if closer is not None:
                await closer()


def build_providers(settings: Settings) -> Providers:
    """Resolve the configured providers.

    DRY_RUN forces a fake for Twilio, ElevenLabs and email — the three that cost
    money or touch a customer. The LLM honours its own setting, because running
    a dry run against a real model is often the point.
    """
    providers = Providers(
        llm=_build_llm(settings),
        twilio=_build_twilio(settings),
        elevenlabs=_build_elevenlabs(settings),
        email=_build_email(settings),
    )
    logger.info(
        "providers resolved",
        extra={
            "dry_run": settings.dry_run,
            "llm": providers.llm.name,
            "twilio": providers.twilio.name,
            "elevenlabs": providers.elevenlabs.name,
            "email": providers.email.name,
        },
    )
    return providers


def _build_llm(settings: Settings) -> LLMProvider:
    match settings.effective_llm_provider:
        case "anthropic":
            from app.providers.real.llm import AnthropicProvider

            return AnthropicProvider(settings)
        case "openai":
            from app.providers.real.llm import OpenAIProvider

            return OpenAIProvider(settings)
        case _:
            return FakeLLMProvider()


def _build_twilio(settings: Settings) -> TwilioProvider:
    if settings.effective_twilio_provider == "twilio":
        from app.providers.real.telephony import TwilioRestProvider

        return TwilioRestProvider(settings)
    return FakeTwilioProvider()


def _build_elevenlabs(settings: Settings) -> ElevenLabsProvider:
    if settings.effective_elevenlabs_provider == "elevenlabs":
        from app.providers.real.voice import ElevenLabsRestProvider

        return ElevenLabsRestProvider(settings)
    return FakeElevenLabsProvider()


def _build_email(settings: Settings) -> EmailProvider:
    match settings.effective_email_provider:
        case "resend":
            from app.providers.real.mail import ResendProvider

            return ResendProvider(settings)
        case "sendgrid":
            from app.providers.real.mail import SendGridProvider

            return SendGridProvider(settings)
        case _:
            return FakeEmailProvider()
