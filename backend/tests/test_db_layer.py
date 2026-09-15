"""Session handling, the repository base, and the readiness probe.

These run against the real database because that is the only place the round
trip through JSONB, timezone-aware timestamps and server defaults is actually
exercised.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.errors import NotFoundError
from app.db.repository import Repository
from app.db.session import create_engine, create_session_factory, make_database_check
from app.main import create_app
from app.models import ProvisioningRun, Tenant
from app.models.enums import ProvisioningStatus, TenantStatus
from app.schemas.business import BusinessHours, DaySchedule, Weekday, dump_json_column
from tests import factories
from tests.support import build_settings


class TenantRepository(Repository[Tenant]):
    model = Tenant


class RunRepository(Repository[ProvisioningRun]):
    model = ProvisioningRun


# ---------------------------------------------------------------------------
# Round trips
# ---------------------------------------------------------------------------
async def test_server_defaults_are_applied(db_session: AsyncSession) -> None:
    tenant = factories.make_tenant()
    db_session.add(tenant)
    await db_session.commit()

    assert isinstance(tenant.id, uuid.UUID)
    assert tenant.created_at.tzinfo is not None
    assert tenant.status is TenantStatus.PENDING


async def test_timestamps_are_timezone_aware(db_session: AsyncSession) -> None:
    """A naive timestamp is how "open at 9" ends up meaning the wrong 9."""
    tenant = factories.make_tenant()
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)

    assert tenant.created_at.utcoffset() is not None


async def test_jsonb_round_trips_a_validated_schema(db_session: AsyncSession) -> None:
    tenant = factories.make_tenant()
    db_session.add(tenant)
    await db_session.commit()

    hours = BusinessHours(
        timezone="America/Los_Angeles",
        days=[
            DaySchedule(day=Weekday.MONDAY, opens_at="09:00", closes_at="18:00"),
            DaySchedule(day=Weekday.SUNDAY, closed=True),
        ],
    )
    profile = factories.make_business_profile(tenant, hours_json=dump_json_column(hours))
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    assert profile.hours_json is not None
    assert BusinessHours.model_validate(profile.hours_json) == hours
    assert profile.services == ["cuts", "color"]


async def test_ids_exist_before_the_row_is_written() -> None:
    """The guarantee Module 4 depends on: an idempotency key can be derived, and
    a child row linked, before anything is flushed."""
    tenant = factories.make_tenant()

    assert isinstance(tenant.id, uuid.UUID)
    assert factories.make_run(tenant).tenant_id == tenant.id


async def test_enum_values_persist_as_their_strings(db_session: AsyncSession) -> None:
    """Read past the ORM's decoding: the column really holds the enum's value."""
    tenant = factories.make_tenant()
    db_session.add(tenant)
    await db_session.commit()

    run = factories.make_run(tenant, status=ProvisioningStatus.NUMBER_PURCHASED)
    db_session.add(run)
    await db_session.commit()

    stored = await db_session.execute(
        text("SELECT status FROM provisioning_runs WHERE id = :id"), {"id": run.id}
    )
    assert stored.scalar_one() == "number_purchased"


async def test_invalid_enum_value_is_refused(db_session: AsyncSession) -> None:
    """``validate_strings`` stops a typo before it reaches the CHECK constraint."""
    tenant = factories.make_tenant()
    db_session.add(tenant)
    await db_session.commit()

    db_session.add(factories.make_run(tenant, status="almost_done"))
    with pytest.raises(StatementError, match="not among the defined enum values"):
        await db_session.commit()


async def test_deleting_a_tenant_cascades(db_session: AsyncSession) -> None:
    """A cancelled tenant must not leave provisioning rows behind."""
    tenant = factories.make_tenant()
    db_session.add(tenant)
    await db_session.commit()

    run = factories.make_run(tenant)
    db_session.add(run)
    await db_session.commit()
    db_session.add(factories.make_step(run))
    await db_session.commit()

    await db_session.delete(tenant)
    await db_session.commit()

    assert await RunRepository(db_session).count() == 0


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------
async def test_repository_reads_back_what_it_added(db_session: AsyncSession) -> None:
    repo = TenantRepository(db_session)
    tenant = repo.add(factories.make_tenant(name="Sunset Salon"))
    await db_session.commit()

    found = await repo.get(tenant.id)
    assert found is not None
    assert found.name == "Sunset Salon"


async def test_repository_get_returns_none_when_absent(db_session: AsyncSession) -> None:
    assert await TenantRepository(db_session).get(uuid.uuid4()) is None


async def test_repository_raises_a_terminal_error_when_absent(db_session: AsyncSession) -> None:
    """A missing row will still be missing on the fifth attempt."""
    repo = TenantRepository(db_session)
    missing = uuid.uuid4()

    with pytest.raises(NotFoundError) as caught:
        await repo.get_or_raise(missing)

    assert caught.value.retryable is False
    assert caught.value.details["id"] == str(missing)
    assert caught.value.details["model"] == "Tenant"


async def test_repository_filters(db_session: AsyncSession) -> None:
    repo = TenantRepository(db_session)
    repo.add(factories.make_tenant(status=TenantStatus.ACTIVE))
    repo.add(factories.make_tenant(status=TenantStatus.ACTIVE))
    repo.add(factories.make_tenant(status=TenantStatus.FAILED))
    await db_session.commit()

    assert await repo.count(status=TenantStatus.ACTIVE) == 2
    assert len(await repo.list_by(status=TenantStatus.FAILED)) == 1

    one = await repo.find_one_by(status=TenantStatus.FAILED)
    assert one is not None and one.status is TenantStatus.FAILED


async def test_repository_delete(db_session: AsyncSession) -> None:
    repo = TenantRepository(db_session)
    tenant = repo.add(factories.make_tenant())
    await db_session.commit()

    await repo.delete(tenant)
    await db_session.commit()

    assert await repo.get(tenant.id) is None


async def test_add_does_not_flush(db_session: AsyncSession) -> None:
    """The caller owns the transaction boundary; several rows may need to land
    together for a provisioning step to be atomic."""
    repo = TenantRepository(db_session)
    repo.add(factories.make_tenant())
    assert repo.session.new


# ---------------------------------------------------------------------------
# Engine, sessions and readiness
# ---------------------------------------------------------------------------
async def test_readiness_check_reaches_postgres(migrated_database: str) -> None:
    engine = create_async_engine(migrated_database)
    try:
        assert await make_database_check(engine)() is None
    finally:
        await engine.dispose()


async def test_readyz_reports_the_database(migrated_database: str) -> None:
    """/readyz now means something: it is answered by a real SELECT 1."""
    app = create_app(build_settings(database_url=migrated_database))

    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        response = await client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == {"ok": True, "detail": None, "critical": True}
    # Redis is registered too, but as a non-critical dependency; it is disabled
    # in the test settings and therefore reports healthy.
    assert body["degraded"] == []


async def test_readyz_reports_a_database_that_is_down() -> None:
    """A wrong port must surface as not-ready, not as a 500."""
    unreachable = "postgresql+asyncpg://receptionist:receptionist@localhost:1/nope"
    app = create_app(build_settings(database_url=unreachable))

    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["database"]["ok"] is False


async def test_healthz_stays_up_when_the_database_is_down() -> None:
    """Liveness must not depend on Postgres, or a blip triggers a restart loop."""
    unreachable = "postgresql+asyncpg://receptionist:receptionist@localhost:1/nope"
    app = create_app(build_settings(database_url=unreachable))

    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        assert (await client.get("/healthz")).status_code == 200


async def test_session_factory_commits_and_isolates(migrated_database: str) -> None:
    """Two sessions from the factory see each other's committed work."""
    settings = build_settings(database_url=migrated_database)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    marker = f"factory-{datetime.now(UTC).timestamp()}"

    try:
        async with factory() as session:
            session.add(factories.make_tenant(name=marker))
            await session.commit()

        async with factory() as session:
            found = await TenantRepository(session).find_one_by(name=marker)
            assert found is not None

            await session.delete(found)
            await session.commit()
    finally:
        await engine.dispose()


async def test_engine_applies_the_statement_timeout(migrated_database: str) -> None:
    """A hung statement must not look like a stuck provisioning run forever."""
    settings = build_settings(database_url=migrated_database, db_statement_timeout_ms=2500)
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            result = await connection.exec_driver_sql("SHOW statement_timeout")
            assert result.scalar_one() == "2500ms"
    finally:
        await engine.dispose()
