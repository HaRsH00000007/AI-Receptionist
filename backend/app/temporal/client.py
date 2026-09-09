"""Connecting to Temporal.

One place builds the client, so local development and Temporal Cloud differ by
configuration rather than by code — which is the whole point of the local/AWS
mapping in the migration plan. Locally that is a plain gRPC connection to the
compose service; in Cloud it is the same call with mTLS material attached.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client, TLSConfig
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.enums import ProvisioningStep
from app.temporal.shared import (
    BILLING_UPDATED_SIGNAL,
    ProvisionInput,
    provision_workflow_id,
)
from app.temporal.workflows import ProvisionOptions, ProvisionTenantWorkflow

logger = get_logger(__name__)


def _tls(settings: Settings) -> TLSConfig | None:
    """mTLS material for Temporal Cloud, or ``None`` for a local server.

    Both halves of the pair are validated at startup by
    ``Settings._temporal_tls_material_present``, so by the time this runs a
    certificate without its key is already impossible.
    """
    material = settings.temporal_tls_material
    if material is None:
        return None
    cert_path, key_path = material
    return TLSConfig(
        client_cert=Path(cert_path).read_bytes(),
        client_private_key=Path(key_path).read_bytes(),
    )


async def connect(settings: Settings) -> Client:
    """A Temporal client for this process."""
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        tls=_tls(settings) or settings.temporal_tls_enabled,
    )
    logger.info(
        "connected to temporal",
        extra={
            "address": settings.temporal_address,
            "namespace": settings.temporal_namespace,
            "task_queue": settings.temporal_task_queue,
        },
    )
    return client


async def start_provisioning(
    client: Client,
    settings: Settings,
    *,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    correlation_id: str,
) -> str:
    """Start (or adopt) the workflow for a provisioning run.

    Idempotent by construction. The workflow id is derived from the run id, so a
    duplicate start — a resubmitted signup, a retried API call, two API replicas
    racing — must not begin a second execution over the same run, because two
    orchestrators would both walk the sequence and could both buy a number.

    Temporal enforces that by *rejecting* the second start, so the rejection is
    caught and treated as success: the existing execution is already doing the
    work, which is precisely what the caller wanted.

    Only identifiers are passed. Nothing here is a credential, because arguments
    are recorded verbatim in workflow history.
    """
    workflow_id = provision_workflow_id(run_id)
    try:
        handle = await client.start_workflow(
            ProvisionTenantWorkflow.run,
            args=[
                ProvisionInput(
                    run_id=str(run_id),
                    tenant_id=str(tenant_id),
                    correlation_id=correlation_id,
                ),
                ProvisionOptions(
                    max_attempts=settings.provisioning_max_attempts,
                    activity_timeout_s=settings.temporal_activity_timeout_s,
                    billing_recheck_interval_s=settings.billing_recheck_interval_s,
                ),
            ],
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
            # A whole-workflow ceiling. Provisioning that has not finished in an
            # hour is not going to; it needs an operator, not another retry.
            execution_timeout=timedelta(seconds=settings.temporal_workflow_timeout_s),
        )
    except WorkflowAlreadyStartedError:
        logger.info(
            "provisioning workflow already running; adopted",
            extra={"run_id": str(run_id), "workflow_id": workflow_id},
        )
        return workflow_id

    logger.info(
        "provisioning workflow started",
        extra={
            "run_id": str(run_id),
            "tenant_id": str(tenant_id),
            "workflow_id": handle.id,
            "correlation_id": correlation_id,
        },
    )
    return handle.id


async def signal_billing_updated(client: Client, run_id: uuid.UUID) -> bool:
    """Wake a workflow parked on the money gate. Returns whether one was found.

    Best-effort by design: the workflow also re-checks on its own timer, so a
    signal that misses (the workflow already finished, or never started) is not
    an error. The signal only ends the wait — the gate is re-evaluated from the
    database when the workflow resumes, so this cannot grant entitlement.
    """
    try:
        handle = client.get_workflow_handle(provision_workflow_id(run_id))
        await handle.signal(BILLING_UPDATED_SIGNAL)
    except RPCError:
        return False
    return True


__all__ = [
    "ProvisioningStep",
    "connect",
    "signal_billing_updated",
    "start_provisioning",
]
