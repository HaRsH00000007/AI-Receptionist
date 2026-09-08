"""Step 5 — import the Twilio number into ElevenLabs and assign the agent.

Two vendor calls that must both land, which is why the ElevenLabs phone id is
stored: a retry after the import succeeded but the assign failed finds the id in
our row and only redoes the assign.

This is the step Aman never verified. Verification is deliberately *not* done
here — it is its own step, so that "the assign call returned 200" and "the link
actually resolves" stay separate claims.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.errors import TerminalError
from app.core.logging import get_logger
from app.models import Agent, PhoneNumber
from app.models.enums import AgentStatus, PhoneNumberStatus
from app.provisioning.context import StepContext, StepResult
from app.services.idempotency import tenant_resource_name

logger = get_logger(__name__)


async def active_number(ctx: StepContext) -> PhoneNumber:
    number = (
        await ctx.session.execute(
            select(PhoneNumber)
            .where(PhoneNumber.tenant_id == ctx.tenant.id)
            .where(PhoneNumber.status == PhoneNumberStatus.ACTIVE)
        )
    ).scalar_one_or_none()
    if number is None or not number.twilio_sid:
        raise TerminalError(
            "no active phone number for tenant",
            code="missing_active_number",
            details={"tenant_id": str(ctx.tenant.id)},
        )
    return number


async def active_agent(ctx: StepContext) -> Agent:
    agent = (
        await ctx.session.execute(
            select(Agent)
            .where(Agent.tenant_id == ctx.tenant.id)
            .where(Agent.status == AgentStatus.ACTIVE)
        )
    ).scalar_one_or_none()
    if agent is None or not agent.elevenlabs_agent_id:
        raise TerminalError(
            "no active agent for tenant",
            code="missing_active_agent",
            details={"tenant_id": str(ctx.tenant.id)},
        )
    return agent


async def run(ctx: StepContext) -> StepResult:
    number = await active_number(ctx)
    agent = await active_agent(ctx)
    assert number.twilio_sid is not None  # narrowed by active_number
    assert agent.elevenlabs_agent_id is not None  # narrowed by active_agent

    # Import, unless a previous attempt already did. Re-importing is safe at the
    # vendor, but skipping it keeps the step fast and the audit trail honest.
    phone_id = number.elevenlabs_phone_id
    if phone_id is None:
        existing = await ctx.providers.elevenlabs.find_phone_number(e164=number.e164)
        if existing is not None:
            phone_id = existing.phone_id
            imported = "adopted"
        else:
            ref = await ctx.providers.elevenlabs.import_phone_number(
                e164=number.e164,
                twilio_sid=number.twilio_sid,
                twilio_account_sid=ctx.settings.twilio_account_sid or "dry-run-sid",
                twilio_auth_token=(
                    ctx.settings.twilio_auth_token.get_secret_value() or "dry-run-token"
                ),
                label=tenant_resource_name(ctx.tenant.id),
            )
            phone_id = ref.phone_id
            imported = "imported"
        number.elevenlabs_phone_id = phone_id
        await ctx.session.flush()
    else:
        imported = "already_imported"

    assigned = await ctx.providers.elevenlabs.assign_agent_to_number(
        phone_id=phone_id, agent_id=agent.elevenlabs_agent_id
    )

    logger.info(
        "number linked to agent",
        extra={
            "tenant_id": str(ctx.tenant.id),
            "e164": number.e164,
            "agent_id": agent.elevenlabs_agent_id,
        },
    )
    return StepResult(
        request={
            "e164": number.e164,
            "twilio_sid": number.twilio_sid,
            "agent_id": agent.elevenlabs_agent_id,
        },
        response={
            "phone_id": phone_id,
            "import": imported,
            "assigned_agent_id": assigned.assigned_agent_id,
        },
    )
