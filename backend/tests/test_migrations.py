"""Migrations must agree with the models, and must reverse cleanly.

The first test here is the one that earns its keep: it asks Alembic to diff the
live schema against ``Base.metadata`` and fails if anything differs. A model
edited without a migration is the classic way a schema drifts, and it is silent
until a query fails in production.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
from tests.db_fixtures import (
    _database_name,
    _recreate_database,
    _run_migrations,
    resolve_test_database_url,
)

EXPECTED_TABLES = {
    "agent_configs",
    "agents",
    "business_profiles",
    "calls",
    "phone_numbers",
    "provisioning_runs",
    "provisioning_steps",
    "tenants",
    "webhook_events",
}


def _diff(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(
        connection,
        opts={"compare_type": True, "compare_server_default": True},
    )
    return list(compare_metadata(context, Base.metadata))


async def test_migrations_produce_the_models_schema(migrated_database: str) -> None:
    """No drift between what the models declare and what the migration builds."""
    engine = create_async_engine(migrated_database)
    try:
        async with engine.connect() as connection:
            differences = await connection.run_sync(_diff)
    finally:
        await engine.dispose()

    assert differences == [], (
        "The schema built by the migrations differs from the models. "
        "Run `uv run alembic revision --autogenerate -m '...'` and review it.\n"
        f"{differences}"
    )


async def test_every_model_is_migrated(migrated_database: str) -> None:
    """A model missing from app.models.__init__ would never reach a migration."""
    engine = create_async_engine(migrated_database)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
    finally:
        await engine.dispose()

    assert tables >= EXPECTED_TABLES
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_downgrade_reverses_the_upgrade(migrated_database: str) -> None:
    """A migration you cannot roll back is a migration you cannot deploy safely.

    Runs against its own scratch database so the suite's schema is untouched.
    """
    from alembic import command
    from alembic.config import Config

    from tests.db_fixtures import BACKEND_ROOT

    url = resolve_test_database_url().replace(
        _database_name(resolve_test_database_url()), "receptionist_downgrade_test"
    )
    asyncio.run(_recreate_database(url))
    _run_migrations(url)

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)

    async def table_names() -> set[str]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
        finally:
            await engine.dispose()

    assert asyncio.run(table_names()) >= EXPECTED_TABLES

    command.downgrade(config, "base")
    remaining = asyncio.run(table_names())

    # alembic_version survives a downgrade to base; nothing of ours should.
    assert remaining & EXPECTED_TABLES == set()


@pytest.mark.parametrize(
    ("table", "index"),
    [
        ("phone_numbers", "uq_phone_numbers_active_per_tenant"),
        ("phone_numbers", "uq_phone_numbers_active_e164"),
        ("provisioning_runs", "uq_provisioning_runs_in_flight_per_tenant"),
        ("agent_configs", "uq_agent_configs_live_per_tenant"),
        ("agents", "uq_agents_active_per_tenant"),
        ("tenants", "uq_tenants_live_contact_email"),
    ],
)
async def test_partial_unique_indexes_exist(migrated_database: str, table: str, index: str) -> None:
    """Named explicitly, because these are the constraints the plan calls out.

    ``test_constraints.py`` proves they behave; this proves they are present and
    partial, so a future migration cannot quietly widen one into a plain index.
    """
    engine = create_async_engine(migrated_database)
    try:
        async with engine.connect() as connection:
            found = await connection.run_sync(
                lambda c: {
                    entry["name"]: entry
                    for entry in inspect(c).get_indexes(table)
                    if entry["name"] == index
                }
            )
    finally:
        await engine.dispose()

    assert index in found, f"{index} is missing from {table}"
    assert found[index]["unique"] is True
    assert found[index].get("dialect_options", {}).get("postgresql_where") is not None
