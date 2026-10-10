"""Health, liveness, and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.cache.redis import redis_client
from src.infrastructure.database.session import get_db
from src.infrastructure.messaging.bus import message_bus

router = APIRouter(tags=["Health"])


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    """Basic liveness check confirming process is running."""
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe")
async def readyz(session: AsyncSession = Depends(get_db)) -> dict[str, object]:
    """Comprehensive readiness probe checking downstream database, cache, and bus."""
    checks: dict[str, bool] = {}

    # 1. Database check
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False

    # 2. Redis check
    checks["redis"] = await redis_client.ping()

    # 3. RabbitMQ check
    checks["rabbitmq"] = await message_bus.is_connected()

    all_healthy = checks["database"] and checks["redis"]
    if not all_healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    return {"status": "ready", "checks": checks}
