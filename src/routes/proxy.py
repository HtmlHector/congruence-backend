"""Loopback Reverse Proxy for Runner Dev Server Previews."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter(prefix="/preview", tags=["Preview Proxy"])


@router.api_route(
    "/{session_id}/{port}/{subpath:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy_preview(
    session_id: str,
    port: int,
    subpath: str,
    request: Request,
) -> Response:
    """Reverse proxy internal runner port to browser preview pane, stripping frame-blocking CSP headers."""
    target_url = f"http://127.0.0.1:{port}/{subpath}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

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
