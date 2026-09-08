"""Declarative base, naming conventions and shared column mixins.

Two decisions here shape every migration that follows.

**Explicit constraint names.** Postgres will happily invent a name for an unnamed
constraint, and Alembic will then generate migrations that drop and recreate
constraints it cannot match by name. The naming convention below makes every
index, constraint and key name deterministic from its columns, so autogenerate
produces stable diffs.

**Enums stored as VARCHAR + CHECK, not native Postgres enums.** A native enum
needs ``ALTER TYPE`` to gain a value, which is awkward inside a migration and
irreversible without recreating the type. This schema will gain provisioning
steps and statuses as the POC evolves, so the check-constraint form is worth the
slightly weaker introspection. See :func:`app.models.enums.enum_column`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Annotation-driven column types, so models read as plain dataclasses.
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        uuid.UUID: PGUUID(as_uuid=True),
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[str]: JSONB,
        list[dict[str, Any]]: JSONB,
        str: String(255),
    }

    def __repr__(self) -> str:
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


class UUIDPrimaryKeyMixin:
    """A UUID primary key, generated client-side at construction.

    A column ``default`` alone would not be enough: SQLAlchemy applies those at
    flush time, so ``Tenant().id`` would be ``None`` until the row hit the
    database. Assigning in ``__init__`` means an id exists the moment the object
    does, which is what lets a provisioning step derive its idempotency key, and
    link child rows, before anything is written.

    Mixins must therefore precede ``Base`` in a model's bases, so that this
    ``__init__`` is the one the MRO finds.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("id", uuid.uuid4())
        super().__init__(**kwargs)


class TimestampMixin:
    """Creation and last-modification timestamps, always timezone-aware.

    ``server_default``/``onupdate`` rather than Python defaults so that rows
    written by a migration or by hand in psql are stamped too.
    """

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
