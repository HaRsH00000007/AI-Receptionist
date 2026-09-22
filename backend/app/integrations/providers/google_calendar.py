"""Google Calendar, over OAuth 2.0 and the Calendar API v3.

Scopes are the narrowest pair that cover the capabilities: ``calendar.events``
to create and cancel events, and ``calendar.freebusy`` to see when the owner is
busy *without* reading what their appointments are. ``openid email`` names the
account on the card. Nothing grants access to other Google data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from app.integrations.providers.base import (
    BusyInterval,
    CalendarEvent,
    CalendarIntegration,
    TokenSet,
    iso_utc,
)
from app.models.enums import IntegrationProvider
from app.providers.http import ProviderHTTPClient

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_BASE = "https://oauth2.googleapis.com"
USERINFO_BASE = "https://openidconnect.googleapis.com"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleCalendar(CalendarIntegration):
    provider = IntegrationProvider.GOOGLE_CALENDAR
    scopes = (
        "openid",
        "email",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.freebusy",
    )

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
    async def busy_intervals(
        self, *, access_token: str, start: datetime, end: datetime
    ) -> list[BusyInterval]:
        async with self._client(CALENDAR_BASE, access_token) as http:
            body = await http.post(
                "/freeBusy",
                json={
                    "timeMin": iso_utc(start),
                    "timeMax": iso_utc(end),
                    "items": [{"id": "primary"}],
                },
            )
        busy = (body or {}).get("calendars", {}).get("primary", {}).get("busy", [])
        return [
            BusyInterval(
                start=datetime.fromisoformat(entry["start"]),
                end=datetime.fromisoformat(entry["end"]),
            )
            for entry in busy
            if isinstance(entry, dict) and "start" in entry and "end" in entry
        ]

    async def create_event(self, *, access_token: str, event: CalendarEvent) -> str:
        async with self._client(CALENDAR_BASE, access_token) as http:
            body = await http.post(
                "/calendars/primary/events",
                json={
                    "summary": event.title,
                    "description": event.description,
                    "start": {"dateTime": iso_utc(event.start)},
                    "end": {"dateTime": iso_utc(event.end)},
                },
            )
        return str(body["id"])

    async def cancel_event(self, *, access_token: str, event_id: str) -> None:
        async with self._client(CALENDAR_BASE, access_token) as http:
            # 410: already deleted. Cancelling twice is not an error.
            await http.delete(f"/calendars/primary/events/{event_id}", expected=(200, 204, 410))
