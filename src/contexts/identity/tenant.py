"""Tenant context shared by API, background workers, and services.

The context provides request/task-local state. PostgreSQL RLS remains the authoritative
security boundary and is bound on the active transaction by tenant_scope.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

# Sentinel that ensures unauthenticated or missing tenant queries fail closed
NO_TENANT_ID = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Verified workspace scope and actor metadata."""

    workspace_id: str
    principal_id: str | None = None
    auth_method: str | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not self.workspace_id or not self.workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        if self.workspace_id == NO_TENANT_ID:
            raise ValueError("the no-tenant sentinel is not a valid tenant")


_current_tenant: ContextVar[TenantContext | None] = ContextVar(
    "current_tenant_context", default=None
)


def current_tenant_context() -> TenantContext | None:
    """Return the current context, or None outside a tenant scope."""
    return _current_tenant.get()


def bind_tenant_context(context: TenantContext) -> Token[TenantContext | None]:
    """Bind a verified tenant context and return a token for restoration."""
    return _current_tenant.set(context)


def clear_tenant_context(token: Token[TenantContext | None] | None = None) -> None:
    """Restore a previous context or clear the current task's context."""
    if token is None:
        _current_tenant.set(None)
    else:
        _current_tenant.reset(token)
