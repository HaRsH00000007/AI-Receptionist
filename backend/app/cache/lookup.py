"""Cached reads that always have a PostgreSQL answer behind them.

Two lookups sit on the audible call path and are therefore cached: which tenant
owns a dialled number, and what that tenant's agent should be told about itself.
Everything else reads the database directly — a cache exists to remove a
measured cost, and adding one anywhere else would be extra invalidation surface
for no latency the caller can hear.

The read order is the same in both cases and never varies:

1. Redis, with a hard millisecond budget.
2. PostgreSQL, which is the source of truth and always correct.
3. A safe default, only where one exists and only where silence is worse.

A cache miss and a Redis outage take the identical path. That is what makes the
"Redis is down" test variant meaningful: it is not a separate code path being
exercised, it is the *same* path with step 1 always failing.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.client import UNKNOWN, CacheClient
from app.cache.keys import number_lookup, tenant_config
from app.core.config import Settings
from app.core.logging import get_logger
from app.models import AgentConfig, PhoneNumber, Tenant
from app.models.enums import PhoneNumberStatus, TenantStatus

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NumberOwner:
    """Who a dialled number belongs to, and enough context to route the call."""

    tenant_id: uuid.UUID
    tenant_status: TenantStatus
    agent_mode: str
    e164: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "tenant_id": str(self.tenant_id),
                "tenant_status": self.tenant_status.value,
                "agent_mode": self.agent_mode,
                "e164": self.e164,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> NumberOwner | None:
        """Parse a cached entry, treating anything unexpected as a miss.

        A cache written by an older deployment, or corrupted, must not raise on
        the call path. Returning ``None`` sends the caller to PostgreSQL, which
        is always right.
        """
        try:
            data = json.loads(raw)
            return cls(
                tenant_id=uuid.UUID(data["tenant_id"]),
                tenant_status=TenantStatus(data["tenant_status"]),
                agent_mode=str(data["agent_mode"]),
                e164=str(data["e164"]),
            )
        except (ValueError, KeyError, TypeError):
            logger.warning("discarding an unreadable cached number lookup")
            return None


class NumberDirectory:
    """Resolve a dialled number to its tenant, fast."""

    def __init__(self, cache: CacheClient, settings: Settings) -> None:
        self.cache = cache
        self.settings = settings

    async def resolve(self, session: AsyncSession, e164: str) -> NumberOwner | None:
        key = number_lookup(e164)

        cached = await self.cache.get(key)
        if cached is not UNKNOWN and cached is not None:
            owner = NumberOwner.from_json(cached)
            if owner is not None:
                return owner

        owner = await self._from_database(session, e164)
        if owner is not None:
            await self.cache.set(
                key, owner.to_json(), ttl_s=self.settings.redis_number_lookup_ttl_s
            )
        # A negative result is deliberately not cached. Numbers are bought and
        # linked continuously, and caching "nobody owns this" would make a
        # freshly provisioned number unreachable for the whole TTL — the exact
        # moment a new customer is most likely to test it.
        return owner

    async def invalidate(self, e164: str) -> None:
        """Drop the entry. Called when a number is linked or released."""
        await self.cache.delete(number_lookup(e164))

    async def _from_database(self, session: AsyncSession, e164: str) -> NumberOwner | None:
        row = (
            await session.execute(
                select(Tenant, PhoneNumber.e164)
                .join(PhoneNumber, PhoneNumber.tenant_id == Tenant.id)
                .where(PhoneNumber.e164 == e164)
                .where(PhoneNumber.status == PhoneNumberStatus.ACTIVE)
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        tenant, number = row
        return NumberOwner(
            tenant_id=tenant.id,
            tenant_status=tenant.status,
            agent_mode=tenant.agent_mode.value,
            e164=number,
        )


class ConfigCache:
    """The live agent config for a tenant, cached by version.

    Keyed on ``(tenant, version)``, so publishing a new version simply writes to
    a different key. There is no invalidation step to forget, and no window in
    which a reader can repopulate the old key after a writer cleared it.
    """

    def __init__(self, cache: CacheClient, settings: Settings) -> None:
        self.cache = cache
        self.settings = settings

    async def get(
        self, session: AsyncSession, tenant_id: uuid.UUID, *, version: int | None = None
    ) -> dict[str, Any] | None:
        """The live config as a plain dict, or ``None`` if the tenant has none.

        With no ``version`` the live row is read from PostgreSQL first to learn
        which version is current — one indexed lookup — and Redis then serves
        the body. The alternative, caching "which version is live", would
        reintroduce exactly the invalidation race the version key avoids.
        """
        live = await self._live_row(session, tenant_id, version)
        if live is None:
            return None

        key = tenant_config(tenant_id, live.version)
        cached = await self.cache.get(key)
        if cached is not UNKNOWN and cached is not None:
            try:
                parsed: dict[str, Any] = json.loads(cached)
                return parsed
            except ValueError:
                logger.warning("discarding an unreadable cached agent config")

        payload = _config_payload(live)
        await self.cache.set(key, json.dumps(payload), ttl_s=self.settings.redis_config_cache_ttl_s)
        return payload

    async def _live_row(
        self, session: AsyncSession, tenant_id: uuid.UUID, version: int | None
    ) -> AgentConfig | None:
        query = select(AgentConfig).where(AgentConfig.tenant_id == tenant_id)
        query = (
            query.where(AgentConfig.version == version)
            if version is not None
            else query.where(AgentConfig.is_live.is_(True))
        )
        return (await session.execute(query.limit(1))).scalar_one_or_none()


def _config_payload(config: AgentConfig) -> dict[str, Any]:
    """What a call needs from a config row, and nothing more.

    Narrow on purpose: this crosses into a cache and then into a vendor request,
    so it carries the prompt and the voice and none of our internal bookkeeping.
    """
    return {
        "version": config.version,
        "system_prompt": config.system_prompt,
        "first_message": config.first_message,
        "voice_id": config.voice_id,
        "model_params": config.model_params_json,
    }
