"""Post-call processing: summary, notification, retry, and what survives failure."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.errors import VendorError
from app.models import Call, PhoneNumber, Tenant
from app.models.enums import CallStatus
from app.schemas.agent_config import CallSummary
from app.services.call_processor import CallProcessor, claim_calls
from app.services.summarizer import Summarizer
from tests.test_provisioning import create_signup
from tests.worker_fixtures import WorkerEnv

TRANSCRIPT = (
    "agent: Thanks for calling Sunset Salon.\n"
    "user: Hi, this is Jamie Rivera. Do you have anything Thursday morning?\n"
    "agent: Let me take your number and someone will confirm.\n"
    "user: It's 805 555 7788.\n"
)


async def active_tenant(env: WorkerEnv) -> tuple[uuid.UUID, str]:
    """A provisioned tenant that owns a real number."""
    tenant_id, _ = await create_signup(env)
    await env.drain()
    async with env.session_factory() as session:
        number = (
            await session.execute(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id))
        ).scalar_one()
    return tenant_id, number.e164


async def store_call(env: WorkerEnv, tenant_id: uuid.UUID, **overrides: object) -> uuid.UUID:
    values: dict[str, object] = {
        "tenant_id": tenant_id,
        "provider_call_id": f"conv_{uuid.uuid4().hex[:8]}",
        "from_e164": "+15559998888",
        "to_e164": "+18055551000",
        "started_at": datetime.now(UTC),
        "duration_s": 96,
        "transcript_json": {"text": TRANSCRIPT},
        "status": CallStatus.RECEIVED,
    }
    values.update(overrides)
    async with env.session_factory() as session:
        call = Call(**values)
        session.add(call)
        await session.commit()
        return call.id


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
async def test_a_stored_call_is_summarized_and_notified(worker_env: WorkerEnv) -> None:
    tenant_id, _ = await active_tenant(worker_env)
    call_id = await store_call(worker_env, tenant_id)

    await worker_env.drain()

    async with worker_env.session_factory() as session:
        call = (await session.execute(select(Call).where(Call.id == call_id))).scalar_one()

    assert call.status is CallStatus.NOTIFIED
    assert call.summary
    assert call.intent == "booking_request"
    assert call.urgency == 2
    assert call.last_error is None

    message = worker_env.email.last_to("owner@sunsetsalon.example.com")
    assert message is not None
    assert "New call" in message.subject
    assert call.summary in message.text


async def test_the_summary_email_carries_the_details(worker_env: WorkerEnv) -> None:
    tenant_id, _ = await active_tenant(worker_env)
    await store_call(worker_env, tenant_id)
    await worker_env.drain()

    message = worker_env.email.last_to("owner@sunsetsalon.example.com")
    assert message is not None
    assert "Prefers a morning slot" in message.text
    assert "Call back to confirm a time." in message.text


async def test_a_processed_call_is_not_processed_again(worker_env: WorkerEnv) -> None:
    """One call, one email — a second pass must not notify twice."""
    tenant_id, _ = await active_tenant(worker_env)
    await store_call(worker_env, tenant_id)

    await worker_env.drain()
    sent = worker_env.email.behaviour.count("send")

    await worker_env.drain()
    assert worker_env.email.behaviour.count("send") == sent


async def test_calls_are_claimed_with_skip_locked(worker_env: WorkerEnv) -> None:
    tenant_id, _ = await active_tenant(worker_env)
    for _ in range(4):
        await store_call(worker_env, tenant_id)

    async with worker_env.session_factory() as first, worker_env.session_factory() as second:
        claimed_first = await claim_calls(first, batch_size=2, lease_s=300)
        claimed_second = await claim_calls(second, batch_size=2, lease_s=300)

    assert len(claimed_first) == 2
    assert len(claimed_second) == 2
    assert set(claimed_first).isdisjoint(claimed_second)


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------
async def test_a_failing_summary_is_retried(worker_env: WorkerEnv) -> None:
    tenant_id, _ = await active_tenant(worker_env)
    call_id = await store_call(worker_env, tenant_id)

    worker_env.llm.behaviour.fail(
        "complete",
        VendorError("llm unwell", vendor="fake-llm", status_code=503, retryable=True),
        times=1,
    )

    await worker_env.drain()

    async with worker_env.session_factory() as session:
        call = (await session.execute(select(Call).where(Call.id == call_id))).scalar_one()
    assert call.status is CallStatus.NOTIFIED
    assert call.attempt == 2


async def test_the_call_record_survives_a_permanent_summary_failure(
    worker_env: WorkerEnv,
) -> None:
    """The transcript is the customer's message; losing it is unacceptable.

    Only the summary is lost.
    """
    tenant_id, _ = await active_tenant(worker_env)
    call_id = await store_call(worker_env, tenant_id)

    worker_env.llm.behaviour.fail(
        "complete",
        VendorError("llm gone", vendor="fake-llm", status_code=503, retryable=True),
        times=10,
    )

    await worker_env.drain()

    async with worker_env.session_factory() as session:
        call = (await session.execute(select(Call).where(Call.id == call_id))).scalar_one()

    assert call.status is CallStatus.FAILED
    assert call.summary is None
    assert call.transcript_json is not None
    assert call.transcript_json["text"] == TRANSCRIPT  # still there
    assert call.last_error and "llm gone" in call.last_error
    assert call.attempt == worker_env.settings.call_summary_max_attempts


async def test_a_malformed_summary_reply_is_retried_then_given_up(
    worker_env: WorkerEnv,
) -> None:
    tenant_id, _ = await active_tenant(worker_env)
    call_id = await store_call(worker_env, tenant_id)
    worker_env.llm.scripted = ["not json"] * 10

    await worker_env.drain()

    async with worker_env.session_factory() as session:
        call = (await session.execute(select(Call).where(Call.id == call_id))).scalar_one()
    assert call.status is CallStatus.FAILED
    assert call.last_error and "summary_schema_rejected" in call.last_error


async def test_an_empty_transcript_fails_immediately(worker_env: WorkerEnv) -> None:
    """Terminal: an empty transcript will still be empty on retry."""
    tenant_id, _ = await active_tenant(worker_env)
    call_id = await store_call(worker_env, tenant_id, transcript_json={"text": ""})

    await worker_env.drain()

    async with worker_env.session_factory() as session:
        call = (await session.execute(select(Call).where(Call.id == call_id))).scalar_one()
    assert call.status is CallStatus.FAILED
    assert call.attempt == 1
    assert call.last_error and "empty_transcript" in call.last_error


async def test_a_failing_email_does_not_buy_a_second_summary(
    worker_env: WorkerEnv,
) -> None:
    """Summarize once, notify separately — a flaky mailer must not cost tokens."""
    tenant_id, _ = await active_tenant(worker_env)
    call_id = await store_call(worker_env, tenant_id)

    worker_env.email.behaviour.fail(
        "send",
        VendorError("mailer unwell", vendor="fake-email", status_code=503, retryable=True),
        times=1,
    )
    llm_calls_before = worker_env.llm.behaviour.count("complete")

    await worker_env.drain()

    async with worker_env.session_factory() as session:
        call = (await session.execute(select(Call).where(Call.id == call_id))).scalar_one()

    assert call.status is CallStatus.NOTIFIED
    # Exactly one summarization despite two notification attempts.
    assert worker_env.llm.behaviour.count("complete") == llm_calls_before + 1


# ---------------------------------------------------------------------------
# The summarizer itself
# ---------------------------------------------------------------------------
async def test_the_summarizer_returns_validated_structure(worker_env: WorkerEnv) -> None:
    summary = await Summarizer(worker_env.llm, worker_env.settings).summarize(
        transcript=TRANSCRIPT,
        business_name="Sunset Salon",
        business_type="salon",
        called_number="+18055551000",
        caller_number="+15559998888",
        duration_s=96,
    )
    assert isinstance(summary, CallSummary)
    assert 1 <= summary.urgency <= 5
    assert summary.summary


async def test_an_unparseable_callback_number_is_dropped(worker_env: WorkerEnv) -> None:
    """A claimed number that is not a number must not be shown as one."""
    from app.services.summarizer import _verify_callback

    claimed = CallSummary(summary="x", callback_number="call me back sometime")
    assert _verify_callback(claimed, "+15559998888").callback_number is None


async def test_a_claimed_callback_number_is_normalized(worker_env: WorkerEnv) -> None:
    from app.services.summarizer import _verify_callback

    claimed = CallSummary(summary="x", callback_number="(805) 555-7788")
    assert _verify_callback(claimed, "+15559998888").callback_number == "+18055557788"


@pytest.mark.parametrize("status", [CallStatus.NOTIFIED, CallStatus.FAILED])
async def test_finished_calls_are_skipped(worker_env: WorkerEnv, status: CallStatus) -> None:
    tenant_id, _ = await active_tenant(worker_env)
    call_id = await store_call(worker_env, tenant_id, status=status)

    processor = CallProcessor(worker_env.settings, worker_env.providers)
    async with worker_env.session_factory() as session:
        report = await processor.process(session, call_id)

    assert report.outcome == "skipped"


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------
async def test_a_summary_goes_only_to_its_own_tenant(worker_env: WorkerEnv) -> None:
    first_id, _ = await active_tenant(worker_env)
    second_id, _ = await create_signup(
        worker_env, notification_email="other@salon.example.com", business_name="Other Salon"
    )
    await worker_env.drain()

    await store_call(worker_env, second_id)
    await worker_env.drain()

    message = worker_env.email.last_to("other@salon.example.com")
    assert message is not None
    assert "Other Salon" in message.subject

    async with worker_env.session_factory() as session:
        first = (await session.execute(select(Tenant).where(Tenant.id == first_id))).scalar_one()
    # The other tenant received only its activation mail, no call summary.
    assert all(
        "New call" not in sent.subject
        for sent in worker_env.email.outbox
        if sent.to == first.contact_email
    )
