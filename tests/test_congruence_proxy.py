"""Tests for Loopback Reverse Proxy."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_proxy_unreachable_port_returns_clean_fallback(client):
    resp = await client.get(
        "/preview/sess_123/59999/api/health",
        headers={"Authorization": "Bearer mock_preview_user"},
    )
    assert resp.status_code == 502
    assert "Preview is unavailable on the configured runner" in resp.text


@pytest.mark.asyncio
async def test_preview_rejects_unsafe_ports_and_traversal(client):
    response = await client.get("/preview/lane_x/80/index.html", headers={"Authorization": "Bearer mock_preview_user"})
    assert response.status_code == 400
    response = await client.get("/preview/lane_x/3000/../etc/passwd", headers={"Authorization": "Bearer mock_preview_user"})
    assert response.status_code in (400, 422)  # HTTP clients may normalize dot segments before sending
    response = await client.get("/preview/lane_x/70000/index.html", headers={"Authorization": "Bearer mock_preview_user"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_preview_never_forwards_auth_or_token_to_runner_upstream(client):
    upstream = AsyncMock()
    upstream.status_code = 200
    upstream.headers = {"content-type": "text/plain", "set-cookie": "secret=upstream"}
    upstream.content = b"private"
    request_mock = AsyncMock(return_value=upstream)

    with (
        patch("src.routes.proxy.httpx.AsyncClient") as client_cls,
        patch("src.routes.proxy.get_runner") as get_runner,
    ):
        client_cls.return_value.__aenter__.return_value.request = request_mock
        get_runner.return_value.get_preview_target = AsyncMock(return_value="http://127.0.0.1:3000/index.html")
        response = await client.get(
            "/preview/lane_x/3000/index.html?token=mock_preview_user&foo=bar",
            headers={"Authorization": "Bearer mock_preview_user", "Cookie": "session=secret"},
        )

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    call = request_mock.await_args.kwargs
    assert "Authorization" not in call["headers"]
    assert "Cookie" not in call["headers"]
    assert "token=" not in call["url"]
    assert "foo=bar" in call["url"]


@pytest.mark.asyncio
async def test_preview_requires_known_lane_for_non_mock_credentials(client):
    from src.infrastructure.auth.jwt import UserClaims

    with patch("src.routes.proxy.decode_jwt_token", return_value=UserClaims(user_id="user_x", email="x@example.com")):
        response = await client.get("/preview/lane_unknown/3000/", headers={"Authorization": "Bearer valid.token"})
    assert response.status_code == 404
