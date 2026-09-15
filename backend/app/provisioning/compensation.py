"""Undo what a failed run bought.

An orphaned Twilio number bills every month, forever, and nobody notices —
which is the single most expensive failure mode in this system
(docs/00_DECISIONS.md section 7.1). So a run that fails terminally does not just
stop: it releases the number and deletes the agent, and records that it did.

Every action here is safe to run twice. Releasing an unknown SID and deleting a
missing agent are both no-ops at the vendor, because compensation itself can be
interrupted and retried.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import Agent, PhoneNumber, ProvisioningRun, Tenant
from app.models.enums import (
    AgentStatus,
    PhoneNumberStatus,
    ProvisioningStatus,
    TenantStatus,
)
from app.providers.registry import Providers

logger = get_logger(__name__)


async def compensate_run(
    session: AsyncSession,
    *,
    run: ProvisioningRun,
    tenant: Tenant,
    providers: Providers,
    settings: Settings,
) -> dict[str, object]:
    """Release vendor resources for an abandoned run.

    Returns a report of what was undone, which is written onto the run so an
    operator can see that money stopped being spent.
    """
    run.status = ProvisioningStatus.COMPENSATING
    await session.commit()

    report: dict[str, object] = {
        "released_number": None,
        "deleted_agent": None,
        "detached_shared_agent": None,
        "errors": [],
    }
    errors: list[str] = []

    agent = (
        await session.execute(
            select(Agent)
            .where(Agent.tenant_id == tenant.id)
            .where(Agent.status == AgentStatus.ACTIVE)
        )
    ).scalar_one_or_none()
    if agent is not None and agent.is_shared:
        # NEVER delete a shared vertical agent. It is serving every other tenant
        # on this vertical, and deleting it to compensate one failed signup
        # would take all of their receptionists down at once — turning a single
        # customer's bad day into an outage. Detaching this tenant's row is the
        # entire compensation: the tenant stops being routed there, and the
        # agent is untouched because we never owned it exclusively.
        agent.status = AgentStatus.DELETED
        report["detached_shared_agent"] = agent.elevenlabs_agent_id
        logger.info(
            "detached a tenant from its shared agent; the agent itself is untouched",
            extra={"tenant_id": str(tenant.id), "agent_id": agent.elevenlabs_agent_id},
        )
    elif agent is not None and agent.elevenlabs_agent_id:
        try:
            await providers.elevenlabs.delete_agent(agent_id=agent.elevenlabs_agent_id)
            report["deleted_agent"] = agent.elevenlabs_agent_id
            agent.status = AgentStatus.DELETED
        except AppError as exc:
            # Recorded, never fatal: failing to delete an agent must not stop us
            # releasing the number, which is the part that costs money.
            errors.append(f"delete_agent: {exc.code}")
            logger.warning(
                "could not delete agent during compensation",
                extra={"tenant_id": str(tenant.id), "code": exc.code},
            )

    number = (
        (
            await session.execute(
                select(PhoneNumber)
                .where(PhoneNumber.tenant_id == tenant.id)
                .where(
                    PhoneNumber.status.in_((PhoneNumberStatus.ACTIVE, PhoneNumberStatus.PENDING))
                )
            )
        )
        .scalars()
        .first()
    )
    if number is not None:
        if number.twilio_sid:
            try:
                await providers.twilio.release_number(sid=number.twilio_sid)
                report["released_number"] = number.e164
                number.status = PhoneNumberStatus.RELEASED
                number.released_at = datetime.now(UTC)
            except AppError as exc:
                errors.append(f"release_number: {exc.code}")
                logger.error(
                    "could not release number during compensation; it will keep billing",
                    extra={"tenant_id": str(tenant.id), "sid": number.twilio_sid, "code": exc.code},
                )
        else:
            # Never bought; the pending row is just intent, so mark it failed
            # rather than released — nothing exists at the vendor to release.
            number.status = PhoneNumberStatus.FAILED

    report["errors"] = errors
    run.status = ProvisioningStatus.COMPENSATED
    run.finished_at = datetime.now(UTC)
    tenant.status = TenantStatus.ABANDONED
    await session.commit()

    logger.info(
        "compensation finished",
        extra={"tenant_id": str(tenant.id), "run_id": str(run.id), **_loggable(report)},
    )
    return report


def _loggable(report: dict[str, object]) -> dict[str, object]:
    return {
        "released_number": report.get("released_number"),
        "deleted_agent": report.get("deleted_agent"),
        "compensation_errors": len(report.get("errors", [])),  # type: ignore[arg-type]
    }
