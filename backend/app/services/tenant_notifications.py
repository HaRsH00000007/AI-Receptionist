"""Staging the emails that are about a business.

The counterpart to :mod:`app.services.account_notifications`, and the
replacement for the old ``NotificationService``, which sent these two inline.

Inline was the wrong shape, and it failed in a way that was invisible. The
activation email was a direct provider call wrapped in ``except AppError``, so
a refusal from the vendor became one ``logger.warning`` and nothing else: no
durable row, no retry, and nothing for an operator to look at — while
``activate.py``'s own docstring claimed "the admin panel can resend", which it
could not, because there was no row to resend. A tenant could go live and the
owner never learn their phone number.

Both messages now go through the outbox, which already does this properly for
call summaries: the obligation is recorded before any send, retried on a
backoff, and left as ``FAILED`` with its reason when the attempts run out.

Both are deduped per tenant, by ``provisioning_key``. A provisioning run is
retried — that is the whole point of the step records — and a retry that
re-announces activation would email the same customer the same number twice.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import Notification, Tenant
from app.models.enums import NotificationKind
from app.services import email_templates as templates
from app.services.notification_outbox import NotificationOutbox, provisioning_key


def status_url(settings: Settings, tenant_id: uuid.UUID) -> str:
    """The page a customer can open without an account.

    Unchanged from the inline sender it replaces, so links already in inboxes
    keep working.
    """
    return f"{settings.public_app_url.rstrip('/')}/status/{tenant_id}"


async def stage_activation(
    session: AsyncSession,
    settings: Settings,
    *,
    tenant: Tenant,
    phone_e164: str,
) -> Notification | None:
    """Queue "your receptionist is live, here is its number".

    Returns ``None`` when it was already queued, which is the normal outcome of
    a re-run step and is a success.
    """
    return await NotificationOutbox(settings).enqueue(
        session,
        tenant_id=tenant.id,
        kind=NotificationKind.PROVISIONING_COMPLETE,
        recipient=tenant.contact_email,
        template_id=templates.TEMPLATE_ACTIVATION,
        template_vars={
            "business_name": tenant.name,
            "phone_e164": phone_e164,
            "status_url": status_url(settings, tenant.id),
        },
        dedupe_key=provisioning_key(tenant.id, NotificationKind.PROVISIONING_COMPLETE),
    )


async def stage_provisioning_failure(
    session: AsyncSession,
    settings: Settings,
    *,
    tenant: Tenant,
    reason: str,
) -> Notification | None:
    """Queue "we could not finish setting this up"."""
    return await NotificationOutbox(settings).enqueue(
        session,
        tenant_id=tenant.id,
        kind=NotificationKind.PROVISIONING_FAILED,
        recipient=tenant.contact_email,
        template_id=templates.TEMPLATE_FAILURE,
        template_vars={
            "business_name": tenant.name,
            "reason": reason,
            "status_url": status_url(settings, tenant.id),
        },
        dedupe_key=provisioning_key(tenant.id, NotificationKind.PROVISIONING_FAILED),
    )


__all__ = ["stage_activation", "stage_provisioning_failure", "status_url"]
