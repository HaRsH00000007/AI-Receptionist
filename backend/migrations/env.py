"""Alembic environment.

Two things differ from the generated template.

The URL comes from :class:`app.core.config.Settings`, not from ``alembic.ini``,
so credentials live in the environment and only ever in the environment.

Migrations run through the async engine, matching the application, so a
migration that works here cannot fail at runtime for driver reasons.
"""

from __future__ import annotations

import asyncio
from typing import Any

from alembic import context
from sqlalchemy.engine import Connection

# Importing the models package is what populates Base.metadata; without it,
# autogenerate would cheerfully produce a migration that drops every table.
import app.models  # noqa: F401
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import create_engine

config = context.config
target_metadata = Base.metadata


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Catch a column whose type changed, not just one that appeared.
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
        render_as_batch=False,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting — for reviewing a migration."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live database."""
    # An explicit URL passed by a caller (the test harness) wins over the
    # environment, and skips reading it entirely — migrating a scratch database
    # must not require the developer's .env to be present and correct.
    override: Any = config.get_main_option("sqlalchemy.url", None)
    settings = Settings(database_url=override, _env_file=None) if override else get_settings()

    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
