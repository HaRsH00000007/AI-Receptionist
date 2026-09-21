"""Step 3 — buy a phone number.

The only irreversible, money-spending step in the run, so it is the one with the
most machinery around it.

**Guard 0 is the money gate**, and it is the reason this module cannot spend
money for an unentitled tenant. ``BILLING_GATE`` already ran as its own step,
but that is not sufficient on its own:

* a step retry re-enters *this* function without re-running the previous step;
* an admin "retry from step" can start the run here directly;
* entitlement can change between the two steps — a card fails, a trial lapses,
  a subscription is cancelled — and the run may have been parked for hours;
* two workers can race, and only the check that happens in the same moment as
  the spend is authoritative.

So entitlement is re-read from PostgreSQL here, immediately before the vendor
call, every time. The gate step gives visibility and a clean parked state; this
check is the one that actually guarantees the money is safe.

Then three idempotency guards, in order:

1. **Our row.** An ACTIVE ``phone_numbers`` row for this tenant means the
   purchase already completed; return it.
2. **The vendor.** Ask Twilio whether a number tagged ``tenant:{id}`` exists.
   Our row may be missing precisely because the worker died between the purchase
   and the commit, so the authoritative answer has to come from Twilio.
3. **A PENDING row, written and committed before the purchase call.** If
   everything else fails, that row plus the friendly name is the trail back to a
   number we own.

Selection is a deterministic ladder — exact area code, same state, any area
code, toll-free — and no model is anywhere near it
(docs/00_DECISIONS.md section 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.errors import VendorError
from app.core.logging import get_logger
from app.data.area_codes import lookup
from app.models import PhoneNumber
from app.models.enums import PhoneNumberStatus
from app.providers.models import AvailableNumber
from app.provisioning.context import StepContext, StepResult
from app.services.billing_gate import require_entitlement
from app.services.idempotency import tenant_resource_name
from app.services.normalization import area_code_of

logger = get_logger(__name__)

#: Recorded on the step when the tenant got the number they picked themselves,
#: which is what distinguishes it from any rung of the fallback ladder.
CUSTOMER_CHOICE = "customer_choice"


@dataclass(frozen=True, slots=True)
class SearchRung:
    """One rung of the fallback ladder, with a name for the audit trail."""

    strategy: str
    area_code: str | None = None
    in_region: str | None = None
    toll_free: bool = False


def build_ladder(area_code: str | None) -> list[SearchRung]:
    """The search order. Pure, ordered, and testable on its own."""
    rungs: list[SearchRung] = []
    if area_code:
        rungs.append(SearchRung("exact_area_code", area_code=area_code))
        state = lookup(area_code).state
        if state:
            rungs.append(SearchRung("same_state", in_region=state))
    rungs.append(SearchRung("any_local"))
    rungs.append(SearchRung("toll_free", toll_free=True))
    return rungs


async def run(ctx: StepContext) -> StepResult:
    tenant = ctx.tenant
    friendly_name = tenant_resource_name(tenant.id)

    # Guard 0: the money gate. Re-read from the database rather than trusting
    # the earlier BILLING_GATE step, because entitlement can have changed since
    # it ran and a retry re-enters here without it. Raises BillingBlockedError,
    # so nothing below executes and Twilio is never contacted.
    #
    # This deliberately runs before the adoption guards too: adopting a number
    # is free, but it would move an unentitled tenant forward towards ACTIVE.
    await require_entitlement(ctx.session, ctx.settings, tenant.id)

    # Guard 1: we already own one.
    active = await _active_number(ctx)
    if active is not None:
        return StepResult(
            request={"tenant_id": str(tenant.id)},
            response={"adopted": "database", "e164": active.e164, "sid": active.twilio_sid},
        )

    # Guard 2: Twilio already sold us one under this tenant's name.
    owned = await ctx.providers.twilio.find_by_friendly_name(friendly_name=friendly_name)
    if owned is not None:
        record = await _upsert_pending(ctx, e164=owned.e164)
        _mark_active(record, sid=owned.sid, e164=owned.e164)
        await ctx.session.flush()
        logger.info(
            "adopted a number already owned at the vendor",
            extra={"tenant_id": str(tenant.id), "sid": owned.sid},
        )
        return StepResult(
            request={"friendly_name": friendly_name},
            response={"adopted": "vendor", "e164": owned.e164, "sid": owned.sid},
        )

    # The number the customer picked at signup, if they picked one. Bought
    # directly rather than searched for again: they chose it from a list, and a
    # fresh search could hand them a different number than the one they saw.
    #
    # It is a preference, not a reservation — nothing is held at the vendor —
    # so a number sold to someone else in the meantime falls through to the
    # ladder below rather than failing the run.
    requested = (tenant.requested_number or "").strip()
    tried: list[str] = []
    pending: PhoneNumber | None = None
    purchased = None
    strategy = ""

    if requested:
        tried.append(CUSTOMER_CHOICE)
        # Guard 3 applies here too: durable intent, committed before the spend.
        pending = await _upsert_pending(ctx, e164=requested)
        await ctx.session.commit()
        await require_entitlement(ctx.session, ctx.settings, tenant.id)
        try:
            purchased = await ctx.providers.twilio.purchase_number(
                e164=requested, friendly_name=friendly_name
            )
            strategy = CUSTOMER_CHOICE
        except VendorError as exc:
            if not _was_taken(exc):
                raise
            logger.info(
                "the number the customer chose was gone; searching instead",
                extra={"tenant_id": str(tenant.id), "e164": requested, "code": exc.code},
            )

    if purchased is None:
        # Deterministic selection.
        candidate, strategy, ladder = await _select_number(ctx)
        tried.extend(ladder)

        # Guard 3: durable intent, committed before any money is spent.
        pending = await _upsert_pending(ctx, e164=candidate.e164)
        await ctx.session.commit()

        # The last word before the charge. `_select_number` above performs vendor
        # searches that can take seconds, and that commit ended the transaction the
        # first check was read in -- so entitlement is confirmed once more, as close
        # to the spend as it is possible to get.
        await require_entitlement(ctx.session, ctx.settings, tenant.id)

        purchased = await ctx.providers.twilio.purchase_number(
            e164=candidate.e164, friendly_name=friendly_name
        )

    # Both paths above write the pending row before purchasing.
    assert pending is not None
    record = pending

    _mark_active(record, sid=purchased.sid, e164=purchased.e164)
    await ctx.session.flush()

    logger.info(
        "number purchased",
        extra={
            "tenant_id": str(tenant.id),
            "e164": purchased.e164,
            "strategy": strategy,
            "dry_run": ctx.settings.dry_run,
        },
    )
    return StepResult(
        request={
            "requested_area_code": tenant.area_code,
            "requested_number": tenant.requested_number,
            "friendly_name": friendly_name,
            "strategies_tried": tried,
        },
        response={
            "e164": purchased.e164,
            "sid": purchased.sid,
            "strategy": strategy,
            "chosen_by_customer": strategy == CUSTOMER_CHOICE,
            "phone_number_id": str(record.id),
        },
    )


def _was_taken(exc: VendorError) -> bool:
    """Did the vendor refuse because that number is no longer for sale?

    Twilio answers 400/404 for a number that has been sold or withdrawn since
    the search, and the in-memory fake raises ``number_unavailable``. Anything
    else — a bad credential, a rate limit, an outage — is a real failure and
    must not be swallowed into a silent fallback.
    """
    status = getattr(exc, "status_code", None)
    return exc.code == "number_unavailable" or status in (400, 404, 409)


# ---------------------------------------------------------------------------
async def _select_number(ctx: StepContext) -> tuple[AvailableNumber, str, list[str]]:
    """Walk the ladder until something is available, or fail loudly."""
    tried: list[str] = []
    for rung in build_ladder(ctx.tenant.area_code):
        tried.append(rung.strategy)
        offers = await ctx.providers.twilio.search_available_numbers(
            area_code=rung.area_code,
            in_region=rung.in_region,
            toll_free=rung.toll_free,
            limit=5,
        )
        usable = [offer for offer in offers if offer.voice_enabled]
        if usable:
            # First match, not a random one: the same search yields the same
            # choice, which makes a retry reproducible.
            return usable[0], rung.strategy, tried

    # Every rung empty. Retryable, because inventory genuinely changes — and
    # visible, because the run stops here with the ladder recorded.
    raise VendorError(
        "no phone number available in any fallback tier",
        vendor=ctx.providers.twilio.name,
        retryable=True,
        code="no_numbers_available",
        details={"area_code": ctx.tenant.area_code, "strategies_tried": tried},
    )


async def _active_number(ctx: StepContext) -> PhoneNumber | None:
    return (
        await ctx.session.execute(
            select(PhoneNumber)
            .where(PhoneNumber.tenant_id == ctx.tenant.id)
            .where(PhoneNumber.status == PhoneNumberStatus.ACTIVE)
        )
    ).scalar_one_or_none()


async def _upsert_pending(ctx: StepContext, *, e164: str) -> PhoneNumber:
    """Reuse this run's pending row if it exists, so a retry does not pile up."""
    existing = (
        await ctx.session.execute(
            select(PhoneNumber)
            .where(PhoneNumber.tenant_id == ctx.tenant.id)
            .where(PhoneNumber.status == PhoneNumberStatus.PENDING)
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.e164 = e164
        existing.area_code = area_code_of(e164)
        return existing

    record = PhoneNumber(
        tenant_id=ctx.tenant.id,
        e164=e164,
        # Derived from the number itself rather than from what was asked for.
        # A customer who picked a nearby number, or a run that fell down the
        # ladder, must not have the requested area code recorded against a
        # number that is not in it.
        area_code=area_code_of(e164),
        status=PhoneNumberStatus.PENDING,
    )
    ctx.session.add(record)
    await ctx.session.flush()
    return record


def _mark_active(record: PhoneNumber, *, sid: str, e164: str) -> None:
    record.twilio_sid = sid
    record.e164 = e164
    record.area_code = area_code_of(e164)
    record.status = PhoneNumberStatus.ACTIVE
    record.purchased_at = datetime.now(UTC)
