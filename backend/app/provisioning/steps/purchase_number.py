"""Step 3 — buy a phone number.

The only irreversible, money-spending step in the run, so it is the one with the
most machinery around it.

Three guards, in order:

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
from app.services.idempotency import tenant_resource_name

logger = get_logger(__name__)


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

    # Deterministic selection.
    candidate, strategy, tried = await _select_number(ctx)

    # Guard 3: durable intent, committed before any money is spent.
    record = await _upsert_pending(ctx, e164=candidate.e164)
    await ctx.session.commit()

    purchased = await ctx.providers.twilio.purchase_number(
        e164=candidate.e164, friendly_name=friendly_name
    )

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
            "friendly_name": friendly_name,
            "strategies_tried": tried,
        },
        response={
            "e164": purchased.e164,
            "sid": purchased.sid,
            "strategy": strategy,
            "phone_number_id": str(record.id),
        },
    )


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
        return existing

    record = PhoneNumber(
        tenant_id=ctx.tenant.id,
        e164=e164,
        area_code=ctx.tenant.area_code,
        status=PhoneNumberStatus.PENDING,
    )
    ctx.session.add(record)
    await ctx.session.flush()
    return record


def _mark_active(record: PhoneNumber, *, sid: str, e164: str) -> None:
    record.twilio_sid = sid
    record.e164 = e164
    record.status = PhoneNumberStatus.ACTIVE
    record.purchased_at = datetime.now(UTC)
