"""Adding, editing and removing the labels a business sorts its calls by.

Unlike a transfer rule or a scheduled greeting, a tag changes **nothing about a
live call**: it is read after the fact, by the classifier, and never rendered
into the agent's prompt. So this service deliberately does *not* publish a
configuration version or push anything to the vendor — doing so would burn a
version number on a change no caller could hear, and make the version history
useless for answering "what changed before the calls got worse?".

Two things here are about concurrency rather than about tags:

* **The cap is enforced under a lock.** Counting and then inserting is a race:
  two requests can both count 24 and both insert. The tenant row is locked for
  the duration, which serialises creates for that tenant and nothing else.
* **Names are unique in the database.** The check below is for the error
  message; the constraint is what makes it true.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import Select, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models import CallTag, CallTagAssignment, Tenant
from app.models.enums import AuditAction
from app.schemas.tags import CallTagInput, CallTagLabel, CallTagUpdate, CallTagView
from app.services.audit import Actor, AuditService

logger = get_logger(__name__)

#: Per tenant, counting only the enabled ones.
#:
#: The limit is on *active* tags rather than on rows because it exists to keep
#: the classifier's prompt readable: twenty-five descriptions is already a lot
#: to ask one model call to weigh, and a disabled tag is not in that prompt. It
#: also means retiring a tag frees a slot without deleting the history that tag
#: explains.
MAX_TAGS = 25


class CallTagService:
    """Every query is filtered by tenant, including the ones that look a tag up
    by its own id: another business's tag id must be indistinguishable from one
    that does not exist."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ---- reads -----------------------------------------------------------
    async def list(self, tenant_id: uuid.UUID) -> list[CallTag]:
        """Every tag, enabled or not, in display order."""
        return list(
            (
                await self.session.execute(
                    select(CallTag)
                    .where(CallTag.tenant_id == tenant_id)
                    .order_by(CallTag.position, CallTag.created_at)
                )
            )
            .scalars()
            .all()
        )

    async def enabled(self, tenant_id: uuid.UUID) -> Sequence[CallTag]:
        """The tags the classifier is allowed to apply.

        Annotated as a ``Sequence`` rather than a ``list`` because ``list`` is
        a method on this class and shadows the builtin inside its own body.
        """
        return [tag for tag in await self.list(tenant_id) if tag.enabled]

    async def get(self, tenant_id: uuid.UUID, tag_id: uuid.UUID) -> CallTag:
        tag = (
            await self.session.execute(
                select(CallTag).where(CallTag.id == tag_id, CallTag.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if tag is None:
            raise NotFoundError("tag not found")
        return tag

    # ---- writes ----------------------------------------------------------
    async def create(self, *, tenant: Tenant, payload: CallTagInput, actor: Actor) -> CallTag:
        # The lock is the point: see the module docstring. Taken on the tenant
        # row, so two businesses adding tags at the same moment never wait on
        # each other.
        await self.session.execute(
            text("SELECT 1 FROM tenants WHERE id = :tenant_id FOR UPDATE"),
            {"tenant_id": tenant.id},
        )

        if payload.enabled:
            active = (
                await self.session.execute(
                    select(func.count(CallTag.id)).where(
                        CallTag.tenant_id == tenant.id, CallTag.enabled.is_(True)
                    )
                )
            ).scalar_one()
            if active >= MAX_TAGS:
                raise ConflictError(
                    f"a receptionist can have at most {MAX_TAGS} active tags",
                    details={"max_tags": MAX_TAGS, "active": active},
                )

        highest = (
            await self.session.execute(
                select(func.max(CallTag.position)).where(CallTag.tenant_id == tenant.id)
            )
        ).scalar_one()

        tag = CallTag(
            tenant_id=tenant.id,
            name=payload.name,
            description=payload.description,
            color=payload.color,
            enabled=payload.enabled,
            position=(highest or 0) + 1,
        )
        self.session.add(tag)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # The unique constraint, reported as the conflict it is rather than
            # as a 500. Reachable both from a duplicate the caller can see and
            # from one inserted concurrently.
            await self.session.rollback()
            raise ConflictError(
                # `tag_name`, not `name`: an ``AppError``'s details are splatted
                # into a log record's `extra`, and `name` is a reserved
                # LogRecord attribute — logging raises rather than overwriting
                # it, which turns this 409 into a 500.
                "a tag with that name already exists",
                details={"tag_name": payload.name},
            ) from exc

        self._record(AuditAction.TAG_CREATED, tenant.id, tag, actor, meta={})
        logger.info(
            "call tag created",
            extra={"tenant_id": str(tenant.id), "tag_id": str(tag.id), "enabled": tag.enabled},
        )
        return tag

    async def update(
        self, *, tenant: Tenant, tag_id: uuid.UUID, payload: CallTagUpdate, actor: Actor
    ) -> CallTag:
        tag = await self.get(tenant.id, tag_id)

        # Switching one back on competes for a slot like a create does.
        if payload.enabled and not tag.enabled:
            await self.session.execute(
                text("SELECT 1 FROM tenants WHERE id = :tenant_id FOR UPDATE"),
                {"tenant_id": tenant.id},
            )
            active = (
                await self.session.execute(
                    select(func.count(CallTag.id)).where(
                        CallTag.tenant_id == tenant.id, CallTag.enabled.is_(True)
                    )
                )
            ).scalar_one()
            if active >= MAX_TAGS:
                raise ConflictError(
                    f"a receptionist can have at most {MAX_TAGS} active tags",
                    details={"max_tags": MAX_TAGS, "active": active},
                )

        changed: list[str] = []
        if payload.name is not None and payload.name != tag.name:
            tag.name = payload.name
            changed.append("name")
        if payload.description is not None and payload.description != tag.description:
            tag.description = payload.description
            changed.append("description")
        if payload.color is not None and payload.color is not tag.color:
            tag.color = payload.color
            changed.append("color")
        if payload.enabled is not None and payload.enabled != tag.enabled:
            tag.enabled = payload.enabled
            changed.append("enabled")

        if not changed:
            return tag

        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                # `tag_name`, not `name`: an ``AppError``'s details are splatted
                # into a log record's `extra`, and `name` is a reserved
                # LogRecord attribute — logging raises rather than overwriting
                # it, which turns this 409 into a 500.
                "a tag with that name already exists",
                details={"tag_name": payload.name},
            ) from exc

        self._record(AuditAction.TAG_UPDATED, tenant.id, tag, actor, meta={"fields": changed})
        logger.info(
            "call tag updated",
            extra={"tenant_id": str(tenant.id), "tag_id": str(tag.id), "fields": changed},
        )
        return tag

    async def delete(self, *, tenant: Tenant, tag_id: uuid.UUID, actor: Actor) -> None:
        """Remove the tag, and with it every assignment it ever made.

        The cascade is deliberate and it is why "switch off" sits beside this in
        the UI: deleting is for a label that was a mistake, disabling is for one
        that has served its purpose. The audit row is written first so the
        record of who removed it survives the rows that are going.
        """
        tag = await self.get(tenant.id, tag_id)
        self._record(AuditAction.TAG_DELETED, tenant.id, tag, actor, meta={"name": tag.name})
        await self.session.delete(tag)
        await self.session.flush()
        logger.info("call tag deleted", extra={"tenant_id": str(tenant.id), "tag_id": str(tag_id)})

    # ---- helpers ---------------------------------------------------------
    def _record(
        self,
        action: AuditAction,
        tenant_id: uuid.UUID,
        tag: CallTag,
        actor: Actor,
        *,
        meta: dict[str, object],
    ) -> None:
        self.audit.record(
            action,
            actor_type=actor.actor_type,
            actor=actor.user,
            tenant_id=tenant_id,
            entity_type="call_tag",
            entity_id=tag.id,
            meta=meta,
        )


def tags_view_payload(tags: Sequence[CallTag]) -> dict[str, object]:
    """The fields both the read and the write responses share."""
    return {
        "tags": [
            CallTagView(
                id=tag.id,
                name=tag.name,
                description=tag.description,
                color=tag.color,
                enabled=tag.enabled,
                position=tag.position,
                created_at=tag.created_at,
            )
            for tag in tags
        ],
        "max_tags": MAX_TAGS,
        "active": sum(1 for tag in tags if tag.enabled),
    }


async def labels_for_calls(
    session: AsyncSession, call_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[CallTagLabel]]:
    """The labels on each of these calls, in one query.

    Keyed by call id so the list endpoint can hand every row its own labels
    without a query per row — a calls page is up to a hundred rows, and an N+1
    there is a hundred round trips for decoration.

    Ordered by the tag's own position, so two calls carrying the same labels
    show them the same way round.
    """
    if not call_ids:
        return {}

    rows = (
        await session.execute(
            select(CallTagAssignment.call_id, CallTag)
            .join(CallTag, CallTag.id == CallTagAssignment.tag_id)
            .where(CallTagAssignment.call_id.in_(call_ids))
            .order_by(CallTag.position, CallTag.created_at)
        )
    ).all()

    labels: dict[uuid.UUID, list[CallTagLabel]] = {}
    for call_id, tag in rows:
        labels.setdefault(call_id, []).append(
            CallTagLabel(id=tag.id, name=tag.name, color=tag.color)
        )
    return labels


def tagged_calls(tenant_id: uuid.UUID, tag_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """Call ids carrying this tag, **scoped to the tenant that owns the tag**.

    The join to ``call_tags`` is not decoration: without it a caller could pass
    another business's tag id and the filter would narrow their own calls by
    somebody else's label. With it, a foreign id simply matches nothing — which
    is the same answer a tag that does not exist gives, and is what the rest of
    this product does with another tenant's identifiers.
    """
    return (
        select(CallTagAssignment.call_id)
        .join(CallTag, CallTag.id == CallTagAssignment.tag_id)
        .where(CallTagAssignment.tag_id == tag_id, CallTag.tenant_id == tenant_id)
    )
