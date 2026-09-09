"""Exactly one orchestrator owns provisioning at a time.

The hazard this guards is specific and expensive. Under Temporal, a run belongs
to a workflow. If the legacy polling loop also claimed it, both would walk the
step sequence, and the step that spends money would be attempted by two
orchestrators that cannot see each other. The database guards would catch most
of that — but "most" is not a guarantee worth relying on when the failure mode
is buying a second phone number.

So the polling worker refuses to claim provisioning runs unless it is the
configured orchestrator, and that refusal is asserted here rather than trusted.
"""

from __future__ import annotations

import uuid

from app.models import ProvisioningRun
from app.models.enums import ProvisioningStatus
from app.schemas.signup import SignupRequest
from app.services.signup import SignupService
from app.worker import Worker
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


async def _seed(env: WorkerEnv) -> uuid.UUID:
    async with env.session_factory() as session:
        result = await SignupService(session, env.settings).submit(
            SignupRequest.model_validate(SIGNUP), correlation_id=uuid.uuid4().hex
        )
        await session.commit()
        return result.run.id


async def test_the_polling_worker_does_not_touch_runs_under_temporal(
    worker_env: WorkerEnv,
) -> None:
    """The guard. Under Temporal the polling loop must leave runs alone.

    Asserted on the run's own state rather than only on a counter: a run that
    is still DRAFT after several ticks was genuinely never advanced.
    """
    run_id = await _seed(worker_env)

    temporal_settings = build_settings(
        database_url=worker_env.settings.database_url,
        orchestrator="temporal",
    )
    worker = Worker(temporal_settings, worker_env.providers, worker_env.session_factory)

    for _ in range(5):
        await worker.tick()

    assert worker.stats.steps_executed == 0
    assert worker_env.twilio.behaviour.count("purchase_number") == 0

    async with worker_env.session_factory() as session:
        run = await session.get(ProvisioningRun, run_id)
        assert run is not None
        assert run.status is ProvisioningStatus.DRAFT


async def test_the_polling_worker_still_runs_under_the_state_machine(
    worker_env: WorkerEnv,
) -> None:
    """The fallback must actually work, or it is not a fallback."""
    await _seed(worker_env)
    assert worker_env.settings.orchestrator == "state_machine"

    await worker_env.drain()

    assert worker_env.worker.stats.steps_executed > 0
    assert worker_env.twilio.behaviour.count("purchase_number") == 1


async def test_post_call_processing_runs_under_either_orchestrator(
    worker_env: WorkerEnv,
) -> None:
    """Summarizing a finished call is queue work, not orchestration.

    It keeps polling regardless, so moving provisioning to Temporal must not
    have taken call processing with it.
    """
    temporal_settings = build_settings(
        database_url=worker_env.settings.database_url,
        orchestrator="temporal",
    )
    worker = Worker(temporal_settings, worker_env.providers, worker_env.session_factory)

    # No calls to process, but the loop must run without raising — the claim
    # query is reached rather than skipped along with provisioning.
    await worker.tick()
    assert worker.stats.polls == 1


async def test_temporal_is_the_default_orchestrator() -> None:
    """M4 made Temporal authoritative; a fresh deployment gets it."""
    from app.core.config import Settings

    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
        _env_file=None,
    )
    assert settings.orchestrator == "temporal"
    assert settings.uses_temporal is True


async def test_a_run_advanced_by_temporal_leaves_no_polling_schedule(
    worker_env: WorkerEnv,
) -> None:
    """`next_attempt_at` must be null after a Temporal-driven step.

    A leftover due timestamp is what would let the legacy claim query pick the
    run up, which is the double-orchestration hazard in its most likely form.
    """
    from temporalio.testing import ActivityEnvironment

    from app.temporal.activities import ProvisioningActivities
    from app.temporal.shared import StepInput

    run_id = await _seed(worker_env)
    activities = ProvisioningActivities(
        worker_env.settings, worker_env.providers, worker_env.session_factory
    )

    # ActivityEnvironment supplies the activity context Temporal would.
    await ActivityEnvironment().run(
        activities.execute_step, StepInput(run_id=str(run_id), step="validate")
    )

    async with worker_env.session_factory() as session:
        run = await session.get(ProvisioningRun, run_id)
        assert run is not None
        assert run.next_attempt_at is None


async def test_claiming_is_the_only_way_the_polling_engine_starts_work(
    worker_env: WorkerEnv,
) -> None:
    """Structural: the guard sits around `claim_runs`, not inside the engine.

    If someone later moves the check, this fails and they have to think about
    it — which is the point.
    """
    import inspect

    from app import worker as worker_module

    source = inspect.getsource(worker_module.Worker.tick)
    claim_index = source.index("claim_runs(")
    guard_index = source.index("uses_temporal")
    assert guard_index < claim_index, (
        "The single-orchestrator guard must precede claim_runs, or the polling "
        "loop can take a run that belongs to a workflow."
    )
