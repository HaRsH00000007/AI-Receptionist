"""Fixtures for tests that drive the real worker.

These use genuine sessions against the real database rather than the
savepoint-wrapped ``db_session``, because the behaviour under test *is* the
transaction behaviour: ``FOR UPDATE SKIP LOCKED``, leases, commits between
steps, and recovery after a crash. A test that wrapped all of that in one
rolled-back transaction would prove nothing.

Tables are truncated after each test instead, which is slower and honest.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import create_engine, create_session_factory
from app.providers.fakes.llm import FakeLLMProvider
from app.providers.fakes.mail import FakeEmailProvider
from app.providers.fakes.telephony import FakeTwilioProvider
from app.providers.fakes.voice import FakeElevenLabsProvider
from app.providers.registry import Providers
from app.worker import Worker
from tests.support import build_settings

#: Every table the tests write to. Truncated together so foreign keys do not
#: dictate an ordering.
_TABLES = (
    "webhook_events",
    "calls",
    "provisioning_steps",
    "provisioning_runs",
    "agents",
    "agent_configs",
    "phone_numbers",
    "business_profiles",
    "tenants",
)


async def truncate_all(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))


@dataclass
class WorkerEnv:
    """A complete, isolated provisioning environment for one test."""

    settings: Settings
    providers: Providers
    session_factory: async_sessionmaker[AsyncSession]
    worker: Worker

    @property
    def twilio(self) -> FakeTwilioProvider:
        assert isinstance(self.providers.twilio, FakeTwilioProvider)
        return self.providers.twilio

    @property
    def elevenlabs(self) -> FakeElevenLabsProvider:
        assert isinstance(self.providers.elevenlabs, FakeElevenLabsProvider)
        return self.providers.elevenlabs

    @property
    def email(self) -> FakeEmailProvider:
        assert isinstance(self.providers.email, FakeEmailProvider)
        return self.providers.email

    @property
    def llm(self) -> FakeLLMProvider:
        assert isinstance(self.providers.llm, FakeLLMProvider)
        return self.providers.llm

    async def drain(self, *, max_ticks: int = 30) -> int:
        """Tick until nothing changes, or the cap is hit.

        The cap is a guard against an infinite loop in the engine turning into a
        hung test rather than a failing one.
        """
        for tick in range(1, max_ticks + 1):
            before = (self.worker.stats.steps_executed, self.worker.stats.calls_processed)
            await self.worker.tick()
            after = (self.worker.stats.steps_executed, self.worker.stats.calls_processed)
            if before == after:
                return tick
        return max_ticks


def build_fake_providers() -> Providers:
    return Providers(
        llm=FakeLLMProvider(),
        twilio=FakeTwilioProvider(),
        elevenlabs=FakeElevenLabsProvider(),
        email=FakeEmailProvider(),
    )


@pytest.fixture
async def worker_env(migrated_database: str) -> AsyncIterator[WorkerEnv]:
    settings = build_settings(
        database_url=migrated_database,
        # No waiting in tests: the backoff ladder is verified directly in
        # test_provisioning rather than by sleeping through it.
        provisioning_backoff_s="0,0,0",
        provisioning_max_attempts=3,
        twilio_account_sid="ACtest",
        twilio_auth_token="test-token",
    )
    engine = create_engine(settings)
    providers = build_fake_providers()
    factory = create_session_factory(engine)

    env = WorkerEnv(
        settings=settings,
        providers=providers,
        session_factory=factory,
        worker=Worker(settings, providers, factory),
    )
    try:
        yield env
    finally:
        await truncate_all(engine)
        await engine.dispose()
