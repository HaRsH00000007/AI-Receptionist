"""Database fixtures.

The suite runs against a real PostgreSQL, never SQLite. The schema uses JSONB,
partial unique indexes and ``FOR UPDATE SKIP LOCKED``; a SQLite stand-in would
pass while proving nothing about the constraints that actually protect the
provisioning workflow.

The scratch database is built by running **the migrations**, not
``metadata.create_all``. That way the suite tests what a deployment will
actually execute, and a model change that nobody wrote a migration for fails
here rather than in production.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://receptionist:receptionist@localhost:55432/receptionist_test"
)


def resolve_test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


def _maintenance_url(url: str) -> str:
    """The same server, but the always-present ``postgres`` database.

    Needed because a database cannot be dropped while you are connected to it.
    """
    base, _, _ = url.rpartition("/")
    return f"{base}/postgres"


def _database_name(url: str) -> str:
    return url.rpartition("/")[2]


async def _recreate_database(url: str) -> None:
    engine = create_async_engine(_maintenance_url(url), isolation_level="AUTOCOMMIT")
    name = _database_name(url)
    try:
        async with engine.connect() as connection:
            await connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            await connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    finally:
        await engine.dispose()


def _run_migrations(url: str) -> None:
    """``alembic upgrade head`` against the scratch database.

    Imported here rather than at module scope so that collecting the suite does
    not depend on Alembic being importable.
    """
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


async def _server_reachable(url: str) -> str | None:
    engine = create_async_engine(_maintenance_url(url))
    try:
        async with engine.connect():
            return None
    except Exception as exc:  # noqa: BLE001 - the reason is the useful part
        return f"{type(exc).__name__}: {exc}"
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    """A freshly migrated scratch database for the whole session.

    Synchronous on purpose: it drives its own event loop, which keeps it clear
    of pytest-asyncio's per-test loop and avoids loop-scope juggling.
    """
    url = resolve_test_database_url()

    failure = asyncio.run(_server_reachable(url))
    if failure is not None:
        pytest.skip(
            f"PostgreSQL is not reachable at {_maintenance_url(url)} ({failure}). "
            "Start it with `docker compose up -d db`, or set TEST_DATABASE_URL."
        )

    asyncio.run(_recreate_database(url))
    _run_migrations(url)
    yield url


@pytest.fixture
async def db_session(migrated_database: str) -> AsyncIterator[AsyncSession]:
    """A session whose writes are rolled back when the test ends.

    The session joins an outer transaction using savepoints, so a test may call
    ``commit()`` — and must, to see a constraint fire — without any of it
    surviving into the next test.
    """
    engine = create_async_engine(migrated_database)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                yield session
            finally:
                await session.close()
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()
