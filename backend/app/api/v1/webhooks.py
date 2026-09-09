"""Webhook endpoints.

These answer quickly and always with a 2xx once the delivery is stored, because
a provider that gets a slow or 5xx response retries — and a retry storm on a
slow endpoint is how duplicate work gets created. The actual summarization and
email happen in the worker.

A rejected delivery (bad signature, unknown tenant, unparseable body) still
returns 200 with a reason, and is recorded. Answering 4xx would make the
provider retry a payload that will never succeed.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from app.api.deps import ProvidersDep, SettingsDep, TemporalClientDep
from app.core.correlation import get_correlation_id, new_correlation_id
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.models import ProvisioningRun
from app.models.enums import ProvisioningStatus, WebhookProvider, WebhookStatus
from app.services.billing import BillingService
from app.services.signatures import public_request_url, verify_elevenlabs, verify_twilio
from app.services.webhooks import WebhookService, derive_event_id, parse_post_call

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookAck(BaseModel):
    """What a provider gets back. Deliberately uninformative to the internet."""

    received: bool
    status: WebhookStatus
    detail: str | None = None


@router.post(
    "/elevenlabs/post-call",
    response_model=WebhookAck,
    status_code=status.HTTP_200_OK,
    summary="ElevenLabs post-call webhook",
)
async def elevenlabs_post_call(
    request: Request, session: SessionDep, settings: SettingsDep
) -> WebhookAck:
    body = await request.body()
    correlation_id = get_correlation_id() or new_correlation_id()

    signature_valid = verify_elevenlabs(
        payload=body,
        header=request.headers.get("elevenlabs-signature", ""),
        secret=settings.elevenlabs_webhook_secret.get_secret_value(),
    )
    if not signature_valid and settings.is_production_like:
        # Refused before anything is parsed or stored against a tenant.
        logger.warning("rejected an unsigned elevenlabs webhook in a production environment")
        return WebhookAck(received=True, status=WebhookStatus.REJECTED, detail="invalid signature")

    try:
        payload: dict[str, Any] = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
    except ValueError:
        logger.warning("elevenlabs webhook body was not valid JSON")
        return WebhookAck(received=True, status=WebhookStatus.REJECTED, detail="malformed payload")

    service = WebhookService(session)
    event = await service.record(
        provider=WebhookProvider.ELEVENLABS,
        event_type=str(payload.get("type", "post_call_transcription")),
        event_id=derive_event_id(WebhookProvider.ELEVENLABS, payload, body),
        payload=payload,
        signature_valid=signature_valid,
        correlation_id=correlation_id,
    )
    if event is None:
        return WebhookAck(received=True, status=WebhookStatus.DUPLICATE)

    parsed = parse_post_call(payload)
    if parsed is None:
        result = await service.reject(event, "payload has no conversation id")
        return WebhookAck(received=True, status=result.status, detail=result.detail)

    result = await service.ingest_post_call(event, parsed)
    return WebhookAck(received=True, status=result.status, detail=result.detail)


@router.post(
    "/twilio/voice-status",
    response_model=WebhookAck,
    status_code=status.HTTP_200_OK,
    summary="Twilio call status callback",
)
async def twilio_voice_status(
    request: Request, session: SessionDep, settings: SettingsDep
) -> WebhookAck:
    """Recorded for the audit trail only.

    ElevenLabs owns the media path in this POC, so Twilio's status callbacks add
    no information the post-call webhook does not already carry. They are stored
    so that a call which never reached ElevenLabs is still visible somewhere.
    """
    # Body first: reading it caches the stream, so the form parser can still
    # read it afterwards. The other order consumes the stream and the second
    # read raises.
    body = await request.body()
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}

    signature_valid = verify_twilio(
        url=public_request_url(
            observed_url=str(request.url),
            path=request.url.path,
            query=request.url.query,
            public_base=settings.public_api_url,
        ),
        params=params,
        header=request.headers.get("x-twilio-signature", ""),
        auth_token=settings.twilio_auth_token.get_secret_value(),
    )
    if not signature_valid and settings.is_production_like:
        logger.warning("rejected an unsigned twilio webhook in a production environment")
        return WebhookAck(received=True, status=WebhookStatus.REJECTED, detail="invalid signature")

    event = await WebhookService(session).record(
        provider=WebhookProvider.TWILIO,
        event_type=params.get("CallStatus", "unknown"),
        event_id=derive_event_id(WebhookProvider.TWILIO, params, body),
        payload=params,
        signature_valid=signature_valid,
        correlation_id=get_correlation_id() or new_correlation_id(),
    )
    if event is None:
        return WebhookAck(received=True, status=WebhookStatus.DUPLICATE)

    event.status = WebhookStatus.PROCESSED
    await session.commit()
    return WebhookAck(received=True, status=WebhookStatus.PROCESSED)


@router.post(
    "/stripe",
    response_model=WebhookAck,
    status_code=status.HTTP_200_OK,
    summary="Stripe billing webhook",
)
async def stripe_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
    temporal: TemporalClientDep,
) -> WebhookAck:
    """Apply a Stripe billing event.

    This endpoint is the only way subscription state changes, which makes
    signature verification a hard requirement rather than a nicety: without it,
    an unauthenticated POST could grant any tenant an active subscription and
    walk straight through the money gate. So unlike the other webhooks here, a
    bad signature is refused **before** anything is stored, in every
    environment — not only production-like ones.

    Everything after verification returns 200. Stripe retries any non-2xx for
    days, and retrying an event we have deliberately ignored, or one that failed
    on a permanent data problem, only produces load.
    """
    body = await request.body()
    correlation_id = get_correlation_id() or new_correlation_id()

    try:
        # Verified against the *raw* bytes. Re-serializing parsed JSON would
        # change the byte sequence and every signature would fail.
        event = providers.payments.verify_webhook(
            payload=body, signature=request.headers.get("stripe-signature", "")
        )
    except AppError as exc:
        # Deliberately not stored: an unverified payload is not evidence of
        # anything, and writing attacker-controlled content into a table the
        # admin panel renders is how a webhook endpoint becomes an injection
        # vector. The rejection is logged with no payload.
        logger.warning(
            "rejected an unverified Stripe webhook",
            extra={"code": exc.code, "correlation_id": correlation_id},
        )
        return WebhookAck(received=True, status=WebhookStatus.REJECTED, detail="invalid signature")

    payload: dict[str, Any] = json.loads(body) if body else {}
    service = WebhookService(session)

    # Stripe's own event id is globally unique and stable across redeliveries,
    # so the unique index on (provider, event_id) is the whole replay defence.
    stored = await service.record(
        provider=WebhookProvider.STRIPE,
        event_type=event.event_type,
        event_id=event.event_id,
        payload=payload,
        signature_valid=True,
        correlation_id=correlation_id,
    )
    if stored is None:
        return WebhookAck(received=True, status=WebhookStatus.DUPLICATE, detail="already processed")

    result = await BillingService(session, settings).apply_event(event.event_type, event.data)

    if result.entitlement_granted and result.tenant_id is not None:
        # The fast path out of BILLING_BLOCKED. A customer who has just paid
        # should see provisioning resume in seconds, not wait for the slow
        # self-heal re-check.
        #
        # Both mechanisms fire. `resume_blocked_runs` re-arms the polling column
        # for the state-machine path; the signal wakes a parked workflow. Which
        # one matters depends on the orchestrator, and doing both keeps the
        # webhook correct under either without branching on configuration.
        #
        # Neither grants anything: the money gate is re-evaluated from the
        # database when the run resumes, so a signal cannot authorize a purchase.
        resumed = await BillingService(session, settings).resume_blocked_runs(result.tenant_id)
        if temporal is not None and resumed:
            await _signal_parked_workflows(temporal, session, result.tenant_id)

    stored.status = WebhookStatus.PROCESSED if result.applied else WebhookStatus.RECEIVED
    logger.info(
        "stripe webhook applied",
        extra={
            "event_type": event.event_type,
            "applied": result.applied,
            "reason": result.reason,
            "tenant_id": str(result.tenant_id) if result.tenant_id else None,
        },
    )
    return WebhookAck(received=True, status=stored.status, detail=result.reason)


async def _signal_parked_workflows(
    temporal: Client,
    session: AsyncSession,
    tenant_id: uuid.UUID,
) -> None:
    """Wake any workflow parked on the money gate for this tenant.

    Best-effort throughout. The workflow re-checks on its own timer anyway, so a
    signal that misses — the workflow finished, never started, or Temporal is
    briefly unreachable — costs latency, not correctness. Failing the webhook
    over it would make Stripe retry an event we have already applied.
    """
    from app.temporal.client import signal_billing_updated

    runs = (
        (
            await session.execute(
                select(ProvisioningRun)
                .where(ProvisioningRun.tenant_id == tenant_id)
                .where(ProvisioningRun.status == ProvisioningStatus.BILLING_BLOCKED)
            )
        )
        .scalars()
        .all()
    )
    for run in runs:
        try:
            await signal_billing_updated(temporal, run.id)
        except Exception:  # noqa: BLE001 - the webhook must still return 2xx
            # Deliberately broad. Anything the Temporal client raises here —
            # a dropped connection, a serialization error, a workflow that has
            # already closed — is less important than acknowledging an event we
            # have already applied. A non-2xx would make Stripe redeliver it.
            logger.warning(
                "could not signal a parked provisioning workflow",
                extra={"run_id": str(run.id), "tenant_id": str(tenant_id)},
            )
