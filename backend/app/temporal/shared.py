"""Types and names shared between the workflow and its activities.

This module is imported *inside the workflow sandbox*, so it must stay free of
database drivers, HTTP clients and anything else that touches the outside world.
It holds identifiers and plain dataclasses, nothing more.

**Nothing here carries a secret.** Activity arguments and return values are
recorded verbatim in Temporal workflow history, which is queryable by anyone
with access to the namespace and is retained long after the run finishes. So the
contract is: workflows pass *identifiers*, and activities load what they need
from the database themselves. An API key, a session token or a webhook secret
must never appear in a workflow argument.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

#: The workflow type name registered with Temporal. Pinned as a constant because
#: renaming the class would otherwise orphan every in-flight execution.
PROVISION_WORKFLOW = "ProvisionTenantWorkflow"

#: Signal sent when a tenant's billing state changes, so a run parked on the
#: money gate resumes immediately instead of waiting for its next poll.
BILLING_UPDATED_SIGNAL = "billing_updated"

#: Query exposing the workflow's own view of progress, for the admin panel and
#: for debugging. The database remains the authoritative answer.
CURRENT_STEP_QUERY = "current_step"


def provision_workflow_id(run_id: uuid.UUID) -> str:
    """The workflow id for a provisioning run.

    Keyed by run, not by tenant. A tenant legitimately has several runs over its
    lifetime — an abandoned one, then a retry — and reusing a tenant-scoped id
    would collide with the previous execution's history.

    Single-flight per tenant is *already* guaranteed, by the partial unique index
    ``uq_provisioning_runs_in_flight_per_tenant``: two in-flight runs for one
    tenant cannot exist, so two workflows for one tenant cannot either. Keeping
    that guarantee in the database rather than in the workflow id means it holds
    even for code paths that never reach Temporal.
    """
    return f"provision-{run_id}"


@dataclass(frozen=True, slots=True)
class ProvisionInput:
    """What starts a provisioning workflow.

    Identifiers only — see the module docstring.
    """

    run_id: str
    tenant_id: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class StepInput:
    """Which step of which run to execute."""

    run_id: str
    step: str


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """What an activity did, summarised for workflow history.

    Deliberately small. The full request/response payloads are written to
    ``provisioning_steps`` where the admin panel reads them; duplicating them
    into history would bloat it and risk carrying vendor detail somewhere it is
    harder to redact.
    """

    step: str
    status: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CompensationOutcome:
    """What the saga undid."""

    released_number: str | None = None
    deleted_agent: str | None = None
    errors: int = 0


#: Error type names surfaced to the workflow through ``ApplicationError.type``.
#:
#: The workflow branches on these rather than on exception classes, because an
#: exception crossing the activity boundary arrives as an ``ApplicationError``
#: with its original type erased to a string.
ERROR_BILLING_BLOCKED = "billing_blocked"
ERROR_TERMINAL = "terminal_error"
