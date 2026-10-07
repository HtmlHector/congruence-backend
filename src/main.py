"""FastAPI application composition root and entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from src.config.logging import configure_logging
from src.config.settings import settings
from src.infrastructure.database.engine import async_engine
from src.infrastructure.database.session import AsyncSessionFactory
from src.infrastructure.stripe.router import router as stripe_router
from src.routes.agents import router as agents_router
from src.routes.audit import router as audit_router
from src.routes.auth import router as auth_router
from src.routes.health import router as health_router
from src.routes.storage import router as storage_router
from src.routes.workspaces import router as workspaces_router

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown hooks."""
    configure_logging()
    logger.info(
        "starting_application",
        environment=settings.environment,
        debug=settings.debug,
    )

    # Seed system roles if DB is accessible
    try:
        async with AsyncSessionFactory() as session:
            roles = [
                ("OWNER", "Full workspace control", ["*"]),
                (
                    "ADMIN",
                    "Administrative access",
                    [
                        "workspace:read",
                        "workspace:write",
                        "member:read",
                        "member:write",
                        "apikey:*",
                        "billing:read",
                        "agent:*",
                    ],
                ),
                (
                    "MEMBER",
                    "Standard member access",
                    ["workspace:read", "agent:chat", "agent:action", "apikey:read"],
                ),
                ("VIEWER", "Read-only access", ["workspace:read"]),
            ]
            for name, desc, perms in roles:
                await session.execute(
                    text(
                        """
                        INSERT INTO identity.roles (role_id, name, description, is_system, permissions)
                        VALUES (gen_random_uuid(), :name, :desc, TRUE, :perms)
                        ON CONFLICT (name, workspace_id) DO UPDATE SET
                            description = EXCLUDED.description,
                            permissions = EXCLUDED.permissions
                        """
                    ),
                    {"name": name, "desc": desc, "perms": perms},
                )
            await session.commit()
    except Exception as exc:
        logger.warning("roles_seed_skipped", detail=str(exc))

    yield

    logger.info("shutting_down_application")
    await async_engine.dispose()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Parabox Backend API",
        description="Production FastAPI service with multi-tenant Postgres RLS, Stripe, LLM Gateway, and Storage.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Root & Health check routes
    app.include_router(health_router)

    # API v1 routes
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(workspaces_router, prefix="/api/v1")
    app.include_router(stripe_router, prefix="/api/v1")
    app.include_router(storage_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")
    app.include_router(agents_router, prefix="/api/v1")

    return app


app = create_app()
