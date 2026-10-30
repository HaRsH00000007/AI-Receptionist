"""The booking tools a receptionist is given when its business has a calendar.

Two ElevenLabs *webhook tools* — ``check_availability`` and
``book_appointment`` — that the voice agent calls mid-conversation, and that
land on :mod:`app.api.v1.voice_tools`. This module decides whether an agent
should have them, describes them, and keeps the vendor's copies in step with
that decision on every push.

**Each business gets its own pair.** The tool's URL names the tenant, and the
tool carries a credential that is valid for that tenant only: an HMAC of the
tenant id under ``AUTH_SECRET_KEY``, checked in constant time by the endpoint.
Nothing the model says can change which business a tool books for — the model
fills in the date and the caller's name, never the URL or the header — and a
credential lifted from one agent opens nobody else's calendar.

**The decision is made at push time, from our database.** An agent has the
tools exactly when booking is switched on for this deployment, the agent is the
business's own (a shared vertical agent cannot carry one tenant's credential),
and the business has a calendar connected that this server can use. Connecting
or disconnecting a calendar re-pushes the agent, so the tools follow the
connection rather than waiting for the next time the owner edits something.

**The instructions travel with the tools.** The booking section of the prompt is
appended at push time rather than written into the versioned configuration, so
an agent can never be told to book without being able to, or be able to without
being told how.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.service import IntegrationService
from app.models import Agent, Tenant
from app.providers.models import ToolParameter, WebhookTool
from app.providers.protocols import ElevenLabsProvider

logger = get_logger(__name__)

#: The header the vendor sends the per-tenant credential in.
TOKEN_HEADER = "X-Receptionist-Tool-Token"

CHECK_AVAILABILITY = "check_availability"
BOOK_APPOINTMENT = "book_appointment"
TOOL_NAMES: tuple[str, ...] = (CHECK_AVAILABILITY, BOOK_APPOINTMENT)

#: Vendor-filled body fields. ElevenLabs' own system variables, so the model
#: can neither see nor invent them.
_CONVERSATION_ID = ToolParameter(name="conversation_id", dynamic_variable="system__conversation_id")
_CALLER_ID = ToolParameter(name="caller_id", dynamic_variable="system__caller_id")


# ---------------------------------------------------------------------------
# The credential
# ---------------------------------------------------------------------------


def tool_token(settings: Settings, tenant_id: uuid.UUID) -> str:
    """The credential a tenant's tools present. Derived, so nothing is stored.

    Domain-separated from every other use of ``AUTH_SECRET_KEY`` by its prefix.
    Rotating that key changes every token; the next push of each agent writes
    the new one into its tools.
    """
    key = settings.auth_secret_key.get_secret_value().encode()
    message = f"voice-tools:v1:{tenant_id}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_tool_token(settings: Settings, tenant_id: uuid.UUID, presented: str | None) -> bool:
    if not presented or not settings.auth_secret_key.get_secret_value():
        return False
    return hmac.compare_digest(presented, tool_token(settings, tenant_id))


# ---------------------------------------------------------------------------
# What the tools look like
# ---------------------------------------------------------------------------


def _tool_url(settings: Settings, tenant_id: uuid.UUID, action: str) -> str:
    base = settings.public_api_url.rstrip("/")
    return f"{base}{settings.api_v1_prefix}/voice-tools/{tenant_id}/calendar/{action}"


def tool_specs(settings: Settings, tenant: Tenant) -> tuple[WebhookTool, ...]:
    headers = {TOKEN_HEADER: tool_token(settings, tenant.id)}
    minutes = settings.booking_slot_minutes
    return (
        WebhookTool(
            name=CHECK_AVAILABILITY,
            description=(
                "Look up free appointment times in the business's calendar for one "
                "day. Call it whenever a caller wants to book, before offering any "
                "time. The result includes today's date in the business's timezone."
            ),
            url=_tool_url(settings, tenant.id, "availability"),
            headers=headers,
            parameters=(
                ToolParameter(
                    name="date",
                    required=True,
                    description=(
                        "The day the caller asked about, as YYYY-MM-DD in the "
                        "business's local time."
                    ),
                ),
                ToolParameter(
                    name="time",
                    description=(
                        "A specific time the caller asked for, as HH:MM in 24-hour "
                        "local time. Leave empty if they did not name one."
                    ),
                ),
                _CONVERSATION_ID,
            ),
        ),
        WebhookTool(
            name=BOOK_APPOINTMENT,
            description=(
                f"Book a {minutes}-minute appointment in the business's calendar. "
                "Call it once, only after check_availability offered the time and "
                "the caller confirmed the day, time and their name."
            ),
            url=_tool_url(settings, tenant.id, "book"),
            headers=headers,
            parameters=(
                ToolParameter(
                    name="date",
                    required=True,
                    description="The appointment day, as YYYY-MM-DD in local time.",
                ),
                ToolParameter(
                    name="time",
                    required=True,
                    description="The start time, as HH:MM in 24-hour local time.",
                ),
                ToolParameter(
                    name="caller_name",
                    required=True,
                    description="The caller's full name, as they gave it.",
                ),
                ToolParameter(
                    name="caller_phone",
                    description=(
                        "A callback number, only if the caller gave one different "
                        "from the number they are calling from."
                    ),
                ),
                ToolParameter(
                    name="caller_email",
                    description="The caller's email address, if they gave one.",
                ),
                ToolParameter(
                    name="reason",
                    description="What the appointment is for, in a few words.",
                ),
                _CALLER_ID,
                _CONVERSATION_ID,
            ),
        ),
    )


def booking_instructions(settings: Settings) -> str:
    """The section of the prompt that teaches the agent to use the tools."""
    minutes = settings.booking_slot_minutes
    return f"""

## Booking appointments

You can book appointments directly into the business's calendar. These
instructions take precedence over anything above that says you cannot make
bookings.

- When a caller wants an appointment, ask which day and time suit them and what
  it is for. Then call `check_availability` with the date, and the time if they
  named one. Every result tells you today's date in the business's timezone; use
  it to work out dates such as "next Tuesday", and call `check_availability`
  again if you are unsure which day the caller means.
- Only offer times that `check_availability` returned. Never guess or invent
  availability. Appointments are {minutes} minutes long.
- Before booking, get the caller's full name. The number they are calling from is
  used to reach them unless they give another one. Read the day and time back and
  wait for a clear yes.
- Then call `book_appointment` once. Tell the caller they are booked only when
  the result is `booked` or `already_booked`, and repeat the day and time.
- If the result is `slot_taken`, apologize and offer the alternatives it gives.
  For any other result, do not promise a time: follow the message in the result,
  and offer to take a message so the team can call back to arrange it.
"""


# ---------------------------------------------------------------------------
# Keeping the vendor in step
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolPlan:
    """What one push should attach, and what it leaves behind to clean up."""

    tool_ids: tuple[str, ...] = ()
    prompt_addendum: str = ""
    #: The new ``agents.vendor_tools`` value, written once the push succeeds.
    record: dict[str, Any] | None = None
    #: Tools the agent had and must no longer: deleted *after* the agent stops
    #: referencing them, so the vendor never holds an agent pointing at nothing.
    stale_ids: tuple[str, ...] = field(default_factory=tuple)


def _fingerprint(tools: tuple[WebhookTool, ...]) -> str:
    """A digest of exactly what the tools were written with, credential included.

    Stored, so it must not let anyone recover the credential: a SHA-256 over the
    whole payload does not.
    """
    payload = json.dumps(
        [tool.model_dump(mode="json") for tool in tools], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def booking_supported(settings: Settings, agent: Agent) -> bool:
    """Whether this deployment and this agent can carry booking tools at all."""
    return (
        settings.calendar_booking_enabled
        and not agent.is_shared
        and bool(settings.auth_secret_key.get_secret_value())
        and bool(settings.public_api_url)
    )


async def plan_tools(
    session: AsyncSession,
    *,
    settings: Settings,
    voice: ElevenLabsProvider,
    tenant: Tenant,
    agent: Agent,
) -> ToolPlan:
    """Make sure the tools this agent should have exist at the vendor.

    Creates or rewrites tools as needed, and returns the ids to attach. It does
    *not* touch the agent: the caller pushes the agent with these ids, then
    calls :func:`finish_tools`.
    """
    current = agent.vendor_tools or {}
    known: dict[str, str] = {
        name: str(tool_id)
        for name, tool_id in (current.get("ids") or {}).items()
        if isinstance(tool_id, str) and tool_id
    }

    wanted = booking_supported(settings, agent) and (
        await IntegrationService(session, settings).booking_calendar(tenant) is not None
    )
    if not wanted:
        return ToolPlan(stale_ids=tuple(known.values()))

    tools = tool_specs(settings, tenant)
    fingerprint = _fingerprint(tools)
    if current.get("fingerprint") == fingerprint and set(known) == set(TOOL_NAMES):
        ids = {name: known[name] for name in TOOL_NAMES}
    else:
        ids = {}
        for tool in tools:
            ids[tool.name] = await voice.upsert_webhook_tool(
                tool_id=known.get(tool.name), tool=tool
            )
    return ToolPlan(
        tool_ids=tuple(ids[name] for name in TOOL_NAMES),
        prompt_addendum=booking_instructions(settings),
        record={"ids": ids, "fingerprint": fingerprint},
        # A tool recreated under a new id leaves its old id behind.
        stale_ids=tuple(tool_id for name, tool_id in known.items() if ids.get(name) != tool_id),
    )


async def finish_tools(voice: ElevenLabsProvider, agent: Agent, plan: ToolPlan) -> None:
    """Record what the agent now has, and delete what it no longer references.

    Deletion is best effort: an orphaned tool references a URL that refuses
    every request without a valid credential, so a failure here costs tidiness,
    not safety, and must not fail a push that already succeeded.
    """
    agent.vendor_tools = plan.record
    for tool_id in plan.stale_ids:
        try:
            await voice.delete_tool(tool_id=tool_id)
        except Exception:  # noqa: BLE001 - best effort, see the docstring
            logger.warning(
                "could not delete a booking tool the agent no longer uses",
                extra={"tenant_id": str(agent.tenant_id), "tool_id": tool_id},
            )
