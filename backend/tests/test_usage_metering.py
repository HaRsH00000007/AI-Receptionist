"""M11 — usage metering.

Metering bugs are expensive in a particular way: they are silent, they compound,
and they surface as a customer disputing an invoice months later with better
records than you have. So the tests here are mostly about *not* counting things
— duplicates, replays, concurrent deliveries — and about the rollup converging
on the right answer however many times it runs.

The other property under test is separation: usage informs warnings and, on the
trial plan, enforcement. It never decides entitlement. A metering bug must not
be able to authorize a phone number purchase.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageDaily, UsageEvent
from app.models.enums import TenantPlan, UsageKind, UsageSource
from app.services.usage import UsageService, current_period_bounds
from tests.factories import make_call, make_tenant

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


async def _tenant(session: AsyncSession, **overrides: object):  # type: ignore[no-untyped-def]
    tenant = make_tenant(**overrides)
    session.add(tenant)
    await session.flush()
    return tenant


# ===========================================================================
# Not counting things twice
# ===========================================================================


async def test_the_same_provider_reference_is_counted_once(
    db_session: AsyncSession,
) -> None:
    """A redelivered post-call webhook must be free."""
    tenant = await _tenant(db_session)
    service = UsageService(db_session)

    first = await service.record(
        tenant_id=tenant.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=120,
        provider="elevenlabs",
        provider_reference="conv_1",
        occurred_at=NOW,
    )
    second = await service.record(
        tenant_id=tenant.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=120,
        provider="elevenlabs",
        provider_reference="conv_1",
        occurred_at=NOW,
    )

    assert first is not None
    assert second is None  # recognised as a duplicate, not appended

    total = (
        await db_session.execute(
            select(func.sum(UsageEvent.quantity)).where(UsageEvent.tenant_id == tenant.id)
        )
    ).scalar_one()
    assert total == 120


async def test_a_duplicate_does_not_poison_the_callers_transaction(
    db_session: AsyncSession,
) -> None:
    """The savepoint.

    Metering shares a session with summarization and notification. A plain
    flush that hit the unique index would roll all of that back, so a duplicate
    delivery would destroy the work it was meant to protect.
    """
    tenant = await _tenant(db_session)
    service = UsageService(db_session)

    await service.record(
        tenant_id=tenant.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=60,
        provider="elevenlabs",
        provider_reference="conv_dup",
        occurred_at=NOW,
    )
    await service.record(
        tenant_id=tenant.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=60,
        provider="elevenlabs",
        provider_reference="conv_dup",
        occurred_at=NOW,
    )

    # The session is still usable and the tenant is still there.
    await db_session.commit()
    assert await db_session.get(type(tenant), tenant.id) is not None


async def test_concurrent_deliveries_of_one_call_meter_once(api_app: FastAPI) -> None:
    """Two workers racing on the same webhook.

    The unique index decides, not a check-then-insert — which would let both
    readers see "absent" and both insert.
    """
    async with api_app.state.session_factory() as setup:
        tenant = make_tenant()
        setup.add(tenant)
        await setup.commit()
        tenant_id = tenant.id

    async def meter() -> None:
        async with api_app.state.session_factory() as session:
            await UsageService(session).record(
                tenant_id=tenant_id,
                kind=UsageKind.CALL_MINUTES,
                quantity=90,
                provider="elevenlabs",
                provider_reference="conv_race",
                occurred_at=NOW,
            )
            await session.commit()

    await asyncio.gather(*(meter() for _ in range(5)), return_exceptions=True)

    async with api_app.state.session_factory() as session:
        rows = (
            await session.execute(
                select(func.count(UsageEvent.id)).where(
                    UsageEvent.provider_reference == "conv_race"
                )
            )
        ).scalar_one()
    assert rows == 1


async def test_two_tenants_may_report_the_same_reference_from_different_providers(
    db_session: AsyncSession,
) -> None:
    """Uniqueness is per provider; vendors number their own events."""
    tenant = await _tenant(db_session)
    service = UsageService(db_session)

    assert (
        await service.record(
            tenant_id=tenant.id,
            kind=UsageKind.CALL_MINUTES,
            quantity=10,
            provider="twilio",
            provider_reference="shared_id",
            occurred_at=NOW,
        )
        is not None
    )
    assert (
        await service.record(
            tenant_id=tenant.id,
            kind=UsageKind.CALL_MINUTES,
            quantity=10,
            provider="elevenlabs",
            provider_reference="shared_id",
            occurred_at=NOW,
        )
        is not None
    )


# ===========================================================================
# Corrections are appended, never edited
# ===========================================================================


async def test_a_correction_is_a_new_negative_row(db_session: AsyncSession) -> None:
    """Editing history to make a total look right makes a dispute unwinnable."""
    tenant = await _tenant(db_session)
    service = UsageService(db_session)

    await service.record(
        tenant_id=tenant.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=600,
        provider="elevenlabs",
        provider_reference="conv_overcounted",
        occurred_at=NOW,
    )
    await service.record(
        tenant_id=tenant.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=-300,
        source=UsageSource.MANUAL_ADJUSTMENT,
        provider="elevenlabs",
        provider_reference="adj_conv_overcounted",
        occurred_at=NOW,
        meta={"reason": "duplicate vendor line"},
    )
    await db_session.commit()

    start, end = date(2026, 9, 1), date(2026, 10, 1)
    totals = await service.totals_for_period(tenant.id, start=start, end=end)
    assert totals.call_seconds == 300
    # Both rows survive, so the total can be explained.
    count = (
        await db_session.execute(
            select(func.count(UsageEvent.id)).where(UsageEvent.tenant_id == tenant.id)
        )
    ).scalar_one()
    assert count == 2


# ===========================================================================
# Periods and rounding
# ===========================================================================


def test_a_period_is_a_half_open_calendar_month() -> None:
    """Half-open, so no event lands in both months or in neither."""
    start, end = current_period_bounds(date(2026, 9, 12))
    assert start == date(2026, 9, 1)
    assert end == date(2026, 10, 1)


def test_december_rolls_into_january() -> None:
    start, end = current_period_bounds(date(2026, 12, 31))
    assert start == date(2026, 12, 1)
    assert end == date(2027, 1, 1)


async def test_minutes_round_up_the_way_vendors_bill(db_session: AsyncSession) -> None:
    """A 10-second call costs a minute. Reporting 0 understates the bill."""
    tenant = await _tenant(db_session)
    service = UsageService(db_session)
    await service.record(
        tenant_id=tenant.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=10,
        provider="elevenlabs",
        provider_reference="conv_short",
        occurred_at=NOW,
    )
    await db_session.commit()

    totals = await service.totals_for_period(
        tenant.id, start=date(2026, 9, 1), end=date(2026, 10, 1)
    )
    assert totals.call_seconds == 10
    assert totals.call_minutes == 1


async def test_usage_outside_the_period_is_not_counted(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    service = UsageService(db_session)
    for moment in (
        datetime(2026, 8, 31, 23, 59, tzinfo=UTC),
        datetime(2026, 10, 1, 0, 1, tzinfo=UTC),
    ):
        await service.record(
            tenant_id=tenant.id,
            kind=UsageKind.CALL_MINUTES,
            quantity=600,
            provider="elevenlabs",
            provider_reference=f"conv_{moment.isoformat()}",
            occurred_at=moment,
        )
    await db_session.commit()

    totals = await service.totals_for_period(
        tenant.id, start=date(2026, 9, 1), end=date(2026, 10, 1)
    )
    assert totals.call_seconds == 0


# ===========================================================================
# The rollup is rebuilt, not incremented
# ===========================================================================


async def test_rebuilding_a_day_twice_is_idempotent(db_session: AsyncSession) -> None:
    """The property that makes a late webhook harmless.

    Incrementing a counter twice is a silent overcharge nobody can later prove
    happened; recomputing converges however many times it runs.
    """
    tenant = await _tenant(db_session)
    service = UsageService(db_session)
    await service.record(
        tenant_id=tenant.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=120,
        provider="elevenlabs",
        provider_reference="conv_a",
        occurred_at=NOW,
    )
    await db_session.commit()

    for _ in range(3):
        await service.rebuild_day(tenant.id, NOW.date())
    await db_session.commit()

    rows = (
        (
            await db_session.execute(
                select(UsageDaily)
                .where(UsageDaily.tenant_id == tenant.id)
                .where(UsageDaily.usage_date == NOW.date())
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].quantity == 120
    assert rows[0].event_count == 1


async def test_a_late_arriving_event_is_picked_up_by_a_rebuild(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant(db_session)
    service = UsageService(db_session)
    await service.record(
        tenant_id=tenant.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=60,
        provider="elevenlabs",
        provider_reference="conv_first",
        occurred_at=NOW,
    )
    await db_session.commit()
    await service.rebuild_day(tenant.id, NOW.date())
    await db_session.commit()

    # A webhook arrives minutes late for the same day.
    await service.record(
        tenant_id=tenant.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=30,
        provider="elevenlabs",
        provider_reference="conv_late",
        occurred_at=NOW - timedelta(hours=1),
    )
    await db_session.commit()
    await service.rebuild_day(tenant.id, NOW.date())
    await db_session.commit()

    row = (
        await db_session.execute(
            select(UsageDaily)
            .where(UsageDaily.tenant_id == tenant.id)
            .where(UsageDaily.kind == UsageKind.CALL_MINUTES)
        )
    ).scalar_one()
    assert row.quantity == 90
    assert row.event_count == 2


# ===========================================================================
# Plan limits — warning, blocking, and what must never happen
# ===========================================================================


async def test_usage_is_measured_against_the_plan(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session, plan=TenantPlan.TRIAL)
    service = UsageService(db_session)
    start, _ = current_period_bounds()

    # 48 minutes against a 60-minute trial allowance.
    await service.record(
        tenant_id=tenant.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=48 * 60,
        provider="elevenlabs",
        provider_reference="conv_bulk",
        occurred_at=datetime.combine(start, datetime.min.time(), tzinfo=UTC) + timedelta(hours=1),
    )
    await db_session.commit()

    usage = await service.current_period(tenant)
    assert usage.included_minutes == 60
    assert usage.percent_used == 80
    assert usage.should_warn is True
    assert usage.over_limit is False
    assert usage.should_block is False


async def test_only_the_trial_plan_blocks_on_overage(db_session: AsyncSession) -> None:
    """Cutting off a salon's phone line over a few dollars is worse for both.

    A trial is the exception because there is no payment method to bill the
    overage to, so continuing would be an uncollectable cost.
    """
    start, _ = current_period_bounds()
    occurred = datetime.combine(start, datetime.min.time(), tzinfo=UTC) + timedelta(hours=1)

    for index, (plan, blocks) in enumerate(
        [(TenantPlan.TRIAL, True), (TenantPlan.STARTER, False), (TenantPlan.PRO, False)]
    ):
        tenant = await _tenant(db_session, plan=plan)
        service = UsageService(db_session)
        await service.record(
            tenant_id=tenant.id,
            kind=UsageKind.CALL_MINUTES,
            # Comfortably past every plan's allowance, including enterprise's
            # 10,000 minutes — the point is the *policy*, not the threshold.
            quantity=1_000_000,
            provider="elevenlabs",
            provider_reference=f"conv_over_{index}",
            occurred_at=occurred,
        )
        await db_session.commit()

        usage = await service.current_period(tenant)
        assert usage.over_limit is True
        assert usage.should_block is blocks


def test_metering_cannot_authorize_provisioning() -> None:
    """Entitlement is answered from `subscriptions`, never from usage.

    The structural guarantee: the money gate does not import this module, so a
    metering bug has no path by which it could authorize a number purchase.
    """
    import inspect

    from app.services import billing_gate

    assert "usage" not in inspect.getsource(billing_gate).lower().split("def ")[0]
    assert not hasattr(billing_gate, "UsageService")


# ===========================================================================
# Tenant scoping
# ===========================================================================


async def test_usage_never_crosses_tenants(db_session: AsyncSession) -> None:
    mine = await _tenant(db_session)
    theirs = await _tenant(db_session)
    service = UsageService(db_session)

    await service.record(
        tenant_id=theirs.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=3600,
        provider="elevenlabs",
        provider_reference="conv_theirs",
        occurred_at=NOW,
    )
    await db_session.commit()

    totals = await service.totals_for_period(mine.id, start=date(2026, 9, 1), end=date(2026, 10, 1))
    assert totals.call_seconds == 0


# ===========================================================================
# The call path meters exactly once
# ===========================================================================


async def test_processing_a_call_twice_meters_it_once(api_app: FastAPI) -> None:
    """A retried summary must not buy a second metering row."""
    from app.services.call_processor import CallProcessor

    async with api_app.state.session_factory() as session:
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()
        call = make_call(tenant, duration_s=180, provider_call_id="conv_metered_once")
        session.add(call)
        await session.commit()
        call_id, tenant_id = call.id, tenant.id

    processor = CallProcessor(api_app.state.settings, api_app.state.providers)
    for _ in range(2):
        async with api_app.state.session_factory() as session:
            await processor.process(session, call_id)

    async with api_app.state.session_factory() as session:
        rows = (
            await session.execute(
                select(func.count(UsageEvent.id)).where(UsageEvent.tenant_id == tenant_id)
            )
        ).scalar_one()
    assert rows == 1
