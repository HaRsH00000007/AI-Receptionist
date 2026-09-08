"""Webhook ingestion.

Store first, act later. Every delivery becomes a ``webhook_events`` row before
anything is interpreted, so a malformed payload or an unknown tenant is
*recorded* rather than dropped — which is the difference between "we never got
it" and "we got it and here is why it failed".

Deduplication is the unique constraint on ``(provider, event_id)``. Providers
retry by design; the second delivery hits the constraint and is reported as a
duplicate, so it cannot produce a second call record, a second summary or a
second email.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Call, PhoneNumber, Tenant, WebhookEvent
from app.models.enums import (
    CallDirection,
    CallStatus,
    PhoneNumberStatus,
    WebhookProvider,
    WebhookStatus,
)
from app.providers.models import PostCallEvent

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IngestResult:
    event_id: uuid.UUID | None
    status: WebhookStatus
    call_id: uuid.UUID | None = None
    detail: str | None = None

    @property
    def duplicate(self) -> bool:
        return self.status is WebhookStatus.DUPLICATE


def derive_event_id(provider: WebhookProvider, payload: dict[str, Any], body: bytes) -> str:
    """The provider's event id, or a hash of the body.

    Falling back to a content hash keeps deduplication working for providers
    that do not send an id: an identical redelivery hashes the same.
    """
    # ElevenLabs nests the conversation id under `data`, so both levels are
    # searched. Without this every post-call delivery would fall back to a body
    # hash, and a redelivery that differed by a single byte would be recorded as
    # a new event (the provider_call_id guard still stops a duplicate call, but
    # the audit trail would be misleading).
    nested = payload.get("data")
    sources = [payload, nested] if isinstance(nested, dict) else [payload]
    for source in sources:
        for key in ("event_id", "conversation_id", "CallSid", "call_sid", "id"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return f"{key}:{value}"
    return "sha256:" + hashlib.sha256(body).hexdigest()


class WebhookService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        provider: WebhookProvider,
        event_type: str,
        event_id: str,
        payload: dict[str, Any],
        signature_valid: bool,
        correlation_id: str,
    ) -> WebhookEvent | None:
        """Persist the delivery, or return ``None`` if it is a duplicate."""
        event = WebhookEvent(
            provider=provider,
            event_id=event_id,
            event_type=event_type,
            payload_json=payload,
            signature_valid=signature_valid,
            correlation_id=correlation_id,
            status=WebhookStatus.RECEIVED,
        )
        self.session.add(event)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            logger.info(
                "duplicate webhook delivery ignored",
                extra={"provider": provider.value, "event_id": event_id},
            )
            return None
        return event

    async def reject(self, event: WebhookEvent, reason: str) -> IngestResult:
        event.status = WebhookStatus.REJECTED
        event.error = reason
        event.processed_at = datetime.now(UTC)
        await self.session.commit()
        return IngestResult(event.id, WebhookStatus.REJECTED, detail=reason)

    # ---- post-call -------------------------------------------------------
    async def ingest_post_call(self, event: WebhookEvent, parsed: PostCallEvent) -> IngestResult:
        """Create the call record. Summarization happens in the worker."""
        tenant = await self._tenant_for(parsed)
        if tenant is None:
            return await self.reject(
                event,
                f"no tenant owns the called number {parsed.called_number or '(missing)'}",
            )

        existing = (
            await self.session.execute(
                select(Call).where(Call.provider_call_id == parsed.provider_call_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # A different event id for a call we already hold. Still a duplicate
            # in the sense that matters: do not make a second record.
            event.status = WebhookStatus.DUPLICATE
            event.tenant_id = tenant.id
            event.processed_at = datetime.now(UTC)
            await self.session.commit()
            return IngestResult(event.id, WebhookStatus.DUPLICATE, call_id=existing.id)

        call = Call(
            tenant_id=tenant.id,
            provider_call_id=parsed.provider_call_id,
            direction=CallDirection.INBOUND,
            from_e164=parsed.caller_number,
            to_e164=parsed.called_number,
            started_at=(
                datetime.fromtimestamp(parsed.started_at_epoch, tz=UTC)
                if parsed.started_at_epoch
                else datetime.now(UTC)
            ),
            duration_s=parsed.duration_s,
            # Only the turns, only as text. The raw payload carries far more
            # than the POC needs, and a transcript is sensitive.
            transcript_json={"text": parsed.transcript_text(), "turns": parsed.transcript},
            status=CallStatus.RECEIVED,
        )
        self.session.add(call)

        event.status = WebhookStatus.PROCESSED
        event.tenant_id = tenant.id
        event.processed_at = datetime.now(UTC)

        try:
            await self.session.commit()
        except IntegrityError:
            # Two deliveries raced; the unique index on provider_call_id decided.
            await self.session.rollback()
            logger.info(
                "call already created by a concurrent delivery",
                extra={"provider_call_id": parsed.provider_call_id},
            )
            return IngestResult(event.id, WebhookStatus.DUPLICATE)

        logger.info(
            "call recorded",
            extra={
                "call_id": str(call.id),
                "tenant_id": str(tenant.id),
                "duration_s": parsed.duration_s,
            },
        )
        return IngestResult(event.id, WebhookStatus.PROCESSED, call_id=call.id)

    async def _tenant_for(self, parsed: PostCallEvent) -> Tenant | None:
        """Resolve the tenant from the dialled number.

        The number is ours and is unique among active rows, which makes this an
        exact lookup rather than a guess.
        """
        if not parsed.called_number:
            return None
        result = await self.session.execute(
            select(Tenant)
            .join(PhoneNumber, PhoneNumber.tenant_id == Tenant.id)
            .where(PhoneNumber.e164 == parsed.called_number)
            .where(PhoneNumber.status == PhoneNumberStatus.ACTIVE)
            .limit(1)
        )
        return result.scalar_one_or_none()


def parse_post_call(payload: dict[str, Any]) -> PostCallEvent | None:
    """Map ElevenLabs' post-call payload onto our narrow event shape.

    Returns ``None`` when the payload has no usable call id — malformed input is
    reported as a rejection, never allowed to raise into the request handler.
    """
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None

    conversation_id = data.get("conversation_id") or payload.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        return None

    raw_metadata = data.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_phone = metadata.get("phone_call")
    phone_call: dict[str, Any] = raw_phone if isinstance(raw_phone, dict) else {}
    transcript = data.get("transcript")

    return PostCallEvent(
        provider_call_id=conversation_id,
        agent_id=data.get("agent_id"),
        called_number=phone_call.get("agent_number") or metadata.get("called_number"),
        caller_number=phone_call.get("external_number") or metadata.get("caller_number"),
        started_at_epoch=metadata.get("start_time_unix_secs"),
        duration_s=metadata.get("call_duration_secs"),
        transcript=transcript if isinstance(transcript, list) else [],
    )
