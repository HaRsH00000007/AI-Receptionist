"""The Temporal worker.

    uv run python -m app.temporal.worker

Hosts the provisioning workflow and its activities. Replaces the polling
worker's provisioning loop; post-call processing still runs in
:mod:`app.worker`, because summarizing a finished call is queue work, not a
long-running orchestration, and Postgres remains a perfectly good queue for it.

Two workers, one authoritative orchestrator: see :mod:`app.worker` for the guard
that stops the polling loop claiming provisioning runs when Temporal owns them.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from temporalio.worker import Worker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_session_factory
from app.providers.registry import build_providers
from app.temporal.activities import ProvisioningActivities
from app.temporal.client import connect
from app.temporal.workflows import ProvisionTenantWorkflow

logger = get_logger(__name__)


async def serve(settings: Settings) -> None:
    """Run the worker until interrupted."""
    engine = create_engine(settings)
    providers = build_providers(settings)
    session_factory = create_session_factory(engine)
    client = await connect(settings)

    activities = ProvisioningActivities(settings, providers, session_factory)
    stopping = asyncio.Event()

    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[ProvisionTenantWorkflow],
        activities=activities.all_activities(),
        # Modest concurrency. Every activity holds a database session for its
        # duration, so this is bounded by the connection pool rather than by
        # anything Temporal cares about — raising it without raising
        # `db_pool_size` would just move the contention.
        max_concurrent_activities=settings.worker_batch_size * 2,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows lacks SIGTERM
            loop.add_signal_handler(sig, stopping.set)

    logger.info(
        "temporal worker started",
        extra={
            "task_queue": settings.temporal_task_queue,
            "namespace": settings.temporal_namespace,
            "dry_run": settings.dry_run,
        },
    )

    async def _shutdown_on_signal() -> None:
        await stopping.wait()
        # `shutdown()` drains: in-flight activities finish rather than being cut
        # off mid-vendor-call, which is what stops a purchase completing at
        # Twilio with nothing recorded on our side.
        await worker.shutdown()

    drain = asyncio.create_task(_shutdown_on_signal())
    try:
        await worker.run()
    finally:
        drain.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await drain
        await providers.aclose()
        await engine.dispose()
        logger.info("temporal worker stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    try:
        asyncio.run(serve(settings))
    except KeyboardInterrupt:  # pragma: no cover - Ctrl-C on Windows
        logger.info("temporal worker interrupted")


if __name__ == "__main__":
    main()
