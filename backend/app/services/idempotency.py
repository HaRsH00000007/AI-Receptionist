"""Idempotency keys for provisioning steps.

The key answers one question: "is this attempt the same logical operation as a
previous one?" If it is, a side effect already recorded under that key can be
adopted rather than repeated — which is what stops a retry buying a second
phone number.

``attempt_group`` is the escape hatch. Retrying a step keeps the key, because it
is the same operation. Deliberately *redoing* it — the admin "retry from step"
action after a number was released — bumps the group, producing a new key, so
the new attempt is honestly a new operation.
"""

from __future__ import annotations

import hashlib
import uuid

from app.models.enums import ProvisioningStep


def step_idempotency_key(run_id: uuid.UUID, step: ProvisioningStep, attempt_group: int = 0) -> str:
    """``sha256(run_id | step | attempt_group)``, hex.

    Derived rather than random so that it is reproducible: a worker that
    restarts mid-step recomputes the same key and recognises its own work.
    """
    material = f"{run_id}:{step.value}:{attempt_group}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def tenant_resource_name(tenant_id: uuid.UUID) -> str:
    """The name we tag vendor resources with.

    Both adoption guards search on this, so it must be stable and unique per
    tenant. Vendor-side, it is the only link back to us when our own row is
    missing because a worker died before committing.
    """
    return f"tenant:{tenant_id}"
