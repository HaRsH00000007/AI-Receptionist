"""Shared machinery for the fake providers.

The fakes are not stubs. They keep state, enforce the same uniqueness the real
vendors do, and can be told to fail — because the orchestration logic under test
is precisely the logic that deals with failure and retry. A fake that always
succeeds would let a broken retry path pass CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.errors import AppError


@dataclass
class Behaviour:
    """Test control surface attached to every fake provider.

    ``fail("purchase_number", VendorError(...), times=2)`` makes the next two
    calls raise and the third succeed, which is how a retryable-then-recovering
    vendor is simulated.
    """

    _planned: dict[str, list[AppError]] = field(default_factory=dict)
    #: Every call, in order, as (operation, kwargs). Lets a test assert that a
    #: retry did *not* call the vendor twice.
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def fail(self, operation: str, error: AppError, *, times: int = 1) -> None:
        self._planned.setdefault(operation, []).extend([error] * times)

    def record(self, operation: str, **kwargs: Any) -> None:
        """Record the call, then raise if a failure was scheduled for it."""
        self.calls.append((operation, kwargs))
        planned = self._planned.get(operation)
        if planned:
            raise planned.pop(0)

    def count(self, operation: str) -> int:
        return sum(1 for name, _ in self.calls if name == operation)

    def reset(self) -> None:
        self._planned.clear()
        self.calls.clear()
