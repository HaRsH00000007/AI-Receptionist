"""Step name → implementation.

A plain map, so the state machine's shape is readable in one screen and adding a
step is a two-line change here plus a new module. The completeness check runs at
import: a step in the enum with no implementation would otherwise fail at 3am on
a real tenant instead of immediately in CI.
"""

from __future__ import annotations

from app.models.enums import STEP_SEQUENCE, ProvisioningStep
from app.provisioning.context import StepFunction
from app.provisioning.steps import (
    activate,
    create_agent,
    generate_config,
    link_number,
    purchase_number,
    validate,
    verify,
)

STEP_IMPLEMENTATIONS: dict[ProvisioningStep, StepFunction] = {
    ProvisioningStep.VALIDATE: validate.run,
    ProvisioningStep.GENERATE_CONFIG: generate_config.run,
    ProvisioningStep.PURCHASE_NUMBER: purchase_number.run,
    ProvisioningStep.CREATE_AGENT: create_agent.run,
    ProvisioningStep.LINK_NUMBER: link_number.run,
    ProvisioningStep.VERIFY: verify.run,
    ProvisioningStep.ACTIVATE: activate.run,
}

#: Steps whose side effects are irreversible and must be undone on abandonment.
#: Ordered last-first, the way a saga unwinds.
COMPENSATABLE_STEPS: tuple[ProvisioningStep, ...] = (
    ProvisioningStep.CREATE_AGENT,
    ProvisioningStep.PURCHASE_NUMBER,
)

_missing = set(STEP_SEQUENCE) - set(STEP_IMPLEMENTATIONS)
if _missing:  # pragma: no cover - a wiring mistake, caught at import
    raise RuntimeError(f"provisioning steps without an implementation: {sorted(_missing)}")


def implementation_for(step: ProvisioningStep) -> StepFunction:
    return STEP_IMPLEMENTATIONS[step]
