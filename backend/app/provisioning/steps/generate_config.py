"""Step 2 — generate the agent configuration.

Idempotent by adoption: if a live config already exists for this tenant at the
current profile version, the step returns it rather than paying for a second
generation. A retry after a crash therefore costs nothing and produces the same
prompt.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.errors import InvalidInputError
from app.core.logging import get_logger
from app.models import AgentConfig, BusinessProfile
from app.provisioning.context import StepContext, StepResult
from app.services.config_generator import ConfigGenerator
from app.services.config_versions import ConfigVersionService
from app.services.prompt_renderer import (
    render_first_message,
    render_system_prompt,
    select_voice_id,
)

logger = get_logger(__name__)


async def run(ctx: StepContext) -> StepResult:
    tenant = ctx.tenant

    profile = (
        await ctx.session.execute(
            select(BusinessProfile).where(BusinessProfile.tenant_id == tenant.id)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise InvalidInputError(
            "tenant has no business profile", details={"tenant_id": str(tenant.id)}
        )

    existing = (
        await ctx.session.execute(
            select(AgentConfig)
            .where(AgentConfig.tenant_id == tenant.id)
            .where(AgentConfig.is_live.is_(True))
        )
    ).scalar_one_or_none()
    if existing is not None and existing.version >= profile.config_version:
        logger.info(
            "adopting the existing live config",
            extra={"tenant_id": str(tenant.id), "version": existing.version},
        )
        return StepResult(
            request={"tenant_id": str(tenant.id)},
            response={"adopted": True, "config_version": existing.version},
        )

    generator = ConfigGenerator(ctx.providers.llm, ctx.settings)
    outcome = await generator.generate(tenant, profile)

    system_prompt = render_system_prompt(
        business_name=tenant.name,
        business_type=tenant.business_type.value,
        config=outcome.config,
    )
    voice_id = select_voice_id(ctx.settings, profile.greeting_style)

    # Configs are append-only; the previous live row steps down rather than
    # being edited, so the history of what was live stays intact. The publish
    # logic is shared with the admin rollback action and the config editor, so
    # that "demote then promote" exists once rather than three times.
    published = await ConfigVersionService(ctx.session).publish(
        tenant_id=tenant.id,
        system_prompt=system_prompt,
        first_message=render_first_message(outcome.config),
        voice_id=voice_id,
        model_params={
            "generated": outcome.config.model_dump(mode="json"),
            # Recorded so a config can be reproduced: which model produced it,
            # at what sampling temperature, against which prompt template.
            "temperature": ctx.settings.llm_temperature,
            "model": ctx.settings.llm_config_model,
            "provider": ctx.providers.llm.name,
        },
        generated_by=outcome.source,
        generator_detail=outcome.detail,
        template_version=outcome.template_version,
    )
    config = published.config

    return StepResult(
        request={
            "tenant_id": str(tenant.id),
            "prompt_template": outcome.template_version,
            "model": ctx.settings.llm_config_model,
        },
        response={
            "config_id": str(config.id),
            "config_version": config.version,
            "replaced_version": published.previous_version,
            "generated_by": outcome.source.value,
            "generator_detail": outcome.detail,
            "voice_id": voice_id,
            "system_prompt_chars": len(system_prompt),
            "services": len(outcome.config.services),
        },
    )
