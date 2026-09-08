"""Resend and SendGrid adapters.

**Not tested against the live APIs.** CI runs the fake provider.
"""

from __future__ import annotations

from app.core.config import Settings
from app.providers.http import ProviderHTTPClient
from app.providers.models import EmailMessage, EmailResult


class ResendProvider:
    name = "resend"

    def __init__(self, settings: Settings) -> None:
        self._from = settings.email_from
        self._client = ProviderHTTPClient(
            vendor="resend",
            base_url=settings.resend_api_base_url,
            timeout_s=settings.provider_timeout_s,
            headers={
                "authorization": f"Bearer {settings.resend_api_key.get_secret_value()}",
                "content-type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, message: EmailMessage) -> EmailResult:
        payload = await self._client.post(
            "/emails",
            json={
                "from": self._from,
                "to": [message.to],
                "subject": message.subject,
                "html": message.html,
                "text": message.text,
            },
        )
        return EmailResult(message_id=(payload or {}).get("id", ""), provider=self.name)


class SendGridProvider:
    name = "sendgrid"

    def __init__(self, settings: Settings) -> None:
        self._from = _bare_address(settings.email_from)
        self._client = ProviderHTTPClient(
            vendor="sendgrid",
            base_url=settings.sendgrid_api_base_url,
            timeout_s=settings.provider_timeout_s,
            headers={
                "authorization": f"Bearer {settings.sendgrid_api_key.get_secret_value()}",
                "content-type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, message: EmailMessage) -> EmailResult:
        # SendGrid answers 202 with an empty body; the id is in a header we do
        # not surface, so an empty message id here is expected, not a failure.
        await self._client.post(
            "/v3/mail/send",
            json={
                "personalizations": [{"to": [{"email": message.to}]}],
                "from": {"email": self._from},
                "subject": message.subject,
                "content": [
                    {"type": "text/plain", "value": message.text},
                    {"type": "text/html", "value": message.html},
                ],
            },
            expected=(200, 202),
        )
        return EmailResult(message_id="", provider=self.name)


def _bare_address(value: str) -> str:
    """Turn ``Name <a@b.test>`` into ``a@b.test``; SendGrid wants them split."""
    if "<" in value and ">" in value:
        return value[value.index("<") + 1 : value.index(">")].strip()
    return value.strip()
