"""Shared FastAPI dependencies.

Kept deliberately thin: dependencies resolve objects the application factory
placed on ``app.state``, so nothing in the request path reaches for a global.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from temporalio.client import Client

from app.core.config import Settings
from app.providers.registry import Providers


def get_settings_dep(request: Request) -> Settings:
    """The settings this application instance was built with."""
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def get_providers_dep(request: Request) -> Providers:
    """The provider bundle this application resolved at startup.

    Requests must never build their own: a fresh bundle would open a second
    connection pool per call, and against the fakes it would be a *different*
    in-memory vendor — so an admin action would address an empty one and
    silently do nothing.
    """
    providers: Providers = request.app.state.providers
    return providers


ProvidersDep = Annotated[Providers, Depends(get_providers_dep)]


def get_temporal_client(request: Request) -> Client | None:
    """The Temporal client, or ``None`` when unavailable.

    Nullable on purpose. A Temporal outage degrades provisioning rather than
    taking the API down, so every caller is forced by the type to decide what
    happens when orchestration is unreachable.
    """
    client: Client | None = getattr(request.app.state, "temporal", None)
    return client


TemporalClientDep = Annotated["Client | None", Depends(get_temporal_client)]
