"""A small in-process rate limiter for the signup endpoint.

Every accepted signup eventually spends money on a phone number, so the form is
never left completely open (docs/00_DECISIONS.md section 7.5).

Deliberately in-process and therefore per-replica: the POC runs one API process,
and the alternative is Redis, which is out of scope. The limit is a speed bump
against a stuck retry loop or a casual script, not a defence against a
distributed attacker — see the README's production roadmap.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from app.core.errors import AppError


class RateLimitedError(AppError):
    """Too many requests from one source."""

    code = "rate_limited"
    http_status = 429
    #: The caller may try again later, so this is honestly retryable.
    retryable = True


class SlidingWindowLimiter:
    """At most ``limit`` events per ``window_s``, per key."""

    def __init__(self, *, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, now: float | None = None) -> None:
        """Record a hit, or raise :class:`RateLimitedError`."""
        moment = time.monotonic() if now is None else now
        hits = self._hits[key]

        cutoff = moment - self.window_s
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= self.limit:
            retry_after = max(0.0, hits[0] + self.window_s - moment)
            raise RateLimitedError(
                "too many signups from this address",
                details={"retry_after_s": round(retry_after, 1)},
            )

        hits.append(moment)

    def reset(self) -> None:
        self._hits.clear()
