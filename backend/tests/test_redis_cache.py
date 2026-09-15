"""M5 — Redis.

The claim under test is the architectural invariant, not the wiring: **losing
Redis degrades latency, never correctness.** So almost every behaviour below is
asserted twice — once with a live server, once with the cache unreachable — and
the second assertion is the one that matters.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import RateLimitedError, SlidingWindowLimiter
from app.cache.client import UNKNOWN, CacheClient
from app.cache.dedupe import WebhookDedupe
from app.cache.keys import number_lookup, tenant_config, webhook_dedupe
from app.cache.locks import DistributedLock
from app.cache.lookup import ConfigCache, NumberDirectory
from app.cache.ratelimit import DistributedRateLimiter
from app.core.config import Settings
from app.models.enums import PhoneNumberStatus, WebhookProvider
from tests.factories import make_agent_config, make_phone_number, make_tenant
from tests.redis_fixtures import (  # noqa: F401
    cache,
    offline_cache,
    redis_settings,
    redis_url,
)
from tests.support import build_settings

# `asyncio_mode = "auto"` in pyproject already runs async tests; an explicit
# asyncio mark here would be applied to the sync tests too and fail them.


# ===========================================================================
# Keys
# ===========================================================================


def test_keys_are_namespaced_and_distinct() -> None:
    """A key collision between two concerns would be silent and awful."""
    tenant = uuid.uuid4()
    keys = {
        number_lookup("+14155550100"),
        tenant_config(tenant, 1),
        tenant_config(tenant, 2),
        webhook_dedupe("stripe", "evt_1"),
    }
    assert len(keys) == 4
    assert all(key.startswith("v1:") for key in keys)


def test_config_keys_are_version_scoped() -> None:
    """Publishing v2 must not be able to read or overwrite v1's entry.

    This is what removes the invalidation race entirely: there is no moment when
    a reader can repopulate a cleared key with a stale body, because the new
    version was never stored under the old key.
    """
    tenant = uuid.uuid4()
    assert tenant_config(tenant, 1) != tenant_config(tenant, 2)


# ===========================================================================
# The client contract: miss and outage are different answers
# ===========================================================================


async def test_an_unreachable_cache_reports_unknown_not_a_miss(
    offline_cache: CacheClient,  # noqa: F811
) -> None:
    """The distinction the whole module rests on.

    If an outage reported "miss", a dedupe check would call a replay new and a
    lock would hand two workers the same critical section.
    """
    assert await offline_cache.get("anything") is UNKNOWN
    assert await offline_cache.set_if_absent("k", "v", ttl_s=10) is UNKNOWN
    assert await offline_cache.incr_within_window("k", window_ms=1000) is UNKNOWN
    assert offline_cache.enabled is False


async def test_a_live_cache_distinguishes_miss_from_value(cache: CacheClient) -> None:  # noqa: F811
    assert await cache.get("v1:absent") is None  # a real miss, not UNKNOWN
    assert await cache.set("v1:present", "value", ttl_s=30) is True
    assert await cache.get("v1:present") == "value"


async def test_set_if_absent_admits_exactly_one_winner(cache: CacheClient) -> None:  # noqa: F811
    assert await cache.set_if_absent("v1:once", "a", ttl_s=30) is True
    assert await cache.set_if_absent("v1:once", "b", ttl_s=30) is False
    # The loser did not overwrite the winner's value.
    assert await cache.get("v1:once") == "a"


# ===========================================================================
# Locks — an optimization that fails open
# ===========================================================================


async def test_a_lock_excludes_a_second_holder(cache: CacheClient) -> None:  # noqa: F811
    lock = DistributedLock(cache)
    first = await lock.acquire("v1:lock:x", ttl_s=30)
    second = await lock.acquire("v1:lock:x", ttl_s=30)

    assert first.acquired is True
    assert second.acquired is False

    await lock.release(first)
    third = await lock.acquire("v1:lock:x", ttl_s=30)
    assert third.acquired is True


async def test_releasing_a_lock_we_no_longer_hold_does_nothing(
    cache: CacheClient,  # noqa: F811
) -> None:
    """The compare-and-delete.

    Without it, a holder whose lock expired mid-step would delete the *next*
    holder's lock on the way out, admitting a third worker while the second
    still believed it was alone.
    """
    lock = DistributedLock(cache)
    mine = await lock.acquire("v1:lock:y", ttl_s=30)

    # Someone else's value now occupies the key.
    await cache.set("v1:lock:y", "someone-elses-token", ttl_s=30)
    await lock.release(mine)

    assert await cache.get("v1:lock:y") == "someone-elses-token"


async def test_a_lock_fails_open_when_redis_is_down(offline_cache: CacheClient) -> None:  # noqa: F811
    """Proceed, and let the database constraint decide.

    Refusing would mean a Redis outage stops provisioning — trading a real
    outage for a safety property the lock never actually provided. The unique
    indexes on provisioning runs and phone numbers are the true guard.
    """
    handle = await DistributedLock(offline_cache).acquire("v1:lock:z", ttl_s=30)
    assert handle.acquired is True
    assert handle.degraded is True


# ===========================================================================
# Webhook dedupe — fails *closed*, deferring to the unique index
# ===========================================================================


async def test_dedupe_reports_only_a_confirmed_repeat(cache: CacheClient) -> None:  # noqa: F811
    dedupe = WebhookDedupe(cache, build_settings())
    assert await dedupe.seen_before(WebhookProvider.STRIPE, "evt_1") is False
    assert await dedupe.seen_before(WebhookProvider.STRIPE, "evt_1") is True
    # A different event is unaffected.
    assert await dedupe.seen_before(WebhookProvider.STRIPE, "evt_2") is False


async def test_dedupe_never_claims_a_repeat_when_redis_is_down(
    offline_cache: CacheClient,  # noqa: F811
) -> None:
    """The asymmetry that keeps a cold cache safe.

    "Seen" must be a positive confirmation. Answering True on an outage would
    silently drop a genuine first delivery; answering False lets the unique
    index on (provider, event_id) decide, which is always correct.
    """
    dedupe = WebhookDedupe(offline_cache, build_settings())
    assert await dedupe.seen_before(WebhookProvider.ELEVENLABS, "conv_1") is False
    assert await dedupe.seen_before(WebhookProvider.ELEVENLABS, "conv_1") is False


async def test_forgetting_a_marker_allows_a_corrected_redelivery(
    cache: CacheClient,  # noqa: F811
) -> None:
    dedupe = WebhookDedupe(cache, build_settings())
    await dedupe.seen_before(WebhookProvider.TWILIO, "CA1")
    await dedupe.forget(WebhookProvider.TWILIO, "CA1")
    assert await dedupe.seen_before(WebhookProvider.TWILIO, "CA1") is False


# ===========================================================================
# Rate limiting — degrades to per-replica, never to unlimited
# ===========================================================================


async def test_the_distributed_limiter_enforces_its_limit(cache: CacheClient) -> None:  # noqa: F811
    limiter = DistributedRateLimiter(
        cache,
        scope="signup",
        limit=3,
        window_s=60,
        fallback=SlidingWindowLimiter(limit=3, window_s=60),
    )
    for _ in range(3):
        await limiter.check("1.2.3.4")

    with pytest.raises(RateLimitedError):
        await limiter.check("1.2.3.4")

    # A different source has its own budget.
    await limiter.check("5.6.7.8")


async def test_the_limiter_still_limits_when_redis_is_down(
    offline_cache: CacheClient,  # noqa: F811
) -> None:
    """A cache outage must not open the signup form.

    Every accepted signup eventually buys a phone number, so "Redis is down,
    let everyone through" is not an acceptable reading of a cache failure. The
    per-replica limiter is weaker than the global one and far stronger than none.
    """
    limiter = DistributedRateLimiter(
        offline_cache,
        scope="signup",
        limit=2,
        window_s=60,
        fallback=SlidingWindowLimiter(limit=2, window_s=60),
    )
    await limiter.check("1.2.3.4")
    await limiter.check("1.2.3.4")

    with pytest.raises(RateLimitedError):
        await limiter.check("1.2.3.4")


# ===========================================================================
# Configuration
# ===========================================================================


def test_redis_is_disabled_by_default() -> None:
    """Nothing starts depending on a service nobody has configured."""
    assert build_settings().redis_enabled is False


def test_a_redis_password_is_never_logged() -> None:
    from app.cache.client import _redacted

    assert (
        _redacted("redis://:hunter2@cache.internal:6379/0") == "redis://***@cache.internal:6379/0"
    )
    assert _redacted("redis://localhost:6379/0") == "redis://localhost:6379/0"


def test_settings_carry_a_tight_call_path_budget() -> None:
    """Redis sits where latency is audible; a slow cache is worse than none."""
    settings: Settings = build_settings()
    assert settings.redis_socket_timeout_s <= 0.5
    assert settings.redis_connect_timeout_s <= 0.5


# ===========================================================================
# Cached lookups — always with PostgreSQL behind them
# ===========================================================================


async def test_a_number_resolves_to_its_tenant_and_is_then_cached(
    db_session: AsyncSession,
    cache: CacheClient,  # noqa: F811
    redis_settings: Settings,  # noqa: F811
) -> None:
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
    db_session.add(number)
    await db_session.commit()

    directory = NumberDirectory(cache, redis_settings)

    first = await directory.resolve(db_session, number.e164)
    assert first is not None
    assert first.tenant_id == tenant.id

    # The second read is served by Redis — provable because the entry is there.
    assert await cache.get(number_lookup(number.e164)) is not None
    second = await directory.resolve(db_session, number.e164)
    assert second == first


async def test_a_number_resolves_identically_with_redis_down(
    db_session: AsyncSession,
    offline_cache: CacheClient,  # noqa: F811
) -> None:
    """The correctness claim, stated as a test.

    Same question, same answer, no cache. Only the latency differs.
    """
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
    db_session.add(number)
    await db_session.commit()

    owner = await NumberDirectory(offline_cache, build_settings()).resolve(db_session, number.e164)
    assert owner is not None
    assert owner.tenant_id == tenant.id


async def test_an_unowned_number_resolves_to_nothing_and_is_not_cached(
    db_session: AsyncSession,
    cache: CacheClient,  # noqa: F811
    redis_settings: Settings,  # noqa: F811
) -> None:
    """A negative result must not be cached.

    Numbers are bought and linked continuously. Caching "nobody owns this"
    would make a freshly provisioned number unreachable for the whole TTL —
    exactly when a new customer is most likely to dial it to test.
    """
    directory = NumberDirectory(cache, redis_settings)
    assert await directory.resolve(db_session, "+14155550999") is None
    assert await cache.get(number_lookup("+14155550999")) is None


async def test_a_released_number_no_longer_resolves(
    db_session: AsyncSession,
    offline_cache: CacheClient,  # noqa: F811
) -> None:
    """Only ACTIVE numbers route. A released number must not reach its old tenant."""
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    # `released_at` is required by a check constraint once the status says
    # released — the invariant that keeps "we let this number go" honest.
    number = make_phone_number(
        tenant,
        status=PhoneNumberStatus.RELEASED,
        released_at=datetime.now(UTC),
    )
    db_session.add(number)
    await db_session.commit()

    owner = await NumberDirectory(offline_cache, build_settings()).resolve(db_session, number.e164)
    assert owner is None


async def test_the_live_config_is_served_and_cached_by_version(
    db_session: AsyncSession,
    cache: CacheClient,  # noqa: F811
    redis_settings: Settings,  # noqa: F811
) -> None:
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    config = make_agent_config(tenant, version=3, is_live=True)
    db_session.add(config)
    await db_session.commit()

    payload = await ConfigCache(cache, redis_settings).get(db_session, tenant.id)
    assert payload is not None
    assert payload["version"] == 3
    assert payload["system_prompt"] == config.system_prompt
    # Stored under the version key, so publishing v4 cannot collide with it.
    assert await cache.get(tenant_config(tenant.id, 3)) is not None
    assert await cache.get(tenant_config(tenant.id, 4)) is None


async def test_the_config_payload_carries_no_internal_bookkeeping(
    db_session: AsyncSession,
    offline_cache: CacheClient,  # noqa: F811
) -> None:
    """This payload crosses a cache and then a vendor boundary.

    It should carry the prompt and the voice, and nothing that would leak our
    own identifiers or generation metadata to a third party.
    """
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(make_agent_config(tenant, is_live=True))
    await db_session.commit()

    payload = await ConfigCache(offline_cache, build_settings()).get(db_session, tenant.id)
    assert payload is not None
    assert set(payload) == {"version", "system_prompt", "first_message", "voice_id", "model_params"}


async def test_a_tenant_with_no_live_config_gets_nothing(
    db_session: AsyncSession,
    offline_cache: CacheClient,  # noqa: F811
) -> None:
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    # Present but not live — a superseded version must never be served.
    db_session.add(make_agent_config(tenant, is_live=False))
    await db_session.commit()

    assert await ConfigCache(offline_cache, build_settings()).get(db_session, tenant.id) is None


# ===========================================================================
# Readiness — a cache outage must not withdraw the replica
# ===========================================================================


async def test_readyz_stays_ready_when_redis_is_down(api_client: AsyncClient) -> None:
    """Redis is registered as a non-critical dependency.

    A replica that withdrew itself over a cache outage would convert a slow
    service into no service — strictly worse for the caller, since every cache
    read has a PostgreSQL fallback that is always correct.
    """
    response = await api_client.get("/readyz")
    assert response.status_code == 200

    checks = response.json()["checks"]
    assert checks["database"]["critical"] is True
    assert checks["redis"]["critical"] is False
