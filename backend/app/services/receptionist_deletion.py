"""Deleting a receptionist a customer no longer wants.

The same shape as :func:`app.provisioning.compensation.compensate_run`, which
undoes a *failed* setup. This undoes a *finished* one, at the owner's request,
and the ordering is the part that matters:

1. **Stop the calls first.** The tenant is cancelled and committed before any
   vendor call, because ``app.telephony.routing.decide`` already answers a
   cancelled tenant with "this number is no longer in service" and hangs up. The
   number-lookup cache carries the tenant's status, so it is invalidated
   immediately — otherwise a cached entry would keep routing callers to a
   receptionist that is being torn down.
2. **Unlink, then delete, at the vendor.** The ElevenLabs phone import goes
   first so the number cannot reach the agent even if the agent delete fails.
3. **Release the Twilio number only when asked**, only when we bought it, and
   only when it belongs to this tenant.

What is *not* done, deliberately:

* **The tenant row is never deleted.** Calls, transcripts, summaries, usage and
  audit rows hang off it with ``ON DELETE CASCADE``; deleting the row to "clean
  up" would erase the business's own history, which it may need for a dispute
  long after it stops being a customer.
* **A shared vertical agent is never deleted** — only detached. It serves every
  other tenant on that vertical (see ``compensation`` for the same guard).
* **Nothing is reported as complete when a vendor call failed.** The failures
  come back as codes and the rows are left in the state a retry can finish
  from: the whole operation is safe to repeat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.client import CacheClient
from app.cache.lookup import NumberDirectory
from app.core.config import Settings
from app.core.errors import AppError, ConflictError
from app.core.logging import get_logger
from app.models import Agent, PhoneNumber, Tenant
from app.models.enums import (
    ActorType,
    AgentStatus,
    AuditAction,
    PhoneNumberStatus,
    TenantStatus,
)
from app.models.website_onboarding import WebsiteOnboarding
from app.providers.registry import Providers
from app.services.audit import Actor, AuditService

logger = get_logger(__name__)

#: Statuses a receptionist can be deleted from. ``CANCELLED`` is included so a
#: delete whose vendor cleanup failed can simply be called again; ``PENDING`` is
#: not, because provisioning is still running and could buy a number moments
#: after we cancelled the tenant.
DELETABLE = frozenset({TenantStatus.ACTIVE, TenantStatus.FAILED, TenantStatus.CANCELLED})

#: Number states that still cost money or still route, and therefore still need
#: handling. A released row is already done.
LIVE_NUMBER_STATUSES = (PhoneNumberStatus.ACTIVE, PhoneNumberStatus.PENDING)


@dataclass(slots=True)
class DeletionOutcome:
    """What the delete actually managed to do.

    ``complete`` is false whenever a vendor call failed, so the UI can say
    "deleted, but the number is still ours" rather than claiming success it
    cannot stand behind.
    """

    tenant_status: str
    #: The vendor agent was deleted (false when there was none, or it failed).
    agent_deleted: bool = False
    #: A shared vertical agent was detached rather than deleted.
    shared_agent_detached: bool = False
    number_released: bool = False
    #: The number we kept, in E.164 — it stays in the Twilio account and keeps
    #: billing until someone releases it.
    number_retained: str | None = None
    #: Why a requested release did not happen, as a stable code.
    release_refused: str | None = None
    #: Vendor failure codes. Never a provider payload or a credential.
    errors: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.errors


class ReceptionistDeletionService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        providers: Providers,
        cache: CacheClient,
    ) -> None:
        self.session = session
        self.settings = settings
        self.providers = providers
        self.cache = cache
        self.audit = AuditService(session)

    async def delete(
        self, *, tenant: Tenant, release_number: bool, actor: Actor
    ) -> DeletionOutcome:
        if tenant.status not in DELETABLE:
            raise ConflictError(
                "a receptionist can be deleted once setup has finished",
                details={"tenant_status": tenant.status.value},
            )

        numbers = (
            (
                await self.session.execute(
                    select(PhoneNumber)
                    .where(PhoneNumber.tenant_id == tenant.id)
                    .where(PhoneNumber.status.in_(LIVE_NUMBER_STATUSES))
                )
            )
            .scalars()
            .all()
        )

        # Step 1: stop answering. Committed on its own so that a vendor call
        # hanging below cannot leave the receptionist taking calls, and the
        # lookup cache is dropped so no caller is routed from a stale entry.
        tenant.status = TenantStatus.CANCELLED
        await self.session.commit()
        await self._forget_cached_numbers(numbers)

        outcome = DeletionOutcome(tenant_status=tenant.status.value)
        await self._unlink_numbers(tenant, numbers, outcome)
        await self._remove_agent(tenant, outcome)
        await self._handle_numbers(tenant, numbers, release_number, outcome)
        await self._discard_website_draft(tenant)

        self.audit.record(
            AuditAction.RECEPTIONIST_DELETED,
            actor_type=actor.actor_type,
            actor=actor.user,
            tenant_id=tenant.id,
            entity_type="tenant",
            entity_id=tenant.id,
            meta={
                "release_requested": release_number,
                "agent_deleted": outcome.agent_deleted,
                "shared_agent_detached": outcome.shared_agent_detached,
                "number_released": outcome.number_released,
                "number_retained": outcome.number_retained,
                "release_refused": outcome.release_refused,
                "errors": outcome.errors,
            },
        )
        await self.session.commit()

        logger.info(
            "receptionist deleted",
            extra={
                "tenant_id": str(tenant.id),
                "release_requested": release_number,
                "number_released": outcome.number_released,
                "agent_deleted": outcome.agent_deleted,
                "cleanup_errors": len(outcome.errors),
            },
        )
        return outcome

    # ------------------------------------------------------------------
    async def _forget_cached_numbers(self, numbers: list[PhoneNumber]) -> None:
        lookup = NumberDirectory(self.cache, self.settings)
        for number in numbers:
            await lookup.invalidate(number.e164)

    async def _discard_website_draft(self, tenant: Tenant) -> None:
        """Throw away the website draft this business was built from.

        It is scratch space for building a receptionist, not a record: what the
        website taught the agent already lives on the business profile, and the
        audit log holds the URL it came from. Keeping it would do two bad
        things — the setup wizard would reopen a half-filled review screen for a
        business that no longer exists, and because one account may hold only
        one draft, it would block the customer from ever setting up another
        receptionist from a website.
        """
        drafts = (
            (
                await self.session.execute(
                    select(WebsiteOnboarding).where(WebsiteOnboarding.tenant_id == tenant.id)
                )
            )
            .scalars()
            .all()
        )
        for draft in drafts:
            await self.session.delete(draft)
        if drafts:
            await self.session.flush()

    async def _unlink_numbers(
        self, tenant: Tenant, numbers: list[PhoneNumber], outcome: DeletionOutcome
    ) -> None:
        """Remove the ElevenLabs phone import, so the number stops reaching the agent.

        Done before the agent is deleted: if the delete below fails, the line is
        already detached from it. The stored id is cleared only on success, so a
        retry knows there is still an import to remove.
        """
        for number in numbers:
            if not number.elevenlabs_phone_id:
                continue
            try:
                await self.providers.elevenlabs.delete_phone_number(
                    phone_id=number.elevenlabs_phone_id
                )
                number.elevenlabs_phone_id = None
            except AppError as exc:
                outcome.errors.append(f"delete_phone_number: {exc.code}")
                logger.warning(
                    "could not remove the number's vendor link",
                    extra={"tenant_id": str(tenant.id), "code": exc.code},
                )
        await self.session.flush()

    async def _remove_agent(self, tenant: Tenant, outcome: DeletionOutcome) -> None:
        agent = (
            await self.session.execute(
                select(Agent)
                .where(Agent.tenant_id == tenant.id)
                .where(Agent.status == AgentStatus.ACTIVE)
            )
        ).scalar_one_or_none()
        if agent is None:
            return

        if agent.is_shared:
            # Detach only. Deleting a shared vertical agent would take down
            # every other tenant's receptionist on that vertical.
            agent.status = AgentStatus.DELETED
            outcome.shared_agent_detached = True
            logger.info(
                "detached a tenant from its shared agent; the agent itself is untouched",
                extra={"tenant_id": str(tenant.id), "agent_id": agent.elevenlabs_agent_id},
            )
            await self.session.flush()
            return

        if not agent.elevenlabs_agent_id:
            # Nothing was ever created at the vendor; the row is bookkeeping.
            agent.status = AgentStatus.DELETED
            await self.session.flush()
            return

        try:
            await self.providers.elevenlabs.delete_agent(agent_id=agent.elevenlabs_agent_id)
        except AppError as exc:
            # The row stays ACTIVE on purpose: it is what a retry looks for.
            # Calls have already stopped — the tenant is cancelled.
            outcome.errors.append(f"delete_agent: {exc.code}")
            logger.warning(
                "could not delete the vendor agent; a retry can finish this",
                extra={"tenant_id": str(tenant.id), "code": exc.code},
            )
            return

        agent.status = AgentStatus.DELETED
        outcome.agent_deleted = True
        await self.session.flush()

    async def _handle_numbers(
        self,
        tenant: Tenant,
        numbers: list[PhoneNumber],
        release_number: bool,
        outcome: DeletionOutcome,
    ) -> None:
        for number in numbers:
            # Queried by tenant_id, so a number belonging to another business
            # can never be reached from here — and the partial unique index on
            # (e164 where active) means no other tenant holds this one live.
            if number.status is PhoneNumberStatus.PENDING or not number.twilio_sid:
                # Intent recorded before a purchase that never completed. There
                # is nothing at the vendor to release; FAILED says exactly that.
                number.status = PhoneNumberStatus.FAILED
                continue

            if not release_number:
                outcome.number_retained = number.e164
                continue

            try:
                await self.providers.twilio.release_number(sid=number.twilio_sid)
            except AppError as exc:
                # The row keeps its SID and stays ACTIVE so a retry can release
                # it. It is still billing, which is why this is an error log.
                outcome.errors.append(f"release_number: {exc.code}")
                outcome.number_retained = number.e164
                logger.error(
                    "could not release the number; it will keep billing until retried",
                    extra={"tenant_id": str(tenant.id), "code": exc.code},
                )
                continue

            number.status = PhoneNumberStatus.RELEASED
            number.released_at = datetime.now(UTC)
            outcome.number_released = True
            self.audit.record(
                AuditAction.NUMBER_RELEASED,
                actor_type=ActorType.SYSTEM,
                actor_label="receptionist_deleted",
                tenant_id=tenant.id,
                entity_type="phone_number",
                entity_id=number.id,
                meta={"e164": number.e164},
            )
        await self.session.flush()
