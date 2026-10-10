"""Application lifecycle and infrastructure container."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from src.infrastructure.auth.clerk import clerk_service
from src.infrastructure.cache.redis import redis_client
from src.infrastructure.messaging.bus import message_bus

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def app_lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and graceful shutdown of infrastructure connections."""
    logger.info("application_starting")

    # Connect Redis cache & Socket.IO pub/sub adapter
    try:
        await redis_client.connect()
    except Exception as exc:
        logger.warning("redis_startup_warning", error=str(exc))

    # Connect RabbitMQ message bus
    try:
        await message_bus.connect()
    except Exception as exc:
        logger.warning("rabbitmq_startup_warning", error=str(exc))

    yield

    logger.info("application_shutting_down")
    await message_bus.disconnect()
    await redis_client.disconnect()
    await clerk_service.close()
    logger.info("application_shutdown_complete")
