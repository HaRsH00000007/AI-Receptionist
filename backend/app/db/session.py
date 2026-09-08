"""Engine, session factory and the database readiness probe.

The engine is created by the application (or worker) at startup and disposed at
shutdown — never at import time — so that nothing acquires a connection just
because a module was imported, and tests can stand up an engine per case.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.readiness import ReadinessCheck


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the async engine.

    ``pool_pre_ping`` costs one round trip per checkout and buys immunity to
    connections killed by a restart or an idle timeout — worth it for a worker
    that may sit idle between runs.

    ``statement_timeout`` is set server-side so a pathological query cannot wedge
    a worker indefinitely; without it, a hung statement looks exactly like a
    stuck provisioning run.
    """
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "statement_timeout": str(settings.db_statement_timeout_ms),
                "application_name": settings.app_name,
            }
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to ``engine``.

    ``expire_on_commit=False`` so that attributes stay readable after the
    request's commit — otherwise every response serialization would trigger a
    lazy reload against a closed session.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Request-scoped session with unit-of-work semantics.

    One transaction per request: it commits if the handler returned, and rolls
    back if anything was raised. Handlers therefore never have to remember to
    commit, and a handler that raises halfway cannot leave a half-written tenant
    behind.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def make_database_check(engine: AsyncEngine) -> ReadinessCheck:
    """A readiness probe that actually talks to Postgres.

    Registered into the app's :class:`~app.core.readiness.ReadinessRegistry` at
    startup, which is what makes ``/readyz`` mean something now that there is a
    dependency to check.
    """

    async def check() -> str | None:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return None

    return check
