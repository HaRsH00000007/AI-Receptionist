"""Regression tests for the defects found in the production-readiness audit.

Each of these failed before its fix. They live together so the reason each one
exists stays legible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import VendorError
from app.models import PhoneNumber, ProvisioningRun, ProvisioningStepRecord, Tenant
from app.models.enums import (
    PhoneNumberStatus,
    ProvisioningStatus,
    ProvisioningStep,
    StepStatus,
    TenantStatus,
    WebhookProvider,
)
from app.provisioning.engine import ProvisioningEngine
from app.services.idempotency import step_idempotency_key
from app.services.signatures import public_request_url
from app.services.webhooks import derive_event_id
from tests.test_provisioning import create_signup, load_run, load_steps
from tests.worker_fixtures import WorkerEnv


# ---------------------------------------------------------------------------
# An interrupted compensation must be finished, not stranded
# ---------------------------------------------------------------------------
async def test_an_interrupted_compensation_is_resumed(worker_env: WorkerEnv) -> None:
    """The money-leak case.

    A crash between "mark the run COMPENSATING" and "release the number" used to
    strand the run forever: it stayed in-flight, so the worker reclaimed it, but
    its failed step blocked every attempt — while the number kept billing.
    """
    tenant_id, run_id = await create_signup(worker_env)

    # Get as far as owning a number, then fail the next step terminally.
    worker_env.elevenlabs.behaviour.fail(
        "create_agent",
        VendorError.from_status("rejected", vendor="fake-elevenlabs", status_code=422),
        times=5,
    )
    await worker_env.drain()

    # Rewind to the state a crash mid-compensation leaves behind: the run is
    # COMPENSATING and the number is still live at the vendor. The vendor is put
    # back first so that the SID our row holds is the SID the vendor holds —
    # re-purchasing would mint a new one and prove nothing.
    still_owned = await worker_env.twilio.purchase_number(
        e164="+18055551234", friendly_name=f"tenant:{tenant_id}"
    )
    async with worker_env.session_factory() as session:
        run = (
            await session.execute(select(ProvisioningRun).where(ProvisioningRun.id == run_id))
        ).scalar_one()
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()
        run.status = ProvisioningStatus.COMPENSATING
        run.next_attempt_at = None
        run.finished_at = None
        number.status = PhoneNumberStatus.ACTIVE
        number.released_at = None
        number.e164 = still_owned.e164
        number.twilio_sid = still_owned.sid
        await session.commit()

    assert worker_env.twilio.purchased  # the vendor still holds it

    await worker_env.drain()

    assert (await load_run(worker_env, run_id)).status is ProvisioningStatus.COMPENSATED
    async with worker_env.session_factory() as session:
        settled = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()

    assert settled.status is PhoneNumberStatus.RELEASED
    assert tenant.status is TenantStatus.ABANDONED
    # Actually handed back, so it stops billing.
    assert worker_env.twilio.purchased == {}


async def test_a_compensating_run_does_not_reach_active(worker_env: WorkerEnv) -> None:
    """Resuming compensation must not be mistaken for finishing the run."""
    _, run_id = await create_signup(worker_env)
    await worker_env.drain()

    async with worker_env.session_factory() as session:
        run = (
            await session.execute(select(ProvisioningRun).where(ProvisioningRun.id == run_id))
        ).scalar_one()
        run.status = ProvisioningStatus.COMPENSATING
        run.next_attempt_at = None
        await session.commit()

    await worker_env.drain()

    assert (await load_run(worker_env, run_id)).status is ProvisioningStatus.COMPENSATED


# ---------------------------------------------------------------------------
# The staleness window must measure the current attempt
# ---------------------------------------------------------------------------
async def test_started_at_is_stamped_on_every_attempt(worker_env: WorkerEnv) -> None:
    """Otherwise a step retried for longer than the step timeout looks abandoned.

    Two workers would then run the same step at once, however recently the
    current attempt actually began.
    """
    _, run_id = await create_signup(worker_env)

    worker_env.twilio.behaviour.fail(
        "search_available_numbers",
        VendorError("flaky", vendor="fake-twilio", status_code=503, retryable=True),
        times=1,
    )

    await worker_env.worker.tick()  # validate
    await worker_env.worker.tick()  # generate_config
    await worker_env.worker.tick()  # purchase attempt 1 -> fails

    first = (await load_steps(worker_env, run_id))[ProvisioningStep.PURCHASE_NUMBER]
    assert first.attempt == 1
    original_start = first.started_at
    assert original_start is not None

    await worker_env.worker.tick()  # purchase attempt 2 -> succeeds

    second = (await load_steps(worker_env, run_id))[ProvisioningStep.PURCHASE_NUMBER]
    assert second.attempt == 2
    assert second.started_at is not None
    assert second.started_at >= original_start


async def test_a_long_running_step_keeps_its_lease_across_retries(
    worker_env: WorkerEnv,
) -> None:
    """A step whose first attempt was long ago is still protected while running."""
    _, run_id = await create_signup(worker_env)
    await worker_env.worker.tick()

    timeout = worker_env.settings.provisioning_step_timeout_s
    async with worker_env.session_factory() as session:
        record = (
            await session.execute(
                select(ProvisioningStepRecord)
                .where(ProvisioningStepRecord.run_id == run_id)
                .where(ProvisioningStepRecord.step_name == ProvisioningStep.GENERATE_CONFIG)
            )
        ).scalar_one()
        record.status = StepStatus.RUNNING
        record.attempt = 4
        # The current attempt began seconds ago, even though the step first ran
        # far longer than the timeout in the past.
        record.started_at = datetime.now(UTC) - timedelta(seconds=5)
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
    assert timeout > 5  # the window really is wider than the elapsed time


# ---------------------------------------------------------------------------
# One formula for idempotency keys
# ---------------------------------------------------------------------------
async def test_a_backfilled_step_uses_the_same_key_formula(worker_env: WorkerEnv) -> None:
    """A step the engine has to create must not invent a second key format."""
    _, run_id = await create_signup(worker_env)

    async with worker_env.session_factory() as session:
        record = (
            await session.execute(
                select(ProvisioningStepRecord)
                .where(ProvisioningStepRecord.run_id == run_id)
                .where(ProvisioningStepRecord.step_name == ProvisioningStep.VALIDATE)
            )
        ).scalar_one()
        await session.delete(record)
        await session.commit()

    await worker_env.worker.tick()

    rebuilt = (await load_steps(worker_env, run_id))[ProvisioningStep.VALIDATE]
    assert rebuilt.idempotency_key == step_idempotency_key(run_id, ProvisioningStep.VALIDATE)
    assert len(rebuilt.idempotency_key) == 64  # a sha256 hex digest, not a raw string


# ---------------------------------------------------------------------------
# Webhook event ids
# ---------------------------------------------------------------------------
def test_the_event_id_is_read_from_the_nested_payload() -> None:
    """ElevenLabs puts the conversation id under `data`.

    Reading only the top level meant every post-call delivery fell back to a
    body hash, so a redelivery differing by one byte looked like a new event.
    """
    payload = {"type": "post_call_transcription", "data": {"conversation_id": "conv_abc"}}
    assert derive_event_id(WebhookProvider.ELEVENLABS, payload, b"{}") == "conversation_id:conv_abc"


def test_a_top_level_id_still_wins() -> None:
    payload = {"event_id": "evt_1", "data": {"conversation_id": "conv_abc"}}
    assert derive_event_id(WebhookProvider.ELEVENLABS, payload, b"{}") == "event_id:evt_1"


def test_a_payload_with_no_id_falls_back_to_a_body_hash() -> None:
    first = derive_event_id(WebhookProvider.ELEVENLABS, {"data": {}}, b'{"a":1}')
    same = derive_event_id(WebhookProvider.ELEVENLABS, {"data": {}}, b'{"a":1}')
    other = derive_event_id(WebhookProvider.ELEVENLABS, {"data": {}}, b'{"a":2}')

    assert first.startswith("sha256:")
    assert first == same
    assert first != other


# ---------------------------------------------------------------------------
# Twilio signature verification behind a proxy
# ---------------------------------------------------------------------------
def test_the_signed_url_is_rebuilt_from_the_public_base() -> None:
    """Twilio signs the URL it called, not the one the server sees behind ngrok."""
    rebuilt = public_request_url(
        observed_url="http://localhost:8000/api/v1/webhooks/twilio/voice-status",
        path="/api/v1/webhooks/twilio/voice-status",
        query="",
        public_base="https://abc123.ngrok.app",
    )
    assert rebuilt == "https://abc123.ngrok.app/api/v1/webhooks/twilio/voice-status"


def test_the_query_string_survives_the_rebuild() -> None:
    assert (
        public_request_url(
            observed_url="http://localhost:8000/hook?a=1",
            path="/hook",
            query="a=1",
            public_base="https://public.test/",
        )
        == "https://public.test/hook?a=1"
    )


def test_without_a_public_base_the_observed_url_is_used() -> None:
    """Correct when nothing sits in front of the process."""
    assert (
        public_request_url(
            observed_url="http://localhost:8000/hook", path="/hook", query="", public_base=""
        )
        == "http://localhost:8000/hook"
    )


@pytest.mark.parametrize("public_base", ["https://public.test", "https://public.test/"])
def test_a_trailing_slash_does_not_double_up(public_base: str) -> None:
    assert (
        public_request_url(observed_url="x", path="/hook", query="", public_base=public_base)
        == "https://public.test/hook"
    )
