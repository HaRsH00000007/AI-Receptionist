"""Redis fixtures.

Against a real Redis, never a mock. The things worth testing here — ``SET NX``
returning false for the second caller, a Lua compare-and-delete, a counter that
expires — are precisely the things a mock would simply agree with, and a test
that only proves the mock behaves like the mock proves nothing.

The suite must still pass with no Redis at all, because "Redis is down" is a
supported operating mode rather than an outage to be mourned. So there are two
kinds of test here: those that need a live server (skipped when there is none)
and those that assert the *degraded* path, which need no server and always run.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest

from app.cache.client import CacheClient, build_cache
from app.core.config import Settings
from tests.support import build_settings

DEFAULT_TEST_REDIS_URL = "redis://localhost:56379/15"


def resolve_test_redis_url() -> str:
    """Database 15, so a developer's own keys on 0 are never touched."""
    return os.environ.get("TEST_REDIS_URL", DEFAULT_TEST_REDIS_URL)


async def _server_reachable(url: str) -> str | None:
    from redis.asyncio import Redis

    redis = Redis.from_url(url, socket_timeout=1.0, socket_connect_timeout=1.0)
    try:
        await redis.ping()
        return None
    except Exception as exc:  # noqa: BLE001 - the reason is the useful part
        return f"{type(exc).__name__}: {exc}"
    finally:
        await redis.aclose()


@pytest.fixture(scope="session")
def redis_url() -> str:
    url = resolve_test_redis_url()
    failure = asyncio.run(_server_reachable(url))
    if failure is not None:
        pytest.skip(
            f"Redis is not reachable at {url} ({failure}). "
            "Start it with `docker compose up -d redis`, or set TEST_REDIS_URL."
        )
    return url


@pytest.fixture
def redis_settings(redis_url: str) -> Settings:
    return build_settings(redis_enabled=True, redis_url=redis_url)


@pytest.fixture
async def cache(redis_settings: Settings) -> AsyncIterator[CacheClient]:
    """A live cache, flushed before and after so tests cannot leak into each other."""
    from redis.asyncio import Redis

    client = build_cache(redis_settings)
    flusher = Redis.from_url(redis_settings.redis_url)
    try:
        await flusher.flushdb()
        yield client
    finally:
        await flusher.flushdb()
        await flusher.aclose()
        await client.aclose()


@pytest.fixture
def offline_cache() -> CacheClient:
    """A cache that will never reach Redis.

    ``REDIS_ENABLED=false`` is the same code path as an unreachable server —
    every operation reports ``UNKNOWN`` — which is what makes this a real test
    of degradation rather than a simulation of one.
    """
    return build_cache(build_settings(redis_enabled=False))
