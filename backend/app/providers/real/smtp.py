"""An SMTP email adapter, for local development against Mailpit.

``EMAIL_PROVIDER=smtp`` has been a documented setting and a valid
``EmailProviderName`` for a long time, and ``.env.local.example`` points it at
the Mailpit in ``docker-compose.yml`` — but no adapter existed, so the registry
fell through to the fake and nothing ever reached the inbox at
``localhost:8025``. Someone checking their email wording saw an empty Mailpit
and no error, which is the worst of both: the setting looked honoured, and the
only way to find out otherwise was to read the registry.

This is the adapter. It is deliberately the *local* path:

* **Stdlib, in a thread.** ``smtplib`` is synchronous, so each send runs in
  :func:`asyncio.to_thread` rather than blocking the event loop. An async SMTP
  client would be one more dependency for a code path that never serves a
  customer — production is Resend, and the one send per local experiment is not
  worth a wheel.
* **It raises the same error shape as the HTTP adapters.** A failure here is a
  :class:`~app.core.errors.VendorError`, so the outbox classifies, retries and
  records it exactly as it would a Resend outage. If the local path failed
  differently from the real one, local testing would stop telling us anything
  about the real one.
* **TLS is opt-in, not assumed.** Mailpit speaks plain SMTP on 1025 and accepts
  anything. ``SMTP_USE_TLS`` turns on STARTTLS for a real relay.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage as MimeMessage

from app.core.config import Settings
from app.core.errors import VendorError
from app.core.logging import get_logger
from app.providers.models import EmailMessage, EmailResult

logger = get_logger(__name__)


class SmtpProvider:
    name = "smtp"

    def __init__(self, settings: Settings) -> None:
        self._from = settings.email_from
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._username = settings.smtp_username
        self._password = settings.smtp_password.get_secret_value()
        self._use_tls = settings.smtp_use_tls
        self._timeout_s = settings.provider_timeout_s

    async def send(self, message: EmailMessage) -> EmailResult:
        mime = MimeMessage()
        mime["From"] = self._from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        # Plain text first, then HTML: a multipart/alternative is read last part
        # first, so this is the order that shows the HTML to a client that can
        # render it and the text to one that cannot.
        mime.set_content(message.text)
        mime.add_alternative(message.html, subtype="html")

        try:
            await asyncio.to_thread(self._deliver, mime)
        except (OSError, smtplib.SMTPException) as exc:
            # Retryable: a local relay that is not up yet, or a real one
            # refusing a connection, is the transient case. A permanent refusal
            # (a rejected recipient) also lands here and will exhaust the
            # outbox's four attempts, which leaves the reason on the row — the
            # outcome we want over guessing which SMTP codes are final.
            raise VendorError(
                f"smtp send failed: {type(exc).__name__}",
                vendor=self.name,
                retryable=True,
                code="smtp_unreachable",
                details={"host": self._host, "port": self._port},
            ) from exc

        logger.info(
            "email handed to smtp",
            extra={"to": message.to, "subject": message.subject, "host": self._host},
        )
        # SMTP gives no message id back, so there is none to report. The same is
        # true of the SendGrid adapter, and the outbox treats an empty id as
        # "delivered, untraceable" rather than as a failure.
        return EmailResult(message_id="", provider=self.name)

    def _deliver(self, mime: MimeMessage) -> None:
        """The blocking half. Runs in a worker thread."""
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout_s) as client:
            if self._use_tls:
                client.starttls()
            if self._username:
                client.login(self._username, self._password)
            client.send_message(mime)
