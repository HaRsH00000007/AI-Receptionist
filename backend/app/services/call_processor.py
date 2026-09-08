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
from app.models import Call, Tenant
from app.models.enums import CallStatus
from app.providers.registry import Providers
from app.provisioning.retry import next_attempt_at, should_retry
from app.schemas.agent_config import CallSummary
from app.services.notifications import NotificationService
from app.services.summarizer import Summarizer

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

        try:
            summary = await self._ensure_summary(session, call, tenant)
            await NotificationService(self.providers.email, self.settings).send_call_summary(
                tenant=tenant, call=call, summary=summary
            )
        except Exception as exc:  # noqa: BLE001 - classified below
            return await self._handle_failure(session, call.id, exc)

        call.status = CallStatus.NOTIFIED
        call.next_attempt_at = None
        call.last_error = None
        await session.commit()

        logger.info(
            "call summarized and notified",
            extra={"call_id": str(call.id), "tenant_id": str(tenant.id)},
        )
        return CallReport(call.id, "notified", call.status)

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
