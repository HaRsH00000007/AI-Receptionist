"""Metering: what a tenant used, and what that means for their plan.

The governing idea is stated on the schema and enforced here: **an in-app
measurement is an estimate, not an invoice.** A webhook delivered twice, or
never delivered, makes our minute count wrong in a way that stays invisible
until a customer disputes a bill. So:

* the ledger is append-only, and a correction is a *new row* — possibly a
  negative one — rather than an edit, so a total can always be reconstructed;
* every row records where its number came from, and a provider-reported figure
  supersedes a measured one rather than adding to it;
* the daily rollup is **recomputed**, never incremented, which is what makes a
  late-arriving webhook harmless;
* idempotency is a unique index on ``(provider, provider_reference)``, so
  importing the same provider line twice cannot double-bill.

Nothing here decides whether a tenant may provision. That question is answered
by :mod:`app.services.billing_gate` from the ``subscriptions`` table. Usage
informs *warnings* and, on plans that block, an enforcement check — but the
money gate and the meter stay separate, because a metering bug must never be
able to authorize a purchase.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Tenant, UsageDaily, UsageEvent
from app.models.billing import PLAN_CAPABILITIES, PlanCapabilities
from app.models.enums import TenantPlan, UsageKind, UsageSource

logger = get_logger(__name__)

#: Billing is monthly, so a "period" is a calendar month in the tenant's own
#: timezone. Using UTC would put a Pacific salon's last evening of the month
#: into the next one, and the customer would see minutes they cannot explain.
_SECONDS_PER_MINUTE = 60


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """One tenant's usage over a period."""

    tenant_id: uuid.UUID
    period_start: date
    period_end: date
    call_seconds: int
    call_count: int
    llm_tokens: int
    cost_cents: int

    @property
    def call_minutes(self) -> int:
        """Rounded up, the way every telephony vendor bills.

        A 10-second call costs a minute. Reporting 0 would understate the bill
        and make our number disagree with the invoice the customer receives.
        """
        return -(-self.call_seconds // _SECONDS_PER_MINUTE)


@dataclass(frozen=True, slots=True)
class PlanUsage:
    """Usage measured against what the plan allows."""

    totals: UsageTotals
    capabilities: PlanCapabilities
    plan: TenantPlan

    @property
    def included_minutes(self) -> int:
        return self.capabilities.included_minutes

    @property
    def percent_used(self) -> int:
        if self.included_minutes <= 0:
            return 0
        return min(999, round(self.totals.call_minutes * 100 / self.included_minutes))

    @property
    def over_limit(self) -> bool:
        return self.totals.call_minutes > self.included_minutes

    @property
    def should_warn(self) -> bool:
        """80% is the warning line from the production plan."""
        return self.percent_used >= 80 and not self.over_limit

    @property
    def should_block(self) -> bool:
        """Whether calls should stop.

        Only the free trial blocks. Cutting off a salon's phone line over a few
        dollars of overage is worse for both parties than billing it — but a
        trial has no payment method to bill, so continuing would be an
        uncollectable cost.
        """
        return self.over_limit and self.capabilities.block_on_overage


class UsageService:
    """Records usage and reports it. Never decides entitlement."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- recording -------------------------------------------------------
    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        kind: UsageKind,
        quantity: int,
        occurred_at: datetime | None = None,
        call_id: uuid.UUID | None = None,
        provider: str | None = None,
        provider_reference: str | None = None,
        source: UsageSource = UsageSource.MEASURED,
        cost_cents: int | None = None,
        meta: dict[str, object] | None = None,
    ) -> UsageEvent | None:
        """Append one metered occurrence.

        Returns ``None`` when the event was already recorded — the duplicate
        case, decided by the unique index on ``(provider, provider_reference)``
        rather than by a prior SELECT, because a check-then-insert loses to a
        concurrent delivery of the same webhook.

        The insert runs inside a **savepoint**. A plain flush that hit the index
        would poison the caller's whole transaction, so metering a call twice
        would roll back the summary and notification work that shares the
        session — a duplicate would break the thing it was supposed to protect.
        The savepoint confines the failure to this one insert.
        """
        event = UsageEvent(
            tenant_id=tenant_id,
            call_id=call_id,
            kind=kind,
            source=source,
            provider=provider,
            quantity=quantity,
            cost_cents=cost_cents,
            occurred_at=occurred_at or datetime.now(UTC),
            provider_reference=provider_reference,
            meta_json=dict(meta or {}),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(event)
                await self.session.flush()
        except IntegrityError:
            logger.info(
                "usage event already recorded; not counted twice",
                extra={
                    "tenant_id": str(tenant_id),
                    "provider": provider,
                    "kind": kind.value,
                },
            )
            return None
        return event

    async def record_call(
        self,
        *,
        tenant_id: uuid.UUID,
        call_id: uuid.UUID,
        duration_s: int,
        provider_call_id: str,
        occurred_at: datetime | None = None,
        provider: str = "elevenlabs",
    ) -> UsageEvent | None:
        """Meter one completed call.

        Keyed on the provider's own call id, which is what makes a redelivered
        post-call webhook free: the second attempt hits the unique index and
        counts nothing.
        """
        return await self.record(
            tenant_id=tenant_id,
            kind=UsageKind.CALL_MINUTES,
            quantity=max(0, duration_s),
            call_id=call_id,
            provider=provider,
            provider_reference=provider_call_id,
            occurred_at=occurred_at,
            source=UsageSource.MEASURED,
        )

    # ---- rollup ----------------------------------------------------------
    async def rebuild_day(self, tenant_id: uuid.UUID, day: date) -> list[UsageDaily]:
        """Recompute one day's rollup from the ledger.

        Recomputed rather than incremented, and that is the entire reason this
        is safe to run repeatedly. Incrementing a counter twice is a silent
        overcharge nobody can later prove happened; recomputing converges on the
        right answer no matter how many times it runs or in what order webhooks
        arrived.
        """
        start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        end = start + timedelta(days=1)

        rows = (
            await self.session.execute(
                select(
                    UsageEvent.kind,
                    func.coalesce(func.sum(UsageEvent.quantity), 0),
                    func.coalesce(func.sum(UsageEvent.cost_cents), 0),
                    func.count(UsageEvent.id),
                )
                .where(UsageEvent.tenant_id == tenant_id)
                .where(UsageEvent.occurred_at >= start)
                .where(UsageEvent.occurred_at < end)
                .group_by(UsageEvent.kind)
            )
        ).all()

        rebuilt: list[UsageDaily] = []
        for kind, quantity, cost_cents, event_count in rows:
            statement = (
                pg_insert(UsageDaily)
                .values(
                    tenant_id=tenant_id,
                    usage_date=day,
                    kind=kind,
                    quantity=int(quantity),
                    cost_cents=int(cost_cents),
                    event_count=int(event_count),
                    computed_at=datetime.now(UTC),
                )
                # Upsert, because the rollup is idempotent by construction and a
                # concurrent rebuild of the same day must not raise.
                .on_conflict_do_update(
                    constraint="uq_usage_daily_tenant_id_usage_date_kind",
                    set_={
                        "quantity": int(quantity),
                        "cost_cents": int(cost_cents),
                        "event_count": int(event_count),
                        "computed_at": datetime.now(UTC),
                    },
                )
                .returning(UsageDaily.id)
            )
            await self.session.execute(statement)

        await self.session.flush()
        return rebuilt

    # ---- reporting -------------------------------------------------------
    async def totals_for_period(
        self, tenant_id: uuid.UUID, *, start: date, end: date
    ) -> UsageTotals:
        """Sum the ledger directly, not the rollup.

        The rollup is a cache for dashboards. Anything that informs a limit or a
        bill reads the ledger, because a stale or half-rebuilt rollup would be
        an invisible way to under- or over-charge.
        """
        start_at = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
        end_at = datetime.combine(end, datetime.min.time(), tzinfo=UTC)

        rows = (
            await self.session.execute(
                select(
                    UsageEvent.kind,
                    func.coalesce(func.sum(UsageEvent.quantity), 0),
                    func.coalesce(func.sum(UsageEvent.cost_cents), 0),
                    func.count(UsageEvent.id),
                )
                .where(UsageEvent.tenant_id == tenant_id)
                .where(UsageEvent.occurred_at >= start_at)
                .where(UsageEvent.occurred_at < end_at)
                .group_by(UsageEvent.kind)
            )
        ).all()

        by_kind = {kind: (int(qty), int(cost), int(count)) for kind, qty, cost, count in rows}
        call_seconds, call_cost, call_count = by_kind.get(UsageKind.CALL_MINUTES, (0, 0, 0))
        llm_tokens, llm_cost, _ = by_kind.get(UsageKind.LLM_TOKENS, (0, 0, 0))
        other_cost = sum(
            cost
            for kind, (_, cost, _) in by_kind.items()
            if kind not in (UsageKind.CALL_MINUTES, UsageKind.LLM_TOKENS)
        )

        return UsageTotals(
            tenant_id=tenant_id,
            period_start=start,
            period_end=end,
            call_seconds=call_seconds,
            call_count=call_count,
            llm_tokens=llm_tokens,
            cost_cents=call_cost + llm_cost + other_cost,
        )

    async def current_period(self, tenant: Tenant) -> PlanUsage:
        """This calendar month's usage, measured against the tenant's plan."""
        start, end = current_period_bounds()
        totals = await self.totals_for_period(tenant.id, start=start, end=end)
        capabilities = PLAN_CAPABILITIES[tenant.plan]
        return PlanUsage(totals=totals, capabilities=capabilities, plan=tenant.plan)


def current_period_bounds(today: date | None = None) -> tuple[date, date]:
    """The current calendar month, as a half-open ``[start, end)`` range.

    Half-open so a call at 23:59:59.999 on the last day lands in the period it
    belongs to, and no event can fall into both months or neither.
    """
    day = today or datetime.now(UTC).date()
    start = day.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


async def rebuild_recent_rollups(session: AsyncSession, *, days: int = 3) -> int:
    """Recompute the last few days of rollups for every tenant with activity.

    A window rather than "yesterday", because a post-call webhook can arrive
    late and must still land in the right day. Three days is comfortably longer
    than any provider's retry schedule, and the rebuild is idempotent, so
    overlapping windows cost a little CPU and can never double-count.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    pairs = (
        await session.execute(
            select(UsageEvent.tenant_id, func.date(UsageEvent.occurred_at))
            .where(UsageEvent.occurred_at >= since)
            .group_by(UsageEvent.tenant_id, func.date(UsageEvent.occurred_at))
        )
    ).all()

    service = UsageService(session)
    for tenant_id, day in pairs:
        await service.rebuild_day(tenant_id, day)
    await session.commit()
    return len(pairs)
