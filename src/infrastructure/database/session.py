"""FastAPI database session dependency."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.engine import async_session_factory
from src.infrastructure.database.rls import initialize_fail_closed_rls

logger = structlog.get_logger(__name__)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a transactional AsyncSession with fail-closed RLS initialized."""
    async with async_session_factory() as session:
        try:
            await initialize_fail_closed_rls(session)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
