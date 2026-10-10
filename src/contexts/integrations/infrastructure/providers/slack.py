"""Slack integration provider — OAuth v2 and channel messaging."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)


class SlackProvider:
    """Slack OAuth v2 and API client."""

    AUTH_URL = "https://slack.com/oauth/v2/authorize"
    TOKEN_URL = "https://slack.com/api/oauth.v2.access"
    API_BASE_URL = "https://slack.com/api"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self.client_id = client_id or settings.integrations.slack_client_id
        self.client_secret = client_secret or settings.integrations.slack_client_secret

    def get_authorize_url(
        self, redirect_uri: str, state: str, scopes: list[str] | None = None
    ) -> str:
        """Construct Slack OAuth v2 authorization URL."""
        scope_str = ",".join(scopes or ["chat:write", "channels:read", "incoming-webhook"])
        params = {
            "client_id": self.client_id,
            "scope": scope_str,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.AUTH_URL}?{query}"

    async def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange temporary code for bot access token."""
        if self.client_id.startswith("slack_client_id_placeholder"):
            return {
                "ok": True,
                "access_token": "xoxb-mock-bot-token-12345",
                "team": {"id": "T12345", "name": "Mock Team"},
                "scope": "chat:write,incoming-webhook",
            }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                raise ValueError(f"Slack OAuth error: {data.get('error')}")
            from typing import cast

            return cast(dict[str, Any], data)

    async def send_message(self, bot_token: str, channel: str, text: str) -> dict[str, Any]:
        """Post a message to a Slack channel."""
        if bot_token.startswith("xoxb-mock"):
            logger.info("slack_mock_message_sent", channel=channel, text=text)
            return {"ok": True, "ts": "1234567890.123456"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self.API_BASE_URL}/chat.postMessage",
                headers={"Authorization": f"Bearer {bot_token}"},
                json={"channel": channel, "text": text},
            )
            response.raise_for_status()
            from typing import cast

            return cast(dict[str, Any], response.json())
