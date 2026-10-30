"""Adding, editing and removing the rules that hand a call to a person.

A change here is three things, in this order, inside one transaction — the same
shape as every other customer-visible change in this product:

1. **The row is written.** The rules table is the truth about where calls go.
2. **A new configuration version is published.** A transfer rule changes what
   the receptionist *does*, so "which version answered this call?" has to be
   able to answer it, and the destinations are recorded on the version.
3. **The vendor is updated**, best-effort. A vendor that is down leaves a stale
   projection the next save repairs; it does not undo the rule.

Step 2 is deliberately *not* ``generate_and_publish``. That path asks the model
to re-derive the whole configuration, and a transfer rule changes no word of the
prompt: the destinations and their conditions are tool configuration, read by
the vendor's own model at call time. Republishing by copying the live version
and swapping the destinations keeps the history honest without paying for a
model call — or risking a regenerated prompt that says something new because
somebody added a phone number.

**Conditions never reach the system prompt.** A prompt that listed the
destinations would let a caller ask the receptionist to read a private mobile
number out loud, and it would be the agent's own text rather than vendor tool
configuration deciding where a call goes. The condition goes to the vendor
attached to its destination, and nowhere else.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models import Agent, AgentConfig, Tenant, TransferRule
from app.models.enums import ActorType, AgentStatus, AuditAction, TenantStatus
from app.providers.registry import Providers
from app.schemas.transfers import TransferRuleInput, TransferRuleUpdate, TransferRuleView
from app.services.audit import Actor, AuditService
from app.services.config_publishing import push_config_to_agent
from app.services.config_versions import ConfigVersionService
from app.services.transfer_targets import PARAMS_KEY, dump_targets, targets_for

logger = get_logger(__name__)

#: Per tenant. Not a technical limit: a receptionist that has to choose between
#: twenty destinations chooses badly, and a business with that many is
#: describing a phone tree, which is a different product.
MAX_RULES = 10


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    """What publishing a rule change managed to do. Never raised, always told."""

    #: ``None`` when there was no live configuration to copy forward.
    config_version: int | None
    synced: bool
    detail: str


class TransferRuleService:
    """Every query here is filtered by tenant, including the ones that look up
    a rule by its own id: another business's rule id must be indistinguishable
    from one that does not exist."""

    def __init__(self, session: AsyncSession, settings: Settings, providers: Providers) -> None:
        self.session = session
        self.settings = settings
        self.providers = providers
        self.audit = AuditService(session)

    # ---- reads -----------------------------------------------------------
    async def list(self, tenant_id: uuid.UUID) -> list[TransferRule]:
        """Every rule, enabled or not, in display order."""
        return list(
            (
                await self.session.execute(
                    select(TransferRule)
                    .where(TransferRule.tenant_id == tenant_id)
                    .order_by(TransferRule.position, TransferRule.created_at)
                )
            )
            .scalars()
            .all()
        )

    @property
    def cold_transfer_available(self) -> bool:
        """Whether a blind transfer can work on this deployment.

        It needs the number to have been imported into ElevenLabs from Twilio
        natively, which is what the ``elevenlabs_native`` call path does. When
        calls are bridged through our own TwiML instead, the vendor is not the
        one holding the Twilio number and a blind transfer has nothing to hand
        the call to.

        Checked when a rule is written, not when one is published: moving a
        tenant to the TwiML path would leave any existing cold rule in place,
        and the honest fix for that is the migration checklist in
        docs/TELEPHONY.md rather than this service silently rewriting a
        business's rules behind their back.
        """
        return self.settings.call_path == "elevenlabs_native"

    async def get(self, tenant_id: uuid.UUID, rule_id: uuid.UUID) -> TransferRule:
        rule = (
            await self.session.execute(
                select(TransferRule)
                .where(TransferRule.id == rule_id)
                .where(TransferRule.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if rule is None:
            raise NotFoundError("transfer rule not found")
        return rule

    # ---- writes ----------------------------------------------------------
    async def create(
        self, *, tenant: Tenant, payload: TransferRuleInput, actor: Actor
    ) -> tuple[TransferRule, PublishOutcome]:
        self._require_live(tenant)
        self._require_supported(payload.transfer_type.value)

        count = (
            await self.session.execute(
                select(func.count(TransferRule.id)).where(TransferRule.tenant_id == tenant.id)
            )
        ).scalar_one()
        if count >= MAX_RULES:
            raise ConflictError(
                f"a receptionist can have at most {MAX_RULES} transfer rules",
                details={"max_rules": MAX_RULES},
            )

        highest = (
            await self.session.execute(
                select(func.max(TransferRule.position)).where(TransferRule.tenant_id == tenant.id)
            )
        ).scalar_one()

        rule = TransferRule(
            tenant_id=tenant.id,
            label=payload.label or None,
            destination_e164=payload.destination_number,
            condition=payload.condition,
            transfer_type=payload.transfer_type,
            enabled=payload.enabled,
            position=(highest or 0) + 1,
        )
        self.session.add(rule)
        await self.session.flush()

        self._record(AuditAction.TRANSFER_RULE_CREATED, tenant, rule, actor, meta={})
        outcome = await self._publish(tenant, actor)
        logger.info(
            "transfer rule created",
            extra={
                "tenant_id": str(tenant.id),
                "rule_id": str(rule.id),
                "transfer_type": rule.transfer_type.value,
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
        payload: TransferRuleUpdate,
        actor: Actor,
    ) -> tuple[TransferRule, PublishOutcome]:
        self._require_live(tenant)
        rule = await self.get(tenant.id, rule_id)

        changed: list[str] = []
        if payload.destination_number is not None and (
            payload.destination_number != rule.destination_e164
        ):
            rule.destination_e164 = payload.destination_number
            changed.append("destination")
        if payload.condition is not None and payload.condition != rule.condition:
            rule.condition = payload.condition
            changed.append("condition")
        if payload.label is not None and (payload.label or None) != rule.label:
            rule.label = payload.label or None
            changed.append("label")
        if payload.transfer_type is not None and payload.transfer_type is not rule.transfer_type:
            self._require_supported(payload.transfer_type.value)
            rule.transfer_type = payload.transfer_type
            changed.append("transfer_type")
        if payload.enabled is not None and payload.enabled != rule.enabled:
            rule.enabled = payload.enabled
            changed.append("enabled")

        if not changed:
            # Nothing to publish, and nothing to tell a vendor. Reported against
            # the live version so the caller still learns where it stands.
            live = await ConfigVersionService(self.session).live(tenant.id)
            return rule, PublishOutcome(
                config_version=live.version if live else None,
                synced=True,
                detail="Nothing changed, so there was nothing to publish.",
            )

        await self.session.flush()
        # Field names only. A destination is a real person's phone number and
        # the audit log is not where it needs to be repeated.
        self._record(
            AuditAction.TRANSFER_RULE_UPDATED, tenant, rule, actor, meta={"fields": changed}
        )
        outcome = await self._publish(tenant, actor)
        logger.info(
            "transfer rule updated",
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

        self._record(AuditAction.TRANSFER_RULE_DELETED, tenant, rule, actor, meta={})
        await self.session.delete(rule)
        await self.session.flush()

        outcome = await self._publish(tenant, actor)
        logger.info(
            "transfer rule deleted",
            extra={
                "tenant_id": str(tenant.id),
                "rule_id": str(rule_id),
                "synced": outcome.synced,
            },
        )
        return outcome

    # ---- publishing ------------------------------------------------------
    async def _publish(self, tenant: Tenant, actor: Actor) -> PublishOutcome:
        """Copy the live version forward with the current destinations.

        No model call and no prompt change: the prompt, opening line and voice
        are carried across verbatim, and ``generated_by`` with it, because the
        words really were generated the way the previous version says they
        were. Only the destinations differ.
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
        params[PARAMS_KEY] = dump_targets(targets_for(rules))

        published = await versions.publish(
            tenant_id=tenant.id,
            system_prompt=live.system_prompt,
            first_message=live.first_message,
            voice_id=live.voice_id,
            model_params=params,
            generated_by=live.generated_by,
            generator_detail="transfer rules changed",
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
            # Unlike a prompt, which a shared agent reads per call from the live
            # version, transfer destinations are configured *on the agent* at
            # the vendor. One shared agent cannot hold one tenant's rules
            # without holding them for every tenant on that vertical, so this
            # says plainly that the rule is saved and not yet in force.
            return (
                False,
                "Saved, but this receptionist shares a voice agent, so transfers "
                "aren't switched on for it yet. Contact support to move it to its "
                "own agent.",
            )

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
                "transfer rules published but the vendor sync failed",
                extra={"tenant_id": str(tenant.id), "config_version": config.version},
            )
            # Specific about the direction of the risk: until this syncs, the
            # vendor still holds the previous set, so a deleted rule may still
            # forward a caller. Nothing retries it automatically.
            return (
                False,
                "Saved, but the voice agent couldn't be updated yet, so calls are "
                "still being transferred the previous way. Save again in a moment, "
                "or contact support if this keeps happening.",
            )

        self.audit.record(
            AuditAction.AGENT_RESYNCED,
            actor_type=ActorType.SYSTEM,
            actor_label="system",
            tenant_id=tenant.id,
            entity_type="agent",
            entity_id=agent.id,
            meta={"config_version": config.version, "trigger": "transfer_rules"},
        )
        return True, "Saved. Your receptionist uses it from the next call."

    # ---- helpers ---------------------------------------------------------
    def _require_live(self, tenant: Tenant) -> None:
        if tenant.status is not TenantStatus.ACTIVE:
            raise ConflictError(
                "transfer rules can be set up once setup has finished",
                details={"tenant_status": tenant.status.value},
            )

    def _require_supported(self, kind: str) -> None:
        """Refuse a transfer type this deployment cannot actually perform.

        The API offers both and the UI hides the one that is unavailable; this
        is the check that makes hiding it more than a suggestion.
        """
        if kind == "cold" and not self.cold_transfer_available:
            raise ConflictError(
                "this receptionist can only transfer with an announcement",
                details={"transfer_type": kind, "call_path": self.settings.call_path},
            )

    def _record(
        self,
        action: AuditAction,
        tenant: Tenant,
        rule: TransferRule,
        actor: Actor,
        *,
        meta: dict[str, object],
    ) -> None:
        self.audit.record(
            action,
            actor_type=actor.actor_type,
            actor=actor.user,
            tenant_id=tenant.id,
            entity_type="transfer_rule",
            entity_id=rule.id,
            meta=meta,
        )


def rules_view_payload(rules: Sequence[TransferRule], *, cold_available: bool) -> dict[str, object]:
    """The fields both the read and the write responses share."""
    return {
        "rules": [
            TransferRuleView(
                id=rule.id,
                label=rule.label,
                destination_e164=rule.destination_e164,
                condition=rule.condition,
                transfer_type=rule.transfer_type,
                enabled=rule.enabled,
                position=rule.position,
                created_at=rule.created_at,
            )
            for rule in rules
        ],
        "max_rules": MAX_RULES,
        "cold_transfer_available": cold_available,
    }
