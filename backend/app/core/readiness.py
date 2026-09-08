"""Readiness checks.

Liveness and readiness answer different questions, so they must not share an
implementation. ``/healthz`` says "this process is alive" and must never fail
because a dependency is briefly slow — otherwise the orchestrator restarts a
perfectly healthy process during a database blip. ``/readyz`` says "this process
can serve traffic" and does consult dependencies.

Dependencies register themselves here rather than being hard-coded into the
endpoint, so Module 2 can add a database ping without touching the API layer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)

# A check returns None when healthy, or a short reason when not.
ReadinessCheck = Callable[[], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str | None = None


class ReadinessRegistry:
    """The set of dependencies that must be reachable to serve traffic."""

    def __init__(self) -> None:
        self._checks: dict[str, ReadinessCheck] = {}

    def register(self, name: str, check: ReadinessCheck) -> None:
        if name in self._checks:
            raise ValueError(f"readiness check {name!r} is already registered")
        self._checks[name] = check

    @property
    def names(self) -> list[str]:
        return sorted(self._checks)

    async def run(self) -> list[CheckResult]:
        """Run every check. A raising check is a failing check, never a 500."""
        results: list[CheckResult] = []
        for name, check in self._checks.items():
            try:
                detail = await check()
            except Exception as exc:  # noqa: BLE001 - a failed probe is data, not a crash
                logger.warning("readiness check failed", extra={"check": name, "error": str(exc)})
                results.append(CheckResult(name=name, ok=False, detail=str(exc)))
            else:
                results.append(CheckResult(name=name, ok=detail is None, detail=detail))
        return results
