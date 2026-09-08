"""Email bodies.

Plain functions returning (subject, html, text). No template engine: three
emails do not justify one, and keeping them as code means they are type-checked
and testable.

Every message is escaped. A business name is user input, and it reaches an inbox.
"""

from __future__ import annotations

from html import escape

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
