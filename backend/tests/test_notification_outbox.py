"""M13 — the notification outbox.

The failure this module exists to prevent is the quiet one: the email provider
is down, an exception is logged, and nobody ever learns that a customer was not
told about a call. Afterwards, "we tried and it vanished" and "we never tried"
look identical.

So the tests are about durability and about *not* sending twice — a redelivered
webhook must not produce a second email — and about the boundary that stops a
notification failure from damaging provisioning.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import VendorError
from app.models import Notification, NotificationAttempt
from app.models.enums import NotificationKind, NotificationStatus
from app.providers.fakes.mail import FakeEmailProvider
from app.providers.models import EmailMessage, EmailResult
from app.services.email_templates import (
    TEMPLATE_ACTIVATION,
    TEMPLATE_CALL_SUMMARY,
    render_template,
)
from app.services.notification_outbox import (
    NotificationOutbox,
    call_summary_key,
    provisioning_key,
)
from tests.factories import make_tenant
from tests.support import build_settings

SUMMARY = {
    "summary": "A caller asked about Thursday availability.",
    "caller_name": "Dana",
    "callback_number": "+15559998888",
    "intent": "booking_request",
    "key_details": ["Prefers mornings"],
    "urgency": 2,
    "needs_human": False,
    "ai_handled_successfully": True,
}


class _BrokenEmailProvider:
    """An email provider that is down."""

    name = "broken"

    def __init__(self, *, retryable: bool = True) -> None:
        self.retryable = retryable
        self.attempts = 0

    async def send(self, message: EmailMessage) -> EmailResult:
        self.attempts += 1
        raise VendorError("smtp connection refused", vendor="smtp", retryable=self.retryable)


def _outbox() -> NotificationOutbox:
    return NotificationOutbox(build_settings())


async def _tenant(session: AsyncSession):  # type: ignore[no-untyped-def]
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    return tenant


async def _queue_summary(session: AsyncSession, tenant, key: str | None = None):  # type: ignore[no-untyped-def]
    return await _outbox().enqueue(
        session,
        tenant_id=tenant.id,
        kind=NotificationKind.CALL_SUMMARY,
        recipient=tenant.contact_email,
        template_id=TEMPLATE_CALL_SUMMARY,
        template_vars={
            "business_name": tenant.name,
            "caller_number": "+15559998888",
            "summary": SUMMARY,
            "status_url": "http://localhost:3000/status/x",
        },
        dedupe_key=key,
    )


# ===========================================================================
# The obligation is durable before anything is sent
# ===========================================================================


async def test_a_notification_row_exists_before_any_send(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant(db_session)
    notification = await _queue_summary(db_session, tenant)
    await db_session.commit()

    assert notification is not None
    assert notification.status is NotificationStatus.PENDING
    assert notification.sent_at is None


async def test_only_the_template_id_and_variables_are_stored(
    db_session: AsyncSession,
) -> None:
    """Not the rendered body.

    A stored body would put the customer's own call content in a second place
    with its own retention question — and would freeze the old wording, so a
    notification queued before a deploy would be delivered with the mistake the
    deploy was fixing.
    """
    tenant = await _tenant(db_session)
    notification = await _queue_summary(db_session, tenant)
    await db_session.commit()

    assert notification is not None
    assert notification.template_id == TEMPLATE_CALL_SUMMARY
    assert set(notification.template_vars_json) == {
        "business_name",
        "caller_number",
        "summary",
        "status_url",
    }
    assert not hasattr(notification, "body")


# ===========================================================================
# Never twice
# ===========================================================================


async def test_the_same_event_is_only_queued_once(db_session: AsyncSession) -> None:
    """A redelivered post-call webhook must not email the customer twice."""
    import uuid

    tenant = await _tenant(db_session)
    call_id = uuid.uuid4()

    first = await _queue_summary(db_session, tenant, call_summary_key(call_id))
    second = await _queue_summary(db_session, tenant, call_summary_key(call_id))
    await db_session.commit()

    assert first is not None
    assert second is None

    count = (
        await db_session.execute(
            select(func.count(Notification.id)).where(Notification.tenant_id == tenant.id)
        )
    ).scalar_one()
    assert count == 1


async def test_a_duplicate_does_not_poison_the_callers_transaction(
    db_session: AsyncSession,
) -> None:
    """The savepoint.

    A plain flush hitting the unique index would roll back the provisioning
    step that requested the notification — a duplicate email attempt would undo
    real work.
    """
    import uuid

    tenant = await _tenant(db_session)
    key = call_summary_key(uuid.uuid4())
    await _queue_summary(db_session, tenant, key)
    await _queue_summary(db_session, tenant, key)

    await db_session.commit()
    assert await db_session.get(type(tenant), tenant.id) is not None


async def test_notifications_without_a_key_may_coexist(db_session: AsyncSession) -> None:
    """A one-off nothing else will try to send needs no key."""
    tenant = await _tenant(db_session)
    assert await _queue_summary(db_session, tenant, None) is not None
    assert await _queue_summary(db_session, tenant, None) is not None
    await db_session.commit()


def test_dedupe_keys_are_derived_not_generated() -> None:
    """Derived, so two independent attempts collide by construction."""
    import uuid

    call_id = uuid.uuid4()
    assert call_summary_key(call_id) == call_summary_key(call_id)

    tenant_id = uuid.uuid4()
    assert provisioning_key(tenant_id, NotificationKind.PROVISIONING_COMPLETE) != provisioning_key(
        tenant_id, NotificationKind.PROVISIONING_FAILED
    )


# ===========================================================================
# Delivery, retry, and giving up
# ===========================================================================


async def test_a_successful_delivery_is_recorded(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    notification = await _queue_summary(db_session, tenant)
    await db_session.commit()
    assert notification is not None

    provider = FakeEmailProvider()
    report = await _outbox().deliver(db_session, notification.id, provider)

    assert report.outcome == "sent"
    await db_session.refresh(notification)
    assert notification.status is NotificationStatus.SENT
    assert notification.sent_at is not None
    assert notification.next_attempt_at is None

    attempts = (
        (
            await db_session.execute(
                select(NotificationAttempt).where(
                    NotificationAttempt.notification_id == notification.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(attempts) == 1
    assert attempts[0].succeeded is True


async def test_a_transient_failure_is_retried_later(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    notification = await _queue_summary(db_session, tenant)
    await db_session.commit()
    assert notification is not None

    report = await _outbox().deliver(db_session, notification.id, _BrokenEmailProvider())

    assert report.outcome == "retrying"
    await db_session.refresh(notification)
    assert notification.status is NotificationStatus.PENDING
    assert notification.next_attempt_at is not None
    assert notification.last_error


async def test_a_failed_attempt_is_kept_with_its_reason(
    db_session: AsyncSession,
) -> None:
    """ "It failed three times" does not answer "why"."""
    tenant = await _tenant(db_session)
    notification = await _queue_summary(db_session, tenant)
    await db_session.commit()
    assert notification is not None

    await _outbox().deliver(db_session, notification.id, _BrokenEmailProvider())

    attempt = (
        await db_session.execute(
            select(NotificationAttempt).where(
                NotificationAttempt.notification_id == notification.id
            )
        )
    ).scalar_one()
    assert attempt.succeeded is False
    assert "connection refused" in (attempt.error or "")


async def test_attempts_are_eventually_exhausted(db_session: AsyncSession) -> None:
    """An unmet obligation stays visible rather than retrying forever."""
    tenant = await _tenant(db_session)
    notification = await _queue_summary(db_session, tenant)
    await db_session.commit()
    assert notification is not None

    outbox = _outbox()
    provider = _BrokenEmailProvider()
    for _ in range(6):
        notification.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        notification.status = (
            NotificationStatus.PENDING
            if notification.status is NotificationStatus.PENDING
            else notification.status
        )
        await db_session.commit()
        report = await outbox.deliver(db_session, notification.id, provider)
        if report.outcome == "failed":
            break

    await db_session.refresh(notification)
    assert notification.status is NotificationStatus.FAILED
    assert notification.next_attempt_at is None


async def test_a_permanent_failure_is_not_retried(db_session: AsyncSession) -> None:
    """Retrying a template that does not exist will never start working."""
    tenant = await _tenant(db_session)
    notification = await _outbox().enqueue(
        db_session,
        tenant_id=tenant.id,
        kind=NotificationKind.CALL_SUMMARY,
        recipient=tenant.contact_email,
        template_id="no-such-template.v9",
        template_vars={},
    )
    await db_session.commit()
    assert notification is not None

    report = await _outbox().deliver(db_session, notification.id, FakeEmailProvider())

    assert report.outcome == "failed"
    await db_session.refresh(notification)
    assert notification.status is NotificationStatus.FAILED


async def test_a_delivered_notification_is_not_sent_again(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant(db_session)
    notification = await _queue_summary(db_session, tenant)
    await db_session.commit()
    assert notification is not None

    provider = FakeEmailProvider()
    await _outbox().deliver(db_session, notification.id, provider)
    second = await _outbox().deliver(db_session, notification.id, provider)

    assert second.outcome == "skipped"
    assert len(provider.outbox) == 1


# ===========================================================================
# Claiming
# ===========================================================================


async def test_only_due_notifications_are_claimed(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    due = await _queue_summary(db_session, tenant)
    later = await _queue_summary(db_session, tenant)
    assert due is not None and later is not None
    later.next_attempt_at = datetime.now(UTC) + timedelta(hours=1)
    await db_session.commit()

    claimed = await _outbox().claim_due(db_session, batch_size=10, lease_s=60)

    assert due.id in claimed
    assert later.id not in claimed


async def test_claiming_leases_so_two_workers_do_not_both_send(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant(db_session)
    notification = await _queue_summary(db_session, tenant)
    await db_session.commit()
    assert notification is not None

    outbox = _outbox()
    first = await outbox.claim_due(db_session, batch_size=10, lease_s=300)
    second = await outbox.claim_due(db_session, batch_size=10, lease_s=300)

    assert notification.id in first
    # The lease pushed next_attempt_at into the future, so it is not due again.
    assert notification.id not in second


# ===========================================================================
# The boundary with provisioning
# ===========================================================================


def test_the_outbox_never_raises_into_its_caller() -> None:
    """A tenant whose receptionist works but whose email bounced is fine.

    One whose provisioning was rolled back over an email is not. `enqueue`
    returns ``None`` for a duplicate rather than raising, and `deliver` reports
    an outcome rather than propagating a provider error.
    """
    import inspect

    source = inspect.getsource(NotificationOutbox.deliver)
    assert "raise" not in source


# ===========================================================================
# Rendering
# ===========================================================================


def test_a_template_renders_to_subject_html_and_text() -> None:
    subject, html, text = render_template(
        TEMPLATE_ACTIVATION,
        {
            "business_name": "Sunset Salon",
            "phone_e164": "+14155550100",
            "status_url": "http://localhost:3000/status/x",
        },
    )
    assert "Sunset Salon" in subject or "Sunset Salon" in html
    assert "+14155550100" in html
    assert text


def test_an_unknown_template_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="unknown notification template"):
        render_template("nope.v1", {})


def test_a_missing_variable_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="missing variable"):
        render_template(TEMPLATE_ACTIVATION, {"business_name": "Sunset Salon"})


def test_a_business_name_cannot_inject_html_into_an_email() -> None:
    """The name comes from a signup form and lands in an HTML document."""
    _, html, _ = render_template(
        TEMPLATE_ACTIVATION,
        {
            "business_name": "<script>alert(1)</script>",
            "phone_e164": "+14155550100",
            "status_url": "http://localhost:3000/status/x",
        },
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_no_credential_reaches_an_email_body() -> None:
    """Emails are forwarded, archived and screenshotted by customers."""
    _, html, text = render_template(
        TEMPLATE_CALL_SUMMARY,
        {
            "business_name": "Sunset Salon",
            "caller_number": "+15559998888",
            "summary": SUMMARY,
            "status_url": "http://localhost:3000/status/x",
        },
    )
    for forbidden in ("api_key", "secret", "Bearer ", "sk-", "auth_token"):
        assert forbidden not in html
        assert forbidden not in text
