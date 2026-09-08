"""A minimal repository base.

The production plan's team-scaling note asks for "one repository layer that every
query goes through" so that tenant isolation is enforced in a single place and
testable there. This is the base that layer is built on.

Deliberately small: it holds the four operations every model needs and nothing
speculative. Query methods specific to a model belong on that model's repository,
added when a caller exists.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.base import Base


class Repository[ModelT: Base]:
    """CRUD primitives for one model, bound to one session."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        """Fetch by primary key, or ``None``."""
        return await self.session.get(self.model, entity_id)

    async def get_or_raise(self, entity_id: uuid.UUID) -> ModelT:
        """Fetch by primary key, or raise :class:`NotFoundError`.

        Terminal by construction: a missing row will still be missing on the
        fifth attempt, so the provisioning engine must not retry it.
        """
        entity = await self.get(entity_id)
        if entity is None:
            raise NotFoundError(
                f"{self.model.__name__} not found",
                details={"model": self.model.__name__, "id": str(entity_id)},
            )
        return entity

    async def find_one_by(self, **filters: Any) -> ModelT | None:
        """Fetch the single row matching ``filters``, or ``None``."""
        result = await self.session.execute(select(self.model).filter_by(**filters).limit(1))
        return result.scalar_one_or_none()

    async def list_by(self, **filters: Any) -> list[ModelT]:
        """Every row matching ``filters``."""
        result = await self.session.execute(select(self.model).filter_by(**filters))
        return list(result.scalars().all())

    async def count(self, **filters: Any) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(self.model).filter_by(**filters)
        )
        return int(result.scalar_one())

    def add(self, entity: ModelT) -> ModelT:
        """Stage a new row.

        Does not flush: the caller decides the transaction boundary, because a
        provisioning step often needs several rows to land atomically.
        """
        self.session.add(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)
