"""Email bodies.

Plain functions returning (subject, html, text). No template engine: three
emails do not justify one, and keeping them as code means they are type-checked
and testable.

Every message is escaped. A business name is user input, and it reaches an inbox.
"""

from __future__ import annotations

from html import escape
from typing import Any

from app.schemas.agent_config import CallSummary

_STYLE = (
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    "line-height:1.5;color:#1a1a1a;max-width:560px"
)


def _wrap(body: str) -> str:
    return f'<div style="{_STYLE}">{body}</div>'


def activation_email(
    *, business_name: str, phone_e164: str, status_url: str
) -> tuple[str, str, str]:
    name = escape(business_name)
    number = escape(phone_e164)
    url = escape(status_url)

    subject = f"Your AI receptionist is live — {business_name}"
    html = _wrap(
        f"<h2>Your AI receptionist is ready</h2>"
        f"<p>{name} now has a dedicated number answered by your AI receptionist:</p>"
        f'<p style="font-size:22px;font-weight:600">{number}</p>'
        f"<p>Call it to hear the greeting and check the details are right. "
        f"Every call appears on your dashboard with a summary.</p>"
        f'<p><a href="{url}">Open your dashboard</a></p>'
    )
    text = (
        f"Your AI receptionist is ready.\n\n"
        f"{business_name} now has a dedicated number: {phone_e164}\n\n"
        f"Call it to hear the greeting and check the details are right. "
        f"Every call appears on your dashboard with a summary.\n\n"
        f"Dashboard: {status_url}\n"
    )
    return subject, html, text


def failure_email(*, business_name: str, reason: str, status_url: str) -> tuple[str, str, str]:
    """Sent when a run fails terminally.

    Says what happened without exposing a vendor error verbatim — the caller
    does not need a stack trace, and it should not leak provider internals.
    """
    name = escape(business_name)
    url = escape(status_url)
    detail = escape(reason)

    subject = f"We could not finish setting up {business_name}"
    html = _wrap(
        f"<h2>Setup did not complete</h2>"
        f"<p>We hit a problem while setting up the AI receptionist for {name} "
        f"and have paused rather than leave it half-configured.</p>"
        f"<p><strong>What happened:</strong> {detail}</p>"
        f"<p>Nothing is being charged for an incomplete setup. Our team has been "
        f"notified and will pick this up.</p>"
        f'<p><a href="{url}">Check the status</a></p>'
    )
    text = (
        f"Setup did not complete.\n\n"
        f"We hit a problem while setting up the AI receptionist for {business_name} "
        f"and have paused rather than leave it half-configured.\n\n"
        f"What happened: {reason}\n\n"
        f"Nothing is being charged for an incomplete setup.\n\n"
        f"Status: {status_url}\n"
    )
    return subject, html, text


def call_summary_email(
    *,
    business_name: str,
    caller_number: str | None,
    summary: CallSummary,
    status_url: str,
) -> tuple[str, str, str]:
    name = escape(business_name)
    url = escape(status_url)
    from_number = escape(caller_number or "unknown number")

    who = summary.caller_name or "A caller"
    urgency_note = " — marked urgent" if summary.urgency >= 4 else ""

    details = "".join(f"<li>{escape(detail)}</li>" for detail in summary.key_details)
    details_block = f"<ul>{details}</ul>" if details else ""

    follow_up = (
        f"<p><strong>Requested follow-up:</strong> {escape(summary.requested_follow_up)}</p>"
        if summary.requested_follow_up
        else ""
    )
    callback = (
        f"<p><strong>Callback number given:</strong> {escape(summary.callback_number)} "
        f'<span style="color:#666">(as stated by the caller)</span></p>'
        if summary.callback_number
        else ""
    )
    human = (
        '<p style="color:#b45309"><strong>This caller asked for a person.</strong></p>'
        if summary.needs_human
        else ""
    )

    subject = f"New call for {business_name}{urgency_note}"
    html = _wrap(
        f"<h2>New call</h2>"
        f"<p><strong>{escape(who)}</strong> called {name} from {from_number}.</p>"
        f"<p>{escape(summary.summary)}</p>"
        f"{human}{callback}{follow_up}{details_block}"
        f'<p><a href="{url}">See all calls</a></p>'
    )

    text_lines = [
        f"New call for {business_name}",
        "",
        f"{who} called from {caller_number or 'unknown number'}.",
        "",
        summary.summary,
    ]
    if summary.needs_human:
        text_lines += ["", "This caller asked for a person."]
    if summary.callback_number:
        text_lines += ["", f"Callback number given: {summary.callback_number} (as stated)"]
    if summary.requested_follow_up:
        text_lines += ["", f"Requested follow-up: {summary.requested_follow_up}"]
    for detail in summary.key_details:
        text_lines.append(f"- {detail}")
    text_lines += ["", f"See all calls: {status_url}"]

    return subject, html, "\n".join(text_lines)


# ---------------------------------------------------------------------------
# Account emails
# ---------------------------------------------------------------------------
# These four are about a *person*, not a business, which is why none of them
# takes a business name and why their notification rows carry no tenant.
#
# All four are also credential delivery, so they share three rules:
#
# * **The link is the whole message.** No tracking redirect, no "see your
#   options here" — a sign-in link that arrives wrapped in a third party's URL
#   is indistinguishable from a phish, and people are right to distrust it.
# * **Say how long it lasts.** Someone who opens the mail an hour late needs to
#   know whether to click or to ask for another, and guessing wrong is a support
#   ticket either way.
# * **Never state whether the account already existed.** These bodies are
#   written so the same wording is truthful for a brand-new account and an old
#   one, for the reason the magic-link endpoint exists to protect.


def _link_button(url: str, label: str) -> str:
    return (
        f'<p><a href="{url}" style="display:inline-block;padding:10px 18px;'
        f"background:#1a1a1a;color:#fff;border-radius:6px;"
        f'text-decoration:none;font-weight:600">{label}</a></p>'
    )


def _expiry(minutes: int) -> str:
    """Render a TTL the way a person would say it: 15 minutes, 1 hour, 24 hours."""
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    return "1 hour" if hours == 1 else f"{hours} hours"


def magic_link_email(*, url: str, expires_in_minutes: int) -> tuple[str, str, str]:
    """The sign-in link.

    Says nothing about who the account belongs to or what it can reach: if this
    message reaches the wrong inbox, it should tell that reader nothing they did
    not already know.
    """
    safe = escape(url)
    window = _expiry(expires_in_minutes)

    subject = "Your sign-in link"
    html = _wrap(
        f"<h2>Sign in</h2>"
        f"<p>Use this link to sign in. It works once and expires in {window}.</p>"
        f"{_link_button(safe, 'Sign in')}"
        f'<p style="color:#666;font-size:13px">If you did not ask to sign in, you '
        f"can ignore this email — nobody can use the link but you.</p>"
    )
    text = (
        f"Sign in\n\n"
        f"Use this link to sign in. It works once and expires in {window}.\n\n"
        f"{url}\n\n"
        f"If you did not ask to sign in, you can ignore this email.\n"
    )
    return subject, html, text


def welcome_email(
    *, full_name: str | None, dashboard_url: str, verify_url: str
) -> tuple[str, str, str]:
    """Sent once, when an account is created.

    Carries the email-verification link rather than sending a second message a
    few seconds behind this one. Verification is not enforced anywhere yet, so a
    dedicated email at signup would be a second unexplained thing in the inbox
    for no benefit the button here does not already give.
    """
    greeting = f"Hi {escape(full_name.split()[0])}," if full_name else "Hi,"
    dashboard = escape(dashboard_url)
    verify = escape(verify_url)

    subject = "Welcome to AI Receptionist"
    html = _wrap(
        f"<h2>Welcome</h2>"
        f"<p>{greeting}</p>"
        f"<p>Your account is ready. The next step is to set up a business — your "
        f"receptionist gets its own phone number once that finishes.</p>"
        f"{_link_button(dashboard, 'Open your dashboard')}"
        f"<p>Confirming your email address means we can reach you about your "
        f"calls, and lets you sign in by link if you forget your password.</p>"
        f'<p><a href="{verify}">Confirm this email address</a></p>'
    )
    text = (
        f"Welcome\n\n"
        f"{greeting.rstrip(',')}\n\n"
        f"Your account is ready. The next step is to set up a business — your "
        f"receptionist gets its own phone number once that finishes.\n\n"
        f"Dashboard: {dashboard_url}\n\n"
        f"Confirming your email address means we can reach you about your calls, "
        f"and lets you sign in by link if you forget your password.\n\n"
        f"Confirm this email address: {verify_url}\n"
    )
    return subject, html, text


def password_reset_email(*, url: str, expires_in_minutes: int) -> tuple[str, str, str]:
    """The reset link.

    Says what to do if it was not requested, because an unexpected reset email
    is the first thing someone sees when another person is trying to get into
    their account — and "ignore it" is genuinely the right advice: the password
    does not change until this link is followed.
    """
    safe = escape(url)
    window = _expiry(expires_in_minutes)

    subject = "Reset your password"
    html = _wrap(
        f"<h2>Reset your password</h2>"
        f"<p>Use this link to choose a new password. It works once and expires in "
        f"{window}.</p>"
        f"{_link_button(safe, 'Choose a new password')}"
        f'<p style="color:#666;font-size:13px">If you did not ask for this, you can '
        f"ignore this email. Your password has not changed, and it will not change "
        f"unless this link is used.</p>"
    )
    text = (
        f"Reset your password\n\n"
        f"Use this link to choose a new password. It works once and expires in "
        f"{window}.\n\n"
        f"{url}\n\n"
        f"If you did not ask for this, you can ignore this email. Your password has "
        f"not changed, and it will not change unless this link is used.\n"
    )
    return subject, html, text


def email_verification_email(*, url: str, expires_in_minutes: int) -> tuple[str, str, str]:
    """Confirm an address, on request.

    The welcome email already carries this link; this one exists for "send it
    again", which is what someone asks for when the first message never arrived.
    """
    safe = escape(url)
    window = _expiry(expires_in_minutes)

    subject = "Confirm your email address"
    html = _wrap(
        f"<h2>Confirm your email address</h2>"
        f"<p>Use this link to confirm this is your address. It expires in "
        f"{window}.</p>"
        f"{_link_button(safe, 'Confirm this address')}"
    )
    text = (
        f"Confirm your email address\n\n"
        f"Use this link to confirm this is your address. It expires in {window}.\n\n"
        f"{url}\n"
    )
    return subject, html, text


# ---------------------------------------------------------------------------
# Rendering by id
# ---------------------------------------------------------------------------
#: Template ids the outbox stores on a notification row.
#:
#: Ids rather than rendered bodies, because the row outlives the send: a
#: notification queued before a deploy is rendered by the *new* template when it
#: is finally delivered, and a stored body would have frozen the old wording —
#: including any mistake that the deploy was fixing.
TEMPLATE_ACTIVATION = "activation.v1"
TEMPLATE_FAILURE = "failure.v1"
TEMPLATE_CALL_SUMMARY = "call_summary.v1"
TEMPLATE_MAGIC_LINK = "magic_link.v1"
TEMPLATE_WELCOME = "welcome.v1"
TEMPLATE_PASSWORD_RESET = "password_reset.v1"
TEMPLATE_EMAIL_VERIFICATION = "email_verification.v1"

#: Every id this module can render.
#:
#: Exported so a test can assert that each id *staged* anywhere in the
#: application appears here. A notification is queued by one module and rendered
#: by another, and the gap between them is where an id with no template hides:
#: it costs nothing until the worker picks the row up, by which time the
#: customer is already waiting for an email that will never render.
TEMPLATE_IDS: frozenset[str] = frozenset(
    {
        TEMPLATE_ACTIVATION,
        TEMPLATE_FAILURE,
        TEMPLATE_CALL_SUMMARY,
        TEMPLATE_MAGIC_LINK,
        TEMPLATE_WELCOME,
        TEMPLATE_PASSWORD_RESET,
        TEMPLATE_EMAIL_VERIFICATION,
    }
)


def render_template(template_id: str, variables: dict[str, Any]) -> tuple[str, str, str]:
    """Render a stored notification into subject, HTML and text.

    Raises :class:`ValueError` for an unknown id or missing variables. That is
    deliberately a *non-retryable* failure in the outbox: re-sending a template
    that does not exist will never start working, and retrying it for two hours
    only delays the operator noticing.
    """
    try:
        match template_id:
            case "activation.v1":
                return activation_email(
                    business_name=str(variables["business_name"]),
                    phone_e164=str(variables["phone_e164"]),
                    status_url=str(variables["status_url"]),
                )
            case "failure.v1":
                return failure_email(
                    business_name=str(variables["business_name"]),
                    reason=str(variables["reason"]),
                    status_url=str(variables["status_url"]),
                )
            case "call_summary.v1":
                return call_summary_email(
                    business_name=str(variables["business_name"]),
                    caller_number=(
                        str(variables["caller_number"])
                        if variables.get("caller_number") is not None
                        else None
                    ),
                    summary=CallSummary.model_validate(variables["summary"]),
                    status_url=str(variables["status_url"]),
                )
            case "magic_link.v1":
                return magic_link_email(
                    url=str(variables["url"]),
                    expires_in_minutes=int(variables["expires_in_minutes"]),
                )
            case "welcome.v1":
                name = variables.get("full_name")
                return welcome_email(
                    full_name=str(name) if name else None,
                    dashboard_url=str(variables["dashboard_url"]),
                    verify_url=str(variables["verify_url"]),
                )
            case "password_reset.v1":
                return password_reset_email(
                    url=str(variables["url"]),
                    expires_in_minutes=int(variables["expires_in_minutes"]),
                )
            case "email_verification.v1":
                return email_verification_email(
                    url=str(variables["url"]),
                    expires_in_minutes=int(variables["expires_in_minutes"]),
                )
            case _:
                raise ValueError(f"unknown notification template: {template_id}")
    except KeyError as exc:
        raise ValueError(f"template {template_id} is missing variable {exc}") from exc
