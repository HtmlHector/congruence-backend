import pytest
from httpx import AsyncClient, ASGITransport
from src.main import app


@pytest.mark.asyncio
async def test_agent_chat_stream_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agents/chat/stream",
            json={
                "prompt": "echo 'hello from test runner'",
                "harness": "Shell",
            },
            headers={"Authorization": "Bearer mock_stream_user"},
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        body = response.text
        assert "data: " in body
        assert "done" in body or "chunk" in body
