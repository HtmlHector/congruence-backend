"""HTTP and WebSocket proxying logic for devbox previews."""

from __future__ import annotations

import re
from typing import AsyncGenerator
import httpx
import structlog
from fastapi import Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
import websockets

from src.contexts.previews.infrastructure.forwarder import DevboxPortBridge

logger = structlog.get_logger(__name__)

# Headers that must not be copied between client and upstream
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

# Response headers that describe how the upstream *encoded* its body. httpx decodes
# the body before we hand it back, so forwarding these makes the browser try to
# decode plain bytes and render nothing at all.
RESPONSE_BODY_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}

_HEAD_REGEX = re.compile(rb"(<head[^>]*>)", re.IGNORECASE)
_META_TAG_REGEX = re.compile(rb"<meta\b[^>]*>", re.IGNORECASE)
_META_REFRESH_REGEX = re.compile(rb"http-equiv\s*=\s*[\"']?refresh", re.IGNORECASE)
_META_CONTENT_REGEX = re.compile(rb"content\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE)


def meta_refresh_target(html_content: bytes, base_href: str) -> str | None:
    """Return the URL a page bounces to via <meta http-equiv="refresh">, if any.

    Search engines and link shims redirect this way instead of with a 3xx, and a
    browser following the tag would jump straight out of the preview frame.
    """
    from urllib.parse import urljoin

    for tag in _META_TAG_REGEX.findall(html_content):
        if not _META_REFRESH_REGEX.search(tag):
            continue
        content_match = _META_CONTENT_REGEX.search(tag)
        if not content_match:
            continue
        raw_directive = content_match.group(1) or content_match.group(2) or b""
        directive = raw_directive.decode("utf-8", "ignore")
        delay_part, _, url_part = directive.partition(";")
        try:
            delay = float(delay_part.strip() or "0")
        except ValueError:
            continue
        if delay > 5:
            continue  # A slow self-refresh is page behaviour, not a redirect.
        url_part = re.sub(r"^\s*url\s*=", "", url_part, flags=re.IGNORECASE).strip().strip("'\"")
        if url_part:
            return urljoin(base_href, url_part)
    return None


def inject_base_tag(html_content: bytes, base_href: str) -> bytes:
    """Inject <base href="..."> into HTML head to resolve relative paths."""
    base_tag = f'<base href="{base_href}">'.encode("utf-8")
    if _HEAD_REGEX.search(html_content):
        return _HEAD_REGEX.sub(rb"\1" + base_tag, html_content, count=1)
    return base_tag + html_content


# Keeps navigation inside the embedded browser: a link click or GET form submit
# becomes a request for this same external-proxy endpoint instead of a jump to
# the site's own origin, which the frame (and its X-Frame-Options) would block.
# Everything is derived from the proxy URL the shim is served from, so it needs no
# project id or ticket baked in.
WEB_NAVIGATION_SHIM = b"""<script>(function(){
  if (window.__congruencePreviewNav) return;
  window.__congruencePreviewNav = true;
  // A URL, not window.location: Location has no searchParams, and relative
  // targets must resolve against the proxy, not the injected <base href>.
  var here = new URL(window.location.href);
  function toProxy(abs) {
    if (!here.searchParams.has('url')) return null;
    var ticket = here.searchParams.get('ticket') || '';
    return here.origin + here.pathname + '?url=' + encodeURIComponent(abs) +
      '&ticket=' + encodeURIComponent(ticket);
  }
  function go(target) {
    try {
      var url = new URL(target, here.href);
      if (url.protocol !== 'http:' && url.protocol !== 'https:') return;
      var next = url;
      if (!(url.pathname === here.pathname && url.searchParams.has('url'))) {
        next = new URL(toProxy(url.href));
      }
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({ type: 'congruence:preview-navigate', url: url.href }, '*');
      }
      window.location.href = next.href;
    } catch (e) {}
  }
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    var raw = a.getAttribute('href') || '';
    if (!raw || raw.charAt(0) === '#' || /^javascript:/i.test(raw)) return;
    e.preventDefault();
    go(a.href);
  }, true);
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form || (form.method && form.method.toLowerCase() !== 'get')) return;
    e.preventDefault();
    try {
      var url = new URL(form.action || here.href, here.href);
      url.search = new URLSearchParams(new FormData(form)).toString();
      go(url.href);
    } catch (err) {}
  }, true);
})();</script>"""


def inject_web_navigation(html_content: bytes) -> bytes:
    """Add the in-frame navigation shim so clicks stay inside the preview."""
    if _HEAD_REGEX.search(html_content):
        return _HEAD_REGEX.sub(rb"\1" + WEB_NAVIGATION_SHIM, html_content, count=1)
    return WEB_NAVIGATION_SHIM + html_content


async def proxy_http_request(
    request: Request,
    bridge: DevboxPortBridge,
    path: str,
    base_href: str,
) -> Response:
    """Forward an HTTP request to the devbox port bridge and return the streamed response."""
    upstream_url = f"http://127.0.0.1:{bridge.local_port}/{path}"
    if request.url.query:
        upstream_url += f"?{request.url.query}"

    # Prepare forwarding headers
    headers = {}
    for key, value in request.headers.items():
        if key.lower() not in HOP_BY_HOP_HEADERS:
            headers[key] = value
    headers["host"] = f"localhost:{bridge.target_port}"
    headers["x-forwarded-for"] = request.client.host if request.client else "127.0.0.1"
    headers["x-forwarded-proto"] = request.url.scheme

    body = await request.body()

    client = httpx.AsyncClient(timeout=60.0, follow_redirects=False)
    try:
        upstream_req = client.build_request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body if body else None,
        )
        upstream_resp = await client.send(upstream_req, stream=True)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError, httpx.NetworkError, httpx.HTTPError) as exc:
        await client.aclose()
        logger.debug("preview_upstream_connect_error", error=str(exc))
        return Response(
            content=b'{"error": "SERVER_NOT_RUNNING", "message": "No server running on specified port"}',
            status_code=502,
            media_type="application/json",
        )

    content_type = upstream_resp.headers.get("content-type", "")
    is_html = "text/html" in content_type.lower()

    resp_headers = {}
    for key, value in upstream_resp.headers.items():
        if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() not in RESPONSE_BODY_HEADERS:
            resp_headers[key] = value

    if is_html:
        # Buffer and inject base tag
        raw_body = await upstream_resp.aread()
        await upstream_resp.aclose()
        await client.aclose()
        modified_body = inject_base_tag(raw_body, base_href)
        resp_headers["content-length"] = str(len(modified_body))
        return Response(
            content=modified_body,
            status_code=upstream_resp.status_code,
            headers=resp_headers,
            media_type=content_type,
        )

    # Stream non-HTML responses
    async def body_stream() -> AsyncGenerator[bytes, None]:
        try:
            async for chunk in upstream_resp.aiter_bytes():
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    return StreamingResponse(
        content=body_stream(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=content_type,
    )


async def proxy_websocket(
    client_ws: WebSocket,
    bridge: DevboxPortBridge,
    path: str,
) -> None:
    """Forward a WebSocket connection to the devbox port bridge (e.g. for Vite / Next HMR)."""
    upstream_url = f"ws://127.0.0.1:{bridge.local_port}/{path}"
    query = client_ws.scope.get("query_string", b"").decode("utf-8")
    if query:
        upstream_url += f"?{query}"

    await client_ws.accept()

    try:
        async with websockets.connect(upstream_url) as upstream_ws:
            async def client_to_upstream() -> None:
                try:
                    while True:
                        msg = await client_ws.receive()
                        if "text" in msg:
                            await upstream_ws.send(msg["text"])
                        elif "bytes" in msg:
                            await upstream_ws.send(msg["bytes"])
                        elif msg.get("type") == "websocket.disconnect":
                            break
                except Exception:
                    pass

            async def upstream_to_client() -> None:
                try:
                    async for msg in upstream_ws:
                        if isinstance(msg, str):
                            await client_ws.send_text(msg)
                        else:
                            await client_ws.send_bytes(msg)
                except Exception:
                    pass

            import asyncio
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
    except Exception as exc:
        logger.debug("websocket_proxy_error", error=str(exc))
    finally:
        try:
            await client_ws.close()
        except Exception:
            pass
