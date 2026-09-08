"""The background worker.

    uv run python -m app.worker

A polling loop over Postgres — no queue broker, no Redis. At this volume
Postgres is a perfectly good queue, and one fewer moving part is one fewer thing
that can be down (docs/00_DECISIONS.md sections 4 and 5).

Two loops share the process: provisioning runs and post-call processing. Both
claim work with ``FOR UPDATE SKIP LOCKED`` and a lease, so several worker
processes can run side by side and a crashed one hands its work back
automatically when the lease expires.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_session_factory
from app.providers.registry import Providers, build_providers
from app.provisioning.engine import ProvisioningEngine, claim_runs
from app.services.call_processor import CallProcessor, claim_calls

logger = get_logger(__name__)


@dataclass(slots=True)
class WorkerStats:
    """Counters, mostly so tests can assert the loop did something."""

    polls: int = 0
    steps_executed: int = 0
    calls_processed: int = 0


class Worker:
    """Owns the loops; each tick is independently safe to abandon."""

    def __init__(
        self,
        settings: Settings,
        providers: Providers,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.settings = settings
        self.providers = providers
        self.session_factory = session_factory
        self.engine = ProvisioningEngine(settings, providers)
        self.calls = CallProcessor(settings, providers)
        self.stats = WorkerStats()
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    # ---- one pass --------------------------------------------------------
    async def tick(self) -> WorkerStats:
        """Claim and execute one batch of each kind of work.

        Every claimed item gets its own session, so one poisoned run cannot roll
        back another's progress.
        """
        self.stats.polls += 1

        async with self.session_factory() as session:
            run_ids = await claim_runs(
                session,
                batch_size=self.settings.worker_batch_size,
                lease_s=self.settings.provisioning_step_timeout_s,
            )

        for run_id in run_ids:
            async with self.session_factory() as session:
                report = await self.engine.execute_next_step(session, run_id)
            self.stats.steps_executed += 1
            logger.debug(
                "run advanced",
                extra={"run_id": str(run_id), "outcome": report.outcome},
            )

        async with self.session_factory() as session:
            call_ids = await claim_calls(
                session,
                batch_size=self.settings.worker_batch_size,
                lease_s=self.settings.provisioning_step_timeout_s,
            )

        for call_id in call_ids:
            async with self.session_factory() as session:
                await self.calls.process(session, call_id)
            self.stats.calls_processed += 1

        return self.stats

    # ---- the loop --------------------------------------------------------
    async def run_forever(self) -> None:
        logger.info(
            "worker started",
            extra={
                "poll_interval_s": self.settings.worker_poll_interval_s,
                "batch_size": self.settings.worker_batch_size,
                "dry_run": self.settings.dry_run,
            },
        )
        while not self._stopping.is_set():
            try:
                await self.tick()
            except Exception:
                logger.exception("worker tick failed; continuing")

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self.settings.worker_poll_interval_s
                )
        logger.info("worker stopped", extra={"polls": self.stats.polls})


async def _serve(settings: Settings) -> None:
    engine: AsyncEngine = create_engine(settings)
    providers = build_providers(settings)
    worker = Worker(settings, providers, create_session_factory(engine))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows lacks SIGTERM
            loop.add_signal_handler(sig, worker.stop)

    try:
        await worker.run_forever()
    finally:
        await providers.aclose()
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    try:
        asyncio.run(_serve(settings))
    except KeyboardInterrupt:  # pragma: no cover - Ctrl-C on Windows
        logger.info("worker interrupted")


if __name__ == "__main__":
    main()
