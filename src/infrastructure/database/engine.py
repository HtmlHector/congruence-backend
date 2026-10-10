"""Database engine and session factory configuration."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.settings import settings

_engine_kwargs: dict[str, object] = {
    "echo": settings.database.echo,
    "future": True,
}

if not settings.database.url.startswith("sqlite"):
    _engine_kwargs.update(
        {
            "pool_size": settings.database.pool_size,
            "max_overflow": settings.database.max_overflow,
            "pool_timeout": settings.database.pool_timeout,
        }
    )

engine: AsyncEngine = create_async_engine(
    settings.database.url,
    **_engine_kwargs,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)
