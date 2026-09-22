"""Adapters for the integrations that have one.

Only providers with a real, working adapter are registered here. The catalog
lists more — marked "coming soon" — and the gap between the two is the honest
answer to "which integrations actually work?".
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.integrations.providers.base import CalendarIntegration, OAuthIntegration
from app.integrations.providers.google_calendar import GoogleCalendar
from app.integrations.providers.microsoft_outlook import MicrosoftOutlook
from app.models.enums import IntegrationProvider

_ADAPTERS: dict[IntegrationProvider, type[OAuthIntegration]] = {
    IntegrationProvider.GOOGLE_CALENDAR: GoogleCalendar,
    IntegrationProvider.MICROSOFT_OUTLOOK: MicrosoftOutlook,
}


def has_adapter(provider: IntegrationProvider) -> bool:
    return provider in _ADAPTERS


def adapter_for(
    provider: IntegrationProvider,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OAuthIntegration | None:
    adapter = _ADAPTERS.get(provider)
    return adapter(settings, transport) if adapter else None


__all__ = [
    "CalendarIntegration",
    "OAuthIntegration",
    "adapter_for",
    "has_adapter",
]
