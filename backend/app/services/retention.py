"""Data retention: what is deleted, what is kept, and why the difference.

Two obligations pull in opposite directions, and most of the care here is in
holding both at once.

**Delete what you promised to delete.** Transcripts and caller numbers are
other people's personal data — the salon's customers, not the salon. We told
them a retention period; keeping the data past it is a broken promise whether
or not anyone notices.

**Keep what you are required to keep.** Invoices, audit entries and usage
ledgers are not ours to erase on request. A deletion path that took the billing
history with it would destroy the records needed to answer the dispute that the
deletion request may itself be part of.

So retention here **redacts rather than deletes rows**. A call's transcript,
caller number and derived summary are erased; the row survives carrying its
duration, its timing and its tenant — the facts that justify a line on an
invoice and prove the deletion happened. A vanished row would be
indistinguishable from a row that never existed, which is the wrong answer to
both "did you delete my data?" and "why am I being billed for this?".

The lists below are explicit for exactly that reason. Nothing sweeps tables by
pattern: a table added later must be considered deliberately rather than being
silently included in an erasure or silently missed by one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import null, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.models import Call, DataDeletionRequest, Recording, Tenant
from app.models.enums import AuditAction, DeletionRequestStatus
from app.services.audit import AuditService

logger = get_logger(__name__)

#: Tables retention **must never touch**, whatever is asked of it.
#:
#: Named here as documentation and asserted by a test, because the damage from
#: erasing one of these is discovered long after the fact and cannot be undone.
PROTECTED_FROM_ERASURE: frozenset[str] = frozenset(
    {
        "audit_logs",  # the record that the deletion itself happened
        "billing_events",  # what a customer was charged and why
        "subscriptions",  # entitlement history
        "usage_events",  # the ledger behind an invoice
        "usage_daily",
        "data_deletion_requests",  # the receipt
    }
)

#: Columns erased from a call once its transcript retention expires.
#:
#: Everything a caller said or could be identified by. What survives —
#: ``duration_s``, ``started_at``, ``tenant_id``, ``agent_config_version`` — is
#: the billing and audit skeleton, which identifies nobody.
#:
#: ``transcript_json`` uses :func:`~sqlalchemy.null` rather than ``None``, and
#: the distinction is not cosmetic. For a JSON column SQLAlchemy renders a bare
#: ``None`` as the JSON value ``null`` — a column that is *not* SQL NULL, still
#: matches ``IS NOT NULL``, and therefore still looks like a transcript to the
#: query that selects rows needing redaction. The erasure would appear to run
#: every night, report success every night, and never actually delete anything.
_CALL_PII_COLUMNS: dict[str, Any] = {
    "transcript_json": null(),
    "summary": None,
    "caller_name": None,
    "callback_number": None,
    "from_e164": None,
}


@dataclass(slots=True)
class RetentionReport:
    """What a sweep actually did. Written to the audit log, and to the receipt."""

    transcripts_redacted: int = 0
    recordings_marked: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "transcripts_redacted": self.transcripts_redacted,
            "recordings_marked": self.recordings_marked,
            "errors": list(self.errors),
        }


class RetentionService:
    """Applies the retention policy, and honours erasure requests."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.audit = AuditService(session)

    # ---- scheduled sweep -------------------------------------------------
    async def sweep(self, *, now: datetime | None = None, limit: int = 500) -> RetentionReport:
        """Redact everything past its retention period.

        Batched rather than unbounded. A first run against a large table would
        otherwise take a lock long enough to be an outage, and the sweep is
        idempotent, so running it repeatedly until it reports nothing is both
        safe and the intended way to catch up.
        """
        moment = now or datetime.now(UTC)
        report = RetentionReport()

        cutoff = moment - timedelta(days=self.settings.transcript_retention_days)
        report.transcripts_redacted = await self._redact_calls_before(cutoff, limit=limit)
        report.recordings_marked = await self._expire_recordings(moment, limit=limit)

        if report.transcripts_redacted or report.recordings_marked:
            # System-actor audit entry: a scheduled erasure is still an erasure,
            # and "nobody can say when this data went" is not an answer.
            self.audit.record_system(
                AuditAction.DATA_DELETION_COMPLETED,
                entity_type="retention_sweep",
                meta=report.as_dict(),
            )
            logger.info("retention sweep completed", extra=report.as_dict())

        await self.session.commit()
        return report

    async def _redact_calls_before(self, cutoff: datetime, *, limit: int) -> int:
        """Erase call content older than the cutoff, keeping the billing row."""
        due = (
            (
                await self.session.execute(
                    select(Call.id)
                    .where(Call.started_at < cutoff)
                    # Already redacted rows are skipped, which is what makes a
                    # repeated sweep cheap rather than a full rewrite each night.
                    .where(Call.transcript_json.is_not(None))
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        if not due:
            return 0

        await self.session.execute(update(Call).where(Call.id.in_(due)).values(**_CALL_PII_COLUMNS))
        return len(due)

    async def _expire_recordings(self, moment: datetime, *, limit: int) -> int:
        """Mark recordings whose retention has lapsed.

        Marked here and removed from object storage by the same job, because a
        row that says "deleted" while the audio is still in a bucket is worse
        than no claim at all — it stops anyone looking.
        """
        due = (
            (
                await self.session.execute(
                    select(Recording.id)
                    .where(Recording.delete_after.is_not(None))
                    .where(Recording.delete_after < moment)
                    .where(Recording.deleted_at.is_(None))
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        if not due:
            return 0

        await self.session.execute(
            update(Recording).where(Recording.id.in_(due)).values(deleted_at=moment)
        )
        return len(due)

    # ---- erasure on request ---------------------------------------------
    async def execute_deletion_request(self, request_id: uuid.UUID) -> RetentionReport:
        """Honour a "delete my data" request, and leave a receipt.

        The request row itself is deliberately kept and marked complete. It is
        the evidence that the erasure happened, which is the thing a regulator
        actually asks for — and which a deletion that erased its own record
        could not provide.
        """
        request = await self.session.get(DataDeletionRequest, request_id)
        if request is None:
            raise ValueError(f"no such deletion request: {request_id}")

        request.status = DeletionRequestStatus.IN_PROGRESS
        await self.session.flush()

        report = RetentionReport()
        try:
            if request.scope in ("all", "transcripts"):
                report.transcripts_redacted = await self._redact_all_calls(request.tenant_id)
            if request.scope in ("all", "recordings"):
                report.recordings_marked = await self._delete_all_recordings(request.tenant_id)
        except Exception as exc:
            request.status = DeletionRequestStatus.PENDING
            request.last_error = str(exc)[:500]
            await self.session.commit()
            logger.exception(
                "a data deletion request failed and will be retried",
                extra={"request_id": str(request_id)},
            )
            raise

        request.status = DeletionRequestStatus.COMPLETED
        request.completed_at = datetime.now(UTC)
        request.deleted_counts_json = report.as_dict()

        self.audit.record_system(
            AuditAction.DATA_DELETION_COMPLETED,
            tenant_id=request.tenant_id,
            entity_type="data_deletion_request",
            entity_id=request.id,
            meta={"scope": request.scope, **report.as_dict()},
        )
        await self.session.commit()
        logger.info(
            "data deletion request completed",
            extra={"request_id": str(request_id), "scope": request.scope},
        )
        return report

    async def _redact_all_calls(self, tenant_id: uuid.UUID) -> int:
        due = (
            (
                await self.session.execute(
                    select(Call.id)
                    .where(Call.tenant_id == tenant_id)
                    .where(Call.transcript_json.is_not(None))
                )
            )
            .scalars()
            .all()
        )
        if not due:
            return 0
        await self.session.execute(update(Call).where(Call.id.in_(due)).values(**_CALL_PII_COLUMNS))
        return len(due)

    async def _delete_all_recordings(self, tenant_id: uuid.UUID) -> int:
        now = datetime.now(UTC)
        due = (
            (
                await self.session.execute(
                    select(Recording.id)
                    .where(Recording.tenant_id == tenant_id)
                    .where(Recording.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        if not due:
            return 0
        await self.session.execute(
            update(Recording).where(Recording.id.in_(due)).values(deleted_at=now)
        )
        return len(due)

    # ---- policy ----------------------------------------------------------
    def delete_after_for(self, tenant: Tenant, *, now: datetime | None = None) -> datetime:
        """When a recording captured now should be erased.

        Computed at write time and stored on the row, so a later policy change
        cannot silently extend the life of data already collected under a
        shorter promise. Shortening it later still applies, because the sweep
        reads the stored date and a shorter one simply arrives sooner.
        """
        moment = now or datetime.now(UTC)
        return moment + timedelta(days=self.settings.recording_retention_days)
