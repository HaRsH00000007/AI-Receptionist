"""Staging the four emails that belong to a person rather than a business.

One module so that every account email is queued the same way, because the bug
this replaces was caused by exactly one of them being queued differently. The
magic-link route built a ``Notification`` by hand, put ``None`` in a ``NOT
NULL`` tenant column and named a template that did not exist. Both mistakes are
impossible to make here: the tenant is not a parameter, and the template id
comes from :mod:`app.services.email_templates` rather than from a string
literal at the call site.

Three decisions worth stating:

**No tenant.** These rows carry ``tenant_id=None`` deliberately. A sign-in link
belongs to an account, which at registration belongs to no business at all and
later may belong to several — there is no truthful value, and inventing one
would attribute a person's password reset to whichever business happened to be
first in their membership list.

**The link is built here, from one setting.** Every URL derives from
``PUBLIC_APP_URL``, so a misconfigured deployment produces links that are
uniformly wrong rather than wrong in one flow nobody tested.

**Staged, never sent.** Each of these returns once the row is durable. Delivery
is the worker's job via :class:`~app.services.notification_outbox.NotificationOutbox`,
which is what gives an auth email the same retries and the same operator
visibility as a call summary.
"""

from __future__ import annotations

from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import Notification, User
from app.models.enums import NotificationKind
from app.services import email_templates as templates
from app.services.notification_outbox import NotificationOutbox, account_key


def _base(settings: Settings) -> str:
    return settings.public_app_url.rstrip("/")


def magic_link_url(settings: Settings, token: str) -> str:
    """Where a sign-in link points. ``/auth/callback`` already exists."""
    return f"{_base(settings)}/auth/callback?token={quote(token, safe='')}"


def password_reset_url(settings: Settings, token: str) -> str:
    return f"{_base(settings)}/auth/reset?token={quote(token, safe='')}"


def email_verification_url(settings: Settings, token: str) -> str:
    return f"{_base(settings)}/auth/verify?token={quote(token, safe='')}"


def dashboard_url(settings: Settings) -> str:
    return f"{_base(settings)}/dashboard"


async def stage_magic_link(
    session: AsyncSession,
    settings: Settings,
    *,
    recipient: str,
    token: str,
) -> Notification | None:
    """Queue a sign-in link.

    No dedupe key: asking twice is a person who did not get the first one, and
    the second request carries a different token — collapsing them would drop
    the only message they are waiting for.
    """
    return await NotificationOutbox(settings).enqueue(
        session,
        tenant_id=None,
        kind=NotificationKind.MAGIC_LINK,
        recipient=recipient,
        template_id=templates.TEMPLATE_MAGIC_LINK,
        template_vars={
            "url": magic_link_url(settings, token),
            "expires_in_minutes": settings.magic_link_ttl_s // 60,
        },
    )


async def stage_welcome(
    session: AsyncSession,
    settings: Settings,
    *,
    user: User,
    verification_token: str,
) -> Notification | None:
    """Queue the one-off welcome, carrying the verification link.

    Deduped on the account: a welcome email is by definition sent once, and the
    key means a retried registration — or a future invite path that also wants
    to welcome somebody — cannot produce two. Returns ``None`` if one was
    already queued, which is a success, not a failure.
    """
    return await NotificationOutbox(settings).enqueue(
        session,
        tenant_id=None,
        kind=NotificationKind.WELCOME,
        recipient=user.email,
        template_id=templates.TEMPLATE_WELCOME,
        template_vars={
            "full_name": user.full_name,
            "dashboard_url": dashboard_url(settings),
            "verify_url": email_verification_url(settings, verification_token),
        },
        dedupe_key=account_key(user.id, NotificationKind.WELCOME),
    )


async def stage_password_reset(
    session: AsyncSession,
    settings: Settings,
    *,
    recipient: str,
    token: str,
) -> Notification | None:
    """Queue a reset link. Undeduped, for the same reason as the sign-in link."""
    return await NotificationOutbox(settings).enqueue(
        session,
        tenant_id=None,
        kind=NotificationKind.PASSWORD_RESET,
        recipient=recipient,
        template_id=templates.TEMPLATE_PASSWORD_RESET,
        template_vars={
            "url": password_reset_url(settings, token),
            "expires_in_minutes": settings.password_reset_ttl_s // 60,
        },
    )


async def stage_email_verification(
    session: AsyncSession,
    settings: Settings,
    *,
    recipient: str,
    token: str,
) -> Notification | None:
    """Queue a verification link, for "send it again"."""
    return await NotificationOutbox(settings).enqueue(
        session,
        tenant_id=None,
        kind=NotificationKind.EMAIL_VERIFICATION,
        recipient=recipient,
        template_id=templates.TEMPLATE_EMAIL_VERIFICATION,
        template_vars={
            "url": email_verification_url(settings, token),
            "expires_in_minutes": settings.email_verification_ttl_s // 60,
        },
    )


__all__ = [
    "dashboard_url",
    "email_verification_url",
    "magic_link_url",
    "password_reset_url",
    "stage_email_verification",
    "stage_magic_link",
    "stage_password_reset",
    "stage_welcome",
]
