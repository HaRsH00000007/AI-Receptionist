"""The shape every integration adapter has.

Two layers, because not every integration is a calendar:

* :class:`OAuthIntegration` — connecting and staying connected: build the
  consent URL, trade the code for tokens, refresh them, name the account, revoke.
* :class:`CalendarIntegration` — what a calendar can do for the receptionist:
  report busy time, create an event, cancel one.

The receptionist never sees any of this. It asks the backend "is 3pm free?" and
"book it"; the backend holds the tokens and makes the calls. A model that held a
customer's OAuth credential could be talked into using it for something else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import Settings
from app.models.enums import IntegrationProvider


@dataclass(frozen=True, slots=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: list[str] = field(default_factory=list)

    @classmethod
    def from_response(
        cls, body: dict[str, Any], *, previous_refresh_token: str | None = None
    ) -> TokenSet:
        """Parse a standard OAuth 2.0 token response.

        A refresh response often omits ``refresh_token`` (Google) or rotates it
        (Microsoft). Keeping the previous one when none comes back is what
        stops a routine refresh from silently disconnecting the customer.
        """
        expires_in = body.get("expires_in")
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(expires_in))
            if isinstance(expires_in, int | str) and str(expires_in).isdigit()
            else None
        )
        scope = body.get("scope")
        return cls(
            access_token=str(body["access_token"]),
            refresh_token=body.get("refresh_token") or previous_refresh_token,
            expires_at=expires_at,
            scopes=scope.split() if isinstance(scope, str) else [],
        )

    def to_credentials(self) -> dict[str, Any]:
        """What goes into the vault. The expiry is also stored in the clear, on
        the row, so a caller can tell a refresh is due without decrypting."""
        return {"access_token": self.access_token, "refresh_token": self.refresh_token}


@dataclass(frozen=True, slots=True)
class BusyInterval:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    title: str
    start: datetime
    end: datetime
    description: str = ""


class OAuthIntegration(ABC):
    provider: IntegrationProvider
    #: The scopes requested. The least that makes the capabilities work.
    scopes: tuple[str, ...]

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        # Tests pass a mock transport; production never does.
        self.transport = transport

    @abstractmethod
    def client_configured(self) -> bool:
        """Whether the operator has registered an OAuth client for this provider."""

    @abstractmethod
    def authorization_url(self, *, state: str, redirect_uri: str) -> str: ...

    @abstractmethod
    async def exchange_code(self, *, code: str, redirect_uri: str) -> TokenSet: ...

    @abstractmethod
    async def refresh(self, *, refresh_token: str) -> TokenSet: ...

    @abstractmethod
    async def account_identity(self, *, access_token: str) -> str | None:
        """The account as the customer would recognise it (an email address)."""

    async def revoke(self, *, credentials: dict[str, Any]) -> None:  # noqa: B027
        """Tell the provider to forget the grant, where it supports that.

        Best effort, and a no-op by default: deleting our copy of the token is
        what disconnecting *means*; revocation at the provider is hygiene.
        """


class CalendarIntegration(OAuthIntegration):
    @abstractmethod
    async def busy_intervals(
        self, *, access_token: str, start: datetime, end: datetime
    ) -> list[BusyInterval]: ...

    @abstractmethod
    async def create_event(self, *, access_token: str, event: CalendarEvent) -> str:
        """Create the event and return the provider's id for it."""

    @abstractmethod
    async def cancel_event(self, *, access_token: str, event_id: str) -> None: ...


def iso_utc(moment: datetime) -> str:
    """RFC 3339 in UTC, which both Google and Microsoft accept."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
