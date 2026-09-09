"""The money gate.

The single most important assertion in this repository is here, and it is
deliberately phrased as a **call count**:

    twilio.behaviour.count("purchase_number") == 0

Asserting "the run failed" or "no phone_numbers row exists" would both pass
against a system that bought a number and then rolled back its own bookkeeping —
which is precisely the failure that costs money every month, forever, on a
number nobody can find. The only assertion that means what it needs to mean is
that the vendor was never asked.

The POC had no gate at all: every anonymous form submission reached Twilio.
`docs/02_PLAN_PRODUCTION.md` §4 calls that "the single biggest leak".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import BillingBlockedError
from app.models import ProvisioningRun, ProvisioningStepRecord, Subscription, Tenant
from app.models.enums import (
    ProvisioningStatus,
    ProvisioningStep,
    StepStatus,
    SubscriptionStatus,
    TenantPlan,
    TenantStatus,
)
from app.services.billing_gate import evaluate, require_entitlement
from tests.factories import make_subscription, make_tenant
from tests.support import build_settings
from tests.worker_fixtures import WorkerEnv

SIGNUP: dict[str, object] = {
    "business_name": "Sunset Salon",
    "business_type": "salon",
    "services": "cuts, color",
    "operating_hours": "Mon-Fri 9-6",
    "greeting_style": "friendly",
    "escalation_rules": "",
    "notification_email": "owner@sunsetsalon.example.com",
    "area_code": "805",
    "plan": "starter",
    "contact_phone": "8055550142",
}


# ===========================================================================
# The decision itself
# ===========================================================================


async def test_no_subscription_is_not_entitled(db_session) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()

    decision = await evaluate(db_session, build_settings(), tenant.id)
    assert decision.allowed is False
    assert decision.reason == "no_active_subscription"


async def test_an_active_subscription_is_entitled(db_session) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(
        make_subscription(
            tenant,
            plan=TenantPlan.PRO,
            status=SubscriptionStatus.ACTIVE,
            trial_ends_at=None,
        )
    )
    await db_session.flush()

    decision = await evaluate(db_session, build_settings(), tenant.id)
    assert decision.allowed is True
    assert decision.plan is TenantPlan.PRO


async def test_a_live_trial_is_entitled(db_session) -> None:  # type: ignore[no-untyped-def]
    """A trial is the product, not a loophole — it provisions."""
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(make_subscription(tenant))  # trialing, ends in 14 days
    await db_session.flush()

    assert (await evaluate(db_session, build_settings(), tenant.id)).allowed is True


async def test_an_expired_trial_is_not_entitled(db_session) -> None:  # type: ignore[no-untyped-def]
    """A stale TRIALING row must not authorize a purchase.

    The row can sit at TRIALING past its end date until some job notices. The
    gate checks the date, not just the status, so the lag cannot spend money.
    """
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(
        make_subscription(tenant, trial_ends_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.flush()

    decision = await evaluate(db_session, build_settings(), tenant.id)
    assert decision.allowed is False
    assert decision.reason == "trial_expired"


@pytest.mark.parametrize(
    "status",
    [SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED],
    ids=["canceled", "expired"],
)
async def test_dead_subscriptions_are_not_entitled(db_session, status) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(make_subscription(tenant, status=status, trial_ends_at=None))
    await db_session.flush()

    assert (await evaluate(db_session, build_settings(), tenant.id)).allowed is False


async def test_past_due_keeps_the_phone_line_up(db_session) -> None:  # type: ignore[no-untyped-def]
    """A failed renewal must not take a paying business's phone line down.

    Stripe's dunning retries for days. Cutting service off on the first failed
    charge punishes a customer whose card simply expired.
    """
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(
        make_subscription(
            tenant, status=SubscriptionStatus.PAST_DUE, plan=TenantPlan.PRO, trial_ends_at=None
        )
    )
    await db_session.flush()

    assert (await evaluate(db_session, build_settings(), tenant.id)).allowed is True


async def test_another_tenants_subscription_cannot_authorize(db_session) -> None:  # type: ignore[no-untyped-def]
    """Requirement G. The gate is scoped in the query, not by a later check."""
    payer, freeloader = make_tenant(), make_tenant()
    db_session.add_all([payer, freeloader])
    await db_session.flush()
    db_session.add(make_subscription(payer, status=SubscriptionStatus.ACTIVE, trial_ends_at=None))
    await db_session.flush()

    assert (await evaluate(db_session, build_settings(), payer.id)).allowed is True
    assert (await evaluate(db_session, build_settings(), freeloader.id)).allowed is False


async def test_require_entitlement_raises_rather_than_returning(db_session) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()

    with pytest.raises(BillingBlockedError) as excinfo:
        await require_entitlement(db_session, build_settings(), tenant.id)
    assert excinfo.value.details["reason"] == "no_active_subscription"
    # Not retryable: backing off will not make a card appear, and consuming the
    # attempt budget would starve a genuine vendor timeout.
    assert excinfo.value.retryable is False


async def test_the_gate_can_be_disabled_only_outside_production(db_session) -> None:  # type: ignore[no-untyped-def]
    """An escape hatch that cannot escape.

    Settings._production_hardening refuses to boot a production-like process
    with the gate off, so this path is reachable only in local and test.
    """
    tenant = make_tenant()
    db_session.add(tenant)
    await db_session.flush()

    local = build_settings(environment="local", billing_gate_enabled=False)
    assert (await evaluate(db_session, local, tenant.id)).reason == "gate_disabled"

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        build_settings(environment="production", billing_gate_enabled=False)


# ===========================================================================
# End to end: does the vendor ever get called?
# ===========================================================================


async def _seed_signup(env: WorkerEnv, **subscription_kwargs: object) -> uuid.UUID:
    """A tenant with a full provisioning run, and the given billing state.

    ``subscription_kwargs`` empty means *no subscription at all* — the
    unauthorized case.
    """
    from app.schemas.signup import SignupRequest
    from app.services.signup import SignupService

    async with env.session_factory() as session:
        # settings=None so SignupService does not grant its own trial; each test
        # states the billing state it wants explicitly.
        result = await SignupService(session).submit(
            SignupRequest.model_validate(SIGNUP), correlation_id="test-corr"
        )
        tenant_id = result.tenant.id
        if subscription_kwargs:
            tenant = await session.get(Tenant, tenant_id)
            assert tenant is not None
            session.add(make_subscription(tenant, **subscription_kwargs))
        await session.commit()
    return tenant_id


async def test_A_unauthorized_billing_never_calls_twilio(worker_env: WorkerEnv) -> None:
    """**The assertion this whole module exists for.**

    Not "the run failed" and not "no phone number row exists" — either would
    pass against a system that bought a number and then tidied up after itself.
    The vendor must never be asked.
    """
    tenant_id = await _seed_signup(worker_env)

    await worker_env.drain()

    assert worker_env.twilio.behaviour.count("purchase_number") == 0

    async with worker_env.session_factory() as session:
        run = (
            await session.execute(
                select(ProvisioningRun).where(ProvisioningRun.tenant_id == tenant_id)
            )
        ).scalar_one()
        # Parked, not FAILED. The distinction is the point: nothing is broken,
        # no compensation is owed, and the run resumes the moment billing says
        # yes. Marking it failed would release resources for a customer who is
        # one card entry away from being entitled.
        assert run.status is ProvisioningStatus.BILLING_BLOCKED

        # And the tenant is emphatically not claiming to have a receptionist.
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        assert tenant.status is TenantStatus.PENDING


async def test_B_authorized_billing_lets_the_purchase_proceed(worker_env: WorkerEnv) -> None:
    tenant_id = await _seed_signup(
        worker_env, status=SubscriptionStatus.ACTIVE, plan=TenantPlan.PRO, trial_ends_at=None
    )

    await worker_env.drain()

    assert worker_env.twilio.behaviour.count("purchase_number") == 1

    async with worker_env.session_factory() as session:
        run = (
            await session.execute(
                select(ProvisioningRun).where(ProvisioningRun.tenant_id == tenant_id)
            )
        ).scalar_one()
        assert run.status is ProvisioningStatus.ACTIVE


async def test_C_a_cancelled_subscription_blocks_the_purchase(worker_env: WorkerEnv) -> None:
    await _seed_signup(worker_env, status=SubscriptionStatus.CANCELED, trial_ends_at=None)
    await worker_env.drain()
    assert worker_env.twilio.behaviour.count("purchase_number") == 0


async def test_C2_an_expired_trial_blocks_the_purchase(worker_env: WorkerEnv) -> None:
    await _seed_signup(
        worker_env,
        status=SubscriptionStatus.TRIALING,
        trial_ends_at=datetime.now(UTC) - timedelta(days=1),
    )
    await worker_env.drain()
    assert worker_env.twilio.behaviour.count("purchase_number") == 0


async def test_D_an_unpaid_subscription_blocks_the_purchase(worker_env: WorkerEnv) -> None:
    await _seed_signup(worker_env, status=SubscriptionStatus.EXPIRED, trial_ends_at=None)
    await worker_env.drain()
    assert worker_env.twilio.behaviour.count("purchase_number") == 0


async def test_H_a_parked_run_stays_parked_across_many_ticks(worker_env: WorkerEnv) -> None:
    """Requirement H/I. Repeated worker passes must not wear the gate down.

    Draining ticks the worker until nothing changes; doing it repeatedly
    simulates both a retry loop and a second worker taking the same run.
    """
    await _seed_signup(worker_env)

    for _ in range(5):
        await worker_env.drain()

    assert worker_env.twilio.behaviour.count("purchase_number") == 0


async def test_I_retrying_a_parked_run_cannot_bypass_the_gate(worker_env: WorkerEnv) -> None:
    """An admin retry re-enters purchase_number without re-running the gate step.

    That is exactly why purchase_number re-checks entitlement itself rather
    than trusting the earlier step.
    """
    tenant_id = await _seed_signup(worker_env)
    await worker_env.drain()

    async with worker_env.session_factory() as session:
        # Force the run past the gate the way a careless admin retry would:
        # mark the gate step succeeded and point the run at the purchase.
        run = (
            await session.execute(
                select(ProvisioningRun).where(ProvisioningRun.tenant_id == tenant_id)
            )
        ).scalar_one()
        gate = (
            await session.execute(
                select(ProvisioningStepRecord)
                .where(ProvisioningStepRecord.run_id == run.id)
                .where(ProvisioningStepRecord.step_name == ProvisioningStep.BILLING_GATE)
            )
        ).scalar_one()
        gate.status = StepStatus.SUCCEEDED
        run.status = ProvisioningStatus.BILLING_AUTHORIZED
        run.next_attempt_at = datetime.now(UTC)
        await session.commit()

    await worker_env.drain()

    # The gate step was bypassed, and the purchase still never happened —
    # because purchase_number asks again, in the same moment as the spend.
    assert worker_env.twilio.behaviour.count("purchase_number") == 0


async def test_entitlement_revoked_mid_run_stops_the_purchase(worker_env: WorkerEnv) -> None:
    """Billing can change between the gate step and the spend.

    The gate step passes, then the subscription is cancelled before the
    purchase step runs. Only a check performed at the moment of the spend
    catches this.
    """
    tenant_id = await _seed_signup(worker_env, status=SubscriptionStatus.ACTIVE, trial_ends_at=None)

    # Run only as far as the gate.
    async with worker_env.session_factory() as session:
        run = (
            await session.execute(
                select(ProvisioningRun).where(ProvisioningRun.tenant_id == tenant_id)
            )
        ).scalar_one()
        for step in (ProvisioningStep.VALIDATE, ProvisioningStep.GENERATE_CONFIG):
            record = (
                await session.execute(
                    select(ProvisioningStepRecord)
                    .where(ProvisioningStepRecord.run_id == run.id)
                    .where(ProvisioningStepRecord.step_name == step)
                )
            ).scalar_one()
            record.status = StepStatus.SUCCEEDED
        await session.commit()

    await worker_env.worker.tick()  # BILLING_GATE passes

    # The customer cancels — or their card fails — before the purchase.
    async with worker_env.session_factory() as session:
        subscription = (
            await session.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))
        ).scalar_one()
        subscription.status = SubscriptionStatus.CANCELED
        await session.commit()

    await worker_env.drain()

    assert worker_env.twilio.behaviour.count("purchase_number") == 0


async def test_J_dry_run_still_provisions_with_fake_providers(worker_env: WorkerEnv) -> None:
    """Requirement J. The existing DRY_RUN path is unchanged by the gate."""
    await _seed_signup(worker_env, status=SubscriptionStatus.ACTIVE, trial_ends_at=None)
    assert worker_env.settings.dry_run is True
    assert worker_env.settings.effective_twilio_provider == "fake"

    await worker_env.drain()

    assert worker_env.twilio.behaviour.count("purchase_number") == 1


async def test_signup_grants_a_trial_so_the_happy_path_provisions(worker_env: WorkerEnv) -> None:
    """The product flow: sign up, get a trial, get a number.

    A gate nobody can pass is not a gate, it is an outage — so the default
    path has to work.
    """
    from app.schemas.signup import SignupRequest
    from app.services.signup import SignupService

    async with worker_env.session_factory() as session:
        await SignupService(session, worker_env.settings).submit(
            SignupRequest.model_validate(SIGNUP), correlation_id="test-corr"
        )
        await session.commit()

    await worker_env.drain()

    assert worker_env.twilio.behaviour.count("purchase_number") == 1


async def test_a_resubmitted_signup_does_not_extend_the_trial(worker_env: WorkerEnv) -> None:
    """Otherwise the trial renews itself for anyone who notices."""
    from app.schemas.signup import SignupRequest
    from app.services.signup import SignupService

    async with worker_env.session_factory() as session:
        service = SignupService(session, worker_env.settings)
        await service.submit(SignupRequest.model_validate(SIGNUP), correlation_id="c1")
        await session.commit()

    async with worker_env.session_factory() as session:
        service = SignupService(session, worker_env.settings)
        await service.submit(SignupRequest.model_validate(SIGNUP), correlation_id="c2")
        await session.commit()

    async with worker_env.session_factory() as session:
        subscriptions = (await session.execute(select(Subscription))).scalars().all()
    assert len(subscriptions) == 1


# ===========================================================================
# Structural guarantees
#
# These assert on the shape of the code rather than its behaviour. A behavioural
# test proves the paths it exercises are safe; these prove no *new* unsafe path
# can be added without someone noticing.
# ===========================================================================


def test_there_is_exactly_one_place_that_buys_a_number() -> None:
    """The whole money-safety argument rests on this being a single call site.

    Two call sites means two things to gate, and the second is the one that gets
    forgotten. If this fails, either route the new caller through
    ``purchase_number.run()`` or gate it the same way — do not simply widen the
    expected set.

    Asserted on file paths rather than line numbers, so ordinary edits to the
    module do not break it.
    """
    import pathlib

    app_root = pathlib.Path(__file__).resolve().parent.parent / "app"
    files: set[str] = set()
    for path in app_root.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "purchase_number(" in line and "def purchase_number" not in line:
                files.add(path.relative_to(app_root).as_posix())

    assert files == {"provisioning/steps/purchase_number.py"}, (
        f"The Twilio purchase must have exactly one call site. Found: {sorted(files)}"
    )


def test_the_purchase_is_immediately_preceded_by_an_entitlement_check() -> None:
    """Adjacency is the property. Anything inserted between them widens the
    window in which entitlement can change unnoticed.
    """
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parent.parent / "app/provisioning/steps/purchase_number.py"
    ).read_text(encoding="utf-8")
    lines = source.splitlines()

    purchase_line = next(
        index
        for index, line in enumerate(lines)
        if "await ctx.providers.twilio.purchase_number(" in line
    )
    preceding = "\n".join(lines[max(0, purchase_line - 5) : purchase_line])
    assert "require_entitlement" in preceding, (
        "The entitlement check must sit immediately before the purchase call."
    )


def test_the_gate_step_precedes_the_purchase_step() -> None:
    from app.models.enums import STEP_SEQUENCE

    gate = STEP_SEQUENCE.index(ProvisioningStep.BILLING_GATE)
    purchase = STEP_SEQUENCE.index(ProvisioningStep.PURCHASE_NUMBER)
    assert gate == purchase - 1


def test_billing_blocked_is_in_flight_not_terminal() -> None:
    """A parked run must keep owning its tenant.

    In-flight means a duplicate signup cannot start a second run alongside it.
    Terminal would mean the worker never looks at it again — a customer who
    pays would then wait forever.
    """
    from app.models.enums import IN_FLIGHT_RUN_STATUSES, TERMINAL_RUN_STATUSES

    assert ProvisioningStatus.BILLING_BLOCKED in IN_FLIGHT_RUN_STATUSES
    assert ProvisioningStatus.BILLING_BLOCKED not in TERMINAL_RUN_STATUSES
