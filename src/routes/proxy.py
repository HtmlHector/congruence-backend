"""Loopback Reverse Proxy for Runner Dev Server Previews."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.contexts.congruence.models import Host, Lane
from src.infrastructure.auth.jwt import decode_jwt_token
from src.infrastructure.auth.tenancy import lane_access
from src.infrastructure.database.session import get_db
from src.infrastructure.runner.registry import get_runner

router = APIRouter(prefix="/preview", tags=["Preview Proxy"])


def _extract_token(request: Request) -> str | None:
    """Read a bearer token from the query string or Authorization header.

    Preview URLs load inside iframes, which cannot set request headers, so
    ``?token=`` is supported for that path.
    """
    token = request.query_params.get("token")
    if token:
        return token
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


async def _require_preview_access(
    request: Request, db: AsyncSession, lane_id: str
) -> None:
    """Authenticate the caller and authorize preview access for the lane."""
    token = _extract_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        user = decode_jwt_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    if settings.auth.allow_dev_tokens and token.startswith(("mock_", "test_")):
        return

    access = await lane_access(db, user, lane_id)
    if access is not True:
        raise HTTPException(status_code=404, detail="Not found")


@router.api_route(
    "/{session_id}/{port}/{subpath:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy_preview(
    request: Request,
    session_id: str,
    port: int,
    subpath: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Reverse proxy internal runner port to browser preview pane, stripping frame-blocking CSP headers.

    Tenancy: ``session_id`` is the owning lane (optionally ``sess_``-prefixed);
    callers may only preview lanes they have access to.
    """
    lane_id = session_id.removeprefix("sess_")
    await _require_preview_access(request, db, lane_id)
    if not 1024 <= port <= 65535:
        raise HTTPException(status_code=400, detail="Preview port must be between 1024 and 65535")
    if any(part == ".." for part in subpath.split("/")) or "\x00" in subpath:
        raise HTTPException(status_code=400, detail="Invalid preview path")

    lane = (await db.execute(select(Lane).where(Lane.id == lane_id))).scalar_one_or_none()
    if lane is None:
        raise HTTPException(status_code=404, detail="Preview lane not found")
    host = (await db.execute(select(Host).where(Host.id == lane.host_id))).scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Preview host not found")

    runner = get_runner()
    try:
        target_url = await runner.get_preview_target(lane_id, port, subpath)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail="Preview routing is unsupported by this runner") from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail="Preview is unavailable on the configured runner") from exc

    upstream_query = urlencode(
        [(key, value) for key, value in parse_qsl(request.url.query, keep_blank_values=True) if key.lower() != "token"]
    )
    if upstream_query:
        target_url = f"{target_url}?{upstream_query}"

    # Deliberately do not forward browser credentials, host-routing headers, or
    # proxy credentials to the untrusted application running in the workspace.
    excluded_headers = {"host", "content-length", "authorization", "cookie", "proxy-authorization", "x-forwarded-host", "x-forwarded-proto"}
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in excluded_headers
    }
    headers["X-Forwarded-Proto"] = "https"

    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body if body else None,
            )

            # Strip restrictive framing headers so preview iframe can render
            response_headers = dict(resp.headers)
            response_headers.pop("x-frame-options", None)
            response_headers.pop("content-security-policy", None)
            response_headers.pop("content-encoding", None)

            # Allow iframe embedding
            response_headers.pop("set-cookie", None)
            response_headers.pop("access-control-allow-origin", None)
            response_headers["X-Congruence-Proxy"] = "active"

            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=response_headers,
                media_type=resp.headers.get("content-type"),
            )
    except httpx.ConnectError:
        return Response(
            content=f"""
            <!DOCTYPE html>
            <html>
              <head>
                <style>
                  body {{ font-family: monospace; background: #0A0A0C; color: #8F9099; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
                  .card {{ border: 1px solid #222228; padding: 24px; border-radius: 8px; background: #121216; text-align: center; }}
                  h3 {{ color: #F0F0F3; margin-top: 0; }}
                </style>
              </head>
              <body>
                <div class="card">
                  <h3>Dev server not listening on port {port}</h3>
                  <p>Start your dev server from the terminal or click "Run dev" in the workspace deck.</p>
                </div>
              </body>
            </html>
            """,
            status_code=502,
            headers={"X-Congruence-Proxy": "active"},
            media_type="text/html",
        )
    except httpx.TimeoutException:
        return PlainTextResponse("Preview server timed out", status_code=504)
    except Exception as e:
        raise HTTPException(status_code=502, detail="Preview request failed") from e
