"""Google Calendar, over OAuth 2.0 and the Calendar API v3.

Scopes are the narrowest set that covers the capabilities: ``calendar.events``
to create and cancel events, ``calendar.freebusy`` to see when the owner is busy
*without* reading what their appointments are, and
``calendar.calendarlist.readonly`` to list the calendars in the account so the
owner can choose which one to book into — it reads the *list*, not the contents.
``openid email`` names the account. Nothing grants access to other Google data.

The calendar-list scope was added after the first connections were made, and
Google grants only what was asked for at the time, so an older connection can
read and write its calendars but cannot enumerate them. That is why
:meth:`list_calendars` is expected to fail with 403 for those, and why the row
records the scopes actually granted.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

from app.core.errors import VendorError
from app.integrations.providers.base import (
    BusyInterval,
    CalendarEvent,
    CalendarIntegration,
    CalendarRef,
    TokenSet,
    iso_utc,
)
from app.models.enums import IntegrationProvider
from app.providers.http import ProviderHTTPClient

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_BASE = "https://oauth2.googleapis.com"
USERINFO_BASE = "https://openidconnect.googleapis.com"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"


CALENDAR_LIST_SCOPE = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"

#: Google's own names for the access levels that allow writing an event.
_WRITABLE_ROLES = frozenset({"owner", "writer"})


class GoogleCalendar(CalendarIntegration):
    provider = IntegrationProvider.GOOGLE_CALENDAR
    scopes = (
        "openid",
        "email",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.freebusy",
        CALENDAR_LIST_SCOPE,
    )
    calendar_list_scope = CALENDAR_LIST_SCOPE

    def client_configured(self) -> bool:
        return bool(
            self.settings.google_oauth_client_id
            and self.settings.google_oauth_client_secret.get_secret_value()
        )

    def _client(self, base_url: str, access_token: str | None = None) -> ProviderHTTPClient:
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else None
        return ProviderHTTPClient(
            vendor="google",
            base_url=base_url,
            timeout_s=self.settings.provider_timeout_s,
            headers=headers,
            transport=self.transport,
        )

    # ---- connecting ------------------------------------------------------
    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        return f"{AUTHORIZE_URL}?" + urlencode(
            {
                "client_id": self.settings.google_oauth_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(self.scopes),
                # A refresh token, so the receptionist can check the calendar at
                # 3am without the owner signing in. `prompt=consent` is what makes
                # Google issue one again on a reconnect.
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "state": state,
            }
        )

    async def exchange_code(self, *, code: str, redirect_uri: str) -> TokenSet:
        async with self._client(TOKEN_BASE) as http:
            body = await http.post(
                "/token",
                data={
                    "code": code,
                    "client_id": self.settings.google_oauth_client_id,
                    "client_secret": self.settings.google_oauth_client_secret.get_secret_value(),
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        return TokenSet.from_response(body)

    async def refresh(self, *, refresh_token: str) -> TokenSet:
        async with self._client(TOKEN_BASE) as http:
            body = await http.post(
                "/token",
                data={
                    "refresh_token": refresh_token,
                    "client_id": self.settings.google_oauth_client_id,
                    "client_secret": self.settings.google_oauth_client_secret.get_secret_value(),
                    "grant_type": "refresh_token",
                },
            )
        return TokenSet.from_response(body, previous_refresh_token=refresh_token)

    async def account_identity(self, *, access_token: str) -> str | None:
        async with self._client(USERINFO_BASE, access_token) as http:
            body = await http.get("/v1/userinfo")
        email = body.get("email") if isinstance(body, dict) else None
        return str(email) if email else None

    async def revoke(self, *, credentials: dict[str, Any]) -> None:
        # Revoking the refresh token revokes the whole grant.
        token = credentials.get("refresh_token") or credentials.get("access_token")
        if not token:
            return
        async with self._client(TOKEN_BASE) as http:
            await http.post("/revoke", data={"token": token}, expected=(200, 400))

    # ---- calendar --------------------------------------------------------
    @staticmethod
    def _target(calendar_id: str | None) -> str:
        """The calendar to act on. ``primary`` is Google's alias for the default.

        Percent-encoded because a secondary calendar's id is an address-shaped
        string that would otherwise split the path.
        """
        return quote(calendar_id or "primary", safe="")

    async def list_calendars(self, *, access_token: str) -> list[CalendarRef]:
        async with self._client(CALENDAR_BASE, access_token) as http:
            body = await http.get("/users/me/calendarList", params={"maxResults": "250"})
        calendars: list[CalendarRef] = []
        for entry in (body or {}).get("items", []):
            if not isinstance(entry, dict) or not entry.get("id") or entry.get("deleted"):
                continue
            calendars.append(
                CalendarRef(
                    id=str(entry["id"]),
                    name=str(entry.get("summaryOverride") or entry.get("summary") or entry["id"]),
                    primary=bool(entry.get("primary")),
                    writable=str(entry.get("accessRole", "")).lower() in _WRITABLE_ROLES,
                )
            )
        # The default first, then by name, so the list reads the same every time
        # rather than in whatever order the API answered.
        calendars.sort(key=lambda calendar: (not calendar.primary, calendar.name.lower()))
        return calendars

    async def busy_intervals(
        self,
        *,
        access_token: str,
        start: datetime,
        end: datetime,
        calendar_id: str | None = None,
    ) -> list[BusyInterval]:
        target = calendar_id or "primary"
        async with self._client(CALENDAR_BASE, access_token) as http:
            body = await http.post(
                "/freeBusy",
                json={
                    "timeMin": iso_utc(start),
                    "timeMax": iso_utc(end),
                    "items": [{"id": target}],
                },
            )
        calendars = (body or {}).get("calendars", {})
        entry = calendars.get(target)
        if not isinstance(entry, dict):
            # freeBusy keys its answer by the id asked for, but an alias may be
            # answered under the real address. One calendar was requested, so
            # there is no ambiguity about whose busy time came back.
            entry = next((value for value in calendars.values() if isinstance(value, dict)), {})
        if entry.get("errors"):
            # Deleted or unshared since it was chosen. Reading that as "nothing
            # is busy" would book over a full day, so it is a failure rather
            # than an empty answer.
            raise VendorError(
                "google refused the calendar in a free/busy query",
                vendor="google",
                retryable=False,
                code="calendar_unreadable",
                details={"errors": entry["errors"][:3]},
            )
        return [
            BusyInterval(
                start=datetime.fromisoformat(slot["start"]),
                end=datetime.fromisoformat(slot["end"]),
            )
            for slot in entry.get("busy", [])
            if isinstance(slot, dict) and "start" in slot and "end" in slot
        ]

    async def create_event(
        self, *, access_token: str, event: CalendarEvent, calendar_id: str | None = None
    ) -> str:
        payload: dict[str, Any] = {
            "summary": event.title,
            "description": event.description,
            "start": {"dateTime": iso_utc(event.start)},
            "end": {"dateTime": iso_utc(event.end)},
        }
        if event.idempotency_key:
            # Google lets the client choose the id (a-v and 0-9, which hex is
            # within) and answers 409 if it already exists. That 409 is the
            # proof a previous attempt created this exact event.
            payload["id"] = event.idempotency_key
        async with self._client(CALENDAR_BASE, access_token) as http:
            try:
                body = await http.post(
                    f"/calendars/{self._target(calendar_id)}/events", json=payload
                )
            except VendorError as exc:
                if exc.status_code == 409 and event.idempotency_key:
                    return event.idempotency_key
                raise
        return str(body["id"])

    async def cancel_event(
        self, *, access_token: str, event_id: str, calendar_id: str | None = None
    ) -> None:
        async with self._client(CALENDAR_BASE, access_token) as http:
            # 410: already deleted. Cancelling twice is not an error.
            await http.delete(
                f"/calendars/{self._target(calendar_id)}/events/{quote(event_id, safe='')}",
                expected=(200, 204, 410),
            )
