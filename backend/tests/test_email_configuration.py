"""Which email provider is built, and whether its sender could ever work.

Two gaps this covers, both of which were live in production.

**The sender.** ``EMAIL_FROM`` was a Gmail address while ``EMAIL_PROVIDER`` was
``resend``. Resend requires the ``from`` domain to be one you have proved you
control by DNS, and nobody can add a DNS record to ``gmail.com`` — so every
single send was refused, with a real key and a healthy-looking config. The
validator makes that a boot failure, because a missing API key already is one
and a sender that cannot send is no less broken.

**The SMTP adapter.** ``EMAIL_PROVIDER=smtp`` has been a documented setting
pointing at the Mailpit in ``docker-compose.yml``, but no adapter existed, so
the registry fell through to the fake and the inbox stayed empty with no error.
"""

from __future__ import annotations

import smtplib
from typing import Any, ClassVar

import pytest

from app.core.errors import VendorError
from app.providers.fakes.mail import FakeEmailProvider
from app.providers.models import EmailMessage
from app.providers.real.mail import ResendProvider, SendGridProvider
from app.providers.real.smtp import SmtpProvider
from app.providers.registry import _build_email
from tests.support import build_settings

OWN_DOMAIN = "AI Receptionist <hello@evenline.example>"


def _real_email_settings(**overrides: Any):  # type: ignore[no-untyped-def]
    """Settings with a real email provider selected, which DRY_RUN would hide."""
    values: dict[str, Any] = {
        "dry_run": False,
        "email_provider": "resend",
        "resend_api_key": "re_not_a_real_key",
        "email_from": OWN_DOMAIN,
    }
    values.update(overrides)
    return build_settings(**values)


# ---------------------------------------------------------------------------
# The sender has to be able to send
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sender",
    [
        "AI Receptionist <someone@gmail.com>",
        "someone@googlemail.com",
        "AI Receptionist <someone@outlook.com>",
        "someone@yahoo.co.uk",
        "someone@icloud.com",
    ],
)
def test_a_free_mail_sender_is_refused_at_boot(sender: str) -> None:
    """The production misconfiguration, as a test.

    These can never be verified, so this is not a warning — there is no version
    of this deployment that delivers mail.
    """
    with pytest.raises(ValueError, match="cannot be verified"):
        _real_email_settings(email_from=sender)


@pytest.mark.parametrize(
    "sender",
    [
        "AI Receptionist <onboarding@example.com>",
        "noreply@example.org",
        "onboarding@resend.dev",
    ],
)
def test_a_placeholder_sender_is_refused_at_boot(sender: str) -> None:
    """A deploy still carrying one of these never had its sender configured.

    ``resend.dev`` is the dangerous one: it is a real, working sender that
    delivers *only* to the account owner's own address, so it looks configured
    and silently reaches no customer.
    """
    with pytest.raises(ValueError, match="placeholder"):
        _real_email_settings(email_from=sender)


def test_a_sender_that_is_not_an_address_is_refused() -> None:
    with pytest.raises(ValueError, match="must be an email address"):
        _real_email_settings(email_from="AI Receptionist")


def test_a_sender_on_our_own_domain_is_accepted() -> None:
    settings = _real_email_settings()

    assert settings.effective_email_provider == "resend"
    assert settings.email_from == OWN_DOMAIN


def test_sendgrid_is_held_to_the_same_rule() -> None:
    """It verifies senders by DNS too, so the mistake is identical."""
    with pytest.raises(ValueError, match="cannot be verified"):
        _real_email_settings(
            email_provider="sendgrid",
            sendgrid_api_key="SG.not-a-real-key",
            email_from="someone@gmail.com",
        )


def test_the_fake_provider_accepts_any_sender() -> None:
    """Nothing leaves the process, so there is no rule to enforce.

    Worth asserting: a validator that fired here would make the whole test
    suite and every local dry run refuse to start.
    """
    settings = build_settings(email_provider="fake", email_from="someone@gmail.com")

    assert settings.effective_email_provider == "fake"


def test_dry_run_accepts_a_sender_that_could_not_send() -> None:
    """DRY_RUN forces the fake, so the rule does not apply.

    This is the escape hatch for a developer whose ``.env`` points at a real
    provider: flipping DRY_RUN on is enough, with no sender changes.
    """
    settings = build_settings(
        dry_run=True,
        email_provider="resend",
        resend_api_key="re_not_a_real_key",
        email_from="someone@gmail.com",
    )

    assert settings.effective_email_provider == "fake"


def test_smtp_accepts_any_sender() -> None:
    """Mailpit does not care, and local development should not have to."""
    settings = build_settings(dry_run=False, email_provider="smtp", email_from="someone@gmail.com")

    assert settings.effective_email_provider == "smtp"


# ---------------------------------------------------------------------------
# Which adapter gets built
# ---------------------------------------------------------------------------


def test_smtp_builds_the_smtp_adapter() -> None:
    """The gap that made ``EMAIL_PROVIDER=smtp`` a silent no-op."""
    provider = _build_email(build_settings(dry_run=False, email_provider="smtp"))

    assert isinstance(provider, SmtpProvider)


def test_resend_builds_the_resend_adapter() -> None:
    assert isinstance(_build_email(_real_email_settings()), ResendProvider)


def test_sendgrid_builds_the_sendgrid_adapter() -> None:
    settings = _real_email_settings(email_provider="sendgrid", sendgrid_api_key="SG.not-a-real-key")

    assert isinstance(_build_email(settings), SendGridProvider)


def test_dry_run_builds_the_fake_whatever_is_configured() -> None:
    settings = build_settings(
        dry_run=True, email_provider="resend", resend_api_key="re_not_a_real_key"
    )

    assert isinstance(_build_email(settings), FakeEmailProvider)


# ---------------------------------------------------------------------------
# The SMTP adapter itself
# ---------------------------------------------------------------------------


class _RecordingSMTP:
    """Stands in for ``smtplib.SMTP``. Records rather than connecting."""

    sent: ClassVar[list[Any]] = []
    started_tls: ClassVar[bool] = False
    logged_in: ClassVar[str | None] = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def __enter__(self) -> _RecordingSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self) -> None:
        type(self).started_tls = True

    def login(self, username: str, password: str) -> None:
        type(self).logged_in = username

    def send_message(self, message: Any) -> None:
        type(self).sent.append(message)


@pytest.fixture(autouse=True)
def _reset_recorder() -> None:
    _RecordingSMTP.sent = []
    _RecordingSMTP.started_tls = False
    _RecordingSMTP.logged_in = None


async def test_an_smtp_send_carries_both_a_text_and_an_html_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multipart, because a client that cannot render HTML still gets the link.

    These are credential emails: a sign-in link that only exists in an HTML part
    is unreachable for anyone reading plain text.
    """
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    provider = SmtpProvider(build_settings(dry_run=False, email_provider="smtp"))

    result = await provider.send(
        EmailMessage(
            to="someone@example.com",
            subject="Your sign-in link",
            html="<p>https://x/a?token=t</p>",
            text="https://x/a?token=t",
        )
    )

    assert result.provider == "smtp"
    assert len(_RecordingSMTP.sent) == 1
    message = _RecordingSMTP.sent[0]
    assert message["To"] == "someone@example.com"
    assert message["Subject"] == "Your sign-in link"
    types = {part.get_content_type() for part in message.walk()}
    assert {"text/plain", "text/html"} <= types


async def test_an_smtp_send_does_not_authenticate_when_there_is_no_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mailpit accepts anything, and offers no AUTH to attempt."""
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    provider = SmtpProvider(build_settings(dry_run=False, email_provider="smtp"))

    await provider.send(EmailMessage(to="a@example.com", subject="s", html="<p>h</p>", text="t"))

    assert _RecordingSMTP.logged_in is None
    assert _RecordingSMTP.started_tls is False


async def test_an_smtp_send_authenticates_and_upgrades_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    provider = SmtpProvider(
        build_settings(
            dry_run=False,
            email_provider="smtp",
            smtp_username="relay-user",
            smtp_password="relay-password",
            smtp_use_tls=True,
        )
    )

    await provider.send(EmailMessage(to="a@example.com", subject="s", html="<p>h</p>", text="t"))

    assert _RecordingSMTP.started_tls is True
    assert _RecordingSMTP.logged_in == "relay-user"


async def test_an_unreachable_relay_is_a_retryable_vendor_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same error shape as the HTTP adapters.

    If the local path failed differently from the real one, local testing would
    tell us nothing about how production behaves — the outbox classifies,
    retries and records by exactly this.
    """

    def _refuse(*_: object, **__: object) -> None:
        raise ConnectionRefusedError("nothing is listening")

    monkeypatch.setattr(smtplib, "SMTP", _refuse)
    provider = SmtpProvider(build_settings(dry_run=False, email_provider="smtp"))

    with pytest.raises(VendorError) as caught:
        await provider.send(
            EmailMessage(to="a@example.com", subject="s", html="<p>h</p>", text="t")
        )

    assert caught.value.retryable is True
    assert caught.value.code == "smtp_unreachable"


async def test_an_smtp_protocol_failure_is_a_vendor_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Rejecting(_RecordingSMTP):
        def send_message(self, message: Any) -> None:
            raise smtplib.SMTPRecipientsRefused({})

    monkeypatch.setattr(smtplib, "SMTP", _Rejecting)
    provider = SmtpProvider(build_settings(dry_run=False, email_provider="smtp"))

    with pytest.raises(VendorError):
        await provider.send(
            EmailMessage(to="a@example.com", subject="s", html="<p>h</p>", text="t")
        )
