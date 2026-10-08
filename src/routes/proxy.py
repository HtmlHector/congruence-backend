"""Loopback Reverse Proxy for Runner Dev Server Previews."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.auth.jwt import decode_jwt_token
from src.infrastructure.auth.tenancy import lane_access
from src.infrastructure.database.session import get_db

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

    access = await lane_access(db, user, lane_id)
    if access is False:
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

    target_url = f"http://127.0.0.1:{port}/{subpath}"
    # Never forward the auth token to the proxied application.
    upstream_query = "&".join(
        part
        for part in (request.url.query or "").split("&")
        if part and not part.startswith("token=")
    )
    if upstream_query:
        target_url = f"{target_url}?{upstream_query}"

    # Extract headers to forward
    excluded_headers = {"host", "content-length"}
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in excluded_headers
    }
    headers["X-Forwarded-Host"] = request.headers.get("host", f"localhost:{port}")
    headers["X-Forwarded-Proto"] = "http"

    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
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
            response_headers["Access-Control-Allow-Origin"] = "*"
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proxy error: {e!s}")
