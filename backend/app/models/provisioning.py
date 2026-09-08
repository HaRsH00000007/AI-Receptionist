"""Provisioning runs and their steps — the durable state machine.

This is the pair of tables that exists because the Make.com chain had no durable
state: a multi-step process with irreversible external side effects, no
idempotency, and no visibility. Everything the worker knows lives here, so a
killed worker resumes rather than losing the run, and the admin panel can answer
"what happened to tenant X" with a single query
(docs/00_DECISIONS.md section 4).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    IN_FLIGHT_RUN_STATUSES,
    ProvisioningStatus,
    ProvisioningStep,
    StepStatus,
    enum_column,
)

if TYPE_CHECKING:
    from app.models.tenant import Tenant


def _in_flight_predicate() -> str:
    """SQL predicate matching runs that still own their tenant.

    Derived from :data:`app.models.enums.IN_FLIGHT_RUN_STATUSES` rather than
    written out, so that adding a status cannot leave the index and the worker
    disagreeing about which runs are live.
    """
    values = ", ".join(f"'{status.value}'" for status in sorted(IN_FLIGHT_RUN_STATUSES))
    return f"status IN ({values})"


class ProvisioningRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One attempt to take a tenant from signup to ACTIVE.

    ``next_attempt_at`` drives the worker's claim query::

        SELECT ... FROM provisioning_runs
        WHERE status NOT IN (terminal) AND next_attempt_at <= now()
        ORDER BY next_attempt_at
        FOR UPDATE SKIP LOCKED LIMIT 5

    It is a column rather than a sleep in the worker because backoff must survive
    a restart: 5s, 30s, 2m, 10m, then FAILED and an alert.
    """

    __tablename__ = "provisioning_runs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[ProvisioningStatus] = mapped_column(
        enum_column(ProvisioningStatus, "provisioning_status"),
        nullable=False,
        default=ProvisioningStatus.DRAFT,
    )
    #: The step being worked on, or the one that failed. Null before the first.
    current_step: Mapped[ProvisioningStep | None] = mapped_column(
        enum_column(ProvisioningStep, "provisioning_step"), nullable=True
    )

    #: Attempts made against ``current_step``. Reset when the step advances.
    attempt: Mapped[int] = mapped_column(nullable=False, default=0)
    #: When the worker may next pick this run up. Null means "not scheduled".
    next_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Shared with every log line the run produces, and shown in the admin panel
    #: so a customer report maps to a trace without a database query.
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="provisioning_runs")
    steps: Mapped[list[ProvisioningStepRecord]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ProvisioningStepRecord.created_at",
    )

    __table_args__ = (
        # One live run per tenant. The distributed-lock problem solved in the
        # database instead of in Redis: two workers cannot both start
        # provisioning, so they cannot both buy a number.
        Index(
            "uq_provisioning_runs_in_flight_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text(_in_flight_predicate()),
        ),
        # Serves the claim query above.
        Index("ix_provisioning_runs_status_next_attempt_at", "status", "next_attempt_at"),
        Index("ix_provisioning_runs_correlation_id", "correlation_id"),
    )


class ProvisioningStepRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One step of one run, with the vendor exchange that produced it.

    Named ``ProvisioningStepRecord`` rather than ``ProvisioningStep`` because
    that name belongs to the enum; confusing the two is how a state machine ends
    up with two vocabularies.

    ``request_json`` and ``response_json`` are what turn a 3am failure into a
    9am fix: the admin panel shows the exact payload sent and the exact vendor
    error returned, instead of a stack trace that lost the interesting part.
    """

    __tablename__ = "provisioning_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provisioning_runs.id", ondelete="CASCADE"), nullable=False
    )

    step_name: Mapped[ProvisioningStep] = mapped_column(
        enum_column(ProvisioningStep, "provisioning_step"), nullable=False
    )
    status: Mapped[StepStatus] = mapped_column(
        enum_column(StepStatus, "step_status"),
        nullable=False,
        default=StepStatus.PENDING,
    )

    #: sha256(run_id || step_name || attempt_group). Globally unique, so a retry
    #: that reuses the key is provably the same logical operation and a vendor
    #: call can be safely skipped or adopted rather than repeated.
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    request_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    attempt: Mapped[int] = mapped_column(nullable=False, default=0)

    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    run: Mapped[ProvisioningRun] = relationship(back_populates="steps")

    __table_args__ = (
        # "UNIQUE (run_id, step_name)" - docs/01_PLAN_POC.md. A step exists once
        # per run; retries update the row rather than appending another.
        UniqueConstraint("run_id", "step_name", name="uq_provisioning_steps_run_id_step_name"),
    )
