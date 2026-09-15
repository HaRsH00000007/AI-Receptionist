"""Redis: cache, locks, rate limiting and webhook dedupe.

One rule governs everything in this package, and it is the rule the production
plan states as an invariant: **PostgreSQL is the source of truth; losing Redis
degrades latency, never correctness.** Every read here has a database fallback,
every lock is an optimization over a database constraint, and every dedupe check
defers to a unique index.

The practical test of that claim is that the suite runs with ``REDIS_ENABLED``
both ways and must pass identically on correctness either way.
"""

from __future__ import annotations

from app.cache.client import UNKNOWN, CacheClient, Unknown, build_cache, make_cache_check
from app.cache.dedupe import WebhookDedupe
from app.cache.locks import DistributedLock, LockHandle
from app.cache.lookup import ConfigCache, NumberDirectory, NumberOwner
from app.cache.ratelimit import DistributedRateLimiter

__all__ = [
    "UNKNOWN",
    "CacheClient",
    "ConfigCache",
    "DistributedLock",
    "DistributedRateLimiter",
    "LockHandle",
    "NumberDirectory",
    "NumberOwner",
    "Unknown",
    "WebhookDedupe",
    "build_cache",
    "make_cache_check",
]
