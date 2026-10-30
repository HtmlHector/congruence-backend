"""Browser preview proxy and ticket tests."""

from __future__ import annotations

import asyncio
import gzip
from typing import Any
import pytest
from httpx import AsyncClient

from src.config.settings import settings
from src.contexts.previews.infrastructure.proxy import (
    inject_base_tag,
    inject_web_navigation,
    meta_refresh_target,
)
from src.security.tickets import verify_ticket


async def _create_project(client: AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/projects",
        json={
            "name": "Preview Test Project",
            "repository_id": "acme/preview-app",
            "default_branch": "main",
        },
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


def test_inject_base_tag() -> None:
    html = b"<!DOCTYPE html><html><head><title>App</title></head><body>Hello</body></html>"
    modified = inject_base_tag(html, "/api/v1/projects/123/preview/3000/")
    assert b'<head><base href="/api/v1/projects/123/preview/3000/"><title>' in modified

    html_no_head = b"<div>Bare content</div>"
    modified_no_head = inject_base_tag(html_no_head, "/api/v1/projects/123/preview/3000/")
    assert modified_no_head.startswith(b'<base href="/api/v1/projects/123/preview/3000/">')


def test_meta_refresh_target_follows_only_redirects() -> None:
    bounce = b'<html><head><meta http-equiv="refresh" content="0; URL=\'https://www.wwe.com/\'"></head></html>'
    assert meta_refresh_target(bounce, "https://duckduckgo.com/l/?uddg=x") == "https://www.wwe.com/"

    relative = b'<meta http-equiv="refresh" content="0;url=/next">'
    assert meta_refresh_target(relative, "https://example.com/a/") == "https://example.com/next"

    assert meta_refresh_target(b"<html><head><meta charset='utf-8'></head></html>", "https://a/") is None
    # A slow self-refresh is page behaviour, not a redirect.
    assert meta_refresh_target(b'<meta http-equiv="refresh" content="30; url=/app">', "https://a/") is None


def test_inject_web_navigation_keeps_clicks_in_the_frame() -> None:
    html = b"<html><head><title>Site</title></head><body><a href='/next'>Next</a></body></html>"
    modified = inject_web_navigation(html)

    assert b"congruence:preview-navigate" in modified
    # Loaded before the page's own scripts so the capture listener is registered first.
    assert modified.index(b"__congruencePreviewNav") < modified.index(b"<title>")

    no_head = inject_web_navigation(b"<div>fragment</div>")
    assert no_head.startswith(b"<script>")


@pytest.mark.asyncio
async def test_preview_ticket_minting(client: AsyncClient) -> None:
    project = await _create_project(client)

    response = await client.get(f"/api/v1/projects/{project['id']}/preview-ticket")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "ticket" in data
    assert data["preview_base_url"].endswith(f"/api/v1/projects/{project['id']}/preview")
    assert data["expires_in"] == settings.preview.cookie_ttl_seconds

    claims = verify_ticket(data["ticket"], secret=settings.preview.cookie_secret).claims
    assert claims["purpose"] == "devbox_preview"
    assert claims["project_id"] == project["id"]
    assert claims["workspace_id"] == "00000000-0000-0000-0000-000000000001"
    assert claims["box_id"]


@pytest.mark.asyncio
async def test_preview_ticket_requires_valid_project(client: AsyncClient) -> None:
    response = await client.get("/api/v1/projects/00000000-0000-0000-0000-0000000000ff/preview-ticket")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_preview_ping_port(client: AsyncClient) -> None:
    project = await _create_project(client)
    ticket_resp = await client.get(f"/api/v1/projects/{project['id']}/preview-ticket")
    ticket = ticket_resp.json()["ticket"]

    # When port is not running
    response = await client.get(
        f"/api/v1/projects/{project['id']}/preview/39999/ping",
        params={"ticket": ticket},
    )
    assert response.status_code == 200
    assert response.json() == {"live": False, "port": 39999}


@pytest.mark.asyncio
async def test_preview_proxy_when_server_offline_returns_502(client: AsyncClient) -> None:
    project = await _create_project(client)
    ticket_resp = await client.get(f"/api/v1/projects/{project['id']}/preview-ticket")
    ticket = ticket_resp.json()["ticket"]

    response = await client.get(
        f"/api/v1/projects/{project['id']}/preview/39998/",
        params={"ticket": ticket},
    )
    assert response.status_code == 502
    assert response.json()["error"] == "SERVER_NOT_RUNNING"


@pytest.mark.asyncio
async def test_preview_proxy_live_http_server(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Set provider to memory so bridge connects to local test server
    monkeypatch.setattr(settings.devbox, "provider", "memory")

    # Start a mock upstream HTTP server
    async def handle_upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        response_body = b"<!DOCTYPE html><html><head><title>Test App</title></head><body><h1>Hello from Devbox</h1></body></html>"
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html\r\n"
            b"Content-Length: " + str(len(response_body)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + response_body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream_srv = await asyncio.start_server(handle_upstream, "127.0.0.1", 0)
    mock_port = upstream_srv.sockets[0].getsockname()[1]

    try:
        project = await _create_project(client)
        ticket_resp = await client.get(f"/api/v1/projects/{project['id']}/preview-ticket")
        ticket = ticket_resp.json()["ticket"]

        # 1. Ping should report True
        ping_resp = await client.get(
            f"/api/v1/projects/{project['id']}/preview/{mock_port}/ping",
            params={"ticket": ticket},
        )
        assert ping_resp.status_code == 200
        assert ping_resp.json()["live"] is True

        # 2. Proxy request should inject base tag and set preview cookie
        proxy_resp = await client.get(
            f"/api/v1/projects/{project['id']}/preview/{mock_port}/",
            params={"ticket": ticket},
        )
        assert proxy_resp.status_code == 200
        assert f'<base href="/api/v1/projects/{project["id"]}/preview/{mock_port}/">' in proxy_resp.text
        assert "Hello from Devbox" in proxy_resp.text
        assert "congruence_preview" in proxy_resp.cookies

        # 3. Subsequent request using cookie (without ticket in query)
        cookie_val = proxy_resp.cookies["congruence_preview"]
        client.cookies.set("congruence_preview", cookie_val)
        next_resp = await client.get(
            f"/api/v1/projects/{project['id']}/preview/{mock_port}/subpage"
        )
        assert next_resp.status_code == 200
    finally:
        upstream_srv.close()
        await upstream_srv.wait_closed()


@pytest.mark.asyncio
async def test_preview_external_web(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Start a mock external web server that returns X-Frame-Options: DENY
    async def handle_external(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        response_body = b"<!DOCTYPE html><html><head><title>External Site</title></head><body>External Web Content</body></html>"
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html\r\n"
            b"X-Frame-Options: DENY\r\n"
            b"Content-Security-Policy: frame-ancestors 'none'\r\n"
            b"Content-Length: " + str(len(response_body)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + response_body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    srv = await asyncio.start_server(handle_external, "127.0.0.1", 0)
    ext_port = srv.sockets[0].getsockname()[1]

    try:
        project = await _create_project(client)
        ticket_resp = await client.get(f"/api/v1/projects/{project['id']}/preview-ticket")
        ticket = ticket_resp.json()["ticket"]

        resp = await client.get(
            f"/api/v1/projects/{project['id']}/preview/external",
            params={"url": f"http://127.0.0.1:{ext_port}/", "ticket": ticket},
        )
        assert resp.status_code == 200
        assert "External Web Content" in resp.text
        # Ensure frame blocking headers are stripped
        assert "x-frame-options" not in resp.headers
        assert "content-security-policy" not in resp.headers
        assert f'<base href="http://127.0.0.1:{ext_port}/">' in resp.text
        assert "__congruencePreviewNav" in resp.text
    finally:
        srv.close()
        await srv.wait_closed()


@pytest.mark.asyncio
async def test_preview_external_does_not_relabel_decoded_bodies(client: AsyncClient) -> None:
    """Upstreams compress their HTML; the proxy must not hand back decoded bytes tagged gzip."""
    page = b"<!DOCTYPE html><html><head><title>Packed</title></head><body>Compressed content</body></html>"
    packed = gzip.compress(page)

    async def handle_external(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html\r\n"
            b"Content-Encoding: gzip\r\n"
            b"Content-Length: " + str(len(packed)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + packed
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    srv = await asyncio.start_server(handle_external, "127.0.0.1", 0)
    ext_port = srv.sockets[0].getsockname()[1]

    try:
        project = await _create_project(client)
        ticket = (await client.get(f"/api/v1/projects/{project['id']}/preview-ticket")).json()["ticket"]

        resp = await client.get(
            f"/api/v1/projects/{project['id']}/preview/external",
            params={"url": f"http://127.0.0.1:{ext_port}/", "ticket": ticket},
        )
        assert resp.status_code == 200
        assert "content-encoding" not in resp.headers
        assert "Compressed content" in resp.text
    finally:
        srv.close()
        await srv.wait_closed()
