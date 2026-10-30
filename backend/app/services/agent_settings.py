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
from app.core.errors import ConflictError, InvalidInputError, NotFoundError
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
from app.services.voice_catalogue import VoiceCatalogue

logger = get_logger(__name__)

#: Fields a customer can change that the agent never reads. Saving one of these
#: must not publish a configuration: the prompt would be byte-identical, and a
#: version whose diff is empty makes the history useless for answering "what
#: changed before the calls got worse?".
_LABEL_ONLY_FIELDS = frozenset({"agent_name"})


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

        if update.voice_id is not None:
            # Checked against the catalogue before anything is written. A voice
            # the account does not have would be a 422 from the vendor on every
            # push from here on, so it is refused at the one moment a customer
            # can still be told which field is wrong.
            allowed = await VoiceCatalogue(self.settings, self.providers.elevenlabs).voice_ids()
            if update.voice_id and update.voice_id not in allowed:
                raise InvalidInputError(
                    "That voice isn't available.", details={"field": "voice_id"}
                )

        changed = self._apply(profile, tenant, update)

        # ``agent_name`` is the owner's label for the receptionist. It is never
        # spoken, never rendered into the prompt and never sent to a vendor, so
        # it cannot change a single word a caller hears — and must not cost a
        # model call or burn a version number.
        publishable = [field for field in changed if field not in _LABEL_ONLY_FIELDS]

        if not publishable:
            # A new version costs a model call and adds a history entry that
            # says nothing. Report the live one instead.
            live = await ConfigVersionService(self.session).live(tenant.id)
            if changed:
                # Something was saved, just nothing the agent says. Record it,
                # because "who renamed this" is still a question worth answering.
                self.audit.record(
                    AuditAction.PROFILE_UPDATED,
                    actor_type=actor.actor_type,
                    actor=actor.user,
                    tenant_id=tenant.id,
                    entity_type="business_profile",
                    entity_id=profile.id,
                    meta={"fields": changed, "published": False},
                )
            if live is not None:
                return AgentUpdateResult(
                    config_version=live.version,
                    previous_version=None,
                    generated_by=live.generated_by.value,
                    synced=True,
                    detail=(
                        "Saved. Nothing your receptionist says has changed, so "
                        "there was no new version to publish."
                        if changed
                        else "Nothing changed, so there was nothing to publish."
                    ),
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

        # The name is a label the owner reads; it reaches no prompt and no
        # vendor. Applied first simply so it is saved even when nothing else is.
        if update.agent_name is not None:
            name = update.agent_name or None
            if name != profile.agent_name:
                profile.agent_name = name
                changed.append("agent_name")

        # Before the hours, and deliberately: hours are *parsed into* a
        # timezone, so a request that changes both must parse against the new
        # one. Getting this order wrong stores "9-6 Pacific" for a business
        # that just told us it is in New York.
        timezone_changed = False
        if update.timezone is not None and update.timezone != tenant.timezone:
            tenant.timezone = update.timezone
            changed.append("timezone")
            timezone_changed = True

        services = list(update.services)  # normalized by the schema
        if services != list(profile.services):
            profile.services = services
            changed.append("services")

        # Re-parsed when the zone moves even if the words did not: the stored
        # hours carry the timezone inside them, so leaving them alone would
        # keep the old zone in the published config.
        hours_text_changed = update.operating_hours != (profile.hours_raw or "")
        if hours_text_changed or timezone_changed:
            profile.hours_raw = update.operating_hours
            hours = parse_opening_hours(update.operating_hours, timezone=tenant.timezone)
            # None when the text matched no known pattern; the generator then
            # reads the raw text, exactly as it does at signup. Never a guess.
            profile.hours_json = dump_json_column(hours) if hours else None
            if hours_text_changed:
                changed.append("operating_hours")

        if update.greeting_style is not profile.greeting_style:
            profile.greeting_style = update.greeting_style
            changed.append("greeting_style")

        if update.voice_id is not None:
            voice = update.voice_id or None
            if voice != profile.voice_id:
                profile.voice_id = voice
                changed.append("voice_id")

        # How it sounds, not what it says — but still a change a caller hears,
        # so both of these publish a version rather than being label-only.
        if update.speaking_speed is not None and update.speaking_speed != profile.speaking_speed:
            profile.speaking_speed = update.speaking_speed
            changed.append("speaking_speed")

        if (
            update.responsiveness is not None
            and update.responsiveness is not profile.responsiveness
        ):
            profile.responsiveness = update.responsiveness
            changed.append("responsiveness")

        greeting = update.custom_greeting or None
        if greeting != profile.greeting_custom:
            profile.greeting_custom = greeting
            changed.append("custom_greeting")

        # ``None`` means the client did not mention it — the Training panel
        # never does — and must not be read as "remove the sign-off".
        if update.custom_goodbye is not None:
            goodbye = update.custom_goodbye or None
            if goodbye != profile.goodbye_custom:
                profile.goodbye_custom = goodbye
                changed.append("custom_goodbye")

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
