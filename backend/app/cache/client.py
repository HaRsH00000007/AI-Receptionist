"""The Redis client, wrapped so that it cannot take the application down.

Every method here answers in one of three ways, and the difference between the
second and the third is the whole design:

* the value, when Redis answered;
* a definite "no", when Redis answered and there was nothing there;
* :data:`UNKNOWN`, when Redis did not answer at all.

A cache that reports "miss" when it actually means "I could not reach the
cache" is dangerous in exactly the places we use one. A dedupe check that
returns "not a duplicate" because Redis timed out would let a replayed webhook
through; a lock that reports "acquired" for the same reason would let two
workers into the same critical section. So the unreachable case is a distinct
answer, and every caller is forced by the type to decide what it means for
*them* — usually "ask PostgreSQL instead", which is always possible because
PostgreSQL is the source of truth.

Timeouts are deliberately tiny (see ``redis_socket_timeout_s``). Redis sits on
the audible call path, where a slow cache is worse than a missing one: a caller
hearing silence is a customer-visible failure, and a 250ms budget spent waiting
leaves nothing for the PostgreSQL read that will answer correctly anyway.
"""

from __future__ import annotations

import enum
from typing import Final

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.readiness import ReadinessCheck

logger = get_logger(__name__)


class Unknown(enum.Enum):
    """A one-member enum so ``UNKNOWN`` narrows correctly under mypy.

    ``None`` cannot carry this meaning: ``None`` is a legitimate cache miss, and
    conflating "nothing is stored" with "I could not ask" is the bug this whole
    module exists to prevent.
    """

    TOKEN = "unknown"


#: Redis was unreachable. Not a miss — an absence of information.
UNKNOWN: Final = Unknown.TOKEN

#: Release a lock only if we still hold it.
#:
#: The compare-and-delete has to be atomic. Between a naive GET and DEL, the
#: lock can expire and be taken by another worker, and the DEL would then delete
#: *their* lock — handing a third worker into the critical section while the
#: second still believes it is alone.
_RELEASE_LOCK_LUA: Final = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

#: A sliding-window counter: increment, and set the expiry only on the first
#: hit. Two commands, one round trip, no race in which a window never expires.
_RATE_LIMIT_LUA: Final = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class CacheClient:
    """A fail-soft Redis facade.

    Never raises :class:`~redis.exceptions.RedisError` at a caller. A caller
    that wants to know whether the cache is healthy asks :meth:`ping`.
    """

    def __init__(self, redis: Redis | None, *, enabled: bool) -> None:
        self._redis = redis
        self._enabled = enabled and redis is not None
        #: Set once a failure has been logged, so a sustained outage produces
        #: one warning rather than one per request. Reset on the next success,
        #: so a recovery is visible too.
        self._degraded = False

    @property
    def enabled(self) -> bool:
        """Whether this client will attempt any Redis call at all."""
        return self._enabled

    # ---- failure handling ------------------------------------------------
    def _failed(self, operation: str, exc: Exception) -> Unknown:
        if not self._degraded:
            self._degraded = True
            logger.warning(
                "redis is unreachable; falling back to postgresql",
                extra={"operation": operation, "error": str(exc)},
            )
        return UNKNOWN

    def _succeeded(self) -> None:
        if self._degraded:
            self._degraded = False
            logger.info("redis is reachable again")

    # ---- primitives ------------------------------------------------------
    async def get(self, key: str) -> str | Unknown | None:
        """The stored value, ``None`` for a miss, ``UNKNOWN`` if Redis is down."""
        if not self._enabled or self._redis is None:
            return UNKNOWN
        try:
            value = await self._redis.get(key)
        except (RedisError, OSError) as exc:
            return self._failed("get", exc)
        self._succeeded()
        return value.decode("utf-8") if isinstance(value, bytes) else value

    async def set(self, key: str, value: str, *, ttl_s: int) -> bool:
        """Store with an expiry. ``False`` means it was not stored.

        Every write carries a TTL. A key without one is a memory leak that a
        restart hides and production eventually finds.
        """
        if not self._enabled or self._redis is None:
            return False
        try:
            await self._redis.set(key, value, ex=ttl_s)
        except (RedisError, OSError) as exc:
            self._failed("set", exc)
            return False
        self._succeeded()
        return True

    async def delete(self, *keys: str) -> bool:
        if not self._enabled or self._redis is None or not keys:
            return False
        try:
            await self._redis.delete(*keys)
        except (RedisError, OSError) as exc:
            self._failed("delete", exc)
            return False
        self._succeeded()
        return True

    async def set_if_absent(self, key: str, value: str, *, ttl_s: int) -> bool | Unknown:
        """``SET key value NX PX ttl``.

        ``True`` — we set it, so we are first. ``False`` — someone else was
        there. ``UNKNOWN`` — Redis did not answer, and the caller must not
        assume either.
        """
        if not self._enabled or self._redis is None:
            return UNKNOWN
        try:
            stored = await self._redis.set(key, value, ex=ttl_s, nx=True)
        except (RedisError, OSError) as exc:
            return self._failed("set_if_absent", exc)
        self._succeeded()
        return bool(stored)

    async def release_lock(self, key: str, token: str) -> bool:
        """Delete ``key`` only if it still holds ``token``."""
        if not self._enabled or self._redis is None:
            return False
        try:
            released = await self._redis.eval(_RELEASE_LOCK_LUA, 1, key, token)
        except (RedisError, OSError) as exc:
            self._failed("release_lock", exc)
            return False
        self._succeeded()
        return bool(released)

    async def incr_within_window(self, key: str, *, window_ms: int) -> int | Unknown:
        """Count this hit inside a fixed window, returning the running total."""
        if not self._enabled or self._redis is None:
            return UNKNOWN
        try:
            count = await self._redis.eval(_RATE_LIMIT_LUA, 1, key, str(window_ms))
        except (RedisError, OSError) as exc:
            return self._failed("incr_within_window", exc)
        self._succeeded()
        return int(count)

    # ---- health ----------------------------------------------------------
    async def ping(self) -> bool:
        if not self._enabled or self._redis is None:
            return False
        try:
            await self._redis.ping()
        except (RedisError, OSError) as exc:
            self._failed("ping", exc)
            return False
        self._succeeded()
        return True

    async def aclose(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except (RedisError, OSError):  # pragma: no cover - shutdown only
                logger.debug("redis connection close failed; ignoring during shutdown")


def build_cache(settings: Settings) -> CacheClient:
    """Resolve the cache for this process.

    With ``REDIS_ENABLED=false`` this returns a client that never calls Redis
    and reports ``UNKNOWN`` for everything — which is precisely the "Redis is
    down" test variant, exercised as a first-class configuration rather than
    simulated with mocks.
    """
    if not settings.redis_enabled:
        logger.info("redis is disabled; caches take their postgresql fallback")
        return CacheClient(None, enabled=False)

    redis = Redis.from_url(
        settings.redis_url,
        socket_timeout=settings.redis_socket_timeout_s,
        socket_connect_timeout=settings.redis_connect_timeout_s,
        # Without this a dead connection in the pool blocks until the OS gives
        # up, which is far longer than any budget on the call path.
        health_check_interval=30,
        retry_on_timeout=False,
    )
    logger.info("redis enabled", extra={"url": _redacted(settings.redis_url)})
    return CacheClient(redis, enabled=True)


def _redacted(url: str) -> str:
    """A Redis URL with any password removed, safe to log."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _, _, host = rest.rpartition("@")
    return f"{scheme}://***@{host}"


def make_cache_check(cache: CacheClient) -> ReadinessCheck:
    """A readiness check for the cache.

    Deliberately **not** registered as a hard readiness dependency by default:
    Redis being down degrades latency, not correctness, and a replica that
    refuses traffic over a cache outage converts a slow service into no service.
    The check exists so the state is *visible*; see ``app.main`` for how it is
    reported without failing readiness.
    """

    async def check() -> str | None:
        if not cache.enabled:
            return None
        return None if await cache.ping() else "redis is not reachable"

    return check
