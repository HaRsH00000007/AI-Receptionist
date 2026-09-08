"""Shared fixtures.

Every test builds its own application from explicit settings. Nothing reads the
developer's environment, so the suite behaves identically on a laptop and in CI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app

# Database and worker fixtures live in their own modules to keep this one
# readable; they are re-exported here so tests can request them by name.
from tests.db_fixtures import (  # noqa: F401
    db_session,
    migrated_database,
    resolve_test_database_url,
)
from tests.support import STUB_DATABASE_URL, build_settings  # noqa: F401
from tests.worker_fixtures import (  # noqa: F401
    WorkerEnv,
    build_fake_providers,
    truncate_all,
    worker_env,
)


@pytest.fixture
def settings() -> Settings:
    return build_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
async def api_app(migrated_database: str) -> AsyncIterator[FastAPI]:  # noqa: F811
    """A fully wired app against the real database.

    Runs the lifespan, so the engine, the provider bundle and the database
    readiness check are all present — the same objects a request would use in
    production, not stand-ins.
    """
    from app.db.session import create_engine
    from tests.worker_fixtures import truncate_all as _truncate

    settings = build_settings(
        database_url=migrated_database,
        twilio_account_sid="ACtest",
        twilio_auth_token="test-token",
        elevenlabs_webhook_secret="test-webhook-secret",
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        try:
            yield application
        finally:
            engine = create_engine(settings)
            try:
                await _truncate(engine)
            finally:
                await engine.dispose()


@pytest.fixture
async def api_client(api_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=api_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
