"""Publishing and rolling back agent configurations.

``agent_configs`` is append-only. Nothing here ever edits a prompt in place: a
change writes a new row with the next version and moves the ``is_live`` flag.
Three properties follow from that, and all three are the reason the table is
shaped this way rather than being a mutable column on ``tenants``:

* **"What was live when this call happened?" has an exact answer.** Calls record
  the version they were served by, so a complaint about what the receptionist
  said can be traced to the precise prompt that said it.
* **Rollback is a flag move, not a vendor call.** Reverting a bad prompt touches
  one boolean in PostgreSQL. It does not depend on ElevenLabs being reachable,
  which matters because the moment you most need to roll back is rarely a calm
  one.
* **ElevenLabs is a projection.** The vendor holds a copy of whatever we last
  pushed. This table holds the truth, so a vendor-side edit or a failed resync
  is a discrepancy to reconcile rather than a loss.

The logic lives here rather than inside the provisioning step because three
callers need it — the step, the admin rollback action, and the config editor —
and three copies of "demote the old row, promote the new one" is exactly the
kind of quiet divergence this project exists to avoid.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidInputError, NotFoundError
from app.core.logging import get_logger
from app.models import AgentConfig
from app.models.enums import ActorType, AgentConfigSource, AuditAction
from app.models.identity import User
from app.services.audit import AuditService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PublishedConfig:
    """A newly published version and what it replaced."""

    config: AgentConfig
    previous_version: int | None

    @property
    def version(self) -> int:
        return self.config.version


class ConfigVersionService:
    """Append-only reads and writes over ``agent_configs``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ---- reads -----------------------------------------------------------
    async def live(self, tenant_id: uuid.UUID) -> AgentConfig | None:
        """The version currently serving calls, if any."""
        return (
            await self.session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == tenant_id)
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one_or_none()

    async def get_version(self, tenant_id: uuid.UUID, version: int) -> AgentConfig | None:
        return (
            await self.session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == tenant_id)
                .where(AgentConfig.version == version)
            )
        ).scalar_one_or_none()

    async def history(self, tenant_id: uuid.UUID, *, limit: int = 50) -> list[AgentConfig]:
        """Newest first. The audit answer to "what have we told this agent?"."""
        return list(
            (
                await self.session.execute(
                    select(AgentConfig)
                    .where(AgentConfig.tenant_id == tenant_id)
                    .order_by(AgentConfig.version.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def next_version(self, tenant_id: uuid.UUID) -> int:
        """One past the highest version this tenant has ever had.

        Deliberately *not* one past the live version: reusing a number after a
        rollback would make two different prompts share an identity, and every
        call that cited the old one would start pointing at the new one.
        """
        highest = (
            await self.session.execute(
                select(AgentConfig.version)
                .where(AgentConfig.tenant_id == tenant_id)
                .order_by(AgentConfig.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return (highest or 0) + 1

    # ---- writes ----------------------------------------------------------
    async def publish(
        self,
        *,
        tenant_id: uuid.UUID,
        system_prompt: str,
        first_message: str,
        voice_id: str | None,
        model_params: dict[str, object],
        generated_by: AgentConfigSource,
        generator_detail: str | None,
        template_version: str | None,
        actor: User | None = None,
    ) -> PublishedConfig:
        """Write the next version and make it live.

        The demote-then-promote order is required, not stylistic. A partial
        unique index (``uq_agent_configs_live_per_tenant``) allows exactly one
        live row per tenant and is checked per statement, so promoting before
        demoting would violate it immediately. The flush between the two is what
        makes the database see them in that order.
        """
        previous = await self.live(tenant_id)
        if previous is not None:
            previous.is_live = False
            await self.session.flush()

        config = AgentConfig(
            tenant_id=tenant_id,
            version=await self.next_version(tenant_id),
            system_prompt=system_prompt,
            first_message=first_message,
            voice_id=voice_id,
            model_params_json=dict(model_params),
            generated_by=generated_by,
            generator_detail=generator_detail,
            template_version=template_version,
            is_live=True,
        )
        self.session.add(config)
        await self.session.flush()

        self._audit(
            AuditAction.CONFIG_CREATED,
            tenant_id=tenant_id,
            config=config,
            actor=actor,
            meta={
                "version": config.version,
                "generated_by": generated_by.value,
                "generator_detail": generator_detail,
                "template_version": template_version,
                "replaced_version": previous.version if previous else None,
            },
        )
        logger.info(
            "agent config published",
            extra={
                "tenant_id": str(tenant_id),
                "version": config.version,
                "generated_by": generated_by.value,
            },
        )
        return PublishedConfig(
            config=config, previous_version=previous.version if previous else None
        )

    async def rollback_to(
        self,
        *,
        tenant_id: uuid.UUID,
        version: int,
        actor: User | None = None,
    ) -> AgentConfig:
        """Make an earlier version live again.

        No vendor call and no new row: the target version already exists, and
        pointing at it is the whole operation. Pushing the restored prompt to
        ElevenLabs is a *separate*, retryable action (``resync-agent``) —
        deliberately separate, because a rollback must succeed even when the
        vendor is unreachable, which is exactly when a bad prompt is most
        urgent to retire.
        """
        target = await self.get_version(tenant_id, version)
        if target is None:
            raise NotFoundError(
                "no such config version",
                details={"tenant_id": str(tenant_id), "version": version},
            )

        current = await self.live(tenant_id)
        if current is not None and current.version == version:
            raise InvalidInputError(
                "that version is already live",
                details={"tenant_id": str(tenant_id), "version": version},
            )

        if current is not None:
            current.is_live = False
            await self.session.flush()

        target.is_live = True
        await self.session.flush()

        self._audit(
            AuditAction.CONFIG_ROLLED_BACK,
            tenant_id=tenant_id,
            config=target,
            actor=actor,
            meta={
                "version": version,
                "rolled_back_from": current.version if current else None,
            },
        )
        logger.warning(
            "agent config rolled back",
            extra={
                "tenant_id": str(tenant_id),
                "version": version,
                "from_version": current.version if current else None,
            },
        )
        return target

    # ---- helpers ---------------------------------------------------------
    def _audit(
        self,
        action: AuditAction,
        *,
        tenant_id: uuid.UUID,
        config: AgentConfig,
        actor: User | None,
        meta: dict[str, object],
    ) -> None:
        self.audit.record(
            action,
            actor_type=ActorType.USER if actor is not None else ActorType.SYSTEM,
            actor=actor,
            actor_label=None if actor is not None else "system",
            tenant_id=tenant_id,
            entity_type="agent_config",
            entity_id=config.id,
            meta=meta,
        )
