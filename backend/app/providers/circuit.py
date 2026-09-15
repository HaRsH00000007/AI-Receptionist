"""A circuit breaker for vendor calls.

Retries assume the failure is transient and that trying again might work. When a
vendor is genuinely down that assumption inverts: every retry adds latency to a
request that will fail anyway, holds a connection, and — on the call path —
makes a caller wait through timeouts before hearing anything. Retrying into a
dead vendor turns their outage into a worse outage of ours.

So after enough consecutive failures the circuit opens and calls fail
*immediately* with a classified error. The value is not that the vendor
recovers sooner; it is that our own fallback runs in milliseconds instead of
after a 30-second timeout, which is the difference between a caller hearing "our
receptionist is briefly unavailable, please leave a message" and a caller
hearing nothing and hanging up.

Three states, the standard ones:

* **closed** — calls pass through; consecutive failures are counted.
* **open** — calls are refused without being attempted, until a cooldown passes.
* **half-open** — one probe is allowed. Success closes the circuit; failure
  reopens it for another cooldown.

**Per process, deliberately.** A shared circuit in Redis would coordinate
replicas, and would also mean a Redis problem could open every circuit at once
— a cache outage escalating into a total vendor outage. A per-process breaker
is less coordinated and cannot fail that way.

What is *not* here matters as much. This breaker never wraps a payment or a
number purchase in a way that could retry them: it only ever prevents a call
from being attempted. A breaker that opened mid-purchase and was then retried by
a caller reading it as transient would be a way to buy two phone numbers, so
callers treat :class:`CircuitOpenError` as the vendor being unavailable and take
their documented fallback, never as "try again immediately".
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field

from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)


class CircuitState(enum.StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(AppError):
    """The vendor is being treated as down; the call was not attempted.

    ``retryable`` is True because the *operation* is legitimately retryable
    later — but callers on the audible path must take their fallback now rather
    than looping, and the orchestrator's backoff is what spaces later attempts.
    """

    code = "circuit_open"
    http_status = 503
    retryable = True


@dataclass(slots=True)
class CircuitBreaker:
    """Consecutive-failure breaker for one vendor."""

    name: str
    #: Consecutive failures before the circuit opens. Low enough to matter on a
    #: real outage, high enough that a single blip does not trip it.
    failure_threshold: int = 5
    #: How long the circuit stays open before a probe is allowed.
    cooldown_s: float = 30.0

    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = field(default=None)

    def _now(self) -> float:
        # Monotonic: a wall-clock jump (NTP, a VM resuming) must not either
        # trap the circuit open forever or reopen it early.
        return time.monotonic()

    def allows(self, *, now: float | None = None) -> bool:
        """Whether a call may be attempted, advancing the state if due."""
        moment = self._now() if now is None else now

        if self.state is CircuitState.CLOSED:
            return True

        if self.state is CircuitState.OPEN:
            if self.opened_at is not None and moment - self.opened_at >= self.cooldown_s:
                self.state = CircuitState.HALF_OPEN
                logger.info(
                    "circuit half-open; allowing one probe",
                    extra={"vendor": self.name},
                )
                return True
            return False

        # Half-open: exactly one probe is in flight. Refusing the rest is the
        # point — letting a thundering herd through on recovery is how a
        # recovering vendor is knocked straight back down.
        return False

    def record_success(self) -> None:
        if self.state is not CircuitState.CLOSED:
            logger.info("circuit closed; vendor recovered", extra={"vendor": self.name})
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self, *, now: float | None = None) -> None:
        moment = self._now() if now is None else now
        self.consecutive_failures += 1

        if self.state is CircuitState.HALF_OPEN:
            # The probe failed. Straight back to open for another cooldown.
            self.state = CircuitState.OPEN
            self.opened_at = moment
            logger.warning("circuit reopened; probe failed", extra={"vendor": self.name})
            return

        if self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = moment
            logger.error(
                "circuit opened; treating the vendor as down",
                extra={"vendor": self.name, "failures": self.consecutive_failures},
            )

    def check(self) -> None:
        """Raise if the circuit is open."""
        if not self.allows():
            raise CircuitOpenError(
                f"{self.name} is unavailable",
                details={"vendor": self.name, "state": self.state.value},
            )


class CircuitRegistry:
    """One breaker per vendor, for this process."""

    def __init__(self, *, failure_threshold: int = 5, cooldown_s: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._breakers: dict[str, CircuitBreaker] = {}

    def for_vendor(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=self.failure_threshold,
                cooldown_s=self.cooldown_s,
            )
        return self._breakers[name]

    def states(self) -> dict[str, str]:
        """What an operator sees on the vendor status board."""
        return {name: breaker.state.value for name, breaker in self._breakers.items()}

    def any_open(self) -> bool:
        return any(breaker.state is not CircuitState.CLOSED for breaker in self._breakers.values())
