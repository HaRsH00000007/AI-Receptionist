"""Keeping the vendor's opening line in step with the clock.

A scheduled greeting is the one piece of configuration in this product whose
correct value changes **without anybody doing anything**. At 11:59 the usual
greeting is right; at 12:00 the lunch one is. Nothing published, nothing saved,
nothing clicked — and on the native call path ElevenLabs owns the number and
answers from the agent's own ``first_message``, so there is no request of ours
in the middle to substitute it.

So something has to move it, and that something is the worker: once a tick it
asks "for each business with greeting rules, is the line the vendor is holding
still the right one?" and pushes when it is not. That is why
``agents.greeting_in_effect`` exists — it is the only record of what the
projection currently says, since the configuration version is identical either
way.

Three properties this relies on, all of them deliberate:

* **It is idempotent.** The push happens only when the resolved greeting differs
  from what was last pushed, so a tenant with no rules, or whose rule is not
  running, costs one comparison and no vendor call.
* **It is bounded.** One query selects only tenants that actually have an
  enabled rule; a deployment where nobody uses the feature does one indexed
  ``EXISTS`` per tick and stops.
* **It never raises.** One tenant whose agent is gone at the vendor must not
  stop the sweep from fixing everyone else's, and must not take the worker tick
  down with it.

The TwiML path does not need any of this: there the greeting is a dynamic
variable resolved per call from the live configuration, which carries the
schedule. This sweep is for the path where the vendor answers on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.models import Agent, AgentConfig, GreetingRule, Tenant
from app.models.enums import AgentStatus, TenantStatus
from app.providers.registry import Providers
from app.services.config_publishing import push_config_to_agent
from app.services.greeting_schedule import active_greeting, schedule_of_config

logger = get_logger(__name__)


async def sweep_scheduled_greetings(
    session: AsyncSession,
    settings: Settings,
    providers: Providers,
    *,
    moment: datetime | None = None,
) -> int:
    """Push the greeting that is in force now, where it is not already there.

    Returns how many agents were updated, which is zero on almost every pass.

    Commits after each agent, like the worker's other loops: one business whose
    vendor agent has gone missing must not roll back the switch that already
    worked for the business before it in the list.
    """
    now = moment or datetime.now(UTC)
    rows = (
        await session.execute(
            select(Tenant, Agent, AgentConfig)
            .join(Agent, Agent.tenant_id == Tenant.id)
            .join(AgentConfig, AgentConfig.tenant_id == Tenant.id)
            .where(Tenant.status == TenantStatus.ACTIVE)
            .where(Agent.status == AgentStatus.ACTIVE)
            .where(Agent.is_shared.is_(False))
            .where(Agent.elevenlabs_agent_id.is_not(None))
            .where(AgentConfig.is_live.is_(True))
            # The bound that makes this cheap. A shared agent is excluded above
            # for the same reason the customer-facing save skips it: one agent
            # cannot hold one tenant's opening line.
            .where(
                exists().where(
                    (GreetingRule.tenant_id == Tenant.id) & GreetingRule.enabled.is_(True)
                )
            )
        )
    ).all()

    pushed = 0
    for tenant, agent, config in rows:
        greeting, scheduled = active_greeting(
            schedule_of_config(config.model_params_json),
            moment=now,
            timezone=tenant.timezone,
            default=config.first_message,
        )
        if greeting == agent.greeting_in_effect:
            continue

        try:
            await push_config_to_agent(
                settings=settings,
                voice=providers.elevenlabs,
                tenant=tenant,
                agent=agent,
                config=config,
                moment=now,
            )
        except Exception:
            # Logged and left alone. The next tick tries again, and until it
            # succeeds the vendor keeps answering with the previous line —
            # which is a stale greeting, not a failed call.
            logger.exception(
                "could not switch a scheduled greeting",
                extra={"tenant_id": str(tenant.id), "agent_id": str(agent.id)},
            )
            await session.rollback()
            continue

        await session.commit()
        pushed += 1
        logger.info(
            "scheduled greeting switched",
            extra={
                "tenant_id": str(tenant.id),
                "config_version": config.version,
                # Which way it moved, without putting the business's own words
                # in a log line: into an exception, or back to the usual line.
                "scheduled": scheduled is not None,
            },
        )
    return pushed
