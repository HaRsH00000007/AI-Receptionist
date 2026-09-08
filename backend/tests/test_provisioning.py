"""The provisioning state machine, driven by the real worker.

These run against real Postgres with real transactions, because the properties
being tested are transactional: leases, ``SKIP LOCKED``, commits between steps,
and what survives a crash. Providers are fakes, so nothing costs money and the
whole path runs with no network.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import InvalidInputError, VendorError
from app.models import (
    Agent,
    AgentConfig,
    BusinessProfile,
    PhoneNumber,
    ProvisioningRun,
    ProvisioningStepRecord,
    Tenant,
)
from app.models.enums import (
    STEP_SEQUENCE,
    AgentConfigSource,
    AgentStatus,
    PhoneNumberStatus,
    ProvisioningStatus,
    ProvisioningStep,
    StepStatus,
    TenantStatus,
)
from app.provisioning.engine import ProvisioningEngine, claim_runs
from app.provisioning.retry import backoff_delay_s, should_retry
from app.provisioning.steps.purchase_number import build_ladder
from app.schemas.signup import SignupRequest
from app.services.signup import SignupService
from tests.worker_fixtures import WorkerEnv, build_fake_providers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
SIGNUP = {
    "business_name": "Sunset Salon",
    "business_type": "salon",
    "services": "cuts, color",
    "operating_hours": "Mon-Fri 9-6, closed Sun",
    "greeting_style": "friendly",
    "escalation_rules": "Text the owner for emergencies",
    "notification_email": "owner@sunsetsalon.example.com",
    "area_code": "805",
    "plan": "starter",
    "contact_phone": "8055550142",
}


async def create_signup(env: WorkerEnv, **overrides: object) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a tenant through the real signup service. Returns (tenant, run)."""
    async with env.session_factory() as session:
        result = await SignupService(session).submit(
            SignupRequest.model_validate({**SIGNUP, **overrides}),
            correlation_id=uuid.uuid4().hex,
        )
        await session.commit()
        return result.tenant.id, result.run.id


async def load_run(env: WorkerEnv, run_id: uuid.UUID) -> ProvisioningRun:
    async with env.session_factory() as session:
        return (
            await session.execute(select(ProvisioningRun).where(ProvisioningRun.id == run_id))
        ).scalar_one()


async def load_steps(
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


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
async def test_a_signup_runs_all_the_way_to_active(worker_env: WorkerEnv) -> None:
    """Definition of done 1: form submission to ACTIVE with zero manual steps."""
    tenant_id, run_id = await create_signup(worker_env)

    await worker_env.drain()

    run = await load_run(worker_env, run_id)
    assert run.status is ProvisioningStatus.ACTIVE
    assert run.finished_at is not None
    assert run.last_error is None

    steps = await load_steps(worker_env, run_id)
    assert {step: record.status for step, record in steps.items()} == dict.fromkeys(
        STEP_SEQUENCE, StepStatus.SUCCEEDED
    )

    async with worker_env.session_factory() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()
        agent = (
            await session.execute(select(Agent).where(Agent.tenant_id == tenant_id))
        ).scalar_one()
        config = (
            await session.execute(select(AgentConfig).where(AgentConfig.tenant_id == tenant_id))
        ).scalar_one()

    assert tenant.status is TenantStatus.ACTIVE
    assert number.status is PhoneNumberStatus.ACTIVE
    assert number.e164.startswith("+1805")  # the requested area code
    assert number.twilio_sid and number.elevenlabs_phone_id
    assert agent.status is AgentStatus.ACTIVE and agent.elevenlabs_agent_id
    assert config.is_live is True


async def test_the_run_visits_every_status_in_order(worker_env: WorkerEnv) -> None:
    """One step per tick, and the status after each is the documented one."""
    _, run_id = await create_signup(worker_env)

    seen: list[ProvisioningStatus] = []
    for _ in range(len(STEP_SEQUENCE)):
        await worker_env.worker.tick()
        seen.append((await load_run(worker_env, run_id)).status)

    assert seen == [
        ProvisioningStatus.VALIDATED,
        ProvisioningStatus.CONFIG_GENERATED,
        ProvisioningStatus.NUMBER_PURCHASED,
        ProvisioningStatus.AGENT_CREATED,
        ProvisioningStatus.NUMBER_LINKED,
        ProvisioningStatus.VERIFIED,
        ProvisioningStatus.ACTIVE,
    ]


async def test_activation_emails_the_owner_with_the_number(worker_env: WorkerEnv) -> None:
    tenant_id, _ = await create_signup(worker_env)
    await worker_env.drain()

    async with worker_env.session_factory() as session:
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()

    message = worker_env.email.last_to("owner@sunsetsalon.example.com")
    assert message is not None
    assert number.e164 in message.text
    assert "Sunset Salon" in message.subject


async def test_the_agent_gets_our_rendered_prompt(worker_env: WorkerEnv) -> None:
    """ElevenLabs is a projection of the database, not the other way round."""
    tenant_id, _ = await create_signup(worker_env)
    await worker_env.drain()

    async with worker_env.session_factory() as session:
        config = (
            await session.execute(select(AgentConfig).where(AgentConfig.tenant_id == tenant_id))
        ).scalar_one()

    stored = next(iter(worker_env.elevenlabs.agents.values()))
    assert stored.system_prompt == config.system_prompt
    assert stored.first_message == config.first_message
    assert "Sunset Salon" in stored.system_prompt
    # Voice comes from the greeting style, never from the model.
    assert stored.voice_id == worker_env.settings.voice_map["friendly"]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
async def test_a_second_drain_changes_nothing(worker_env: WorkerEnv) -> None:
    """A finished run must not be picked up and re-executed."""
    _, _run_id = await create_signup(worker_env)
    await worker_env.drain()

    purchases = worker_env.twilio.behaviour.count("purchase_number")
    agents = worker_env.elevenlabs.behaviour.count("create_agent")

    await worker_env.drain()

    assert worker_env.twilio.behaviour.count("purchase_number") == purchases == 1
    assert worker_env.elevenlabs.behaviour.count("create_agent") == agents == 1


async def test_a_crash_after_purchase_adopts_instead_of_rebuying(
    worker_env: WorkerEnv,
) -> None:
    """The money-losing failure this system exists to prevent.

    Simulates a worker that bought a number and died before committing: the
    Twilio side has the number tagged with the tenant name, our side has
    nothing. The retry must adopt it.
    """
    tenant_id, _run_id = await create_signup(worker_env)

    # Walk up to the purchase step.
    await worker_env.worker.tick()  # validate
    await worker_env.worker.tick()  # generate_config

    # The vendor already sold us one under this tenant's name.
    await worker_env.twilio.purchase_number(
        e164="+18055559999", friendly_name=f"tenant:{tenant_id}"
    )
    assert worker_env.twilio.behaviour.count("purchase_number") == 1

    await worker_env.worker.tick()  # purchase_number

    # Adopted, not bought again.
    assert worker_env.twilio.behaviour.count("purchase_number") == 1
    async with worker_env.session_factory() as session:
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()
    assert number.e164 == "+18055559999"
    assert number.status is PhoneNumberStatus.ACTIVE


async def test_a_pending_row_is_written_before_the_purchase_call(
    worker_env: WorkerEnv,
) -> None:
    """Durable intent, committed before money is spent.

    The purchase is made to fail; the pending row must already be on disk, which
    is what gives a later attempt something to recover from.
    """
    tenant_id, _ = await create_signup(worker_env)
    await worker_env.worker.tick()
    await worker_env.worker.tick()

    worker_env.twilio.behaviour.fail(
        "purchase_number",
        VendorError("boom", vendor="fake-twilio", retryable=True),
    )
    await worker_env.worker.tick()

    async with worker_env.session_factory() as session:
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()
    assert number.status is PhoneNumberStatus.PENDING
    assert number.twilio_sid is None


async def test_config_generation_is_not_paid_for_twice(worker_env: WorkerEnv) -> None:
    """A live config at the current profile version is adopted, not regenerated."""
    _, run_id = await create_signup(worker_env)
    await worker_env.drain()

    calls = worker_env.llm.behaviour.count("complete")
    assert calls == 1

    # Reset the later steps and run again; the config step must adopt.
    async with worker_env.session_factory() as session:
        records = (
            (
                await session.execute(
                    select(ProvisioningStepRecord).where(ProvisioningStepRecord.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        for record in records:
            if record.step_name is not ProvisioningStep.VALIDATE:
                record.status = StepStatus.PENDING
        run = (
            await session.execute(select(ProvisioningRun).where(ProvisioningRun.id == run_id))
        ).scalar_one()
        run.status = ProvisioningStatus.VALIDATED
        run.next_attempt_at = None
        run.finished_at = None
        await session.commit()

    await worker_env.drain()
    assert worker_env.llm.behaviour.count("complete") == calls


# ---------------------------------------------------------------------------
# Retry and failure
# ---------------------------------------------------------------------------
async def test_a_retryable_failure_is_retried_and_recovers(worker_env: WorkerEnv) -> None:
    """Definition of done 2: a failed step surfaces and succeeds on retry."""
    _, run_id = await create_signup(worker_env)

    worker_env.twilio.behaviour.fail(
        "search_available_numbers",
        VendorError("twilio is unwell", vendor="fake-twilio", status_code=503, retryable=True),
        times=2,
    )

    await worker_env.drain()

    run = await load_run(worker_env, run_id)
    assert run.status is ProvisioningStatus.ACTIVE

    step = (await load_steps(worker_env, run_id))[ProvisioningStep.PURCHASE_NUMBER]
    assert step.status is StepStatus.SUCCEEDED
    assert step.attempt == 3  # two failures, then success


async def test_a_terminal_error_is_never_retried(worker_env: WorkerEnv) -> None:
    """ "Never retry a 400" - a 4xx will fail identically forever."""
    _, run_id = await create_signup(worker_env)

    worker_env.twilio.behaviour.fail(
        "purchase_number",
        VendorError.from_status("bad request", vendor="fake-twilio", status_code=400),
        times=5,
    )

    await worker_env.drain()

    run = await load_run(worker_env, run_id)
    assert run.status is ProvisioningStatus.COMPENSATED
    step = (await load_steps(worker_env, run_id))[ProvisioningStep.PURCHASE_NUMBER]
    assert step.status is StepStatus.FAILED
    # One attempt only: the budget was not burned on a hopeless call.
    assert step.attempt == 1


async def test_a_run_fails_after_the_attempt_budget(worker_env: WorkerEnv) -> None:
    _, run_id = await create_signup(worker_env)

    worker_env.twilio.behaviour.fail(
        "search_available_numbers",
        VendorError("still unwell", vendor="fake-twilio", status_code=503, retryable=True),
        times=10,
    )

    await worker_env.drain()

    step = (await load_steps(worker_env, run_id))[ProvisioningStep.PURCHASE_NUMBER]
    assert step.attempt == worker_env.settings.provisioning_max_attempts
    assert step.status is StepStatus.FAILED
    assert step.error and "still unwell" in step.error


async def test_the_error_is_visible_on_the_run(worker_env: WorkerEnv) -> None:
    """A failure nobody can see is the failure mode this replaces."""
    _, run_id = await create_signup(worker_env)
    worker_env.twilio.behaviour.fail(
        "purchase_number",
        VendorError.from_status("no stock", vendor="fake-twilio", status_code=400),
    )

    await worker_env.drain()

    run = await load_run(worker_env, run_id)
    assert run.last_error and "no stock" in run.last_error
    assert run.correlation_id


# ---------------------------------------------------------------------------
# Compensation
# ---------------------------------------------------------------------------
async def test_a_terminal_failure_releases_the_number(worker_env: WorkerEnv) -> None:
    """An orphaned number bills monthly, forever. It must be handed back."""
    tenant_id, run_id = await create_signup(worker_env)

    # Fail after the number is bought.
    worker_env.elevenlabs.behaviour.fail(
        "create_agent",
        VendorError.from_status("agent rejected", vendor="fake-elevenlabs", status_code=422),
        times=5,
    )

    await worker_env.drain()

    run = await load_run(worker_env, run_id)
    assert run.status is ProvisioningStatus.COMPENSATED

    async with worker_env.session_factory() as session:
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()

    assert number.status is PhoneNumberStatus.RELEASED
    assert number.released_at is not None
    assert tenant.status is TenantStatus.ABANDONED
    # Actually handed back at the vendor, not just marked in our database.
    assert number.twilio_sid in worker_env.twilio.released
    assert worker_env.twilio.purchased == {}


async def test_compensation_notifies_the_owner(worker_env: WorkerEnv) -> None:
    await create_signup(worker_env)
    worker_env.elevenlabs.behaviour.fail(
        "create_agent",
        VendorError.from_status("nope", vendor="fake-elevenlabs", status_code=422),
        times=5,
    )
    await worker_env.drain()

    message = worker_env.email.last_to("owner@sunsetsalon.example.com")
    assert message is not None
    assert "could not finish" in message.subject.lower()
    # The customer gets a reason, never a vendor stack trace.
    assert "Traceback" not in message.text


async def test_compensation_is_safe_to_run_twice(worker_env: WorkerEnv) -> None:
    """Compensation can itself be interrupted, so it must be idempotent."""
    tenant_id, run_id = await create_signup(worker_env)
    worker_env.elevenlabs.behaviour.fail(
        "create_agent",
        VendorError.from_status("nope", vendor="fake-elevenlabs", status_code=422),
        times=5,
    )
    await worker_env.drain()

    from app.provisioning.compensation import compensate_run

    async with worker_env.session_factory() as session:
        run = (
            await session.execute(select(ProvisioningRun).where(ProvisioningRun.id == run_id))
        ).scalar_one()
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
        report = await compensate_run(
            session,
            run=run,
            tenant=tenant,
            providers=worker_env.providers,
            settings=worker_env.settings,
        )

    assert report["errors"] == []


# ---------------------------------------------------------------------------
# Crash recovery and concurrency
# ---------------------------------------------------------------------------
async def test_a_run_resumes_after_the_worker_dies(worker_env: WorkerEnv) -> None:
    """Definition of done 3: killing the worker mid-run loses nothing."""
    _, run_id = await create_signup(worker_env)

    await worker_env.worker.tick()
    await worker_env.worker.tick()
    midway = await load_run(worker_env, run_id)
    assert midway.status is ProvisioningStatus.CONFIG_GENERATED

    # A brand new worker process, sharing only the database.
    from app.worker import Worker

    replacement = Worker(worker_env.settings, worker_env.providers, worker_env.session_factory)
    for _ in range(10):
        await replacement.tick()

    assert (await load_run(worker_env, run_id)).status is ProvisioningStatus.ACTIVE


async def test_a_step_orphaned_mid_flight_is_reclaimed(worker_env: WorkerEnv) -> None:
    """A RUNNING row whose lease expired was abandoned by a dead worker."""
    _, run_id = await create_signup(worker_env)
    await worker_env.worker.tick()

    async with worker_env.session_factory() as session:
        record = (
            await session.execute(
                select(ProvisioningStepRecord)
                .where(ProvisioningStepRecord.run_id == run_id)
                .where(ProvisioningStepRecord.step_name == ProvisioningStep.GENERATE_CONFIG)
            )
        ).scalar_one()
        record.status = StepStatus.RUNNING
        record.started_at = datetime.now(UTC) - timedelta(
            seconds=worker_env.settings.provisioning_step_timeout_s + 60
        )
        run = (
            await session.execute(select(ProvisioningRun).where(ProvisioningRun.id == run_id))
        ).scalar_one()
        run.next_attempt_at = None
        await session.commit()

    await worker_env.drain()
    assert (await load_run(worker_env, run_id)).status is ProvisioningStatus.ACTIVE


async def test_a_running_step_inside_its_lease_is_left_alone(worker_env: WorkerEnv) -> None:
    """Another worker is holding it; taking it would duplicate the work."""
    _, run_id = await create_signup(worker_env)
    await worker_env.worker.tick()

    async with worker_env.session_factory() as session:
        record = (
            await session.execute(
                select(ProvisioningStepRecord)
                .where(ProvisioningStepRecord.run_id == run_id)
                .where(ProvisioningStepRecord.step_name == ProvisioningStep.GENERATE_CONFIG)
            )
        ).scalar_one()
        record.status = StepStatus.RUNNING
        record.started_at = datetime.now(UTC)
        run = (
            await session.execute(select(ProvisioningRun).where(ProvisioningRun.id == run_id))
        ).scalar_one()
        run.next_attempt_at = None
        await session.commit()

    engine = ProvisioningEngine(worker_env.settings, worker_env.providers)
    async with worker_env.session_factory() as session:
        report = await engine.execute_next_step(session, run_id)

    assert report.outcome == "idle"
    assert worker_env.llm.behaviour.count("complete") == 0


async def test_two_workers_do_not_claim_the_same_run(worker_env: WorkerEnv) -> None:
    """``FOR UPDATE SKIP LOCKED``: the second poller skips, it does not block."""
    run_ids = set()
    for index in range(4):
        _, run_id = await create_signup(
            worker_env, notification_email=f"owner{index}@salon.example.com"
        )
        run_ids.add(run_id)

    async with worker_env.session_factory() as first, worker_env.session_factory() as second:
        # The first transaction holds its rows; the second must not see them.
        claimed_first = await claim_runs(first, batch_size=2, lease_s=300)
        claimed_second = await claim_runs(second, batch_size=2, lease_s=300)

    assert len(claimed_first) == 2
    assert len(claimed_second) == 2
    assert set(claimed_first).isdisjoint(claimed_second)
    assert set(claimed_first) | set(claimed_second) == run_ids


async def test_a_leased_run_is_not_claimed_again(worker_env: WorkerEnv) -> None:
    _, run_id = await create_signup(worker_env)

    async with worker_env.session_factory() as session:
        first = await claim_runs(session, batch_size=5, lease_s=300)
    async with worker_env.session_factory() as session:
        second = await claim_runs(session, batch_size=5, lease_s=300)

    assert first == [run_id]
    assert second == []


async def test_two_workers_running_concurrently_produce_one_number(
    worker_env: WorkerEnv,
) -> None:
    """The end-to-end version of the anti-double-spend guarantee."""
    import asyncio

    from app.worker import Worker

    tenant_id, run_id = await create_signup(worker_env)

    workers = [
        Worker(worker_env.settings, worker_env.providers, worker_env.session_factory)
        for _ in range(3)
    ]
    for _ in range(12):
        await asyncio.gather(*(worker.tick() for worker in workers))

    assert (await load_run(worker_env, run_id)).status is ProvisioningStatus.ACTIVE
    async with worker_env.session_factory() as session:
        numbers = (
            (await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
    assert len(numbers) == 1
    assert worker_env.twilio.behaviour.count("purchase_number") == 1


# ---------------------------------------------------------------------------
# The search ladder
# ---------------------------------------------------------------------------
def test_the_ladder_is_ordered_and_deterministic() -> None:
    """Exact area code, then same state, then anything, then toll-free."""
    ladder = build_ladder("805")
    assert [rung.strategy for rung in ladder] == [
        "exact_area_code",
        "same_state",
        "any_local",
        "toll_free",
    ]
    assert ladder[0].area_code == "805"
    assert ladder[1].in_region == "CA"
    assert ladder[3].toll_free is True


def test_the_ladder_skips_the_state_rung_for_an_unknown_code() -> None:
    assert [rung.strategy for rung in build_ladder(None)] == ["any_local", "toll_free"]


async def test_an_area_code_with_no_stock_falls_back_to_the_state(
    worker_env: WorkerEnv,
) -> None:
    """A number in the right state beats no number at all."""
    tenant_id, run_id = await create_signup(worker_env, area_code="209")  # CA, unstocked

    await worker_env.drain()

    assert (await load_run(worker_env, run_id)).status is ProvisioningStatus.ACTIVE
    async with worker_env.session_factory() as session:
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()

    step = (await load_steps(worker_env, run_id))[ProvisioningStep.PURCHASE_NUMBER]
    assert step.response_json is not None
    assert step.response_json["strategy"] == "same_state"
    assert step.response_json["e164"] == number.e164


async def test_the_strategies_tried_are_recorded(worker_env: WorkerEnv) -> None:
    """The admin panel should show why a number came from where it did."""
    _, run_id = await create_signup(worker_env, area_code="209")
    await worker_env.drain()

    step = (await load_steps(worker_env, run_id))[ProvisioningStep.PURCHASE_NUMBER]
    assert step.request_json is not None
    assert step.request_json["strategies_tried"] == ["exact_area_code", "same_state"]


# ---------------------------------------------------------------------------
# Validation step
# ---------------------------------------------------------------------------
async def test_a_tenant_without_services_fails_terminally(worker_env: WorkerEnv) -> None:
    tenant_id, run_id = await create_signup(worker_env)

    async with worker_env.session_factory() as session:
        profile = (
            await session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == tenant_id)
            )
        ).scalar_one()
        profile.services = []
        await session.commit()

    await worker_env.drain()

    run = await load_run(worker_env, run_id)
    assert run.status is ProvisioningStatus.COMPENSATED
    step = (await load_steps(worker_env, run_id))[ProvisioningStep.VALIDATE]
    assert step.status is StepStatus.FAILED
    assert step.attempt == 1  # terminal, so no retries


async def test_validation_derives_the_timezone(worker_env: WorkerEnv) -> None:
    tenant_id, _run_id = await create_signup(
        worker_env, area_code="212", contact_phone="2125550188"
    )
    await worker_env.worker.tick()

    async with worker_env.session_factory() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    assert tenant.timezone == "America/New_York"


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
async def test_verification_catches_an_unlinked_number(worker_env: WorkerEnv) -> None:
    """The item that shipped unverified in the system this replaces.

    The link is silently undone after the assign step; verify must refuse to let
    the run reach ACTIVE.
    """
    _, run_id = await create_signup(worker_env)

    for _ in range(5):  # validate .. link_number
        await worker_env.worker.tick()

    for phone in worker_env.elevenlabs.phones.values():
        phone.assigned_agent_id = None  # the link never really took

    await worker_env.drain()

    run = await load_run(worker_env, run_id)
    assert run.status is not ProvisioningStatus.ACTIVE
    step = (await load_steps(worker_env, run_id))[ProvisioningStep.VERIFY]
    assert step.error and "not linked" in step.error


async def test_verification_reads_both_objects_back(worker_env: WorkerEnv) -> None:
    _, run_id = await create_signup(worker_env)
    await worker_env.drain()

    step = (await load_steps(worker_env, run_id))[ProvisioningStep.VERIFY]
    assert step.response_json == {
        "agent_exists": True,
        "phone_exists": True,
        "assigned_agent_id": step.response_json["assigned_agent_id"],  # type: ignore[index]
        "voice_id": step.response_json["voice_id"],  # type: ignore[index]
    }


# ---------------------------------------------------------------------------
# Retry policy, in isolation
# ---------------------------------------------------------------------------
def test_backoff_climbs_then_plateaus() -> None:
    schedule = (5, 30, 120, 600)
    assert [backoff_delay_s(attempt, schedule) for attempt in range(1, 7)] == [
        5,
        30,
        120,
        600,
        600,
        600,
    ]


def test_retryable_errors_retry_until_the_budget_runs_out() -> None:
    error = VendorError("flaky", vendor="x", retryable=True)
    assert should_retry(error, attempt=1, max_attempts=3) is True
    assert should_retry(error, attempt=3, max_attempts=3) is False


def test_terminal_errors_never_retry() -> None:
    assert should_retry(InvalidInputError("bad"), attempt=1, max_attempts=5) is False


def test_an_unexpected_exception_gets_one_more_go() -> None:
    """An unknown crash is more often a blip than a permanent truth."""
    assert should_retry(RuntimeError("?"), attempt=1, max_attempts=3) is True
    assert should_retry(RuntimeError("?"), attempt=3, max_attempts=3) is False


# ---------------------------------------------------------------------------
# Config generation inside the run
# ---------------------------------------------------------------------------
async def test_a_broken_llm_falls_back_to_the_template(worker_env: WorkerEnv) -> None:
    """Signup must never hard-block on a vendor being down."""
    worker_env.llm.scripted = ["this is not json", "still not json"]

    tenant_id, run_id = await create_signup(worker_env)
    await worker_env.drain()

    assert (await load_run(worker_env, run_id)).status is ProvisioningStatus.ACTIVE
    async with worker_env.session_factory() as session:
        config = (
            await session.execute(select(AgentConfig).where(AgentConfig.tenant_id == tenant_id))
        ).scalar_one()

    assert config.generated_by is AgentConfigSource.TEMPLATE_FALLBACK
    assert "Sunset Salon" in config.system_prompt
    assert "cuts" in config.system_prompt


async def test_the_fallback_is_reached_only_after_a_repair_attempt(
    worker_env: WorkerEnv,
) -> None:
    """One repair, then stop asking — not an unbounded conversation."""
    worker_env.llm.scripted = ["not json", "still not json"]
    await create_signup(worker_env)
    await worker_env.drain()

    assert worker_env.llm.behaviour.count("complete") == 2


async def test_a_repaired_reply_is_accepted(worker_env: WorkerEnv) -> None:
    """The first reply is malformed; the second validates and is used."""
    worker_env.llm.scripted = ["{ oops"]
    tenant_id, _ = await create_signup(worker_env)
    await worker_env.drain()

    async with worker_env.session_factory() as session:
        config = (
            await session.execute(select(AgentConfig).where(AgentConfig.tenant_id == tenant_id))
        ).scalar_one()
    assert config.generated_by is AgentConfigSource.LLM
    assert worker_env.llm.behaviour.count("complete") == 2


@pytest.mark.parametrize(
    "reply",
    [
        '{"business_summary": "x"}',  # missing required fields
        '{"business_summary": "x", "services": [], "greeting": "hi"}',  # empty services
        "[]",  # not an object
    ],
)
async def test_schema_violations_are_rejected(worker_env: WorkerEnv, reply: str) -> None:
    worker_env.llm.scripted = [reply, reply]
    tenant_id, _ = await create_signup(worker_env)
    await worker_env.drain()

    async with worker_env.session_factory() as session:
        config = (
            await session.execute(select(AgentConfig).where(AgentConfig.tenant_id == tenant_id))
        ).scalar_one()
    assert config.generated_by is AgentConfigSource.TEMPLATE_FALLBACK


async def test_an_llm_outage_falls_back_without_burning_retries(
    worker_env: WorkerEnv,
) -> None:
    """A transport failure is not a schema failure; do not ask twice."""
    worker_env.llm.behaviour.fail(
        "complete",
        VendorError("llm down", vendor="fake-llm", status_code=503, retryable=True),
        times=1,
    )
    tenant_id, run_id = await create_signup(worker_env)
    await worker_env.drain()

    assert (await load_run(worker_env, run_id)).status is ProvisioningStatus.ACTIVE
    async with worker_env.session_factory() as session:
        config = (
            await session.execute(select(AgentConfig).where(AgentConfig.tenant_id == tenant_id))
        ).scalar_one()
    assert config.generated_by is AgentConfigSource.TEMPLATE_FALLBACK


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------
async def test_two_tenants_provision_independently(worker_env: WorkerEnv) -> None:
    ids = []
    for index in range(3):
        tenant_id, _ = await create_signup(
            worker_env,
            notification_email=f"owner{index}@salon.example.com",
            business_name=f"Salon {index}",
        )
        ids.append(tenant_id)

    await worker_env.drain()

    async with worker_env.session_factory() as session:
        numbers = (await session.execute(select(PhoneNumber))).scalars().all()
        agents = (await session.execute(select(Agent))).scalars().all()

    assert len(numbers) == 3
    assert len({number.e164 for number in numbers}) == 3
    assert len({agent.elevenlabs_agent_id for agent in agents}) == 3
    assert {number.tenant_id for number in numbers} == set(ids)


async def test_one_failing_tenant_does_not_stop_the_others(worker_env: WorkerEnv) -> None:
    good_id, _ = await create_signup(
        worker_env, notification_email="good@salon.example.com", business_name="Good Salon"
    )
    bad_id, _bad_run = await create_signup(
        worker_env, notification_email="bad@salon.example.com", business_name="Bad Salon"
    )

    async with worker_env.session_factory() as session:
        profile = (
            await session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == bad_id)
            )
        ).scalar_one()
        profile.services = []
        await session.commit()

    await worker_env.drain()

    async with worker_env.session_factory() as session:
        good = (await session.execute(select(Tenant).where(Tenant.id == good_id))).scalar_one()
        bad = (await session.execute(select(Tenant).where(Tenant.id == bad_id))).scalar_one()

    assert good.status is TenantStatus.ACTIVE
    assert bad.status is TenantStatus.ABANDONED


# ---------------------------------------------------------------------------
# DRY_RUN
# ---------------------------------------------------------------------------
def test_dry_run_forces_fakes_for_everything_that_costs_money() -> None:
    from app.core.config import Settings
    from app.providers.registry import build_providers

    settings = Settings(
        database_url="postgresql+asyncpg://a:b@localhost:55432/c",
        dry_run=True,
        twilio_provider="twilio",
        elevenlabs_provider="elevenlabs",
        email_provider="resend",
        twilio_account_sid="AC",
        twilio_auth_token="token",
        elevenlabs_api_key="key",
        resend_api_key="key",
        _env_file=None,
    )
    providers = build_providers(settings)

    assert providers.twilio.name == "fake-twilio"
    assert providers.elevenlabs.name == "fake-elevenlabs"
    assert providers.email.name == "fake-email"


def test_a_real_provider_without_its_credential_fails_at_startup() -> None:
    """Discovered at boot, not halfway through a tenant's provisioning."""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="TWILIO_ACCOUNT_SID"):
        Settings(
            database_url="postgresql+asyncpg://a:b@localhost:55432/c",
            dry_run=False,
            twilio_provider="twilio",
            _env_file=None,
        )


def test_fake_providers_satisfy_the_protocols() -> None:
    from app.providers.protocols import (
        ElevenLabsProvider,
        EmailProvider,
        LLMProvider,
        TwilioProvider,
    )

    providers = build_fake_providers()
    assert isinstance(providers.llm, LLMProvider)
    assert isinstance(providers.twilio, TwilioProvider)
    assert isinstance(providers.elevenlabs, ElevenLabsProvider)
    assert isinstance(providers.email, EmailProvider)
