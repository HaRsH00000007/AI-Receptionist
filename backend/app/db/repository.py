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

from sqlalchemy import ColumnElement, func, select
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


class TenantScopedRepository[ModelT: Base](Repository[ModelT]):
    """A repository that cannot return another tenant's rows.

    Multi-tenancy fails in one specific way: a query that forgot its
    ``tenant_id`` filter. It does not raise, it does not look wrong in review,
    and it returns a plausible answer — someone else's. The defence has to be
    structural rather than a rule everyone remembers, so this class inverts the
    default: the filter is applied by the base class, and a subclass adding a
    query gets it for free rather than having to remember it.

    Consequently there is deliberately **no** unscoped escape hatch here. Code
    that genuinely spans tenants — the admin panel, the number reaper, the
    nightly aggregator — uses the plain :class:`Repository`, which reads as a
    conscious choice at the call site and is easy to grep for.

    ``get()`` returns ``None`` for a row belonging to another tenant rather than
    raising, so a cross-tenant probe is indistinguishable from a genuine 404 and
    cannot be used to confirm that an id exists.
    """

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        super().__init__(session)
        self.tenant_id = tenant_id

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Reject a model without a ``tenant_id`` at import time.

        Otherwise the mistake surfaces as an ``AttributeError`` on the first
        query in production, which is both late and hard to read.
        """
        super().__init_subclass__(**kwargs)
        model = getattr(cls, "model", None)
        if model is not None and "tenant_id" not in model.__table__.columns:
            raise TypeError(
                f"{cls.__name__} is tenant-scoped but {model.__name__} has no tenant_id column; "
                f"use Repository instead."
            )

    @property
    def _scope(self) -> ColumnElement[bool]:
        """The filter every query in this class is built on."""
        return self.model.__table__.c.tenant_id == self.tenant_id

    # ---- Overrides -------------------------------------------------------
    # Each of these narrows a base-class method to this tenant. They are
    # overrides rather than new names so that a caller holding a Repository
    # reference cannot accidentally reach the unscoped version.

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        """Fetch by primary key, but only within this tenant."""
        result = await self.session.execute(
            select(self.model).where(self.model.__table__.c.id == entity_id, self._scope).limit(1)
        )
        return result.scalar_one_or_none()

    async def find_one_by(self, **filters: Any) -> ModelT | None:
        result = await self.session.execute(
            select(self.model).filter_by(**filters).where(self._scope).limit(1)
        )
        return result.scalar_one_or_none()

    async def list_by(self, **filters: Any) -> list[ModelT]:
        result = await self.session.execute(
            select(self.model).filter_by(**filters).where(self._scope)
        )
        return list(result.scalars().all())

    async def count(self, **filters: Any) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(self.model).filter_by(**filters).where(self._scope)
        )
        return int(result.scalar_one())

    def add(self, entity: ModelT) -> ModelT:
        """Stage a new row, stamping or verifying its tenant.

        An unset ``tenant_id`` is filled in; a *mismatched* one is refused
        rather than silently corrected, because a caller that built the object
        with a different tenant has a bug worth surfacing.
        """
        current = getattr(entity, "tenant_id", None)
        if current is None:
            entity.tenant_id = self.tenant_id  # type: ignore[attr-defined]
        elif current != self.tenant_id:
            raise PermissionError(
                f"refusing to write a {type(entity).__name__} owned by tenant {current} "
                f"through a repository scoped to {self.tenant_id}"
            )
        return super().add(entity)

    async def delete(self, entity: ModelT) -> None:
        """Delete, but never another tenant's row."""
        owner = getattr(entity, "tenant_id", None)
        if owner != self.tenant_id:
            raise PermissionError(
                f"refusing to delete a {type(entity).__name__} owned by tenant {owner} "
                f"through a repository scoped to {self.tenant_id}"
            )
        await super().delete(entity)
