"""The provisioning workflow.

Runs against Temporal's in-process time-skipping environment, not a live server:
no Temporal Cloud account, no compose dependency, and a retry ladder that would
take ten minutes of wall clock completes instantly because the environment fast-
forwards its own timers.

Activities are the *real* ones, backed by the real database and the fake
vendors. That is deliberate — the point of M4 is that the step implementations
and their money-safety guards are unchanged, so testing against stubbed
activities would prove nothing about the property that matters.

The load-bearing test here is the same one as M10, restated under Temporal:

    twilio.behaviour.count("purchase_number") == 0

Temporal retries aggressively, so a gate that held under a single-pass polling
engine is not automatically safe under a workflow that will re-run an activity
on timeout. That has to be proven, not assumed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

from app.core.errors import VendorError
from app.models import PhoneNumber, ProvisioningRun, ProvisioningStepRecord, Tenant
from app.models.enums import (
    PhoneNumberStatus,
    ProvisioningStatus,
    ProvisioningStep,
    StepStatus,
    SubscriptionStatus,
    TenantStatus,
)
from app.schemas.signup import SignupRequest
from app.services.signup import SignupService
from app.temporal.activities import ProvisioningActivities
from app.temporal.shared import BILLING_UPDATED_SIGNAL, ProvisionInput, provision_workflow_id
from app.temporal.workflows import ProvisionOptions, ProvisionTenantWorkflow
from tests.factories import make_subscription
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

#: Short and few. The environment skips time, so a long ladder costs nothing in
#: wall clock — but a small budget keeps the *history* readable when a test
#: fails, and makes "retries exhausted" arrive in a predictable number of steps.
FAST = ProvisionOptions(max_attempts=3, activity_timeout_s=30, billing_recheck_interval_s=60)


async def _seed(env: WorkerEnv, *, entitled: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    """A tenant with a provisioning run, and the billing state under test."""
    async with env.session_factory() as session:
        result = await SignupService(session).submit(
            SignupRequest.model_validate(SIGNUP), correlation_id=uuid.uuid4().hex
        )
        tenant_id, run_id = result.tenant.id, result.run.id
        if entitled:
            tenant = await session.get(Tenant, tenant_id)
            assert tenant is not None
            session.add(
                make_subscription(tenant, status=SubscriptionStatus.ACTIVE, trial_ends_at=None)
            )
        await session.commit()
    return tenant_id, run_id


async def _run_workflow(
    env: WorkerEnv,
    temporal: WorkflowEnvironment,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    options: ProvisionOptions = FAST,
) -> object:
    """Start the workflow with real activities and wait for it to finish."""
    activities = ProvisioningActivities(env.settings, env.providers, env.session_factory)
    queue = f"test-{uuid.uuid4().hex[:8]}"

    async with Worker(
        temporal.client,
        task_queue=queue,
        workflows=[ProvisionTenantWorkflow],
        activities=activities.all_activities(),
    ):
        return await temporal.client.execute_workflow(
            ProvisionTenantWorkflow.run,
            args=[
                ProvisionInput(
                    run_id=str(run_id), tenant_id=str(tenant_id), correlation_id="t-corr"
                ),
                options,
            ],
            id=provision_workflow_id(run_id),
            task_queue=queue,
        )


async def _load_run(env: WorkerEnv, run_id: uuid.UUID) -> ProvisioningRun:
    async with env.session_factory() as session:
        run = await session.get(ProvisioningRun, run_id)
        assert run is not None
        return run


async def _load_steps(
    env: WorkerEnv, run_id: uuid.UUID
) -> dict[ProvisioningStep, ProvisioningStepRecord]:
    async with env.session_factory() as session:
        records = (
            (
                await session.execute(
                    select(ProvisioningStepRecord).where(ProvisioningStepRecord.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        return {record.step_name: record for record in records}


@pytest.fixture
async def temporal_env():  # type: ignore[no-untyped-def]
    """Temporal's time-skipping test server.

    Downloads a small test binary on first use and runs it in-process. No
    Temporal Cloud, no compose service — the suite stays runnable with no
    network beyond that first fetch.
    """
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


# ===========================================================================
# Happy path
# ===========================================================================


async def test_the_workflow_provisions_a_tenant_end_to_end(
    worker_env: WorkerEnv, temporal_env: WorkflowEnvironment
) -> None:
    tenant_id, run_id = await _seed(worker_env)

    result = await _run_workflow(worker_env, temporal_env, run_id, tenant_id)

    assert result.status == "active"  # type: ignore[attr-defined]

    run = await _load_run(worker_env, run_id)
    assert run.status is ProvisioningStatus.ACTIVE
    assert run.finished_at is not None
    # Temporal owns scheduling now; a leftover due timestamp would invite the
    # legacy polling claim query to run a second orchestrator over this run.
    assert run.next_attempt_at is None

    steps = await _load_steps(worker_env, run_id)
    assert all(record.status is StepStatus.SUCCEEDED for record in steps.values())
    assert worker_env.twilio.behaviour.count("purchase_number") == 1


async def test_the_database_remains_the_read_model(
    worker_env: WorkerEnv, temporal_env: WorkflowEnvironment
) -> None:
    """The API must keep answering from Postgres, not from workflow history."""
    tenant_id, run_id = await _seed(worker_env)
    await _run_workflow(worker_env, temporal_env, run_id, tenant_id)

    steps = await _load_steps(worker_env, run_id)
    # Every step in the documented sequence has a row, with the vendor payloads
    # the admin panel renders.
    assert set(steps) == set(ProvisioningStep)
    purchase = steps[ProvisioningStep.PURCHASE_NUMBER]
    assert purchase.response_json and "e164" in purchase.response_json


# ===========================================================================
# The money gate, under Temporal
# ===========================================================================


async def test_unauthorized_billing_never_calls_twilio(
    worker_env: WorkerEnv, temporal_env: WorkflowEnvironment
) -> None:
    """**The M10 guarantee, restated under Temporal.**

    The workflow parks on the gate and waits. It never reaches the purchase, so
    the vendor is never asked — which is the only assertion that means what it
    needs to mean.
    """
    tenant_id, run_id = await _seed(worker_env, entitled=False)

    activities = ProvisioningActivities(
        worker_env.settings, worker_env.providers, worker_env.session_factory
    )
    queue = f"test-{uuid.uuid4().hex[:8]}"

    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[ProvisionTenantWorkflow],
        activities=activities.all_activities(),
    ):
        handle = await temporal_env.client.start_workflow(
            ProvisionTenantWorkflow.run,
            args=[
                ProvisionInput(
                    run_id=str(run_id), tenant_id=str(tenant_id), correlation_id="t-corr"
                ),
                FAST,
            ],
            id=provision_workflow_id(run_id),
            task_queue=queue,
        )

        # Let the workflow reach the gate, park, and loop a few times. Time
        # skipping means the re-check interval elapses instantly.
        await temporal_env.sleep(timedelta(seconds=200))

        assert worker_env.twilio.behaviour.count("purchase_number") == 0
        # Parked, not FAILED: nothing is broken, no compensation was owed and
        # nothing was released. The workflow is still alive, waiting.
        run = await _load_run(worker_env, run_id)
        assert run.status is ProvisioningStatus.BILLING_BLOCKED
        await handle.cancel()


async def test_paying_mid_flight_releases_the_parked_workflow(
    worker_env: WorkerEnv, temporal_env: WorkflowEnvironment
) -> None:
    """The signal is the fast path out of BILLING_BLOCKED.

    It only ends the wait — the gate is re-evaluated from the database, so the
    signal cannot authorize anything by itself.
    """
    tenant_id, run_id = await _seed(worker_env, entitled=False)

    activities = ProvisioningActivities(
        worker_env.settings, worker_env.providers, worker_env.session_factory
    )
    queue = f"test-{uuid.uuid4().hex[:8]}"

    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[ProvisionTenantWorkflow],
        activities=activities.all_activities(),
    ):
        handle = await temporal_env.client.start_workflow(
            ProvisionTenantWorkflow.run,
            args=[
                ProvisionInput(
                    run_id=str(run_id), tenant_id=str(tenant_id), correlation_id="t-corr"
                ),
                FAST,
            ],
            id=provision_workflow_id(run_id),
            task_queue=queue,
        )
        await temporal_env.sleep(timedelta(seconds=90))
        assert worker_env.twilio.behaviour.count("purchase_number") == 0

        # The customer pays.
        async with worker_env.session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            assert tenant is not None
            session.add(
                make_subscription(tenant, status=SubscriptionStatus.ACTIVE, trial_ends_at=None)
            )
            await session.commit()

        await handle.signal(BILLING_UPDATED_SIGNAL)
        result = await handle.result()

    assert result.status == "active"
    assert worker_env.twilio.behaviour.count("purchase_number") == 1


async def test_entitlement_revoked_between_the_gate_and_the_spend_blocks(
    worker_env: WorkerEnv,
) -> None:
    """The gate step passed, then billing lapsed before the purchase ran.

    Driven through the activities directly rather than through the workflow,
    because the window under test is a *race* and reproducing it via workflow
    timing would be flaky. What matters is the guarantee, and the guarantee
    lives in `purchase_number`: it re-reads entitlement immediately before the
    vendor call, so a gate that passed minutes ago cannot authorize a spend.

    This is why M4 did not move the billing decision up into the workflow.
    """
    from app.models import Subscription
    from app.temporal.activities import ProvisioningActivities
    from app.temporal.shared import StepInput

    tenant_id, run_id = await _seed(worker_env, entitled=True)
    activities = ProvisioningActivities(
        worker_env.settings, worker_env.providers, worker_env.session_factory
    )

    # Driven through ActivityEnvironment, which supplies the activity context
    # Temporal would. Calling the method directly works until something reads
    # `activity.info()`, and then fails for a reason unrelated to the test.
    env = ActivityEnvironment()
    for step in ("validate", "generate_config", "billing_gate"):
        await env.run(activities.execute_step, StepInput(run_id=str(run_id), step=step))

    # The gate has passed. Now the customer cancels — or their card fails.
    async with worker_env.session_factory() as session:
        subscription = (
            await session.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))
        ).scalar_one()
        subscription.status = SubscriptionStatus.CANCELED
        await session.commit()

    with pytest.raises(ApplicationError) as excinfo:
        await env.run(
            activities.execute_step,
            StepInput(run_id=str(run_id), step="purchase_number"),
        )

    # Non-retryable, so Temporal stops rather than hammering a permanent state.
    assert excinfo.value.type == "billing_blocked"
    assert excinfo.value.non_retryable is True
    assert worker_env.twilio.behaviour.count("purchase_number") == 0


# ===========================================================================
# Failure, retry and compensation
# ===========================================================================


async def test_a_transient_vendor_failure_is_retried_and_recovers(
    worker_env: WorkerEnv, temporal_env: WorkflowEnvironment
) -> None:
    """Temporal's retry policy replaces the hand-rolled backoff ladder."""
    tenant_id, run_id = await _seed(worker_env)
    worker_env.twilio.behaviour.fail(
        "search_available_numbers",
        VendorError("flaky", vendor="fake-twilio", status_code=503, retryable=True),
        times=1,
    )

    result = await _run_workflow(worker_env, temporal_env, run_id, tenant_id)

    assert result.status == "active"  # type: ignore[attr-defined]
    # Retried, not duplicated: exactly one number was bought.
    assert worker_env.twilio.behaviour.count("purchase_number") == 1


async def test_a_terminal_validation_failure_is_not_retried(
    worker_env: WorkerEnv, temporal_env: WorkflowEnvironment
) -> None:
    """A rejected signup must fail fast, not burn the retry schedule."""
    tenant_id, run_id = await _seed(worker_env)
    async with worker_env.session_factory() as session:
        # Remove the profile the validate step requires.
        from app.models import BusinessProfile

        profile = (
            await session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == tenant_id)
            )
        ).scalar_one()
        await session.delete(profile)
        await session.commit()

    with pytest.raises(WorkflowFailureError):
        await _run_workflow(worker_env, temporal_env, run_id, tenant_id)

    steps = await _load_steps(worker_env, run_id)
    validate = steps[ProvisioningStep.VALIDATE]
    # One attempt. A non-retryable error stops Temporal immediately.
    assert validate.attempt == 1
    assert validate.status is StepStatus.FAILED

    run = await _load_run(worker_env, run_id)
    assert run.status is ProvisioningStatus.FAILED
    assert worker_env.twilio.behaviour.count("purchase_number") == 0


async def test_a_failure_after_the_purchase_releases_the_number(
    worker_env: WorkerEnv, temporal_env: WorkflowEnvironment
) -> None:
    """The saga. An orphaned number bills monthly, forever."""
    tenant_id, run_id = await _seed(worker_env)
    worker_env.elevenlabs.behaviour.fail(
        "create_agent",
        VendorError("permanently broken", vendor="fake-elevenlabs", retryable=False),
        times=10,
    )

    with pytest.raises(WorkflowFailureError):
        await _run_workflow(worker_env, temporal_env, run_id, tenant_id)

    async with worker_env.session_factory() as session:
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()
        assert number.status is PhoneNumberStatus.RELEASED
        assert number.released_at is not None

    run = await _load_run(worker_env, run_id)
    assert run.status is ProvisioningStatus.COMPENSATED

    async with worker_env.session_factory() as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        assert tenant.status is TenantStatus.ABANDONED


async def test_compensation_is_idempotent(worker_env: WorkerEnv) -> None:
    """Temporal retries a timed-out activity even when it already succeeded.

    Compensation must therefore be safe to run twice — releasing an unknown SID
    is a no-op at the vendor, and the second pass must not corrupt the row.
    """
    from app.temporal.shared import ProvisionInput as PI

    tenant_id, run_id = await _seed(worker_env)

    # Give the tenant a number to release.
    async with worker_env.session_factory() as session:
        purchased = await worker_env.twilio.purchase_number(
            e164="+18055551234", friendly_name=f"tenant:{tenant_id}"
        )
        session.add(
            PhoneNumber(
                tenant_id=tenant_id,
                e164=purchased.e164,
                twilio_sid=purchased.sid,
                status=PhoneNumberStatus.ACTIVE,
                purchased_at=datetime.now(UTC),
            )
        )
        await session.commit()

    activities = ProvisioningActivities(
        worker_env.settings, worker_env.providers, worker_env.session_factory
    )
    payload = PI(run_id=str(run_id), tenant_id=str(tenant_id), correlation_id="c")

    first = await activities.compensate(payload)
    second = await activities.compensate(payload)

    assert first.released_number == "+18055551234"
    # The second pass finds nothing left to release and says so, rather than
    # failing or double-releasing.
    assert second.released_number is None

    async with worker_env.session_factory() as session:
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()
        assert number.status is PhoneNumberStatus.RELEASED


# ===========================================================================
# Concurrency and recovery
# ===========================================================================


async def test_a_duplicate_workflow_start_adopts_the_running_one(
    worker_env: WorkerEnv, temporal_env: WorkflowEnvironment
) -> None:
    """Two starts, one execution — and therefore one phone number.

    Exercised through `start_provisioning`, because that is where adoption
    lives: the raw SDK call deliberately raises `WorkflowAlreadyStartedError`,
    and the helper turns that rejection into "the run is already being worked
    on", which is what a resubmitted signup actually wants.
    """
    from app.temporal.client import start_provisioning

    tenant_id, run_id = await _seed(worker_env)
    activities = ProvisioningActivities(
        worker_env.settings, worker_env.providers, worker_env.session_factory
    )
    settings = worker_env.settings.model_copy(
        update={"temporal_task_queue": f"test-{uuid.uuid4().hex[:8]}"}
    )

    async with Worker(
        temporal_env.client,
        task_queue=settings.temporal_task_queue,
        workflows=[ProvisionTenantWorkflow],
        activities=activities.all_activities(),
    ):
        first = await start_provisioning(
            temporal_env.client,
            settings,
            run_id=run_id,
            tenant_id=tenant_id,
            correlation_id="c1",
        )
        second = await start_provisioning(
            temporal_env.client,
            settings,
            run_id=run_id,
            tenant_id=tenant_id,
            correlation_id="c2",
        )

        # Same workflow id, and the second call adopted rather than raising.
        assert first == second == provision_workflow_id(run_id)

        await temporal_env.client.get_workflow_handle(first).result()

    # One execution, one purchase.
    assert worker_env.twilio.behaviour.count("purchase_number") == 1


async def test_the_workflow_id_is_derived_from_the_run() -> None:
    """Keyed by run, not tenant: a tenant legitimately has several runs."""
    run_id = uuid.uuid4()
    assert provision_workflow_id(run_id) == f"provision-{run_id}"


async def test_an_activity_retry_does_not_rebuy_a_number(
    worker_env: WorkerEnv, temporal_env: WorkflowEnvironment
) -> None:
    """The adoption guard, under Temporal's aggressive retries.

    The vendor already holds a number tagged for this tenant — the state a
    worker leaves behind when it dies between the purchase and the commit. The
    activity must adopt it rather than buy another.
    """
    tenant_id, run_id = await _seed(worker_env)

    await worker_env.twilio.purchase_number(
        e164="+18055559999", friendly_name=f"tenant:{tenant_id}"
    )
    assert worker_env.twilio.behaviour.count("purchase_number") == 1

    result = await _run_workflow(worker_env, temporal_env, run_id, tenant_id)

    assert result.status == "active"  # type: ignore[attr-defined]
    # Still one. The workflow adopted the existing number.
    assert worker_env.twilio.behaviour.count("purchase_number") == 1


# ===========================================================================
# Secrets
# ===========================================================================


def test_workflow_arguments_carry_no_secrets() -> None:
    """Activity arguments are recorded verbatim in workflow history.

    History is queryable by anyone with namespace access and is retained long
    after the run ends, so the contract is identifiers only — activities load
    what they need themselves.
    """
    from dataclasses import fields

    from app.temporal.shared import ProvisionInput as PI
    from app.temporal.shared import StepInput as SI

    allowed = {"run_id", "tenant_id", "correlation_id", "step"}
    for dataclass_type in (PI, SI):
        names = {field.name for field in fields(dataclass_type)}
        assert names <= allowed, f"{dataclass_type.__name__} carries unexpected fields: {names}"
