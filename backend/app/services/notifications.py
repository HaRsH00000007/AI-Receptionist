"""Outbound notifications.

Thin on purpose. The provider handles delivery, the templates handle wording,
and this decides who gets told what. Retries are not attempted here: the caller
is a provisioning step or the call processor, both of which already have durable
retry state, and a second retry loop inside a third would make failures harder
to reason about, not easier.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.logging import get_logger
from app.models import Call, Tenant
from app.providers.models import EmailMessage, EmailResult
from app.providers.protocols import EmailProvider
from app.schemas.agent_config import CallSummary
from app.services.email_templates import activation_email, call_summary_email, failure_email

logger = get_logger(__name__)


class NotificationService:
    def __init__(self, provider: EmailProvider, settings: Settings) -> None:
        self.provider = provider
        self.settings = settings

    def _status_url(self, tenant: Tenant) -> str:
        return f"{self.settings.public_app_url.rstrip('/')}/status/{tenant.id}"

    async def _send(self, *, to: str, parts: tuple[str, str, str]) -> EmailResult:
        subject, html, text = parts
        result = await self.provider.send(
            EmailMessage(to=to, subject=subject, html=html, text=text)
        )
        # Recipient and subject only — a call summary is customer content.
        logger.info(
            "notification sent",
            extra={"to": to, "subject": subject, "provider": result.provider},
        )
        return result

    async def send_activation(self, *, tenant: Tenant, phone_e164: str) -> EmailResult:
        return await self._send(
            to=tenant.contact_email,
            parts=activation_email(
                business_name=tenant.name,
                phone_e164=phone_e164,
                status_url=self._status_url(tenant),
            ),
        )

    async def send_failure(self, *, tenant: Tenant, reason: str) -> EmailResult:
        return await self._send(
            to=tenant.contact_email,
            parts=failure_email(
                business_name=tenant.name,
                reason=reason,
                status_url=self._status_url(tenant),
            ),
        )

    async def send_call_summary(
        self, *, tenant: Tenant, call: Call, summary: CallSummary
    ) -> EmailResult:
        return await self._send(
            to=tenant.contact_email,
            parts=call_summary_email(
                business_name=tenant.name,
                caller_number=call.from_e164,
                summary=summary,
                status_url=self._status_url(tenant),
            ),
        )
