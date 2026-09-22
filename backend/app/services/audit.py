"""Writing the audit log.

One entry point, :meth:`AuditService.record`, so that every audited action is
shaped identically and no caller invents its own column conventions.

The service deliberately offers no update or delete. Immutability is the whole
value: a log the application can rewrite proves nothing about what the
application did.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.correlation import get_correlation_id
from app.core.logging import get_logger
from app.models.audit import AuditLog
from app.models.enums import ActorType, AuditAction
from app.models.identity import User

logger = get_logger(__name__)

#: Metadata keys that must never be persisted, whatever a caller passes.
#:
#: The audit log is read by support staff and exported to customers, so it is
#: the last place a credential should be able to reach. Callers are not trusted
#: to remember that — a dict spread from a request body is one careless line
#: away from including an Authorization header — so the filter lives here,
#: where every write passes through it.
_REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "secret",
        "client_secret",
        "auth_token",
        "session",
        "session_token",
        "webhook_secret",
        "signature",
        "x-api-key",
    }
)

_REDACTED_PLACEHOLDER = "[redacted]"


def redact(meta: dict[str, Any]) -> dict[str, Any]:
    """Strip anything credential-shaped from audit metadata.

    Matches on a normalized key name and recurses into nested dicts, because the
    offending value is usually one level down in a copied request payload rather
    than at the top level.
    """
    cleaned: dict[str, Any] = {}
    for key, value in meta.items():
        if key.replace("-", "_").lower() in _REDACTED_KEYS:
            cleaned[key] = _REDACTED_PLACEHOLDER
        elif isinstance(value, dict):
            cleaned[key] = redact(value)
        else:
            cleaned[key] = value
    return cleaned


@dataclass(frozen=True, slots=True)
class Actor:
    """Who did something, as the audit log should record them.

    Carried as a pair because the type is not derivable from the user alone: the
    same operator is ``admin`` acting as themselves and ``impersonation`` acting
    as a customer, and the log must never blur the two.
    """

    user: User
    actor_type: ActorType


class AuditService:
    """Append-only writes to :class:`~app.models.audit.AuditLog`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def record(
        self,
        action: AuditAction,
        *,
        actor_type: ActorType,
        tenant_id: uuid.UUID | None = None,
        actor: User | None = None,
        actor_label: str | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        impersonated_by_user_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Stage one audit row.

        Staged rather than committed: an audit entry belongs to the same
        transaction as the change it describes. Committing separately would
        allow a log entry for a change that then rolled back — a record of
        something that never happened, which is worse than no record.

        ``actor_label`` is denormalized from the actor at write time so that the
        row stays readable after the user is deleted and the foreign key goes
        null.
        """
        entry = AuditLog(
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_user_id=actor.id if actor is not None else None,
            actor_label=actor_label or (actor.email if actor is not None else None),
            impersonated_by_user_id=impersonated_by_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            correlation_id=get_correlation_id(),
            ip_address=ip_address,
            user_agent=user_agent[:1024] if user_agent else None,
            meta_json=redact(meta or {}),
        )
        self.session.add(entry)
        logger.info(
            "audit",
            extra={
                "action": action.value,
                "actor_type": actor_type.value,
                "tenant_id": str(tenant_id) if tenant_id else None,
                "entity_type": entity_type,
            },
        )
        return entry

    def record_system(
        self,
        action: AuditAction,
        *,
        tenant_id: uuid.UUID | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        meta: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Convenience for background work, which has no human actor."""
        return self.record(
            action,
            actor_type=ActorType.SYSTEM,
            actor_label="system",
            tenant_id=tenant_id,
            entity_type=entity_type,
            entity_id=entity_id,
            meta=meta,
        )
