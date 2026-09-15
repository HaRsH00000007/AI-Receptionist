"""The notification outbox.

A notification is an *obligation*, and the durable row is what makes it one. The
alternative — calling the email provider inline and hoping — has a failure mode
that is genuinely indistinguishable from success: the provider is down, an
exception is logged, and nobody ever learns that a customer was not told about
a call. "We tried to tell them and it vanished" and "we never tried" look the
same afterwards.

So the row exists **before** any send is attempted, and it survives until
delivery succeeds or the attempts are exhausted. That ordering buys three
things:

* a retry has something to find after a restart or a deploy;
* an operator can see what is owed and what failed, rather than grepping logs;
* the send can be moved off the request path entirely, so a slow provider never
  makes a webhook time out and be redelivered.

Deduplication is a unique index on ``dedupe_key``, derived from the event being
announced rather than generated. Two workers racing on the same redelivered
webhook therefore collide in the database instead of both emailing the customer.

Nothing here is allowed to break provisioning. A notification failure marks its
own row and stops; it never fails the step that requested it, because a tenant
whose receptionist works but whose welcome email bounced is in a far better
position than one whose provisioning was rolled back over an email.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import Notification, NotificationAttempt
from app.models.enums import NotificationKind, NotificationStatus
from app.providers.models import EmailMessage
from app.providers.protocols import EmailProvider
from app.services.email_templates import render_template

logger = get_logger(__name__)

#: Backoff between delivery attempts, in seconds. Longer than the provisioning
#: ladder: an email provider outage is usually minutes, and hammering it makes
#: us look like an abusive sender rather than getting the mail through sooner.
_BACKOFF_S: tuple[int, ...] = (60, 300, 1_800, 7_200)


def call_summary_key(call_id: uuid.UUID) -> str:
    """One summary email per call, however many times the webhook arrives."""
    return f"call_summary:{call_id}"


def provisioning_key(tenant_id: uuid.UUID, kind: NotificationKind) -> str:
    """One activation or failure email per tenant, however many retries run."""
    return f"{kind.value}:{tenant_id}"


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    notification_id: uuid.UUID
    outcome: str  # sent | retrying | failed | skipped
    status: NotificationStatus


class NotificationOutbox:
    """Durable enqueue, claim and deliver."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ---- enqueue ---------------------------------------------------------
    async def enqueue(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        kind: NotificationKind,
        recipient: str,
        template_id: str,
        template_vars: dict[str, object],
        call_id: uuid.UUID | None = None,
        dedupe_key: str | None = None,
    ) -> Notification | None:
        """Record that we owe someone a message.

        Returns ``None`` when an identical obligation already exists — decided
        by the unique index rather than a prior SELECT, because a check followed
        by an insert loses to a concurrent delivery of the same webhook.

        Only the template id and its variables are stored, never the rendered
        body. Keeping the rendered text would put a call summary — the
        customer's own content — in a second place with its own retention
        question, for no benefit that re-rendering does not provide.
        """
        notification = Notification(
            tenant_id=tenant_id,
            call_id=call_id,
            kind=kind,
            status=NotificationStatus.PENDING,
            recipient=recipient,
            template_id=template_id,
            template_vars_json=dict(template_vars),
            dedupe_key=dedupe_key,
            next_attempt_at=datetime.now(UTC),
        )
        try:
            async with session.begin_nested():
                session.add(notification)
                await session.flush()
        except IntegrityError:
            # The savepoint confines this to the insert. A plain flush would
            # poison the caller's transaction, so a duplicate notification would
            # roll back the provisioning step that requested it.
            logger.info(
                "notification already queued; not queued twice",
                extra={"tenant_id": str(tenant_id), "kind": kind.value},
            )
            return None
        return notification

    # ---- claim -----------------------------------------------------------
    async def claim_due(
        self,
        session: AsyncSession,
        *,
        batch_size: int,
        lease_s: int,
        now: datetime | None = None,
    ) -> list[uuid.UUID]:
        """Lease notifications that are due, skipping ones another worker holds."""
        moment = now or datetime.now(UTC)
        rows = (
            (
                await session.execute(
                    select(Notification)
                    .where(Notification.status == NotificationStatus.PENDING)
                    .where(
                        (Notification.next_attempt_at.is_(None))
                        | (Notification.next_attempt_at <= moment)
                    )
                    .order_by(Notification.next_attempt_at.nulls_first(), Notification.created_at)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        lease_until = moment + timedelta(seconds=lease_s)
        for row in rows:
            row.next_attempt_at = lease_until
        await session.commit()
        return [row.id for row in rows]

    # ---- deliver ---------------------------------------------------------
    async def deliver(
        self,
        session: AsyncSession,
        notification_id: uuid.UUID,
        provider: EmailProvider,
    ) -> DeliveryReport:
        """Attempt one delivery, recording the attempt either way."""
        notification = await session.get(Notification, notification_id)
        if notification is None or notification.status is not NotificationStatus.PENDING:
            status = notification.status if notification else NotificationStatus.FAILED
            return DeliveryReport(notification_id, "skipped", status)

        notification.attempt += 1
        attempt_number = notification.attempt

        try:
            subject, html, text = render_template(
                notification.template_id, notification.template_vars_json
            )
            result = await provider.send(
                EmailMessage(to=notification.recipient, subject=subject, html=html, text=text)
            )
        except (AppError, ValueError) as exc:
            return await self._record_failure(session, notification, attempt_number, exc)

        session.add(
            NotificationAttempt(
                notification_id=notification.id,
                attempt=attempt_number,
                provider=result.provider,
                succeeded=True,
                provider_message_id=result.message_id,
            )
        )
        notification.status = NotificationStatus.SENT
        notification.sent_at = datetime.now(UTC)
        notification.next_attempt_at = None
        notification.last_error = None
        notification.subject = subject
        await session.commit()

        # Recipient and subject only. A call summary is the customer's content
        # and does not belong in our logs.
        logger.info(
            "notification delivered",
            extra={
                "notification_id": str(notification.id),
                "kind": notification.kind.value,
                "attempt": attempt_number,
            },
        )
        return DeliveryReport(notification.id, "sent", notification.status)

    async def _record_failure(
        self,
        session: AsyncSession,
        notification: Notification,
        attempt_number: int,
        exc: Exception,
    ) -> DeliveryReport:
        """Schedule a retry, or give up and leave the reason on the row."""
        reason = str(exc)[:500]
        session.add(
            NotificationAttempt(
                notification_id=notification.id,
                attempt=attempt_number,
                provider="unknown",
                succeeded=False,
                error=reason,
            )
        )
        notification.last_error = reason

        # A rendering failure is permanent by nature: an unknown template id or
        # a missing variable will not start working on the fourth attempt, and
        # retrying for two hours only delays an operator noticing. A *vendor*
        # failure is transient unless it says otherwise.
        retryable = isinstance(exc, AppError) and exc.retryable
        exhausted = attempt_number >= len(_BACKOFF_S)

        if retryable and not exhausted:
            delay = _BACKOFF_S[min(attempt_number - 1, len(_BACKOFF_S) - 1)]
            notification.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
            outcome = "retrying"
        else:
            # Terminal. The row stays PENDING-free and FAILED so an operator can
            # see an unmet obligation rather than it disappearing from the queue.
            notification.status = NotificationStatus.FAILED
            notification.next_attempt_at = None
            outcome = "failed"
            logger.warning(
                "giving up on a notification",
                extra={
                    "notification_id": str(notification.id),
                    "kind": notification.kind.value,
                    "attempts": attempt_number,
                },
            )

        await session.commit()
        return DeliveryReport(notification.id, outcome, notification.status)
