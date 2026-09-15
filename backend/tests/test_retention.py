"""M12 — PII and retention.

Two failures are being defended against, and they are opposites:

* keeping personal data past the period we promised — a broken promise to
  people who are not even our customers, the salon's callers rather than the
  salon;
* deleting records we are obliged to keep — invoices, audit entries, the usage
  ledger — which destroys the evidence needed to answer the very dispute a
  deletion request may be part of.

The resolution is redaction rather than row deletion, and the tests below check
both halves: that the content really goes, and that the billing skeleton really
stays.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Call, DataDeletionRequest, Recording, UsageEvent
from app.models.enums import DeletionRequestStatus, UsageKind
from app.services.retention import PROTECTED_FROM_ERASURE, RetentionService
from app.services.usage import UsageService
from tests.factories import make_call, make_tenant
from tests.support import build_settings

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def _service(session: AsyncSession, **overrides: object) -> RetentionService:
    return RetentionService(session, build_settings(**overrides))


async def _tenant(session: AsyncSession):  # type: ignore[no-untyped-def]
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    return tenant


# ===========================================================================
# The scheduled sweep
# ===========================================================================


async def test_an_expired_transcript_is_erased(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    old = make_call(
        tenant,
        provider_call_id="conv_old",
        started_at=NOW - timedelta(days=120),
        transcript_json={"text": "my card number is 4111 1111 1111 1111"},
        summary="Caller gave card details",
        caller_name="Dana",
        callback_number="+15559998888",
        from_e164="+15559998888",
    )
    db_session.add(old)
    await db_session.commit()

    report = await _service(db_session, transcript_retention_days=90).sweep(now=NOW)

    assert report.transcripts_redacted == 1
    await db_session.refresh(old)
    assert old.transcript_json is None
    assert old.summary is None
    assert old.caller_name is None
    assert old.callback_number is None
    assert old.from_e164 is None


async def test_a_call_inside_its_retention_period_is_untouched(
    db_session: AsyncSession,
) -> None:
    """The other half of the promise: we keep what we said we would keep."""
    tenant = await _tenant(db_session)
    recent = make_call(
        tenant,
        provider_call_id="conv_recent",
        started_at=NOW - timedelta(days=10),
        transcript_json={"text": "still within retention"},
    )
    db_session.add(recent)
    await db_session.commit()

    report = await _service(db_session, transcript_retention_days=90).sweep(now=NOW)

    assert report.transcripts_redacted == 0
    await db_session.refresh(recent)
    assert recent.transcript_json is not None


async def test_the_billing_skeleton_survives_redaction(db_session: AsyncSession) -> None:
    """A vanished row cannot answer "why am I billed for this?".

    Duration, timing, tenant and config version identify nobody and are exactly
    what justifies an invoice line.
    """
    tenant = await _tenant(db_session)
    call = make_call(
        tenant,
        provider_call_id="conv_billing",
        started_at=NOW - timedelta(days=200),
        duration_s=180,
        transcript_json={"text": "content"},
        agent_config_version=3,
    )
    db_session.add(call)
    await db_session.commit()

    await _service(db_session, transcript_retention_days=90).sweep(now=NOW)
    await db_session.refresh(call)

    assert call.duration_s == 180
    assert call.started_at is not None
    assert call.tenant_id == tenant.id
    assert call.agent_config_version == 3
    assert call.provider_call_id == "conv_billing"


async def test_the_sweep_is_idempotent(db_session: AsyncSession) -> None:
    """Run it nightly, run it twice, run it after a crash — same result."""
    tenant = await _tenant(db_session)
    db_session.add(
        make_call(
            tenant,
            provider_call_id="conv_twice",
            started_at=NOW - timedelta(days=200),
            transcript_json={"text": "content"},
        )
    )
    await db_session.commit()

    service = _service(db_session, transcript_retention_days=90)
    first = await service.sweep(now=NOW)
    second = await service.sweep(now=NOW)

    assert first.transcripts_redacted == 1
    # Nothing left to do, so the second pass is free rather than a full rewrite.
    assert second.transcripts_redacted == 0


async def test_the_sweep_is_batched(db_session: AsyncSession) -> None:
    """An unbounded first run would hold a lock long enough to be an outage."""
    tenant = await _tenant(db_session)
    for index in range(5):
        db_session.add(
            make_call(
                tenant,
                provider_call_id=f"conv_batch_{index}",
                started_at=NOW - timedelta(days=200),
                transcript_json={"text": "content"},
            )
        )
    await db_session.commit()

    service = _service(db_session, transcript_retention_days=90)
    assert (await service.sweep(now=NOW, limit=2)).transcripts_redacted == 2
    assert (await service.sweep(now=NOW, limit=2)).transcripts_redacted == 2
    assert (await service.sweep(now=NOW, limit=2)).transcripts_redacted == 1


async def test_an_expired_recording_is_marked_deleted(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    call = make_call(tenant, provider_call_id="conv_rec")
    db_session.add(call)
    await db_session.flush()
    recording = Recording(
        tenant_id=tenant.id,
        call_id=call.id,
        storage_key=f"recordings/{call.id}.mp3",
        delete_after=NOW - timedelta(days=1),
    )
    db_session.add(recording)
    await db_session.commit()

    report = await _service(db_session).sweep(now=NOW)

    assert report.recordings_marked == 1
    await db_session.refresh(recording)
    assert recording.deleted_at is not None


async def test_retention_is_configurable(db_session: AsyncSession) -> None:
    """A shorter policy takes effect immediately; the sweep reads the setting."""
    tenant = await _tenant(db_session)
    db_session.add(
        make_call(
            tenant,
            provider_call_id="conv_config",
            started_at=NOW - timedelta(days=20),
            transcript_json={"text": "content"},
        )
    )
    await db_session.commit()

    lenient = await _service(db_session, transcript_retention_days=90).sweep(now=NOW)
    assert lenient.transcripts_redacted == 0

    strict = await _service(db_session, transcript_retention_days=7).sweep(now=NOW)
    assert strict.transcripts_redacted == 1


# ===========================================================================
# What must never be erased
# ===========================================================================


def test_the_protected_tables_are_named_explicitly() -> None:
    """Nothing sweeps by pattern.

    A table added later must be considered deliberately rather than swept into
    an erasure, or silently missed by one.
    """
    assert "audit_logs" in PROTECTED_FROM_ERASURE
    assert "billing_events" in PROTECTED_FROM_ERASURE
    assert "usage_events" in PROTECTED_FROM_ERASURE
    assert "data_deletion_requests" in PROTECTED_FROM_ERASURE


async def test_erasure_leaves_the_usage_ledger_intact(db_session: AsyncSession) -> None:
    """Deleting the ledger would destroy the evidence behind an invoice."""
    tenant = await _tenant(db_session)
    call = make_call(
        tenant,
        provider_call_id="conv_ledger",
        started_at=NOW - timedelta(days=200),
        duration_s=300,
        transcript_json={"text": "content"},
    )
    db_session.add(call)
    await db_session.flush()
    await UsageService(db_session).record(
        tenant_id=tenant.id,
        kind=UsageKind.CALL_MINUTES,
        quantity=300,
        call_id=call.id,
        provider="elevenlabs",
        provider_reference="conv_ledger",
        occurred_at=NOW - timedelta(days=200),
    )
    await db_session.commit()

    await _service(db_session, transcript_retention_days=90).sweep(now=NOW)

    remaining = (
        await db_session.execute(
            select(func.count(UsageEvent.id)).where(UsageEvent.tenant_id == tenant.id)
        )
    ).scalar_one()
    assert remaining == 1


async def test_a_sweep_records_that_it_ran(db_session: AsyncSession) -> None:
    """ "Nobody can say when this data went" is not an answer."""
    tenant = await _tenant(db_session)
    db_session.add(
        make_call(
            tenant,
            provider_call_id="conv_audited",
            started_at=NOW - timedelta(days=200),
            transcript_json={"text": "content"},
        )
    )
    await db_session.commit()

    await _service(db_session, transcript_retention_days=90).sweep(now=NOW)

    entries = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.entity_type == "retention_sweep")
            )
        )
        .scalars()
        .all()
    )
    assert len(entries) == 1
    assert entries[0].meta_json["transcripts_redacted"] == 1


# ===========================================================================
# Erasure on request
# ===========================================================================


async def test_a_deletion_request_erases_the_tenants_transcripts(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant(db_session)
    for index in range(3):
        db_session.add(
            make_call(
                tenant,
                provider_call_id=f"conv_erase_{index}",
                # Recent: an explicit request does not wait for retention.
                started_at=NOW - timedelta(days=1),
                transcript_json={"text": "personal content"},
            )
        )
    request = DataDeletionRequest(tenant_id=tenant.id, scope="all")
    db_session.add(request)
    await db_session.commit()

    report = await _service(db_session).execute_deletion_request(request.id)

    assert report.transcripts_redacted == 3
    remaining = (
        await db_session.execute(
            select(func.count(Call.id))
            .where(Call.tenant_id == tenant.id)
            .where(Call.transcript_json.is_not(None))
        )
    ).scalar_one()
    assert remaining == 0


async def test_the_request_row_survives_as_the_receipt(db_session: AsyncSession) -> None:
    """A deletion that erased its own record could not prove it happened."""
    tenant = await _tenant(db_session)
    db_session.add(
        make_call(tenant, provider_call_id="conv_receipt", transcript_json={"text": "x"})
    )
    request = DataDeletionRequest(tenant_id=tenant.id, scope="all")
    db_session.add(request)
    await db_session.commit()

    await _service(db_session).execute_deletion_request(request.id)
    await db_session.refresh(request)

    assert request.status is DeletionRequestStatus.COMPLETED
    assert request.completed_at is not None
    assert request.deleted_counts_json["transcripts_redacted"] == 1


async def test_a_deletion_request_never_reaches_another_tenant(
    db_session: AsyncSession,
) -> None:
    mine = await _tenant(db_session)
    theirs = await _tenant(db_session)
    db_session.add(
        make_call(theirs, provider_call_id="conv_theirs", transcript_json={"text": "theirs"})
    )
    request = DataDeletionRequest(tenant_id=mine.id, scope="all")
    db_session.add(request)
    await db_session.commit()

    await _service(db_session).execute_deletion_request(request.id)

    survived = (
        await db_session.execute(
            select(func.count(Call.id))
            .where(Call.tenant_id == theirs.id)
            .where(Call.transcript_json.is_not(None))
        )
    ).scalar_one()
    assert survived == 1


async def test_a_scoped_request_erases_only_that_scope(db_session: AsyncSession) -> None:
    """ "Delete my recordings" and "close my account" are different requests."""
    tenant = await _tenant(db_session)
    call = make_call(tenant, provider_call_id="conv_scoped", transcript_json={"text": "kept"})
    db_session.add(call)
    await db_session.flush()
    db_session.add(
        Recording(tenant_id=tenant.id, call_id=call.id, storage_key=f"recordings/{call.id}.mp3")
    )
    request = DataDeletionRequest(tenant_id=tenant.id, scope="recordings")
    db_session.add(request)
    await db_session.commit()

    report = await _service(db_session).execute_deletion_request(request.id)

    assert report.recordings_marked == 1
    assert report.transcripts_redacted == 0
    await db_session.refresh(call)
    assert call.transcript_json is not None


# ===========================================================================
# Policy is frozen at write time
# ===========================================================================


def test_a_recordings_deletion_date_is_computed_when_it_is_written() -> None:
    """A later, longer policy must not extend data already collected.

    Storing the date rather than recomputing it from the current setting is
    what makes a promise made at collection time survive a change of mind.
    """
    service = RetentionService(None, build_settings(recording_retention_days=30))  # type: ignore[arg-type]
    delete_after = service.delete_after_for(make_tenant(), now=NOW)
    assert delete_after == NOW + timedelta(days=30)
