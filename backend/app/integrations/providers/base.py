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
class CalendarRef:
    """One calendar in a connected account, as the owner would recognise it."""

    id: str
    name: str
    #: The account's default calendar. Offered first, and what an account that
    #: has never chosen falls back to.
    primary: bool = False
    #: Whether the grant allows writing events to it. A calendar someone shared
    #: read-only can be read for availability but never booked into, so offering
    #: it as a booking target would promise appointments that cannot be made.
    writable: bool = True


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    title: str
    start: datetime
    end: datetime
    description: str = ""
    #: The same key for the same booking, however many times it is attempted.
    #: Lowercase hex, so it is also a valid Google event id. Adapters use it to
    #: make a retried create — after a timeout, say, where the first attempt
    #: may have succeeded — find the first event instead of adding a second.
    idempotency_key: str | None = None


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
    """A calendar account.

    Every call names the calendar it acts on. ``calendar_id=None`` means the
    account's default calendar, which is what an account connected before
    calendars could be chosen still uses — so the parameter is additive and no
    existing connection changes behaviour by being upgraded.
    """

    #: The extra scope needed to enumerate the account's calendars, where the
    #: provider requires one beyond those already granted. A connection made
    #: before it was asked for will not have it, so ``list_calendars`` is
    #: expected to fail for those and the caller offers a reconnect.
    calendar_list_scope: str | None = None

    @abstractmethod
    async def list_calendars(self, *, access_token: str) -> list[CalendarRef]:
        """The calendars in the account, default first."""

    @abstractmethod
    async def busy_intervals(
        self,
        *,
        access_token: str,
        start: datetime,
        end: datetime,
        calendar_id: str | None = None,
    ) -> list[BusyInterval]: ...

    @abstractmethod
    async def create_event(
        self, *, access_token: str, event: CalendarEvent, calendar_id: str | None = None
    ) -> str:
        """Create the event and return the provider's id for it."""

    @abstractmethod
    async def cancel_event(
        self, *, access_token: str, event_id: str, calendar_id: str | None = None
    ) -> None: ...


def iso_utc(moment: datetime) -> str:
    """RFC 3339 in UTC, which both Google and Microsoft accept."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
