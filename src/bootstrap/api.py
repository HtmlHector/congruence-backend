"""FastAPI application factory and server entrypoint."""

from __future__ import annotations

import structlog
import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.adapters.api.routes.audit import router as audit_router
from src.adapters.api.routes.auth import router as auth_router
from src.adapters.api.routes.billing import router as billing_router
from src.adapters.api.routes.health import router as health_router
from src.adapters.api.routes.integrations import router as integrations_router
from src.adapters.api.routes.projects import router as projects_router
from src.adapters.api.routes.storage import router as storage_router
from src.adapters.api.routes.webhooks.clerk import router as clerk_webhook_router
from src.adapters.api.routes.webhooks.stripe import router as stripe_webhook_router
from src.adapters.api.routes.workspaces import router as workspaces_router
from src.bootstrap.container import app_lifespan
from src.bootstrap.socketio import sio_app
from src.config.settings import settings

logger = structlog.get_logger(__name__)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app.name,
        version="0.1.0",
        description="Parabox Backend Starter — production foundation with Auth, Billing, RLS & Realtime",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=app_lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Global Exception Handler matching frontend apiFetch ApiError contract
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Internal server error", "detail": str(exc)},
        )

    # Health probes mounted at root
    app.include_router(health_router)

    # API v1 routes
    api_v1 = settings.app.api_prefix
    app.include_router(auth_router, prefix=api_v1)
    app.include_router(workspaces_router, prefix=api_v1)
    app.include_router(billing_router, prefix=api_v1)
    app.include_router(projects_router, prefix=api_v1)
    app.include_router(integrations_router, prefix=api_v1)
    app.include_router(audit_router, prefix=api_v1)
    app.include_router(storage_router, prefix=api_v1)
    app.include_router(clerk_webhook_router, prefix=api_v1)
    app.include_router(stripe_webhook_router, prefix=api_v1)

    # Mount Socket.IO realtime server
    app.mount("/socket.io", sio_app)

    return app


app = create_app()


def start_api() -> None:
    """CLI script entrypoint to run API server."""
    uvicorn.run(
        "src.bootstrap.api:app",
        host=settings.app.host,
        port=settings.app.port,
        reload=settings.app.debug,
    )


if __name__ == "__main__":
    start_api()
