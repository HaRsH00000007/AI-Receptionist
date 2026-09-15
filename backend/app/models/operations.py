"""Operational tables: idempotency, notifications, recordings, data deletion.

Grouped because each one exists to make an *unreliable* thing safe — a retried
request, a bounced email, an object store that is not the database, a legal
obligation with a deadline — rather than because they share a domain.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    DeletionRequestStatus,
    NotificationKind,
    NotificationStatus,
    enum_column,
)

if TYPE_CHECKING:
    pass


class IdempotencyKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A remembered response for a client-supplied ``Idempotency-Key``.

    In the database rather than in memory, and that is the entire point. An
    in-process dictionary forgets on restart and is not shared between replicas,
    so the two moments it is most needed — a deploy mid-request, and a client
    retrying against a different instance — are exactly the two it fails at.

    ``request_fingerprint`` is a hash of the request body. Reusing a key with a
    *different* body is a client bug, and returning the first response for it
    would silently apply the wrong operation; that case is refused rather than
    served from cache.
    """

    __tablename__ = "idempotency_keys"

    #: Nullable: signup has no tenant yet, and signup is precisely where a
    #: double-submit is most expensive.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    #: The client's key, scoped by endpoint so the same key on two endpoints is
    #: two independent operations.
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Null while the first request is still running. Its absence is what a
    #: concurrent duplicate sees, and what tells it to wait rather than proceed.
    response_status: Mapped[int | None] = mapped_column(nullable=True)
    response_body_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Rows are pruned after this. Keys are not kept forever: the table would
    #: grow without bound, and a key replayed months later is not a retry.
    expires_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "endpoint",
            "idempotency_key",
            name="uq_idempotency_keys_endpoint_idempotency_key",
        ),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A message we owe someone, and whether it has been delivered.

    A row exists *before* any send is attempted. That ordering is the design: an
    email provider that is down must leave a durable obligation behind rather
    than an exception in a log, so the retry has something to find. "We tried to
    tell them and it vanished" is indistinguishable from never trying.
    """

    __tablename__ = "notifications"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calls.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[NotificationKind] = mapped_column(
        enum_column(NotificationKind, "notification_kind"), nullable=False
    )
    status: Mapped[NotificationStatus] = mapped_column(
        enum_column(NotificationStatus, "notification_status"),
        nullable=False,
        default=NotificationStatus.PENDING,
    )
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The template and variables, not the rendered body. Storing the rendered
    #: text would put a call summary — customer PII — in a second place with its
    #: own retention question, for no benefit a re-render does not provide.
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_vars_json: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    #: What makes this notification *this* notification.
    #:
    #: Without it, a redelivered post-call webhook produces a second row and the
    #: customer gets the same call summary twice — the exact failure the webhook
    #: dedupe exists to prevent, reintroduced one layer down. Derived from the
    #: thing being announced (a call id, a provisioning run) rather than
    #: generated, so two independent attempts to announce the same event
    #: collide by construction.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)

    attempt: Mapped[int] = mapped_column(nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    attempts: Mapped[list[NotificationAttempt]] = relationship(
        back_populates="notification", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # The sender's claim query: what is due, oldest first.
        Index("ix_notifications_status_next_attempt_at", "status", "next_attempt_at"),
        Index("ix_notifications_tenant_id_created_at", "tenant_id", "created_at"),
        # One notification per announceable event. Partial, because a row with
        # no dedupe key is a one-off that nothing else will try to send.
        Index(
            "uq_notifications_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
    )


class NotificationAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One delivery attempt, kept even when it failed.

    Separate from the counter on :class:`Notification` because "it failed three
    times" does not answer "why", and the provider's error on attempt one is
    usually the one that explains the other two.
    """

    __tablename__ = "notification_attempts"

    notification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    succeeded: Mapped[bool] = mapped_column(nullable=False, default=False)
    #: The provider's message id, for tracing a complaint back to a send.
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    notification: Mapped[Notification] = relationship(back_populates="attempts")

    __table_args__ = (
        UniqueConstraint(
            "notification_id",
            "attempt",
            name="uq_notification_attempts_notification_id_attempt",
        ),
    )


class Recording(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Metadata for an audio recording. The audio itself lives in object storage.

    Large binaries do not belong in PostgreSQL: they bloat backups, slow
    restores, and push the working set out of cache to serve bytes a dedicated
    object store serves better. So the bytes go to MinIO locally and S3 in
    production, and the row keeps what has to be queryable, joinable and
    transactional — which the object store is none of.

    Recording is not unconditional. Consent requirements are jurisdictional, so
    ``consent_announced`` records that the caller was told, and its absence is a
    reason not to retain rather than a detail to reconstruct later.
    """

    __tablename__ = "recordings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    #: Bucket-relative key. Not a URL: a signed URL expires, and storing one
    #: would bake an endpoint and a credential lifetime into a permanent row.
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    storage_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="s3")
    content_type: Mapped[str] = mapped_column(String(64), nullable=False, default="audio/mpeg")
    size_bytes: Mapped[int | None] = mapped_column(nullable=True)
    duration_s: Mapped[int | None] = mapped_column(nullable=True)
    consent_announced: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )
    #: Computed from the tenant's retention policy at write time, so a later
    #: policy change cannot silently extend the life of data already collected
    #: under a shorter promise.
    delete_after: Mapped[datetime | None] = mapped_column(nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_recordings_tenant_id_created_at", "tenant_id", "created_at"),
        # Drives the retention sweeper: what is due for deletion and not yet
        # deleted.
        Index(
            "ix_recordings_delete_after",
            "delete_after",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="size_not_negative",
        ),
    )


class DataDeletionRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A request to erase a tenant's data, and the record that it was honoured.

    A deletion path that leaves no evidence it ran is not a deletion path you
    can answer a regulator with. This table is what turns "we deleted it" into
    something checkable: what was asked, when, by whom, what was actually
    removed, and when it finished.

    The row itself is deliberately retained after completion — it is the receipt.
    """

    __tablename__ = "data_deletion_requests"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[DeletionRequestStatus] = mapped_column(
        enum_column(DeletionRequestStatus, "deletion_request_status"),
        nullable=False,
        default=DeletionRequestStatus.PENDING,
    )
    #: What to erase — "transcripts", "recordings", "all". A scope rather than
    #: an implied everything, because "delete my recordings" and "close my
    #: account" are different requests with different consequences.
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="all")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Counts of what was actually removed, per entity. The evidence.
    deleted_counts_json: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_data_deletion_requests_status", "status"),
        Index("ix_data_deletion_requests_tenant_id_created_at", "tenant_id", "created_at"),
        CheckConstraint(
            f"status <> '{DeletionRequestStatus.COMPLETED.value}' OR completed_at IS NOT NULL",
            name="completed_at_required_once_completed",
        ),
    )
