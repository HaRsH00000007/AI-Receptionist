"""A distributed lock, and an honest account of what it is worth.

This lock is an **optimization, not a guarantee**. It stops two workers doing
the same expensive work at the same moment, which saves a vendor call and a
little contention. It does not, and cannot, protect an invariant: a Redis lock
can be lost to a failover, an expiry under a slow step, or a network partition,
and any correctness argument that rests on one is wrong.

Every invariant that actually matters here is enforced by PostgreSQL instead:

* one in-flight provisioning run per tenant — ``uq_provisioning_runs_in_flight_per_tenant``
* one live number per tenant, one live e164 — partial unique indexes
* one call row per ``provider_call_id`` — a unique index
* one webhook per ``(provider, event_id)`` — a unique index

So when :meth:`acquire` cannot reach Redis it returns *acquired*. That is
deliberate and it is the safe direction: proceeding means the database
constraint decides, which is the outcome we want anyway. Refusing instead would
mean a Redis outage stops provisioning entirely — trading a real outage for an
imaginary safety property.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.cache.client import UNKNOWN, CacheClient
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LockHandle:
    """The outcome of an acquisition attempt."""

    key: str
    token: str
    acquired: bool
    #: True when Redis could not be consulted. The caller proceeded because the
    #: database constraint is the real guard; worth logging, not worth failing.
    degraded: bool = False


class DistributedLock:
    """Single-flight over a key, best-effort."""

    def __init__(self, cache: CacheClient) -> None:
        self.cache = cache

    async def acquire(self, key: str, *, ttl_s: int) -> LockHandle:
        """Try to take the lock.

        The token is unique per attempt so that :meth:`release` can refuse to
        delete a lock that has since expired and been taken by someone else.
        """
        token = uuid.uuid4().hex
        outcome = await self.cache.set_if_absent(key, token, ttl_s=ttl_s)

        if outcome is UNKNOWN:
            # Fail open. See the module docstring: the database is the guard.
            return LockHandle(key=key, token=token, acquired=True, degraded=True)
        return LockHandle(key=key, token=token, acquired=bool(outcome))

    async def release(self, handle: LockHandle) -> None:
        """Release, but only if we still hold it."""
        if not handle.acquired or handle.degraded:
            return
        await self.cache.release_lock(handle.key, handle.token)

    @asynccontextmanager
    async def hold(self, key: str, *, ttl_s: int) -> AsyncIterator[LockHandle]:
        """Acquire for the duration of a block, releasing even on failure."""
        handle = await self.acquire(key, ttl_s=ttl_s)
        try:
            yield handle
        finally:
            await self.release(handle)
