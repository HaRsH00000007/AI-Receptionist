"""The endpoints a voice agent calls mid-conversation.

Called by ElevenLabs, not by a browser, on behalf of one business whose id is in
the path. Three rules, all following from the caller being a vendor relaying a
language model:

* **The credential is checked before anything is read.** Each business's tools
  carry their own HMAC credential (:mod:`app.services.calendar_tools`); a
  request without the right one for the tenant in its path is refused with 401
  and touches nothing. An unknown tenant looks exactly like a wrong credential.
* **Everything else is a 200 with a result code.** A 4xx is something the model
  has to interpret; ``{"result": "slot_taken", "message": "..."}`` is something
  it can act on. Bad input is ``invalid_request`` with what to ask the caller.
* **Nothing is trusted from the body that the vendor did not fill in.** The
  conversation id and caller number come from the vendor's system variables;
  the date, time and name are checked like any other user input.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.deps import SettingsDep
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.integrations.service import IntegrationService
from app.models import Tenant
from app.models.enums import TenantStatus
from app.services.appointment_booking import (
    AppointmentBooking,
    AvailabilityRequest,
    BookingRequest,
    Result,
    invalid_request,
)
from app.services.calendar_tools import TOKEN_HEADER, verify_tool_token

logger = get_logger(__name__)

router = APIRouter(prefix="/voice-tools", tags=["voice-tools"], include_in_schema=False)

#: Larger than any real tool call; enough to refuse a body that is not one.
_MAX_BODY_BYTES = 16_384


def _transport(request: Request) -> httpx.AsyncBaseTransport | None:
    """A mock transport the test suite may install. Always None in production."""
    return getattr(request.app.state, "integration_transport", None)


def _refused() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"result": "unauthorized", "message": "This tool is not authorized."},
    )


async def _authorized_tenant(
    request: Request, session: SessionDep, settings: Settings, tenant_id: uuid.UUID
) -> Tenant | None:
    if not verify_tool_token(settings, tenant_id, request.headers.get(TOKEN_HEADER)):
        logger.warning("refused a voice tool call with a bad credential")
        return None
    return await session.get(Tenant, tenant_id)


async def _body(request: Request) -> dict[str, Any] | None:
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        return None
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _unavailable_reason(settings: Settings, tenant: Tenant) -> dict[str, Any] | None:
    if not settings.calendar_booking_enabled or tenant.status is not TenantStatus.ACTIVE:
        return {
            "result": Result.CALENDAR_UNAVAILABLE.value,
            "message": "Booking is not available right now. Offer to take a message so "
            "the team can call back to arrange it.",
        }
    return None


_UNREADABLE = {
    "result": Result.INVALID_REQUEST.value,
    "message": "The request could not be read. Try again with the date and time.",
}


@router.post("/{tenant_id}/calendar/availability")
async def check_availability(
    tenant_id: uuid.UUID, request: Request, session: SessionDep, settings: SettingsDep
) -> JSONResponse:
    tenant = await _authorized_tenant(request, session, settings, tenant_id)
    if tenant is None:
        return _refused()
    if (reason := _unavailable_reason(settings, tenant)) is not None:
        return JSONResponse(reason)
    body = await _body(request)
    if body is None:
        return JSONResponse(_UNREADABLE)
    try:
        parsed = AvailabilityRequest.model_validate(body)
    except ValidationError as exc:
        return JSONResponse(invalid_request(exc))
    integrations = IntegrationService(session, settings, transport=_transport(request))
    answer = await AppointmentBooking(session, settings, integrations).check_availability(
        tenant, parsed
    )
    return JSONResponse(answer)


@router.post("/{tenant_id}/calendar/book")
async def book_appointment(
    tenant_id: uuid.UUID, request: Request, session: SessionDep, settings: SettingsDep
) -> JSONResponse:
    tenant = await _authorized_tenant(request, session, settings, tenant_id)
    if tenant is None:
        return _refused()
    if (reason := _unavailable_reason(settings, tenant)) is not None:
        return JSONResponse(reason)
    body = await _body(request)
    if body is None:
        return JSONResponse(_UNREADABLE)
    try:
        parsed = BookingRequest.model_validate(body)
    except ValidationError as exc:
        return JSONResponse(invalid_request(exc))
    integrations = IntegrationService(session, settings, transport=_transport(request))
    answer = await AppointmentBooking(session, settings, integrations).book(tenant, parsed)
    return JSONResponse(answer)
