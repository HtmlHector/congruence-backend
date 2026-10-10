"""Standalone worker process entrypoint."""

from __future__ import annotations

import asyncio
import signal

import structlog

from src.adapters.workers.worker_runner import register_all_workers
from src.infrastructure.cache.redis import redis_client
from src.infrastructure.messaging.bus import message_bus

logger = structlog.get_logger(__name__)


async def run_workers() -> None:
    """Connect infrastructure and run background worker consumers."""
    logger.info("workers_process_starting")
    await redis_client.connect()
    await message_bus.connect()

    await register_all_workers(message_bus)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    import contextlib

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    logger.info("workers_process_running_waiting_for_messages")
    await stop_event.wait()

    logger.info("workers_process_stopping")
    await message_bus.disconnect()
    await redis_client.disconnect()
    logger.info("workers_process_stopped")


def start_workers() -> None:
    """CLI script entrypoint to run worker process."""
    import contextlib

    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(run_workers())


if __name__ == "__main__":
    start_workers()
