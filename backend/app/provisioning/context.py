"""What a provisioning step is handed, and what it gives back.

The signature is deliberately narrow — one context in, one result out, no
globals, no ambient session. That is what makes a step a pure unit that can be
tested on its own, retried without surprises, and later lifted into a Temporal
activity with the body unchanged (docs/00_DECISIONS.md section 4).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import ProvisioningRun, ProvisioningStepRecord, Tenant
from app.providers.registry import Providers


@dataclass(slots=True)
class StepContext:
    """Everything one step is allowed to touch."""

    session: AsyncSession
    settings: Settings
    providers: Providers
    tenant: Tenant
    run: ProvisioningRun
    record: ProvisioningStepRecord

    @property
    def correlation_id(self) -> str:
        return self.run.correlation_id

    @property
    def idempotency_key(self) -> str:
        return self.record.idempotency_key


@dataclass(slots=True)
class StepResult:
    """What a step reports back.

    ``request`` and ``response`` are persisted verbatim onto the step row and are
    what the admin panel shows — the exact payload sent and the exact answer
    received. That is the difference between a 3am failure taking five minutes
    and taking a day.
    """

    #: Summarised outbound payload. Never contains credentials.
    request: dict[str, Any] = field(default_factory=dict)
    #: Summarised vendor response.
    response: dict[str, Any] = field(default_factory=dict)


#: One step = one function. Signature fixed so the registry stays a plain map.
StepFunction = Callable[[StepContext], Awaitable[StepResult]]
