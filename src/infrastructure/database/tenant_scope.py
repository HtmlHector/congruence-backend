"""PostgreSQL row-level-security (RLS) context propagation.

RLS settings must be applied to the active transaction running queries.
The helper accepts application-verified workspace identifiers only.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.identity.tenant import (
    NO_TENANT_ID,
    TenantContext,
    bind_tenant_context,
)

RLS_SETTING: Final = "app.current_workspace_id"


async def set_rls_workspace(
    session: AsyncSession,
    workspace_id: str | None,
) -> None:
    """Set the transaction-local workspace value on the active DB session.

    None is converted to NO_TENANT_ID sentinel, keeping queries fail-closed.
    set_config('app.current_workspace_id', :workspace_id, true) scopes the
    setting to the active transaction.
    """
    value = NO_TENANT_ID if workspace_id is None else workspace_id.strip()
    if not value:
        raise ValueError("workspace_id must not be empty")
    bind = getattr(session, "bind", None)
    if not bind and hasattr(session, "get_bind"):
        try:
            bind = session.get_bind()
        except Exception:
            bind = None
    if bind and getattr(getattr(bind, "dialect", None), "name", "") == "sqlite":
        return
    if bind:
        await session.execute(
            text("SELECT set_config('app.current_workspace_id', :workspace_id, true)"),
            {"workspace_id": value},
        )


async def initialize_rls(session: AsyncSession) -> None:
    """Initialize transaction with fail-closed tenant value."""
    await set_rls_workspace(session, None)


async def bind_rls_workspace(
    session: AsyncSession,
    context: TenantContext,
) -> None:
    """Bind verified application context and update the active transaction."""
    bind_tenant_context(context)
    await set_rls_workspace(session, context.workspace_id)
