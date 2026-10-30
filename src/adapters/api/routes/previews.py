"""Browser preview reverse-proxy endpoints and ticket minting."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.config.settings import settings
from src.contexts.devbox.application.service import DevboxService
from src.contexts.previews.application.service import PreviewError, PreviewService
from src.contexts.previews.infrastructure.proxy import (
    inject_base_tag,
    inject_web_navigation,
    meta_refresh_target,
    proxy_http_request,
    proxy_websocket,
)
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["Previews"])

PREVIEW_COOKIE_NAME = "congruence_preview"


class PreviewTicketResponse(BaseModel):
    ticket: str
    preview_base_url: str
    expires_in: int


class PortPingResponse(BaseModel):
    live: bool
    port: int


@router.get("/projects/{project_id}/preview-ticket", response_model=PreviewTicketResponse)
async def mint_preview_ticket(
    project_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Generate a signed ticket granting browser preview access for a project devbox."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    service = PreviewService(session)
    try:
        ticket = await service.mint_ticket(
            project_id=project_id,
            workspace_id=tenant.workspace_uuid,
            user_id=tenant.user_id or "anonymous",
        )
    except PreviewError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if exc.code == "PROJECT_NOT_FOUND" else status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    return PreviewTicketResponse(
        ticket=ticket,
        preview_base_url=f"{settings.app.api_prefix}/projects/{project_id}/preview",
        expires_in=settings.preview.cookie_ttl_seconds,
    )


def _unpack_token(token_str: str) -> tuple[int | None, str]:
    if ":" in token_str:
        prefix, rest = token_str.split(":", 1)
        try:
            return int(prefix), rest
        except ValueError:
            pass
    return None, token_str


@router.get("/projects/{project_id}/preview/{port}/ping", response_model=PortPingResponse)
async def ping_preview_port(
    project_id: UUID,
    port: int,
    ticket: str | None = Query(default=None),
    preview_cookie: str | None = Cookie(default=None, alias=PREVIEW_COOKIE_NAME),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Check whether a dev server is actively listening on the target port."""
    service = PreviewService(session)
    raw = ticket or preview_cookie
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing preview token")

    _, token = _unpack_token(raw)
    try:
        claims = service.verify_ticket_or_token(token)
    except PreviewError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc

    if claims.project_id != str(project_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project mismatch")

    bridge = await service.get_bridge(claims.box_id, port)
    is_live = await bridge.check_listening()
    return PortPingResponse(live=is_live, port=port)


@router.get("/projects/{project_id}/preview/external")
async def preview_external_web(
    project_id: UUID,
    url: str = Query(...),
    ticket: str | None = Query(default=None),
    preview_cookie: str | None = Cookie(default=None, alias=PREVIEW_COOKIE_NAME),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Proxy external web browsing requests, stripping framing restrictions (X-Frame-Options, CSP)."""
    service = PreviewService(session)
    raw = ticket or preview_cookie
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing preview token")

    _, token = _unpack_token(raw)
    try:
        claims = service.verify_ticket_or_token(token)
    except PreviewError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc

    if claims.project_id != str(project_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project mismatch")

    target_url = url.strip()
    if not target_url.startswith(("http://", "https://")):
        target_url = f"https://{target_url}"

    import httpx

    client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    request_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) CongruenceBrowser/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        resp = await client.get(target_url, headers=request_headers)
        # Result links commonly bounce through a meta-refresh interstitial; follow
        # it here, otherwise the frame navigates to the real site and gets blocked.
        for _ in range(3):
            if "text/html" not in resp.headers.get("content-type", "").lower():
                break
            bounce = meta_refresh_target(resp.content, str(resp.url))
            if not bounce or bounce == str(resp.url):
                break
            resp = await client.get(bounce, headers=request_headers)
    except Exception as exc:
        await client.aclose()
        return Response(
            content=f"<html><body><h3>Failed to load web page</h3><p>{str(exc)}</p></body></html>".encode("utf-8"),
            status_code=502,
            media_type="text/html",
        )

    content_type = resp.headers.get("content-type", "text/html")
    is_html = "text/html" in content_type.lower()

    resp_headers = {}
    # httpx hands us an already-decoded body, so the upstream's encoding headers
    # must not travel with it. The frame and policy headers would stop the page
    # rendering in the panel, and the CSP would also kill the navigation shim.
    blocked_headers = {
        "x-frame-options",
        "content-security-policy",
        "content-security-policy-report-only",
        "content-encoding",
        "content-length",
        "transfer-encoding",
    }
    for k, v in resp.headers.items():
        if k.lower() not in blocked_headers:
            resp_headers[k] = v

    if is_html:
        raw_body = resp.content
        await client.aclose()
        # Redirects (consent pages, www vs apex, trailing slash) mean the page's own
        # relative links resolve against where the request actually landed.
        base_url = str(resp.url)
        modified = inject_web_navigation(inject_base_tag(raw_body, base_url))
        resp_headers["content-length"] = str(len(modified))
        return Response(
            content=modified,
            status_code=resp.status_code,
            headers=resp_headers,
            media_type=content_type,
        )

    await client.aclose()
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=resp_headers,
        media_type=content_type,
    )


@router.api_route(
    "/projects/{project_id}/preview/{port}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def preview_proxy_route(
    project_id: UUID,
    port: int,
    path: str,
    request: Request,
    ticket: str | None = Query(default=None),
    preview_cookie: str | None = Cookie(default=None, alias=PREVIEW_COOKIE_NAME),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Reverse-proxy an HTTP request to the running devbox port with base tag injection."""
    service = PreviewService(session)
    raw = ticket or preview_cookie
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing preview ticket or cookie")

    _, token = _unpack_token(raw)
    try:
        claims = service.verify_ticket_or_token(token)
    except PreviewError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc

    if claims.project_id != str(project_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project mismatch")

    bridge = await service.get_bridge(claims.box_id, port)
    base_href = f"{settings.app.api_prefix}/projects/{project_id}/preview/{port}/"

    response = await proxy_http_request(
        request=request,
        bridge=bridge,
        path=path,
        base_href=base_href,
    )

    # Set or refresh the preview cookie storing both active port and token
    cookie_value = f"{port}:{token}"
    response.set_cookie(
        key=PREVIEW_COOKIE_NAME,
        value=cookie_value,
        max_age=settings.preview.cookie_ttl_seconds,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.websocket("/projects/{project_id}/preview/{port}/{path:path}")
async def preview_websocket_route(
    websocket: WebSocket,
    project_id: UUID,
    port: int,
    path: str,
    ticket: str | None = Query(default=None),
) -> None:
    """Reverse-proxy a WebSocket connection to the devbox port for HMR."""
    raw = ticket or websocket.cookies.get(PREVIEW_COOKIE_NAME)
    if not raw:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    _, token = _unpack_token(raw)
    from src.infrastructure.database.engine import async_session_factory

    async with async_session_factory() as db:
        service = PreviewService(db)
        try:
            claims = service.verify_ticket_or_token(token)
        except PreviewError:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        if claims.project_id != str(project_id):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        bridge = await service.get_bridge(claims.box_id, port)
        await proxy_websocket(websocket, bridge, path)
