"""Rate limiting across replicas.

The in-process limiter in :mod:`app.api.rate_limit` is per-replica, which means
its real limit is ``limit x replicas`` — fine for one container, wrong the
moment there are three. This puts the counter in Redis so the limit is the
limit regardless of how many processes are serving.

**The fallback direction matters.** When Redis is unreachable this degrades to
the in-process limiter rather than to no limiting at all. The endpoints behind
it are the two that cost money or hand out credentials — signup buys a phone
number, and a magic link is a bearer token in an inbox — so "the cache is down,
let everyone through" is not an acceptable reading of a cache outage. A
per-replica limit is weaker than a global one and much stronger than none.
"""

from __future__ import annotations

from app.api.rate_limit import RateLimitedError, SlidingWindowLimiter
from app.cache.client import UNKNOWN, CacheClient
from app.cache.keys import rate_limit as rate_limit_key
from app.core.logging import get_logger

logger = get_logger(__name__)


class DistributedRateLimiter:
    """A fixed-window counter in Redis, with a local limiter behind it."""

    def __init__(
        self,
        cache: CacheClient,
        *,
        scope: str,
        limit: int,
        window_s: float,
        fallback: SlidingWindowLimiter,
    ) -> None:
        self.cache = cache
        self.scope = scope
        self.limit = limit
        self.window_s = window_s
        self.fallback = fallback

    async def check(self, identifier: str) -> None:
        """Record a hit, or raise :class:`RateLimitedError`."""
        count = await self.cache.incr_within_window(
            rate_limit_key(self.scope, identifier),
            window_ms=int(self.window_s * 1000),
        )

        if count is UNKNOWN:
            # Redis is down. Still limit — just per-replica.
            self.fallback.check(identifier)
            return

        if count > self.limit:
            raise RateLimitedError(
                "too many requests from this source",
                details={"scope": self.scope, "retry_after_s": round(self.window_s, 1)},
            )
