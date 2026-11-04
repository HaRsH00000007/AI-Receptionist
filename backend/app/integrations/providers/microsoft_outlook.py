"""Microsoft Outlook calendars, over the Microsoft identity platform and Graph.

``Calendars.ReadWrite`` is the single Graph permission that covers reading busy
time and creating events; ``offline_access`` is what yields a refresh token;
``User.Read`` names the account. Microsoft rotates refresh tokens on use, so
every refresh result is written back (see ``TokenSet.from_response``).

Availability reads ``calendarView`` rather than ``getSchedule``: it needs no
mailbox address, works for personal accounts, and ``showAs`` says exactly which
events block time.

``Calendars.ReadWrite`` already covers enumerating the account's calendars, so
choosing one needs no extra scope here and no reconnect — unlike Google, where
the list is a scope of its own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote, urlencode

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

LOGIN_BASE = "https://login.microsoftonline.com"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

#: The `showAs` values that mean the owner cannot take an appointment.
_BLOCKING = frozenset({"busy", "tentative", "oof", "workingelsewhere"})

#: Graph's roles on a calendar that allow creating an event in it.
_WRITABLE_ROLES = frozenset({"write", "owner"})


class MicrosoftOutlook(CalendarIntegration):
    provider = IntegrationProvider.MICROSOFT_OUTLOOK
    scopes = ("offline_access", "openid", "email", "User.Read", "Calendars.ReadWrite")

    def client_configured(self) -> bool:
        return bool(
            self.settings.microsoft_oauth_client_id
            and self.settings.microsoft_oauth_client_secret.get_secret_value()
        )

    @property
    def _tenant_path(self) -> str:
        return f"/{quote(self.settings.microsoft_oauth_tenant or 'common', safe='')}/oauth2/v2.0"

    def _client(
        self, base_url: str, access_token: str | None = None, *, utc: bool = False
    ) -> ProviderHTTPClient:
        headers: dict[str, str] = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        if utc:
            # Graph otherwise answers in the mailbox's own time zone, with no
            # offset in the string.
            headers["Prefer"] = 'outlook.timezone="UTC"'
        return ProviderHTTPClient(
            vendor="microsoft",
            base_url=base_url,
            timeout_s=self.settings.provider_timeout_s,
            headers=headers or None,
            transport=self.transport,
        )

    # ---- connecting ------------------------------------------------------
    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        return f"{LOGIN_BASE}{self._tenant_path}/authorize?" + urlencode(
            {
                "client_id": self.settings.microsoft_oauth_client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "response_mode": "query",
                "scope": " ".join(self.scopes),
                "prompt": "select_account",
                "state": state,
            }
        )

    def _token_form(self, **fields: str) -> dict[str, str]:
        return {
            "client_id": self.settings.microsoft_oauth_client_id,
            "client_secret": self.settings.microsoft_oauth_client_secret.get_secret_value(),
            "scope": " ".join(self.scopes),
            **fields,
        }

    async def exchange_code(self, *, code: str, redirect_uri: str) -> TokenSet:
        async with self._client(LOGIN_BASE) as http:
            body = await http.post(
                f"{self._tenant_path}/token",
                data=self._token_form(
                    code=code, redirect_uri=redirect_uri, grant_type="authorization_code"
                ),
            )
        return TokenSet.from_response(body)

    async def refresh(self, *, refresh_token: str) -> TokenSet:
        async with self._client(LOGIN_BASE) as http:
            body = await http.post(
                f"{self._tenant_path}/token",
                data=self._token_form(refresh_token=refresh_token, grant_type="refresh_token"),
            )
        return TokenSet.from_response(body, previous_refresh_token=refresh_token)

    async def account_identity(self, *, access_token: str) -> str | None:
        async with self._client(GRAPH_BASE, access_token) as http:
            body = await http.get("/me", params={"$select": "mail,userPrincipalName"})
        if not isinstance(body, dict):
            return None
        identity = body.get("mail") or body.get("userPrincipalName")
        return str(identity) if identity else None

    # Microsoft has no endpoint to revoke one delegated grant from the app side;
    # deleting our copy is the whole of disconnecting, so the default no-op
    # `revoke` stands. The customer can remove the app at myapps.microsoft.com.

    # ---- calendar --------------------------------------------------------
    @staticmethod
    def _scope(calendar_id: str | None) -> str:
        """The path prefix for one calendar, or the mailbox's default.

        Graph spells the default as ``/me/events`` rather than a calendar id, so
        an account that has not chosen keeps exactly the path it had before
        calendars could be chosen.
        """
        return f"/me/calendars/{quote(calendar_id, safe='')}" if calendar_id else "/me"

    async def list_calendars(self, *, access_token: str) -> list[CalendarRef]:
        async with self._client(GRAPH_BASE, access_token) as http:
            body = await http.get(
                "/me/calendars",
                params={"$select": "id,name,isDefaultCalendar,canEdit", "$top": "250"},
            )
        calendars: list[CalendarRef] = []
        for entry in (body or {}).get("value", []):
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            calendars.append(
                CalendarRef(
                    id=str(entry["id"]),
                    name=str(entry.get("name") or entry["id"]),
                    primary=bool(entry.get("isDefaultCalendar")),
                    # `canEdit` is absent on some mailboxes; absent is not a
                    # refusal, and a create that is refused is reported then.
                    writable=bool(entry.get("canEdit", True)),
                )
            )
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
        async with self._client(GRAPH_BASE, access_token, utc=True) as http:
            body = await http.get(
                f"{self._scope(calendar_id)}/calendarView",
                params={
                    "startDateTime": iso_utc(start),
                    "endDateTime": iso_utc(end),
                    "$select": "start,end,showAs,isCancelled",
                    "$top": "250",
                },
            )
        intervals: list[BusyInterval] = []
        for entry in (body or {}).get("value", []):
            if not isinstance(entry, dict) or entry.get("isCancelled"):
                continue
            if str(entry.get("showAs", "")).lower() not in _BLOCKING:
                continue
            intervals.append(
                BusyInterval(
                    start=_graph_time(entry["start"]["dateTime"]),
                    end=_graph_time(entry["end"]["dateTime"]),
                )
            )
        return intervals

    async def create_event(
        self, *, access_token: str, event: CalendarEvent, calendar_id: str | None = None
    ) -> str:
        async with self._client(GRAPH_BASE, access_token) as http:
            body = await http.post(
                f"{self._scope(calendar_id)}/events",
                json={
                    "subject": event.title,
                    "body": {"contentType": "text", "content": event.description},
                    "start": {"dateTime": iso_utc(event.start).rstrip("Z"), "timeZone": "UTC"},
                    "end": {"dateTime": iso_utc(event.end).rstrip("Z"), "timeZone": "UTC"},
                    # Graph's own idempotency field: a second create carrying
                    # the same transactionId returns the event the first made.
                    **({"transactionId": event.idempotency_key} if event.idempotency_key else {}),
                },
            )
        return str(body["id"])

    async def cancel_event(
        self, *, access_token: str, event_id: str, calendar_id: str | None = None
    ) -> None:
        async with self._client(GRAPH_BASE, access_token) as http:
            # An event is addressable under /me/events whichever calendar holds
            # it, so cancelling does not need the calendar — but it is accepted
            # and used, because a mailbox with delegated access resolves the id
            # against the calendar it was created in.
            await http.delete(
                f"{self._scope(calendar_id)}/events/{quote(event_id, safe='')}",
                expected=(204, 404),
            )


def _graph_time(value: str) -> datetime:
    """Graph's `dateTime` has up to seven fractional digits and no offset.

    With the UTC preference header it is UTC; the fraction is trimmed to what
    ``fromisoformat`` accepts.
    """
    head, _, fraction = value.partition(".")
    parsed = datetime.fromisoformat(f"{head}.{fraction[:6]}" if fraction else head)
    return parsed.replace(tzinfo=UTC)
