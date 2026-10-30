"""Turning a business profile into a live configuration, and pushing it out.

Two callers generate a configuration: the provisioning step that writes the
first one, and the customer's own "save" on the Agent page that writes every
one after it. One function serves both so that an edited receptionist is built
exactly the way a new one is — same generator, same prompt rendering, same voice
selection, same model-parameter record. A second copy would drift, and the drift
would show up as "my receptionist changed personality when I fixed my hours".

Pushing to the vendor is separate from publishing, as everywhere else here: the
database is the source of truth and the vendor holds a projection of it
(docs/00_DECISIONS.md section 2). A publish that succeeded with a vendor that
was down is a successful publish with a stale projection, not a failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.models import Agent, AgentConfig, BusinessProfile, Tenant
from app.models.enums import AgentStatus
from app.models.greeting_rule import GreetingRule
from app.models.identity import User
from app.models.transfer_rule import TransferRule
from app.providers.models import Delivery
from app.providers.protocols import ElevenLabsProvider, LLMProvider
from app.services.calendar_tools import finish_tools, plan_tools
from app.services.config_generator import (
    ConfigGenerator,
    GenerationOutcome,
    enabled_question_labels,
)
from app.services.config_versions import ConfigVersionService, PublishedConfig
from app.services.greeting_schedule import PARAMS_KEY as GREETING_KEY
from app.services.greeting_schedule import (
    active_greeting,
    dump_schedule,
    schedule_for,
    schedule_of_config,
)
from app.services.idempotency import tenant_resource_name
from app.services.prompt_renderer import (
    render_first_message,
    render_system_prompt,
    select_voice_id,
)
from app.services.transfer_targets import (
    PARAMS_KEY,
    dump_targets,
    targets_for,
    targets_of_config,
)

logger = get_logger(__name__)

#: Where the delivery settings live inside ``model_params_json``.
DELIVERY_KEY = "delivery"


def delivery_of_config(model_params: dict[str, object] | None) -> Delivery:
    """The speed and eagerness a published configuration carries.

    Read defensively: this parses a JSONB column that may have been written by
    an older release, and a bad value should cost the vendor's default rather
    than the push. The vendor's own range is 0.7 to 1.2, so anything outside it
    is dropped — it would be a 422 on every push from then on.
    """
    raw = (model_params or {}).get(DELIVERY_KEY)
    if not isinstance(raw, dict):
        return Delivery()
    speed: float | None = None
    try:
        value = raw.get("speed")
        if value is not None and 0.7 <= float(value) <= 1.2:
            speed = float(value)
    except (TypeError, ValueError):
        logger.warning("ignoring an unreadable speaking speed on a published config")
    eagerness = raw.get("eagerness")
    return Delivery(
        speed=speed,
        eagerness=eagerness if eagerness in {"patient", "normal", "eager"} else None,
    )


@dataclass(frozen=True, slots=True)
class GeneratedAndPublished:
    published: PublishedConfig
    outcome: GenerationOutcome
    voice_id: str
    system_prompt_chars: int


async def generate_and_publish(
    session: AsyncSession,
    *,
    settings: Settings,
    llm: LLMProvider,
    tenant: Tenant,
    profile: BusinessProfile,
    actor: User | None = None,
) -> GeneratedAndPublished:
    """Generate a configuration from the profile and make it the live version.

    Append-only: the previous live row steps down rather than being edited, so
    the history of what was live stays intact. Does not commit.
    """
    outcome = await ConfigGenerator(llm, settings).generate(tenant, profile)

    system_prompt = render_system_prompt(
        business_name=tenant.name,
        business_type=tenant.business_type.value,
        config=outcome.config,
        collect=enabled_question_labels(profile.caller_questions_json),
        closing=profile.goodbye_custom or "",
    )
    # An explicit choice wins over the greeting-style default.
    #
    # Taken as stored, and deliberately *not* re-checked against
    # ``ELEVENLABS_VOICE_MAP``: that map holds three style defaults, while the
    # picker offers the account's whole catalogue
    # (:mod:`app.services.voice_catalogue`). Re-checking against it would have
    # accepted a customer's choice of "Eric" on the way in and then silently
    # reset them to the style voice on their next unrelated edit, which is a
    # worse failure than the one the check was written to prevent.
    #
    # The gate is at write time, where the catalogue is actually readable. If a
    # voice is later withdrawn from the account the vendor refuses the push and
    # the save reports it, rather than the receptionist changing voice without
    # anybody being told.
    voice_id = profile.voice_id or select_voice_id(settings, profile.greeting_style)

    # Carried, not re-derived from anything the generator said: the transfer
    # destinations are the business's own rows, and a version that forgot them
    # would publish an agent that stops forwarding the moment the owner edits
    # their opening hours.
    rules = list(
        (
            await session.execute(
                select(TransferRule)
                .where(TransferRule.tenant_id == tenant.id)
                .order_by(TransferRule.position, TransferRule.created_at)
            )
        )
        .scalars()
        .all()
    )

    # Same reasoning for the greeting schedule: a republished version that
    # dropped it would have the worker reset every tenant's opening line back
    # to the default on its next pass, in the middle of their lunch hour.
    greetings = list(
        (
            await session.execute(
                select(GreetingRule)
                .where(GreetingRule.tenant_id == tenant.id)
                .order_by(GreetingRule.position, GreetingRule.created_at)
            )
        )
        .scalars()
        .all()
    )

    published = await ConfigVersionService(session).publish(
        tenant_id=tenant.id,
        system_prompt=system_prompt,
        first_message=render_first_message(outcome.config),
        voice_id=voice_id,
        model_params={
            PARAMS_KEY: dump_targets(targets_for(rules)),
            GREETING_KEY: dump_schedule(schedule_for(greetings)),
            # On the version, like the transfers and the greeting schedule, so
            # that rolling back to a version restores how it *sounded* and not
            # only what it said.
            DELIVERY_KEY: {
                "speed": profile.speaking_speed,
                "eagerness": (profile.responsiveness.eagerness if profile.responsiveness else None),
            },
            "generated": outcome.config.model_dump(mode="json"),
            # Recorded so a config can be reproduced: which model produced it,
            # at what sampling temperature, against which prompt template.
            "temperature": settings.llm_temperature,
            "model": settings.llm_config_model,
            "provider": llm.name,
        },
        generated_by=outcome.source,
        generator_detail=outcome.detail,
        template_version=outcome.template_version,
        actor=actor,
    )
    return GeneratedAndPublished(
        published=published,
        outcome=outcome,
        voice_id=voice_id,
        system_prompt_chars=len(system_prompt),
    )


async def push_config_to_agent(
    *,
    session: AsyncSession,
    settings: Settings,
    voice: ElevenLabsProvider,
    tenant: Tenant,
    agent: Agent,
    config: AgentConfig,
    moment: datetime | None = None,
) -> None:
    """Overwrite the vendor's copy of a per-tenant agent with ``config``.

    Always safe to run: it copies our truth onto the projection, never the other
    way round. Callers must not pass a shared vertical agent — pushing one
    tenant's prompt there would overwrite the prompt every other tenant on that
    vertical is served by. The check lives in the callers, because each has its
    own right answer: the admin action refuses, the customer save skips.

    The opening line pushed is the one in force *now*, which is the default
    unless a scheduled greeting is running (:mod:`app.services.greeting_schedule`).
    Resolved here, in the one place every caller already goes through, rather
    than in each of them: a push that sent ``config.first_message`` verbatim
    would silently cancel a tenant's lunch-hour greeting every time they edited
    anything else. What was sent is recorded on the agent so the worker's sweep
    can tell whether the projection still matches the clock.

    The booking tools are resolved here for the same reason
    (:mod:`app.services.calendar_tools`): the push is the complete set of tools,
    so a caller that skipped this step would strip a connected business's
    receptionist of its ability to book. ``session`` is what reads whether the
    business has a calendar.
    """
    assert agent.elevenlabs_agent_id, "push_config_to_agent needs a vendor agent id"
    assert not agent.is_shared, "never push one tenant's config onto a shared agent"

    greeting, scheduled = active_greeting(
        schedule_of_config(config.model_params_json),
        moment=moment or datetime.now(UTC),
        timezone=tenant.timezone,
        default=config.first_message,
    )

    tools = await plan_tools(session, settings=settings, voice=voice, tenant=tenant, agent=agent)

    await voice.update_agent(
        agent_id=agent.elevenlabs_agent_id,
        name=tenant_resource_name(tenant.id),
        system_prompt=config.system_prompt + tools.prompt_addendum,
        first_message=greeting,
        voice_id=config.voice_id or settings.elevenlabs_default_voice_id,
        delivery=delivery_of_config(config.model_params_json),
        # From the version being pushed, not from the rules table: pushing a
        # version means making the vendor match *that* version, so a rollback
        # restores the destinations that version was published with rather than
        # whatever the rules happen to say now.
        transfers=targets_of_config(config.model_params_json),
        tool_ids=tools.tool_ids,
    )
    await finish_tools(voice, agent, tools)
    agent.agent_config_id = config.id
    agent.greeting_in_effect = greeting
    agent.synced_at = datetime.now(UTC)
    logger.info(
        "agent synced to config",
        extra={
            "tenant_id": str(tenant.id),
            "config_version": config.version,
            "scheduled_greeting": scheduled is not None,
            "booking_tools": bool(tools.tool_ids),
        },
    )


async def resync_tenant_agent(
    session: AsyncSession,
    *,
    settings: Settings,
    voice: ElevenLabsProvider,
    tenant: Tenant,
) -> bool:
    """Re-push a business's own agent from its live version.

    For changes that alter what the agent *can do* without publishing a new
    version — today, a calendar connected or disconnected, which adds or
    removes the booking tools. Best effort and reported by its return value:
    the change that prompted it has already happened, and the next push of any
    kind carries the tools anyway. Shared agents are skipped, as everywhere.
    """
    agent = (
        await session.execute(
            select(Agent)
            .where(Agent.tenant_id == tenant.id)
            .where(Agent.status == AgentStatus.ACTIVE)
        )
    ).scalar_one_or_none()
    if agent is None or not agent.elevenlabs_agent_id or agent.is_shared:
        return False
    config = (
        await session.execute(
            select(AgentConfig)
            .where(AgentConfig.tenant_id == tenant.id)
            .where(AgentConfig.is_live.is_(True))
        )
    ).scalar_one_or_none()
    if config is None:
        return False
    try:
        await push_config_to_agent(
            session=session,
            settings=settings,
            voice=voice,
            tenant=tenant,
            agent=agent,
            config=config,
        )
    except Exception:
        logger.exception(
            "could not update the voice agent's booking tools",
            extra={"tenant_id": str(tenant.id)},
        )
        return False
    return True
