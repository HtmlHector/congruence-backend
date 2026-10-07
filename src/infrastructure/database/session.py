"""AsyncSession factory and FastAPI dependency for request-scoped sessions."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infrastructure.database.engine import async_engine
from src.infrastructure.database.tenant_scope import initialize_rls

AsyncSessionFactory = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session.

    Initializes fail-closed RLS context on session checkout.
    Commits on successful handler completion, or rolls back on exception.
    """
    async with AsyncSessionFactory() as session:
        try:
            await initialize_rls(session)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


get_db_session = get_db
