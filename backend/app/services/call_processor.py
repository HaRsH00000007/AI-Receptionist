"""Turn a stored call into a summary and a notification.

Runs in the worker rather than in the webhook handler. ElevenLabs retries a
webhook that does not answer quickly, and doing an LLM call plus an email inside
the request would make slow-but-fine look identical to failed — and produce
duplicate deliveries. The webhook stores the call and returns; this picks it up.

Same durable-retry shape as provisioning: attempt, ``next_attempt_at``, last
error, all on the row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.correlation import new_correlation_id, reset_correlation_id, set_correlation_id
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import Call, CallTagAssignment, Tenant
from app.models.enums import CallStatus, NotificationKind
from app.providers.registry import Providers
from app.provisioning.retry import next_attempt_at, should_retry
from app.schemas.agent_config import CallSummary
from app.services.call_tags import CallTagService
from app.services.contacts import ContactService
from app.services.email_templates import TEMPLATE_CALL_SUMMARY
from app.services.notification_outbox import NotificationOutbox, call_summary_key
from app.services.summarizer import Summarizer
from app.services.tag_classifier import TagClassifier
from app.services.usage import UsageService

logger = get_logger(__name__)

#: Calls in these statuses still have work outstanding.
PENDING_CALL_STATUSES = (CallStatus.RECEIVED, CallStatus.TRANSCRIBED, CallStatus.SUMMARIZED)


@dataclass(frozen=True, slots=True)
class CallReport:
    call_id: uuid.UUID
    outcome: str  # notified | retrying | failed | skipped
    status: CallStatus


async def claim_calls(
    session: AsyncSession, *, batch_size: int, lease_s: int, now: datetime | None = None
) -> list[uuid.UUID]:
    """Lease calls that need processing, skipping ones another worker holds."""
    from datetime import timedelta

    moment = now or datetime.now(UTC)
    result = await session.execute(
        select(Call)
        .where(Call.status.in_(PENDING_CALL_STATUSES))
        .where((Call.next_attempt_at.is_(None)) | (Call.next_attempt_at <= moment))
        .order_by(Call.next_attempt_at.nulls_first(), Call.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    calls = list(result.scalars().all())
    lease_until = moment + timedelta(seconds=lease_s)
    for call in calls:
        call.next_attempt_at = lease_until
    await session.commit()
    return [call.id for call in calls]


class CallProcessor:
    def __init__(self, settings: Settings, providers: Providers) -> None:
        self.settings = settings
        self.providers = providers

    async def process(self, session: AsyncSession, call_id: uuid.UUID) -> CallReport:
        call = await session.get(Call, call_id)
        if call is None or call.status in (CallStatus.NOTIFIED, CallStatus.FAILED):
            status = call.status if call else CallStatus.FAILED
            return CallReport(call_id, "skipped", status)

        tenant = await session.get(Tenant, call.tenant_id)
        if tenant is None:  # pragma: no cover - guarded by a foreign key
            return CallReport(call_id, "skipped", call.status)

        token = set_correlation_id(new_correlation_id())
        try:
            return await self._process(session, call, tenant)
        finally:
            reset_correlation_id(token)

    async def _process(self, session: AsyncSession, call: Call, tenant: Tenant) -> CallReport:
        call.attempt += 1
        await session.commit()

        # Metered before summarization, and separately from it. A call that
        # happened was used, whether or not the LLM can describe it — metering
        # it only on the success path would silently under-bill every call whose
        # summary failed. Keyed on the provider's call id, so the retries below
        # cannot count the same call twice.
        #
        # Staged, not committed: the row rides along on whichever commit this
        # attempt reaches. Committing here would expire the call and tenant
        # mid-flight and make the metering step change how the rest behaves.
        await self._meter(session, call, tenant)

        try:
            summary = await self._ensure_summary(session, call, tenant)
            # Queued, not sent. The obligation becomes durable here and the
            # worker delivers it, so a provider outage leaves something to
            # retry rather than an exception in a log — and a redelivered
            # webhook collides on the dedupe key instead of emailing twice.
            await NotificationOutbox(self.settings).enqueue(
                session,
                tenant_id=tenant.id,
                kind=NotificationKind.CALL_SUMMARY,
                recipient=tenant.contact_email,
                template_id=TEMPLATE_CALL_SUMMARY,
                template_vars={
                    "business_name": tenant.name,
                    "caller_number": call.from_e164,
                    "summary": summary.model_dump(mode="json"),
                    "status_url": f"{self.settings.public_app_url.rstrip('/')}/status/{tenant.id}",
                },
                call_id=call.id,
                dedupe_key=call_summary_key(call.id),
            )
        except Exception as exc:  # noqa: BLE001 - classified below
            return await self._handle_failure(session, call.id, exc)

        # Label the call against the business's own tags. After the summary,
        # because the summary is context for it, and swallowed for the same
        # reason the contact below is: a label is a convenience and must never
        # cost the business its call record or its email.
        await self._apply_tags(session, call, tenant)

        # Record the caller in the address book. Deliberately after the
        # summary, because that is where a name becomes available, and
        # deliberately swallowed: a contact is a convenience, and failing to
        # write one must never cost the business its call record or its email.
        await self._capture_contact(session, call, tenant)

        call.status = CallStatus.NOTIFIED
        call.next_attempt_at = None
        call.last_error = None
        await session.commit()

        logger.info(
            "call summarized and notified",
            extra={"call_id": str(call.id), "tenant_id": str(tenant.id)},
        )
        return CallReport(call.id, "notified", call.status)

    async def _apply_tags(self, session: AsyncSession, call: Call, tenant: Tenant) -> None:
        """Classify the call against the tenant's tags, exactly once, never fatally.

        **Retry behaviour.** A marker is written into ``transcript_json`` beside
        the cached summary once the classifier has run, so a call that reaches
        this step again — because the email provider failed and the job retried
        — does not pay for a second model call. The marker records the ids that
        were chosen, so it also survives as an explanation of why a call carries
        the labels it does. If the transaction carrying it is lost the
        classifier runs again, which costs one model call and writes the same
        rows; the composite key below is what makes that harmless.

        **Idempotency.** The assignment's primary key is ``(call_id, tag_id)``,
        so re-running merges onto the rows it already wrote instead of
        duplicating them.

        **Isolation.** The writes happen inside a savepoint and every failure is
        logged and dropped — with no ``session.rollback()``, deliberately: by
        the time this runs the caller has already staged the notification on
        this transaction, and rolling the session back would answer a failed
        *label* by throwing away the customer's *email*. A savepoint undoes only
        what this method did.

        The model call itself is made outside the savepoint, so a slow vendor
        does not hold a write transaction open while it thinks.
        """
        if (call.transcript_json or {}).get("tags_json") is not None:
            return

        try:
            tags = await CallTagService(session).enabled(tenant.id)
            if not tags:
                # No tags configured: no model call, and no marker either, so a
                # business that adds tags later is not locked out of labelling
                # the calls it already has.
                return

            chosen = await TagClassifier(self.providers.llm, self.settings).classify(
                tags=tags,
                transcript=_transcript_text(call),
                summary=call.summary,
                business_name=tenant.name,
                business_type=tenant.business_type.value,
            )

            async with session.begin_nested():
                for tag_id in chosen:
                    await session.merge(CallTagAssignment(call_id=call.id, tag_id=tag_id))
                call.transcript_json = {
                    **(call.transcript_json or {}),
                    "tags_json": {"ids": [str(tag_id) for tag_id in chosen]},
                }
            logger.info(
                "call tagged",
                extra={
                    "call_id": str(call.id),
                    "tenant_id": str(tenant.id),
                    "tags": len(chosen),
                    "considered": len(tags),
                },
            )
        except Exception:  # a label must never cost us a call record
            logger.warning(
                "could not tag a call; the call, its summary and its email are unaffected",
                extra={"call_id": str(call.id), "tenant_id": str(tenant.id)},
                exc_info=True,
            )

    async def _capture_contact(self, session: AsyncSession, call: Call, tenant: Tenant) -> None:
        """Add or update the caller's contact record, never fatally.

        Idempotent through the unique constraint on (tenant, number), so a
        redelivered webhook updates the row it already created rather than
        making a second one. Any failure is logged and dropped: the call is the
        obligation here, and an address-book entry is not worth losing it over.
        """
        try:
            # Inside a savepoint: whatever goes wrong here is undone without
            # touching the notification already staged on this transaction.
            async with session.begin_nested():
                await ContactService(session).capture_from_call(tenant_id=tenant.id, call=call)
        except Exception:  # noqa: BLE001 - a contact must never cost us a call record
            logger.warning(
                "could not record the caller as a contact; the call is unaffected",
                extra={"call_id": str(call.id), "tenant_id": str(tenant.id)},
            )

    async def _meter(self, session: AsyncSession, call: Call, tenant: Tenant) -> None:
        """Record this call's minutes, exactly once.

        Failure here is logged and swallowed: a metering problem must not stop a
        customer being told about their call. The ledger is append-only and
        reconciled against provider usage reports later, so a missed row is a
        correctable discrepancy rather than lost work — whereas an undelivered
        message is a lost customer.
        """
        if not call.duration_s:
            return
        try:
            await UsageService(session).record_call(
                tenant_id=tenant.id,
                call_id=call.id,
                duration_s=call.duration_s,
                provider_call_id=call.provider_call_id,
                occurred_at=call.started_at,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "could not meter a call; the ledger will be reconciled later",
                extra={"call_id": str(call.id), "tenant_id": str(tenant.id)},
            )

    async def _ensure_summary(
        self, session: AsyncSession, call: Call, tenant: Tenant
    ) -> CallSummary:
        """Summarize, unless a previous attempt already did.

        Splitting summarize-then-notify means a failing email provider does not
        buy a second LLM call every retry.
        """
        stored = (call.transcript_json or {}).get("summary_json")
        if call.status is CallStatus.SUMMARIZED and isinstance(stored, dict):
            return CallSummary.model_validate(stored)

        transcript = _transcript_text(call)
        summary = await Summarizer(self.providers.llm, self.settings).summarize(
            transcript=transcript,
            business_name=tenant.name,
            business_type=tenant.business_type.value,
            called_number=call.to_e164,
            caller_number=call.from_e164,
            duration_s=call.duration_s,
        )

        call.summary = summary.summary
        call.caller_name = summary.caller_name
        call.callback_number = summary.callback_number
        call.intent = summary.intent
        call.urgency = summary.urgency
        call.status = CallStatus.SUMMARIZED
        call.transcript_json = {
            **(call.transcript_json or {}),
            "summary_json": summary.model_dump(mode="json"),
        }
        await session.commit()
        return summary

    async def _handle_failure(
        self, session: AsyncSession, call_id: uuid.UUID, exc: Exception
    ) -> CallReport:
        # Re-read rather than merge: see the note in ProvisioningEngine.
        await session.rollback()
        call = await session.get(Call, call_id)
        if call is None:  # pragma: no cover
            raise exc

        message = (
            f"[{exc.code}] {exc.message}"
            if isinstance(exc, AppError)
            else f"[unexpected] {type(exc).__name__}: {exc}"
        )[:1000]
        call.last_error = message

        if should_retry(
            exc, attempt=call.attempt, max_attempts=self.settings.call_summary_max_attempts
        ):
            call.next_attempt_at = next_attempt_at(call.attempt, self.settings.backoff_schedule_s)
            await session.commit()
            logger.warning(
                "call processing failed, will retry",
                extra={"call_id": str(call.id), "attempt": call.attempt, "error": message},
            )
            return CallReport(call.id, "retrying", call.status)

        # The call record and its transcript survive; only the summary is lost.
        call.status = CallStatus.FAILED
        call.next_attempt_at = None
        await session.commit()
        logger.error(
            "call processing failed permanently; the call record is retained",
            extra={"call_id": str(call.id), "error": message},
        )
        return CallReport(call.id, "failed", call.status)


def _transcript_text(call: Call) -> str:
    """The conversation as plain text, from whatever the webhook stored."""
    payload = call.transcript_json or {}
    text = payload.get("text")
    if isinstance(text, str):
        return text

    lines: list[str] = []
    for turn in payload.get("turns", []):
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role", "unknown"))
        message = str(turn.get("message") or "").strip()
        if message:
            lines.append(f"{role}: {message}")
    return "\n".join(lines)
