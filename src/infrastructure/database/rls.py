"""PostgreSQL Row-Level Security (RLS) enforcement helpers.

Ensures every transaction sets app.current_workspace_id for fail-closed multi-tenancy.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.shared.tenant_context import NO_TENANT_ID, TenantContext


async def set_rls_workspace(session: AsyncSession, workspace_id: str | None) -> None:
    """Set the transaction-local workspace value on the active DB session."""
    if not settings.database.enable_rls:
        return

    # Use bound parameter with set_config to prevent SQL injection
    val = NO_TENANT_ID if workspace_id is None else workspace_id.strip()
    if not val:
        val = NO_TENANT_ID

    await session.execute(
        text("SELECT set_config('app.current_workspace_id', :workspace_id, true)"),
        {"workspace_id": val},
    )


async def initialize_fail_closed_rls(session: AsyncSession) -> None:
    """Initialize transaction with fail-closed sentinel so it never inherits prior context."""
    await set_rls_workspace(session, None)


async def bind_rls_workspace(session: AsyncSession, context: TenantContext) -> None:
    """Bind verified application tenant context to active transaction."""
    await set_rls_workspace(session, context.workspace_id)
