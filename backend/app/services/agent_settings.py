"""The customer changing their own receptionist.

A save is three things, in this order, inside one transaction:

1. **The profile is updated** — the business's own words, which the prompt is
   built from. ``raw_form_json`` is left as it was: it is the signup snapshot,
   and the audit row below records what changed and who changed it.
2. **A new configuration version is published** by the same code path that
   wrote the first one (:func:`generate_and_publish`). Append-only: the old
   version steps down, it is never edited, and every call keeps pointing at the
   version that actually answered it.
3. **The vendor is updated**, best-effort. The database is the truth; a vendor
   that is down leaves a stale projection that the next sync repairs, and the
   customer is told so rather than told their change failed.

Only a live receptionist can be edited. While provisioning is still running the
``generate_config`` step owns the configuration, and two writers racing over
"which version is live" is exactly the kind of bug that only shows up in prod.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models import Agent, AgentConfig, BusinessProfile, Tenant
from app.models.enums import ActorType, AgentStatus, AuditAction, TenantStatus
from app.providers.registry import Providers
from app.schemas.business import EscalationPolicy, dump_json_column
from app.schemas.portal import AgentSettingsUpdate, AgentUpdateResult
from app.services.audit import Actor, AuditService
from app.services.config_publishing import generate_and_publish, push_config_to_agent
from app.services.config_versions import ConfigVersionService
from app.services.normalization import parse_opening_hours

logger = get_logger(__name__)


class AgentSettingsService:
    def __init__(self, session: AsyncSession, settings: Settings, providers: Providers) -> None:
        self.session = session
        self.settings = settings
        self.providers = providers
        self.audit = AuditService(session)

    async def update(
        self, *, tenant: Tenant, update: AgentSettingsUpdate, actor: Actor
    ) -> AgentUpdateResult:
        if tenant.status is not TenantStatus.ACTIVE:
            raise ConflictError(
                "your receptionist can be edited once setup has finished",
                details={"tenant_status": tenant.status.value},
            )

        profile = (
            await self.session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == tenant.id)
            )
        ).scalar_one_or_none()
        if profile is None:
            raise NotFoundError("tenant has no business profile")

        changed = self._apply(profile, tenant, update)
        if not changed:
            # A new version costs a model call and adds a history entry that
            # says nothing. Report the live one instead.
            live = await ConfigVersionService(self.session).live(tenant.id)
            if live is not None:
                return AgentUpdateResult(
                    config_version=live.version,
                    previous_version=None,
                    generated_by=live.generated_by.value,
                    synced=True,
                    detail="Nothing changed, so there was nothing to publish.",
                )

        # Bumped on every publish, so a config cached against the old profile
        # version cannot be adopted as current.
        profile.config_version += 1

        result = await generate_and_publish(
            self.session,
            settings=self.settings,
            llm=self.providers.llm,
            tenant=tenant,
            profile=profile,
            actor=actor.user,
        )
        config = result.published.config

        self.audit.record(
            AuditAction.PROFILE_UPDATED,
            actor_type=actor.actor_type,
            actor=actor.user,
            tenant_id=tenant.id,
            entity_type="business_profile",
            entity_id=profile.id,
            # Field names only. The values are the business's own words and are
            # already on the profile and in the new config version.
            meta={"fields": changed, "config_version": config.version},
        )

        synced, detail = await self._sync_vendor(tenant, config)
        logger.info(
            "customer published a new agent config",
            extra={
                "tenant_id": str(tenant.id),
                "config_version": config.version,
                "fields": changed,
                "synced": synced,
            },
        )
        return AgentUpdateResult(
            config_version=config.version,
            previous_version=result.published.previous_version,
            generated_by=config.generated_by.value,
            synced=synced,
            detail=detail,
        )

    # ------------------------------------------------------------------
    def _apply(
        self, profile: BusinessProfile, tenant: Tenant, update: AgentSettingsUpdate
    ) -> list[str]:
        """Write the new values onto the profile; return which fields changed."""
        changed: list[str] = []
        services = list(update.services)  # normalized by the schema
        if services != list(profile.services):
            profile.services = services
            changed.append("services")

        if update.operating_hours != (profile.hours_raw or ""):
            profile.hours_raw = update.operating_hours
            hours = parse_opening_hours(update.operating_hours, timezone=tenant.timezone)
            # None when the text matched no known pattern; the generator then
            # reads the raw text, exactly as it does at signup. Never a guess.
            profile.hours_json = dump_json_column(hours) if hours else None
            changed.append("operating_hours")

        if update.greeting_style is not profile.greeting_style:
            profile.greeting_style = update.greeting_style
            changed.append("greeting_style")

        greeting = update.custom_greeting or None
        if greeting != profile.greeting_custom:
            profile.greeting_custom = greeting
            changed.append("custom_greeting")

        rules = update.escalation_rules or None
        if rules != profile.escalation_raw:
            profile.escalation_raw = rules
            # The same rule signup applies: no rules means "take a message and
            # email the owner"; written rules are interpreted by the generator.
            profile.escalation_json = (
                dump_json_column(EscalationPolicy(notify_email=tenant.contact_email))
                if rules is None
                else None
            )
            changed.append("escalation_rules")

        return changed

    async def _sync_vendor(self, tenant: Tenant, config: AgentConfig) -> tuple[bool, str]:
        agent = (
            await self.session.execute(
                select(Agent)
                .where(Agent.tenant_id == tenant.id)
                .where(Agent.status == AgentStatus.ACTIVE)
            )
        ).scalar_one_or_none()

        if agent is None or not agent.elevenlabs_agent_id:
            return False, "Saved. Your receptionist will use it once its agent is connected."
        if agent.is_shared:
            # A shared vertical agent reads the live version on every call, by
            # version-keyed cache, so publishing *is* the rollout. Pushing one
            # tenant's prompt onto it would overwrite every other tenant's.
            return True, "Saved. Your receptionist uses it from the next call."

        try:
            await push_config_to_agent(
                settings=self.settings,
                voice=self.providers.elevenlabs,
                tenant=tenant,
                agent=agent,
                config=config,
            )
        except Exception:
            # The new version is live in our database either way. Reported, not
            # raised: failing the save would tell the owner their change was
            # lost when it was not.
            logger.exception(
                "config published but the vendor sync failed",
                extra={"tenant_id": str(tenant.id), "config_version": config.version},
            )
            # Nothing retries this automatically, so the message must not say
            # it will. Saving again re-pushes; so does the operator's resync.
            return (
                False,
                "Saved, but the voice agent couldn't be updated yet, so callers still "
                "hear the previous version. Save again in a moment, or contact support "
                "if this keeps happening.",
            )

        self.audit.record(
            AuditAction.AGENT_RESYNCED,
            actor_type=ActorType.SYSTEM,
            actor_label="system",
            tenant_id=tenant.id,
            entity_type="agent",
            entity_id=agent.id,
            meta={"config_version": config.version, "trigger": "customer_edit"},
        )
        return True, "Saved. Your receptionist uses it from the next call."
