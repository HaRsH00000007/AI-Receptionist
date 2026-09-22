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

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.models import Agent, AgentConfig, BusinessProfile, Tenant
from app.models.identity import User
from app.providers.protocols import ElevenLabsProvider, LLMProvider
from app.services.config_generator import ConfigGenerator, GenerationOutcome
from app.services.config_versions import ConfigVersionService, PublishedConfig
from app.services.idempotency import tenant_resource_name
from app.services.prompt_renderer import (
    render_first_message,
    render_system_prompt,
    select_voice_id,
)

logger = get_logger(__name__)


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
    )
    voice_id = select_voice_id(settings, profile.greeting_style)

    published = await ConfigVersionService(session).publish(
        tenant_id=tenant.id,
        system_prompt=system_prompt,
        first_message=render_first_message(outcome.config),
        voice_id=voice_id,
        model_params={
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
    settings: Settings,
    voice: ElevenLabsProvider,
    tenant: Tenant,
    agent: Agent,
    config: AgentConfig,
) -> None:
    """Overwrite the vendor's copy of a per-tenant agent with ``config``.

    Always safe to run: it copies our truth onto the projection, never the other
    way round. Callers must not pass a shared vertical agent — pushing one
    tenant's prompt there would overwrite the prompt every other tenant on that
    vertical is served by. The check lives in the callers, because each has its
    own right answer: the admin action refuses, the customer save skips.
    """
    assert agent.elevenlabs_agent_id, "push_config_to_agent needs a vendor agent id"
    assert not agent.is_shared, "never push one tenant's config onto a shared agent"

    await voice.update_agent(
        agent_id=agent.elevenlabs_agent_id,
        name=tenant_resource_name(tenant.id),
        system_prompt=config.system_prompt,
        first_message=config.first_message,
        voice_id=config.voice_id or settings.elevenlabs_default_voice_id,
    )
    agent.agent_config_id = config.id
    agent.synced_at = datetime.now(UTC)
    logger.info(
        "agent synced to config",
        extra={"tenant_id": str(tenant.id), "config_version": config.version},
    )
