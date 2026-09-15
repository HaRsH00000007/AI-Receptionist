"""Webhook deduplication — the fast half.

The authoritative half is the unique index on ``webhook_events (provider,
event_id)``. That index is what actually makes a replayed delivery harmless, it
works with Redis stopped, and it survives a restart. This module only saves the
database a round trip on the overwhelmingly common case: a provider retrying a
delivery we finished handling minutes ago.

The asymmetry below is the important part. A *positive* answer from Redis
("seen") short-circuits. A negative or unknown answer does **not** conclude
"new" — it falls through to the database, which decides. Getting that backwards
would mean a Redis eviction or a cold cache turned a duplicate into a second
call record, a second summary and a second email to the customer.
"""

from __future__ import annotations

from app.cache.client import UNKNOWN, CacheClient
from app.cache.keys import webhook_dedupe
from app.core.config import Settings
from app.core.logging import get_logger
from app.models.enums import WebhookProvider

logger = get_logger(__name__)


class WebhookDedupe:
    """A short-circuit for deliveries we have already stored."""

    def __init__(self, cache: CacheClient, settings: Settings) -> None:
        self.cache = cache
        self.settings = settings

    async def seen_before(self, provider: WebhookProvider, event_id: str) -> bool:
        """``True`` only when Redis positively confirms a previous delivery.

        Never ``True`` on a cache miss or an outage — those fall through to the
        unique index, which cannot be wrong.
        """
        key = webhook_dedupe(provider.value, event_id)
        marked = await self.cache.set_if_absent(
            key, "1", ttl_s=self.settings.redis_webhook_dedupe_ttl_s
        )
        if marked is UNKNOWN:
            return False
        # `set_if_absent` returning False means the key was already there, which
        # means a previous delivery marked it.
        return not marked

    async def forget(self, provider: WebhookProvider, event_id: str) -> None:
        """Drop the marker.

        Called when a delivery was marked but then *not* stored — a malformed
        payload rejected before it reached the database, say. Leaving the marker
        would make a corrected redelivery of the same event id look like a
        duplicate and be silently dropped.
        """
        await self.cache.delete(webhook_dedupe(provider.value, event_id))
