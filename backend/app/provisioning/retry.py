"""Retry policy.

Backoff lives in the database as ``next_attempt_at`` rather than in a sleep,
because a schedule that only exists in a worker's memory is lost the moment the
process restarts — which is exactly when you need it most.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.errors import AppError


def backoff_delay_s(attempt: int, schedule: tuple[int, ...]) -> int:
    """Delay before attempt number ``attempt`` (1-based).

    The last entry repeats, so a long schedule degrades to a steady poll rather
    than to an unbounded wait.
    """
    if not schedule:
        return 60
    index = min(max(attempt, 1) - 1, len(schedule) - 1)
    return schedule[index]


def next_attempt_at(
    attempt: int, schedule: tuple[int, ...], *, now: datetime | None = None
) -> datetime:
    moment = now or datetime.now(UTC)
    return moment + timedelta(seconds=backoff_delay_s(attempt, schedule))


def should_retry(error: Exception, *, attempt: int, max_attempts: int) -> bool:
    """Whether a failed step gets another go.

    Two independent conditions, both required. The error must be *classified*
    retryable — never inferred from its type here, because that judgement
    belongs with the code that raised it — and the attempt budget must not be
    exhausted. An unknown exception is treated as retryable once: an unexpected
    crash is more often a blip than a permanent truth, and the attempt cap stops
    it looping forever.
    """
    if attempt >= max_attempts:
        return False
    if isinstance(error, AppError):
        return error.retryable
    return True
