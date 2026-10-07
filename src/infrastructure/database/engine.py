"""Creates and exports the async SQLAlchemy engine.

Connection pooling is configured via settings. Connection arguments disable
the client-side prepared-statement cache to guarantee full compatibility with
transaction-mode PgBouncer / Supabase poolers.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config.settings import settings

is_sqlite = settings.database.url.startswith("sqlite")

if is_sqlite:
    async_engine: AsyncEngine = create_async_engine(
        settings.database.url,
        echo=settings.database.echo,
    )
else:
    async_engine: AsyncEngine = create_async_engine(
        settings.database.url,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.pool_overflow,
        pool_timeout=settings.database.pool_timeout,
        pool_recycle=settings.database.pool_recycle,
        pool_pre_ping=True,
        echo=settings.database.echo,
        connect_args={
            "statement_cache_size": 0,
            "server_settings": {
                "statement_timeout": "30s",
                "idle_in_transaction_session_timeout": "30s",
            },
        },
    )
