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
from src.adapters.api.routes.credentials import router as credentials_router
from src.adapters.api.routes.devboxes import router as devboxes_router
from src.adapters.api.routes.environments import router as environments_router
from src.adapters.api.routes.health import router as health_router
from src.adapters.api.routes.integrations import router as integrations_router
from src.adapters.api.routes.previews import PREVIEW_COOKIE_NAME, router as previews_router
from src.adapters.api.routes.projects import router as projects_router
from src.adapters.api.routes.shell import router as shell_router
from src.adapters.api.routes.storage import router as storage_router
from src.adapters.api.routes.terminals import router as terminals_router
from src.adapters.api.routes.usage import router as usage_router
from src.adapters.api.routes.webhooks.clerk import router as clerk_webhook_router
from src.adapters.api.routes.webhooks.stripe import router as stripe_webhook_router
from src.adapters.api.routes.workspaces import router as workspaces_router
from src.adapters.api.routes.worktrees import router as worktrees_router
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

    # Fallback asset proxying for preview iframes requesting root paths (e.g. /_next/..., /vite.svg)
    @app.middleware("http")
    async def preview_asset_fallback_middleware(request: Request, call_next):
        path = request.url.path
        if not path.startswith(("/api", "/docs", "/openapi.json", "/redoc", "/health", "/socket.io")):
            preview_cookie = request.cookies.get(PREVIEW_COOKIE_NAME)
            if preview_cookie and ":" in preview_cookie:
                port_str, token = preview_cookie.split(":", 1)
                try:
                    port = int(port_str)
                    from src.infrastructure.database.engine import async_session_factory
                    from src.contexts.previews.application.service import PreviewService
                    from src.contexts.previews.infrastructure.proxy import proxy_http_request
                    async with async_session_factory() as db:
                        claims = PreviewService(db).verify_ticket_or_token(token)
                        bridge = await PreviewService(db).get_bridge(claims.box_id, port)
                        clean_path = path.lstrip("/")
                        base_href = f"{settings.app.api_prefix}/projects/{claims.project_id}/preview/{port}/"
                        return await proxy_http_request(request, bridge, clean_path, base_href)
                except Exception:
                    pass
        return await call_next(request)

    # API v1 routes
    api_v1 = settings.app.api_prefix
    app.include_router(auth_router, prefix=api_v1)
    app.include_router(workspaces_router, prefix=api_v1)
    app.include_router(billing_router, prefix=api_v1)
    app.include_router(projects_router, prefix=api_v1)
    app.include_router(devboxes_router, prefix=api_v1)
    app.include_router(previews_router, prefix=api_v1)
    app.include_router(worktrees_router, prefix=api_v1)
    app.include_router(terminals_router, prefix=api_v1)
    app.include_router(shell_router, prefix=api_v1)
    app.include_router(credentials_router, prefix=api_v1)
    app.include_router(environments_router, prefix=api_v1)
    app.include_router(usage_router, prefix=api_v1)
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
