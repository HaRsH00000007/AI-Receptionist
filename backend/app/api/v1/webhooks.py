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
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from app.api.deps import SettingsDep
from app.core.correlation import get_correlation_id, new_correlation_id
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.models.enums import WebhookProvider, WebhookStatus
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
