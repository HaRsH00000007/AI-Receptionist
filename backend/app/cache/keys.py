"""Every Redis key this application uses, built in one place.

Keys are namespaced and versioned rather than composed at each call site. Two
reasons, both learned the hard way elsewhere:

* A typo in a key string is invisible. A cache that silently never hits looks
  exactly like a cache that is working, only slower, and nothing fails.
* Key *shape* is a compatibility surface. Changing what goes into ``cfg:`` while
  an old replica is still running means two processes disagreeing about what a
  key means, which is worse than no cache at all. Bumping ``_NAMESPACE`` on a
  breaking change makes the old keys unreachable instead of misread.

The patterns themselves are fixed by ``docs/02_PLAN_PRODUCTION.md`` section 3.
"""

from __future__ import annotations

import uuid

#: Bumped when a key's *meaning* or encoding changes. Old keys then expire
#: unread rather than being interpreted under the new rules.
_NAMESPACE = "v1"


def _key(*parts: str) -> str:
    return ":".join((_NAMESPACE, *parts))


def number_lookup(e164: str) -> str:
    """``num:{e164}`` — dialled number to tenant, on the audible call path."""
    return _key("num", e164)


def tenant_config(tenant_id: uuid.UUID, version: int) -> str:
    """``cfg:{tenant}:v{version}`` — the rendered agent config.

    Version-keyed on purpose. An invalidation race — writer deletes, reader
    repopulates from a stale read, cache now holds the old config with a fresh
    TTL — cannot happen if the new version simply uses a different key. The old
    key is never deleted; it expires unreferenced.
    """
    return _key("cfg", str(tenant_id), f"v{version}")


def webhook_dedupe(provider: str, event_id: str) -> str:
    """``wh:{provider}:{event_id}`` — the fast half of replay protection.

    The authoritative half is the unique index on ``webhook_events``. This only
    saves the database a round trip on the common case of a provider retrying a
    delivery we have already handled.
    """
    return _key("wh", provider, event_id)


def provision_lock(tenant_id: uuid.UUID) -> str:
    """``lock:provision:{tenant}`` — single-flight provisioning.

    Also only an optimization: the real guarantee is the partial unique index
    ``uq_provisioning_runs_in_flight_per_tenant``, which holds even when Redis
    is down, and which no amount of lock contention can bypass.
    """
    return _key("lock", "provision", str(tenant_id))


def rate_limit(scope: str, identifier: str) -> str:
    """``rl:{scope}:{identifier}`` — one sliding window."""
    return _key("rl", scope, identifier)


def call_session(provider_call_id: str) -> str:
    """``call:{provider_call_id}`` — in-flight call context.

    Written by the inbound TwiML handler and read by ``/voice/init`` moments
    later. Short-lived by nature: the conversation either starts or it does not.
    """
    return _key("call", provider_call_id)
