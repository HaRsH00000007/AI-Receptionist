"""The audit log.

Immutable and append-only. There is no update path and no delete path in the
application — a log that can be edited by the system it audits proves nothing,
and the only thing worse than no audit trail is one that reads as authoritative
while being wrong.

What it has to answer, months later and usually to someone unhappy: who changed
this configuration, who released this number, who looked at this customer's
transcripts, and was that person acting as themselves or impersonating.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import ActorType, AuditAction, enum_column


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """One recorded action.

    Note the absence of ``TimestampMixin``: an audit row has a creation time and
    no meaningful modification time, and offering an ``updated_at`` would imply
    editing is expected. ``occurred_at`` is set explicitly by the writer.

    ``tenant_id`` is nullable because not everything happens inside a tenant — a
    failed login names an email address that may belong to no account, and a
    platform-wide admin action belongs to none.
    """

    __tablename__ = "audit_logs"

    #: Server-side default so a row written by a migration or by hand in psql is
    #: stamped too, and so the clock is the database's rather than whichever
    #: worker happened to write it — two workers with drifting clocks would
    #: otherwise produce an audit trail that appears to run backwards.
    occurred_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )

    # ---- Who -------------------------------------------------------------
    actor_type: Mapped[ActorType] = mapped_column(
        enum_column(ActorType, "actor_type"), nullable=False
    )
    #: SET NULL rather than CASCADE: deleting a user must not erase the record
    #: of what they did. `actor_label` survives as the human-readable trace.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: A denormalized copy of the actor's email or system name, frozen at write
    #: time. Deliberate duplication: the foreign key can go null and an address
    #: can change, and "admin@x deleted this" must stay readable regardless.
    actor_label: Mapped[str | None] = mapped_column(String(320), nullable=True)
    #: Set only when the action was taken through impersonation. Its presence,
    #: not a flag, is what distinguishes an impersonated action from the
    #: customer's own — so the distinction cannot be lost by forgetting a flag.
    impersonated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # ---- What ------------------------------------------------------------
    action: Mapped[AuditAction] = mapped_column(
        enum_column(AuditAction, "audit_action"), nullable=False
    )
    #: The affected row, as type + id rather than a foreign key. A real FK would
    #: need one nullable column per auditable table, and would delete history
    #: when the entity went away — exactly backwards for an audit log.
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # ---- Context ---------------------------------------------------------
    #: Ties this row to a request, its logs and its trace. The thread you pull
    #: when reconstructing an incident.
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Free-form detail: what changed, from what, to what.
    #:
    #: NEVER a secret. Not an API key, not a session token, not a full
    #: transcript. This column is read by support staff and exported to
    #: customers, so it is the last place a credential should be able to reach.
    meta_json: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (
        # "What happened to this tenant, most recent first" — the admin panel's
        # primary query.
        Index("ix_audit_logs_tenant_id_occurred_at", "tenant_id", "occurred_at"),
        # "What has this person done" — the one that matters after an incident.
        Index("ix_audit_logs_actor_user_id_occurred_at", "actor_user_id", "occurred_at"),
        Index("ix_audit_logs_action_occurred_at", "action", "occurred_at"),
        Index("ix_audit_logs_entity_type_entity_id", "entity_type", "entity_id"),
    )
