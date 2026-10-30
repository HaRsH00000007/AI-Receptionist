"""Adding, editing and removing the greetings that replace the usual one.

The same three steps, in the same order, inside one transaction, as every other
customer-visible change here:

1. **The row is written.** The rules table is the truth about what a caller
   hears and when.
2. **A new configuration version is published**, copied forward from the live
   one with the schedule swapped. No model call: a scheduled greeting changes
   no word of the system prompt, and asking the generator to re-derive the
   whole configuration because somebody typed their lunch hours would risk a
   prompt that says something new.
3. **The vendor is updated**, best-effort, with whichever greeting is in force
   *at that moment* — see :func:`app.services.config_publishing.push_config_to_agent`.

**Why the opening line on the version is still the default one.** The published
``first_message`` stays the business's normal greeting, and the schedule travels
beside it in ``model_params``. Publishing the currently-active exception instead
would make the version history unreadable — "version 41, lunch" and "version 42,
not lunch" every single day — and a rollback would restore whatever happened to
be true at the moment it was published. The exception is applied where it
belongs: to the vendor's copy, by the worker, as the clock moves
(:mod:`app.services.greeting_sync`).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.core.logging import get_logger
from app.models import Agent, AgentConfig, GreetingRule, Tenant
from app.models.enums import ActorType, AgentStatus, AuditAction, GreetingScheduleKind, TenantStatus
from app.providers.registry import Providers
from app.schemas.greetings import (
    GreetingRuleInput,
    GreetingRuleUpdate,
    GreetingRuleView,
    window_fields,
)
from app.services.audit import Actor, AuditService
from app.services.config_publishing import push_config_to_agent
from app.services.config_versions import ConfigVersionService
from app.services.greeting_schedule import (
    PARAMS_KEY,
    active_greeting,
    dump_schedule,
    schedule_for,
)

logger = get_logger(__name__)

#: Per tenant, across both kinds. Generous enough for a year of public holidays
#: plus a lunch hour, small enough that the list stays something a person can
#: read and the published schedule stays a short array.
MAX_RULES = 24


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    """What publishing a rule change managed to do. Never raised, always told."""

    #: ``None`` when there was no live configuration to copy forward.
    config_version: int | None
    synced: bool
    detail: str


class GreetingRuleService:
    """Every query is filtered by tenant, including the ones that look a rule up
    by its own id: another business's rule id must be indistinguishable from one
    that does not exist."""

    def __init__(self, session: AsyncSession, settings: Settings, providers: Providers) -> None:
        self.session = session
        self.settings = settings
        self.providers = providers
        self.audit = AuditService(session)

    # ---- reads -----------------------------------------------------------
    async def list(self, tenant_id: uuid.UUID) -> list[GreetingRule]:
        """Every rule, enabled or not, in display order."""
        return list(
            (
                await self.session.execute(
                    select(GreetingRule)
                    .where(GreetingRule.tenant_id == tenant_id)
                    .order_by(GreetingRule.position, GreetingRule.created_at)
                )
            )
            .scalars()
            .all()
        )

    async def get(self, tenant_id: uuid.UUID, rule_id: uuid.UUID) -> GreetingRule:
        rule = (
            await self.session.execute(
                select(GreetingRule)
                .where(GreetingRule.id == rule_id)
                .where(GreetingRule.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if rule is None:
            raise NotFoundError("greeting rule not found")
        return rule

    async def default_greeting(self, tenant_id: uuid.UUID) -> str | None:
        """The opening line callers hear when no rule matches.

        Read from the live configuration rather than the profile: the profile
        holds what the owner *typed*, which is blank for the majority who let
        setup write one for them, and the page has to show the line that is
        actually spoken.
        """
        live = await ConfigVersionService(self.session).live(tenant_id)
        return live.first_message if live else None

    # ---- writes ----------------------------------------------------------
    async def create(
        self, *, tenant: Tenant, payload: GreetingRuleInput, actor: Actor
    ) -> tuple[GreetingRule, PublishOutcome]:
        self._require_live(tenant)

        count = (
            await self.session.execute(
                select(func.count(GreetingRule.id)).where(GreetingRule.tenant_id == tenant.id)
            )
        ).scalar_one()
        if count >= MAX_RULES:
            raise ConflictError(
                f"a receptionist can have at most {MAX_RULES} scheduled greetings",
                details={"max_rules": MAX_RULES},
            )

        highest = (
            await self.session.execute(
                select(func.max(GreetingRule.position)).where(GreetingRule.tenant_id == tenant.id)
            )
        ).scalar_one()

        rule = GreetingRule(
            tenant_id=tenant.id,
            kind=payload.kind,
            message=payload.message,
            enabled=payload.enabled,
            position=(highest or 0) + 1,
            **window_fields(payload),
        )
        self.session.add(rule)
        await self.session.flush()

        self._record(AuditAction.GREETING_RULE_CREATED, tenant, rule, actor, meta={})
        outcome = await self._publish(tenant, actor)
        logger.info(
            "greeting rule created",
            extra={
                "tenant_id": str(tenant.id),
                "rule_id": str(rule.id),
                "kind": rule.kind.value,
                "enabled": rule.enabled,
                "synced": outcome.synced,
            },
        )
        return rule, outcome

    async def update(
        self,
        *,
        tenant: Tenant,
        rule_id: uuid.UUID,
        payload: GreetingRuleUpdate,
        actor: Actor,
    ) -> tuple[GreetingRule, PublishOutcome]:
        self._require_live(tenant)
        rule = await self.get(tenant.id, rule_id)
        self._require_same_kind(rule, payload)

        changed: list[str] = []
        if payload.message is not None and payload.message != rule.message:
            rule.message = payload.message
            changed.append("message")
        if payload.weekdays is not None and payload.weekdays != list(rule.weekdays_json or ()):
            rule.weekdays_json = payload.weekdays or None
            changed.append("weekdays")
        # ``model_fields_set`` rather than a null check: clearing a dated rule's
        # window back to "all day" is a real edit, and it is sent as two
        # explicit nulls. A null that only means "I did not mention this" has to
        # be told apart from one that means "remove it", or the checkbox that
        # turns the window off would silently do nothing.
        if "start_minute" in payload.model_fields_set and (
            payload.start_minute != rule.start_minute or payload.end_minute != rule.end_minute
        ):
            if rule.kind is GreetingScheduleKind.WEEKLY and payload.start_minute is None:
                raise InvalidInputError("a weekly greeting needs a start and an end time")
            rule.start_minute = payload.start_minute
            rule.end_minute = payload.end_minute
            changed.append("window")
        if payload.start_date is not None and (
            payload.start_date != rule.start_date
            or (payload.end_date or payload.start_date) != rule.end_date
        ):
            rule.start_date = payload.start_date
            rule.end_date = payload.end_date or payload.start_date
            changed.append("dates")
        if payload.enabled is not None and payload.enabled != rule.enabled:
            rule.enabled = payload.enabled
            changed.append("enabled")

        if not changed:
            # Nothing to publish and nothing to tell a vendor. Reported against
            # the live version so the caller still learns where it stands.
            live = await ConfigVersionService(self.session).live(tenant.id)
            return rule, PublishOutcome(
                config_version=live.version if live else None,
                synced=True,
                detail="Nothing changed, so there was nothing to publish.",
            )

        await self.session.flush()
        self._record(
            AuditAction.GREETING_RULE_UPDATED, tenant, rule, actor, meta={"fields": changed}
        )
        outcome = await self._publish(tenant, actor)
        logger.info(
            "greeting rule updated",
            extra={
                "tenant_id": str(tenant.id),
                "rule_id": str(rule.id),
                "fields": changed,
                "synced": outcome.synced,
            },
        )
        return rule, outcome

    async def delete(self, *, tenant: Tenant, rule_id: uuid.UUID, actor: Actor) -> PublishOutcome:
        self._require_live(tenant)
        rule = await self.get(tenant.id, rule_id)

        self._record(AuditAction.GREETING_RULE_DELETED, tenant, rule, actor, meta={})
        await self.session.delete(rule)
        await self.session.flush()

        outcome = await self._publish(tenant, actor)
        logger.info(
            "greeting rule deleted",
            extra={
                "tenant_id": str(tenant.id),
                "rule_id": str(rule_id),
                "synced": outcome.synced,
            },
        )
        return outcome

    # ---- publishing ------------------------------------------------------
    async def _publish(self, tenant: Tenant, actor: Actor) -> PublishOutcome:
        """Copy the live version forward with the current schedule.

        The prompt, the default opening line and the voice are carried across
        verbatim, and ``generated_by`` with them, because those words really
        were generated the way the previous version says they were. Only the
        schedule differs.
        """
        versions = ConfigVersionService(self.session)
        live = await versions.live(tenant.id)
        if live is None:
            # Possible only for a tenant that is active with no published
            # configuration, which should not happen — but saving the rule and
            # saying so is better than a 500 that loses it.
            return PublishOutcome(
                config_version=None,
                synced=False,
                detail="Saved. Your receptionist will use it once its setup has finished.",
            )

        rules = await self.list(tenant.id)
        params = dict(live.model_params_json or {})
        params[PARAMS_KEY] = dump_schedule(schedule_for(rules))

        published = await versions.publish(
            tenant_id=tenant.id,
            system_prompt=live.system_prompt,
            first_message=live.first_message,
            voice_id=live.voice_id,
            model_params=params,
            generated_by=live.generated_by,
            generator_detail="greeting schedule changed",
            template_version=live.template_version,
            actor=actor.user,
        )
        synced, detail = await self._sync_vendor(tenant, published.config)
        return PublishOutcome(config_version=published.version, synced=synced, detail=detail)

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
            # A shared vertical agent reads the live version per call through
            # the version-keyed cache, and the greeting is one of the dynamic
            # variables it is given — so publishing *is* the rollout, and
            # pushing one tenant's opening line onto it would overwrite every
            # other tenant's.
            return True, "Saved. Your receptionist uses it from the next call."

        try:
            await push_config_to_agent(
                session=self.session,
                settings=self.settings,
                voice=self.providers.elevenlabs,
                tenant=tenant,
                agent=agent,
                config=config,
            )
        except Exception:
            logger.exception(
                "greeting schedule published but the vendor sync failed",
                extra={"tenant_id": str(tenant.id), "config_version": config.version},
            )
            # Specific about the direction of the risk: until this syncs, the
            # vendor still holds the previous opening line, so callers hear the
            # old one. The worker's sweep retries it on its next pass, which is
            # why this one may say so.
            return (
                False,
                "Saved, but the voice agent couldn't be updated yet, so callers still "
                "hear the previous greeting. It will be retried automatically; save "
                "again or contact support if it stays this way.",
            )

        self.audit.record(
            AuditAction.AGENT_RESYNCED,
            actor_type=ActorType.SYSTEM,
            actor_label="system",
            tenant_id=tenant.id,
            entity_type="agent",
            entity_id=agent.id,
            meta={"config_version": config.version, "trigger": "greeting_schedule"},
        )
        return True, "Saved. Your receptionist uses it from the next call."

    # ---- helpers ---------------------------------------------------------
    def _require_live(self, tenant: Tenant) -> None:
        if tenant.status is not TenantStatus.ACTIVE:
            raise ConflictError(
                "scheduled greetings can be set up once setup has finished",
                details={"tenant_status": tenant.status.value},
            )

    def _require_same_kind(self, rule: GreetingRule, payload: GreetingRuleUpdate) -> None:
        """A rule cannot be edited into the other kind.

        The schema cannot check this on its own — it never sees the stored row —
        and silently ignoring the wrong kind's fields would accept "make this
        lunch rule apply on the 25th" and then not do it.
        """
        if rule.kind is GreetingScheduleKind.WEEKLY and (
            payload.start_date is not None or payload.end_date is not None
        ):
            raise InvalidInputError("a weekly greeting repeats; it does not take dates")
        if rule.kind is GreetingScheduleKind.DATED and payload.weekdays:
            raise InvalidInputError("a greeting for specific dates does not take weekdays")

    def _record(
        self,
        action: AuditAction,
        tenant: Tenant,
        rule: GreetingRule,
        actor: Actor,
        *,
        meta: dict[str, object],
    ) -> None:
        self.audit.record(
            action,
            actor_type=actor.actor_type,
            actor=actor.user,
            tenant_id=tenant.id,
            entity_type="greeting_rule",
            entity_id=rule.id,
            meta={**meta, "kind": rule.kind.value},
        )


def rules_view_payload(
    rules: Sequence[GreetingRule],
    *,
    timezone: str,
    default_greeting: str | None,
    moment: datetime | None = None,
) -> dict[str, object]:
    """The fields both the read and the write responses share.

    ``active_message`` is computed from the rules themselves rather than from
    whatever was last pushed to the vendor: this answers "what should a call
    right now be answered with", which is the question the page is asking, and
    it stays right even while a sync is outstanding.
    """
    schedule = schedule_for(rules)
    active, _ = active_greeting(
        schedule,
        moment=moment or datetime.now(UTC),
        timezone=timezone,
        default=default_greeting or "",
    )
    return {
        "rules": [
            GreetingRuleView(
                id=rule.id,
                kind=rule.kind,
                message=rule.message,
                weekdays=list(rule.weekdays_json or ()),
                start_minute=rule.start_minute,
                end_minute=rule.end_minute,
                start_date=rule.start_date,
                end_date=rule.end_date,
                enabled=rule.enabled,
                position=rule.position,
                created_at=rule.created_at,
            )
            for rule in rules
        ],
        "max_rules": MAX_RULES,
        "timezone": timezone,
        "default_greeting": default_greeting,
        "active_message": active or None,
    }
