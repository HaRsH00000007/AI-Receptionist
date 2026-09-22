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
from app.services.config_publishing import generate_and_publish

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

    # Configs are append-only; the previous live row steps down rather than
    # being edited, so the history of what was live stays intact. Generation and
    # publishing are shared with the customer's agent editor, so a receptionist
    # rebuilt after an edit is built exactly the way a new one is.
    result = await generate_and_publish(
        ctx.session,
        settings=ctx.settings,
        llm=ctx.providers.llm,
        tenant=tenant,
        profile=profile,
    )
    outcome = result.outcome
    published = result.published
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
            "voice_id": result.voice_id,
            "system_prompt_chars": result.system_prompt_chars,
            "services": len(outcome.config.services),
        },
    )
