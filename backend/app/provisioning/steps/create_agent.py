"""Step 4 — create the ElevenLabs agent.

One agent per tenant, which is the POC shape and is known technical debt
(docs/00_DECISIONS.md section 2). What keeps it survivable is that the agent is
a *projection*: the prompt and voice come from the live ``agent_configs`` row, and
``agent_config_id`` records which version the vendor object reflects. Nothing here
reads a prompt back from ElevenLabs and treats it as truth.

Idempotent by adoption on the agent name, which is ``tenant:{id}``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.core.errors import TerminalError
from app.core.logging import get_logger
from app.models import Agent, AgentConfig
from app.models.enums import AgentStatus
from app.provisioning.context import StepContext, StepResult
from app.services.idempotency import tenant_resource_name

logger = get_logger(__name__)


async def live_config(ctx: StepContext) -> AgentConfig:
    config = (
        await ctx.session.execute(
            select(AgentConfig)
            .where(AgentConfig.tenant_id == ctx.tenant.id)
            .where(AgentConfig.is_live.is_(True))
        )
    ).scalar_one_or_none()
    if config is None:
        # Terminal: the previous step is supposed to guarantee this, so its
        # absence is a bug, not a blip, and retrying would only hide it.
        raise TerminalError(
            "no live agent config for tenant",
            code="missing_live_config",
            details={"tenant_id": str(ctx.tenant.id)},
        )
    return config


async def run(ctx: StepContext) -> StepResult:
    tenant = ctx.tenant
    config = await live_config(ctx)
    agent_name = tenant_resource_name(tenant.id)

    existing = (
        await ctx.session.execute(
            select(Agent)
            .where(Agent.tenant_id == tenant.id)
            .where(Agent.status == AgentStatus.ACTIVE)
        )
    ).scalar_one_or_none()
    if existing is not None and existing.elevenlabs_agent_id:
        return StepResult(
            request={"tenant_id": str(tenant.id)},
            response={"adopted": "database", "agent_id": existing.elevenlabs_agent_id},
        )

    # The vendor may already hold an agent from an attempt that died before it
    # could be recorded. Adopt and re-push our config rather than create a twin.
    adopted = await ctx.providers.elevenlabs.find_agent_by_name(name=agent_name)
    if adopted is not None:
        ref = await ctx.providers.elevenlabs.update_agent(
            agent_id=adopted.agent_id,
            name=agent_name,
            system_prompt=config.system_prompt,
            first_message=config.first_message,
            voice_id=config.voice_id or ctx.settings.elevenlabs_default_voice_id,
        )
        source = "vendor"
    else:
        ref = await ctx.providers.elevenlabs.create_agent(
            name=agent_name,
            system_prompt=config.system_prompt,
            first_message=config.first_message,
            voice_id=config.voice_id or ctx.settings.elevenlabs_default_voice_id,
        )
        source = "created"

    record = existing or Agent(tenant_id=tenant.id, agent_config_id=config.id)
    record.agent_config_id = config.id
    record.elevenlabs_agent_id = ref.agent_id
    record.status = AgentStatus.ACTIVE
    record.synced_at = datetime.now(UTC)
    if existing is None:
        ctx.session.add(record)
    await ctx.session.flush()

    logger.info(
        "agent ready",
        extra={"tenant_id": str(tenant.id), "agent_id": ref.agent_id, "source": source},
    )
    return StepResult(
        request={
            "name": agent_name,
            "voice_id": config.voice_id,
            "config_version": config.version,
        },
        response={"agent_id": ref.agent_id, "source": source, "agent_row_id": str(record.id)},
    )
