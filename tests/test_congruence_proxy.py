"""Tests for Loopback Reverse Proxy."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.mark.asyncio
async def test_proxy_unreachable_port_returns_clean_fallback():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Request a port where nothing is listening (e.g. 59999)
        resp = await client.get(
            "/preview/sess_123/59999/api/health",
            headers={"Authorization": "Bearer mock_preview_user"},
        )
        # Should return 502 with helpful explanation card
        assert resp.status_code == 502
        assert "Dev server not listening on port 59999" in resp.text
        assert resp.headers.get("x-congruence-proxy") == "active"
