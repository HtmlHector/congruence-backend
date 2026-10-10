"""Redis cache, lock, and session management client."""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)


class RedisClient:
    """Async Redis wrapper for caching and distributed locks."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or settings.redis.url
        self._pool: aioredis.ConnectionPool | None = None
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        """Initialize Redis connection pool."""
        if self._client is None:
            self._pool = aioredis.ConnectionPool.from_url(
                self._url,
                max_connections=settings.redis.max_connections,
                socket_timeout=settings.redis.socket_timeout,
                decode_responses=True,
            )
            self._client = aioredis.Redis(connection_pool=self._pool)
            logger.info("redis_connected", url=self._url)

    async def disconnect(self) -> None:
        """Close connection pool."""
        if self._client:
            await self._client.close()
            self._client = None
        if self._pool:
            await self._pool.disconnect()
            self._pool = None
        logger.info("redis_disconnected")

    @property
    def client(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError("RedisClient not connected. Call connect() first.")
        return self._client

    async def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return bool(await self.client.ping())
        except Exception as exc:
            logger.warning("redis_ping_failed", error=str(exc))
            return False

    async def get_json(self, key: str) -> Any | None:
        """Fetch and deserialize a JSON cached value."""
        data = await self.client.get(key)
        if data is None:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return data

    async def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Serialize and set a cached value with optional expiration."""
        payload = json.dumps(value)
        if ttl_seconds:
            await self.client.setex(key, ttl_seconds, payload)
        else:
            await self.client.set(key, payload)

    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        count = await self.client.delete(key)
        return bool(count > 0)


redis_client = RedisClient()
