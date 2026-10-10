"""Tenant context propagation for fail-closed multi-tenancy.

Carries workspace identity across asynchronous task boundaries, database sessions,
RabbitMQ events, and realtime channels.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from fastapi import Request

NO_TENANT_ID: Final[str] = "00000000-0000-0000-0000-000000000000"

_tenant_var: contextvars.ContextVar[TenantContext | None] = contextvars.ContextVar(
    "tenant_context", default=None
)


@dataclass(frozen=True)
class TenantContext:
    """Carries verified workspace context across process & task boundaries."""

    workspace_id: str
    user_id: str | None = None
    role: str | None = None

    @property
    def workspace_uuid(self) -> UUID | None:
        try:
            return UUID(self.workspace_id)
        except (ValueError, TypeError):
            return None


def get_current_tenant() -> TenantContext | None:
    """Retrieve the active tenant context for the current async task."""
    return _tenant_var.get()


def set_current_tenant(context: TenantContext | None) -> contextvars.Token:
    """Set the active tenant context for the current async task."""
    return _tenant_var.set(context)


def extract_workspace_id(request: Request) -> str | None:
    """Extract workspace ID from request state or custom header."""
    return getattr(request.state, "workspace_id", None) or request.headers.get("X-Workspace-Id")
