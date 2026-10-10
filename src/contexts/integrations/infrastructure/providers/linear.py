"""Linear integration provider — OAuth and GraphQL issue tracking."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)


class LinearProvider:
    """Linear OAuth and GraphQL API client."""

    AUTH_URL = "https://linear.app/oauth/authorize"
    TOKEN_URL = "https://api.linear.app/oauth/token"
    API_BASE_URL = "https://api.linear.app/graphql"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self.client_id = client_id or settings.integrations.linear_client_id
        self.client_secret = client_secret or settings.integrations.linear_client_secret

    def get_authorize_url(
        self, redirect_uri: str, state: str, scopes: list[str] | None = None
    ) -> str:
        """Construct Linear OAuth authorization URL."""
        scope_str = ",".join(scopes or ["read", "write", "issues:create"])
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scope_str,
            "state": state,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.AUTH_URL}?{query}"

    async def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange temporary code for Linear access token."""
        if self.client_id.startswith("linear_client_id_placeholder"):
            return {
                "access_token": "lin_mock_token_12345",
                "token_type": "Bearer",
                "expires_in": 31536000,
                "scope": ["read", "write"],
            }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            data = response.json()
            from typing import cast

            return cast(dict[str, Any], data)

    async def get_viewer(self, access_token: str) -> dict[str, Any]:
        """Query current authenticated Linear viewer."""
        if access_token.startswith("lin_mock"):
            return {"id": "usr_mock_1", "name": "Linear User", "email": "user@parabox.so"}

        query = "{ viewer { id name email } }"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.API_BASE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                json={"query": query},
            )
            response.raise_for_status()
            from typing import cast

            data = cast(dict[str, Any], response.json())
            return cast(dict[str, Any], data.get("data", {}).get("viewer", {}))
