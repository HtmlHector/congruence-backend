"""API routes exports."""

from src.infrastructure.stripe.router import router as stripe_router
from src.routes.agents import router as agents_router
from src.routes.audit import router as audit_router
from src.routes.auth import router as auth_router
from src.routes.health import router as health_router
from src.routes.storage import router as storage_router
from src.routes.workspaces import router as workspaces_router

__all__ = [
    "agents_router",
    "audit_router",
    "auth_router",
    "health_router",
    "storage_router",
    "stripe_router",
    "workspaces_router",
]
