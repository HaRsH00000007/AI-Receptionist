"""What should happen to an inbound call.

The decision is a pure function over a small resolved input, separate from the
HTTP handler and from every database call. That separation is the point: this is
the one piece of logic a customer *hears*, every branch of it is a different
experience for a real person on a phone, and branches buried inside a request
handler get tested by whichever one the happy path happens to take.

The ordering below is a priority list, and the rule behind it is that **a caller
must never hear silence**. Every path ends in either a working agent, a spoken
explanation, or a voicemail. There is no path that answers and says nothing, and
no path that drops the line on a number a business is advertising.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass

from app.models.enums import TenantStatus


class CallDisposition(enum.StrEnum):
    """How a call is to be handled."""

    #: Hand the media stream to the voice agent. The intended path.
    CONNECT_AGENT = "connect_agent"
    #: The agent is unreachable or unconfigured: explain, then take a message.
    VOICEMAIL = "voicemail"
    #: We own the number but cannot serve it: explain, then hang up.
    UNAVAILABLE = "unavailable"
    #: The number resolves to no tenant. Refuse without answering.
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class RoutingInput:
    """Everything the decision needs, already resolved.

    Deliberately flat and vendor-free. Nothing here is an ORM object, so the
    decision cannot lazily trigger a query on the call path, and a test can
    construct any situation directly.
    """

    dialled_e164: str | None
    tenant_id: uuid.UUID | None
    tenant_status: TenantStatus | None
    agent_id: str | None
    business_name: str | None


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    disposition: CallDisposition
    tenant_id: uuid.UUID | None = None
    agent_id: str | None = None
    #: What the caller is told. Empty for CONNECT_AGENT, where the agent speaks.
    message: str = ""
    #: A stable code for logs and metrics — never shown to a caller.
    reason: str = ""


def decide(routing: RoutingInput) -> RoutingDecision:
    """Choose a disposition for one inbound call."""
    business = routing.business_name or "this business"

    # 1. A number nobody owns. This is the only case where not answering is
    #    right: answering costs a billed minute and confirms to a scanner that
    #    the line is live.
    if routing.tenant_id is None or routing.dialled_e164 is None:
        return RoutingDecision(
            disposition=CallDisposition.REJECT,
            reason="unknown_number",
        )

    # 2. A tenant who is no longer a customer. Say so and stop; a voicemail
    #    nobody will read is worse than a clear ending.
    if routing.tenant_status in (TenantStatus.CANCELLED, TenantStatus.ABANDONED):
        return RoutingDecision(
            disposition=CallDisposition.UNAVAILABLE,
            tenant_id=routing.tenant_id,
            message=(
                f"Thank you for calling {business}. This number is no longer in service. Goodbye."
            ),
            reason=f"tenant_{routing.tenant_status.value}",
        )

    # 3. Provisioning has not finished, or the agent was never created. The
    #    number is already live and may already be advertised, so the call is
    #    answered and a message taken rather than dropped.
    if not routing.agent_id:
        return RoutingDecision(
            disposition=CallDisposition.VOICEMAIL,
            tenant_id=routing.tenant_id,
            message=(
                f"Thank you for calling {business}. "
                "Our receptionist is not available right now. "
                "Please leave a message after the tone and we will get back to you."
            ),
            reason="no_agent",
        )

    # 4. Pending is not an error — the number can be dialled before the welcome
    #    email lands. With an agent present, serve the call normally.
    if routing.tenant_status in (TenantStatus.ACTIVE, TenantStatus.PENDING):
        return RoutingDecision(
            disposition=CallDisposition.CONNECT_AGENT,
            tenant_id=routing.tenant_id,
            agent_id=routing.agent_id,
            reason="ok",
        )

    # 5. Anything else — a FAILED tenant that nonetheless has an agent. Take a
    #    message: the business's caller is not the right person to absorb our
    #    provisioning problem.
    return RoutingDecision(
        disposition=CallDisposition.VOICEMAIL,
        tenant_id=routing.tenant_id,
        message=(
            f"Thank you for calling {business}. "
            "We cannot take your call automatically right now. "
            "Please leave a message after the tone."
        ),
        reason=f"tenant_{routing.tenant_status.value if routing.tenant_status else 'unknown'}",
    )


def vendor_outage_decision(routing: RoutingInput) -> RoutingDecision:
    """The decision to use when the voice vendor is known to be unreachable.

    Separate from :func:`decide` so an outage is an explicit, audited branch
    rather than an exception handler that happens to produce similar TwiML. A
    caller hearing a recorded apology and leaving a message is a degraded
    service; a caller hearing nothing is a lost customer.
    """
    business = routing.business_name or "this business"
    return RoutingDecision(
        disposition=CallDisposition.VOICEMAIL,
        tenant_id=routing.tenant_id,
        message=(
            f"Thank you for calling {business}. "
            "Our automated receptionist is temporarily unavailable. "
            "Please leave a message after the tone and we will return your call."
        ),
        reason="vendor_outage",
    )
