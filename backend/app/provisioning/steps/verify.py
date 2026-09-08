"""Step 6 — verify, by reading both objects back.

"The link step was never verified" was the single outstanding item on the system
this replaces, so verification is a real step that makes real calls, not an
assumption inherited from a 200 response.

It asserts three things at the vendor, not in our database:

* the agent still exists;
* the number still exists;
* the number resolves to *our* agent.

A mismatch is retryable — vendor propagation is not always instant — and the run
will not reach ACTIVE until it holds.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.errors import TerminalError, VendorError
from app.core.logging import get_logger
from app.provisioning.context import StepContext, StepResult
from app.provisioning.steps.link_number import active_agent, active_number

logger = get_logger(__name__)


async def run(ctx: StepContext) -> StepResult:
    number = await active_number(ctx)
    agent = await active_agent(ctx)
    expected_agent_id = agent.elevenlabs_agent_id

    if number.elevenlabs_phone_id is None:
        raise TerminalError(
            "number was never imported into elevenlabs",
            code="missing_elevenlabs_phone_id",
            details={"tenant_id": str(ctx.tenant.id)},
        )

    agent_ref = await ctx.providers.elevenlabs.get_agent(agent_id=str(expected_agent_id))
    if agent_ref is None:
        raise VendorError(
            "agent not found at the vendor during verification",
            vendor=ctx.providers.elevenlabs.name,
            retryable=True,
            code="agent_missing",
            details={"agent_id": expected_agent_id},
        )

    phone_ref = await ctx.providers.elevenlabs.find_phone_number(e164=number.e164)
    if phone_ref is None:
        raise VendorError(
            "number not found at the vendor during verification",
            vendor=ctx.providers.elevenlabs.name,
            retryable=True,
            code="phone_missing",
            details={"e164": number.e164},
        )

    if phone_ref.assigned_agent_id != expected_agent_id:
        # The assign call may have returned before the assignment propagated.
        # Retryable, and loud: this is the exact failure that used to ship
        # silently and leave a customer's number ringing into nothing.
        raise VendorError(
            "number is not linked to this tenant's agent",
            vendor=ctx.providers.elevenlabs.name,
            retryable=True,
            code="link_not_resolved",
            details={
                "e164": number.e164,
                "expected_agent_id": expected_agent_id,
                "actual_agent_id": phone_ref.assigned_agent_id,
            },
        )

    agent.synced_at = datetime.now(UTC)
    await ctx.session.flush()

    logger.info(
        "link verified at the vendor",
        extra={"tenant_id": str(ctx.tenant.id), "e164": number.e164},
    )
    return StepResult(
        request={"e164": number.e164, "expected_agent_id": expected_agent_id},
        response={
            "agent_exists": True,
            "phone_exists": True,
            "assigned_agent_id": phone_ref.assigned_agent_id,
            "voice_id": agent_ref.voice_id,
        },
    )
