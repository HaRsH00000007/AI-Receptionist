"""The background worker.

    uv run python -m app.worker

A polling loop over Postgres — no queue broker, no Redis. At this volume
Postgres is a perfectly good queue, and one fewer moving part is one fewer thing
that can be down (docs/00_DECISIONS.md sections 4 and 5).

**Provisioning orchestration moved to Temporal in M4.** When
``ORCHESTRATOR=temporal`` — the default — this worker does *not* claim
provisioning runs, and :mod:`app.temporal.worker` is the only thing driving
them. That guard is a money-safety property, not tidiness: two orchestrators
polling the same runs would both advance them, and the step that spends money
would be attempted twice. There is exactly one authoritative orchestrator at a
time.

The polling engine is retained, behind ``ORCHESTRATOR=state_machine``, as a
documented fallback: it is the path 500-odd tests already prove, and keeping it
runnable means a Temporal outage has an answer that is not "provisioning is
down". It is not the default and is not maintained as an equal path.

Post-call processing keeps polling either way — summarizing a finished call is
queue work, not a long-running orchestration.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_session_factory
from app.providers.registry import Providers, build_providers
from app.provisioning.engine import ProvisioningEngine, claim_runs
from app.services.call_processor import CallProcessor, claim_calls
from app.services.notification_outbox import NotificationOutbox
from app.services.retention import RetentionService
from app.services.usage import rebuild_recent_rollups

logger = get_logger(__name__)


@dataclass(slots=True)
class WorkerStats:
    """Counters, mostly so tests can assert the loop did something."""

    polls: int = 0
    steps_executed: int = 0
    calls_processed: int = 0
    notifications_delivered: int = 0
    maintenance_runs: int = 0


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
        self.outbox = NotificationOutbox(settings)
        # Epoch, so the first tick runs maintenance immediately rather than
        # waiting an interval after a deploy.
        self._last_maintenance = datetime.fromtimestamp(0, tz=UTC)
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

        # The single-orchestrator guard. Under Temporal these runs belong to a
        # workflow; claiming them here would run a second orchestrator over the
        # same steps and could buy a second phone number.
        if not self.settings.uses_temporal:
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

        # The notification outbox. Delivered here rather than inline so that a
        # slow or failing email provider cannot make a webhook time out and be
        # redelivered — and so an undeliverable message stays visible as an
        # unmet obligation instead of vanishing into a log line.
        async with self.session_factory() as session:
            notification_ids = await self.outbox.claim_due(
                session,
                batch_size=self.settings.worker_batch_size,
                lease_s=self.settings.provisioning_step_timeout_s,
            )

        for notification_id in notification_ids:
            async with self.session_factory() as session:
                await self.outbox.deliver(session, notification_id, self.providers.email)
            self.stats.notifications_delivered += 1

        await self._maintenance()
        return self.stats

    # ---- periodic maintenance -------------------------------------------
    async def _maintenance(self) -> None:
        """Retention and usage rollups, run on a slow cadence.

        In this loop rather than in a separate cron container, because a
        retention policy that depends on someone remembering to deploy a
        scheduler is a retention policy that silently stops running. Everything
        here is idempotent and batched, so running it often is cheap and
        skipping a tick costs nothing.

        Failures are logged and swallowed: a retention problem must not stop the
        worker from summarizing calls and sending mail, which is the work
        customers actually notice.
        """
        moment = datetime.now(UTC)
        if moment - self._last_maintenance < timedelta(
            seconds=self.settings.maintenance_interval_s
        ):
            return
        self._last_maintenance = moment
        self.stats.maintenance_runs += 1

        try:
            async with self.session_factory() as session:
                report = await RetentionService(session, self.settings).sweep()
            if report.transcripts_redacted or report.recordings_marked:
                logger.info("retention applied", extra=report.as_dict())
        except Exception:
            logger.exception("the retention sweep failed; it will run again next tick")

        try:
            async with self.session_factory() as session:
                await rebuild_recent_rollups(session)
        except Exception:
            logger.exception("the usage rollup failed; totals still read the ledger")

    # ---- the loop --------------------------------------------------------
    async def run_forever(self) -> None:
        logger.info(
            "worker started",
            extra={
                "poll_interval_s": self.settings.worker_poll_interval_s,
                "batch_size": self.settings.worker_batch_size,
                "dry_run": self.settings.dry_run,
                "orchestrator": self.settings.orchestrator,
                # Loud on purpose: which process owns provisioning is the first
                # thing to check when a run is not advancing.
                "provisioning_owner": (
                    "temporal" if self.settings.uses_temporal else "this worker"
                ),
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
