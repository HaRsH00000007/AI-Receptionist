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
    # being edited, so the history of what was live stays intact.
    if existing is not None:
        existing.is_live = False
        await ctx.session.flush()

    config = AgentConfig(
        tenant_id=tenant.id,
        version=await _next_version(ctx, tenant.id),
        system_prompt=system_prompt,
        first_message=render_first_message(outcome.config),
        voice_id=voice_id,
        model_params_json={
            "generated": outcome.config.model_dump(mode="json"),
            "temperature": ctx.settings.llm_temperature,
        },
        generated_by=outcome.source,
        generator_detail=outcome.detail,
        template_version=outcome.template_version,
        is_live=True,
    )
    ctx.session.add(config)
    await ctx.session.flush()

    return StepResult(
        request={
            "tenant_id": str(tenant.id),
            "prompt_template": outcome.template_version,
            "model": ctx.settings.llm_config_model,
        },
        response={
            "config_id": str(config.id),
            "config_version": config.version,
            "generated_by": outcome.source.value,
            "generator_detail": outcome.detail,
            "voice_id": voice_id,
            "system_prompt_chars": len(system_prompt),
            "services": len(outcome.config.services),
        },
    )


async def _next_version(ctx: StepContext, tenant_id: object) -> int:
    highest = (
        await ctx.session.execute(
            select(AgentConfig.version)
            .where(AgentConfig.tenant_id == tenant_id)
            .order_by(AgentConfig.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return (highest or 0) + 1
