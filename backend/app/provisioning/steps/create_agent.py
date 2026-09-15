"""Step 4 — attach the tenant to a voice agent.

Two topologies, chosen per tenant by ``tenants.agent_mode`` so that a migration
moves customers in verifiable batches rather than flipping everyone at once:

* **per_tenant** (the POC shape) — one ElevenLabs agent per customer, carrying
  that customer's prompt. Idempotent by adoption on the agent name,
  ``tenant:{id}``, so an attempt that died before committing is adopted rather
  than duplicated.
* **shared_vertical** (the production shape) — the tenant is pointed at a
  pre-existing agent shared across its vertical. **No vendor object is created
  or modified.** The tenant's identity reaches the conversation through
  ``/voice/init`` instead, which means a prompt improvement ships once to
  everyone rather than as one vendor call per customer.

In both cases the agent is a *projection*: the prompt and voice come from the
live ``agent_configs`` row, and ``agent_config_id`` records which version the
vendor object reflects. Nothing here reads a prompt back from ElevenLabs and
treats it as truth.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.core.errors import TerminalError
from app.core.logging import get_logger
from app.models import Agent, AgentConfig
from app.models.enums import AgentMode, AgentStatus
from app.provisioning.context import StepContext, StepResult
from app.services.idempotency import tenant_resource_name
from app.services.shared_agents import effective_agent_mode, resolve_shared_agent

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

    mode = effective_agent_mode(ctx.settings, tenant.agent_mode)
    if mode is AgentMode.SHARED_VERTICAL:
        return await _attach_shared(ctx, config)

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
    record.is_shared = False
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


async def _attach_shared(ctx: StepContext, config: AgentConfig) -> StepResult:
    """Point the tenant at its vertical's shared agent.

    Deliberately makes **no vendor call at all** — not create, not update. The
    shared agent already exists and is serving other customers; pushing this
    tenant's prompt onto it would overwrite theirs, which is the single worst
    thing this step could do. Per-tenant behaviour arrives at call time through
    ``/voice/init``.

    ``is_shared`` is recorded on the row so compensation can refuse to delete
    it. That flag is the guard between one tenant's failed provisioning and
    every other tenant on the vertical losing their receptionist.
    """
    tenant = ctx.tenant
    shared = resolve_shared_agent(ctx.settings, tenant.business_type)
    if shared is None:
        # Unreachable via `effective_agent_mode`, which downgrades a tenant with
        # no configured agent. Kept as a terminal guard rather than an assert:
        # attaching a tenant to nothing would silently produce a number that
        # rings into a voicemail forever.
        raise TerminalError(
            "tenant is in shared agent mode but no shared agent is configured",
            code="missing_shared_agent",
            details={"business_type": tenant.business_type.value},
        )

    existing = (
        await ctx.session.execute(
            select(Agent)
            .where(Agent.tenant_id == tenant.id)
            .where(Agent.status == AgentStatus.ACTIVE)
        )
    ).scalar_one_or_none()

    record = existing or Agent(tenant_id=tenant.id, agent_config_id=config.id)
    record.agent_config_id = config.id
    record.elevenlabs_agent_id = shared.agent_id
    record.status = AgentStatus.ACTIVE
    record.is_shared = True
    record.synced_at = datetime.now(UTC)
    if existing is None:
        ctx.session.add(record)
    await ctx.session.flush()

    logger.info(
        "tenant attached to a shared vertical agent",
        extra={
            "tenant_id": str(tenant.id),
            "agent_id": shared.agent_id,
            "vertical": shared.vertical,
        },
    )
    return StepResult(
        request={"vertical": shared.vertical, "config_version": config.version},
        response={
            "agent_id": shared.agent_id,
            "source": "shared",
            "shared": True,
            "agent_row_id": str(record.id),
        },
    )
