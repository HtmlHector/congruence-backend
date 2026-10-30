"""GitHub integration provider — OAuth, repository access, and webhook verification."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)


class GitHubProvider:
    """GitHub OAuth and REST API client."""

    AUTH_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    API_BASE_URL = "https://api.github.com"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        self.client_id = client_id or settings.integrations.github_client_id
        self.client_secret = client_secret or settings.integrations.github_client_secret
        self.webhook_secret = webhook_secret or settings.integrations.github_webhook_secret

    def get_authorize_url(
        self, redirect_uri: str, state: str, scopes: list[str] | None = None
    ) -> str:
        """Construct GitHub OAuth authorization redirect URL."""
        scope_str = " ".join(scopes or ["repo", "read:user", "user:email"])
        # urlencode matters: a redirect URI with its own query string would
        # otherwise leak its params into the authorize request.
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "scope": scope_str,
                "state": state,
            }
        )
        return f"{self.AUTH_URL}?{query}"

    async def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange temporary code for OAuth access token."""
        if self.client_id.startswith("gh_client_id_placeholder"):
            return {
                "access_token": "gho_mock_access_token_12345",
                "token_type": "bearer",
                "scope": "repo,read:user",
            }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise ValueError(f"GitHub OAuth error: {data.get('error_description')}")
            from typing import cast

            return cast(dict[str, Any], data)

    def verify_webhook_signature(self, payload: bytes, signature_header: str) -> bool:
        """Verify HMAC-SHA256 signature from GitHub webhook header (X-Hub-Signature-256)."""
        if self.webhook_secret.startswith(
            "gh_webhook_secret_placeholder"
        ) and settings.app.environment in ("development", "test"):
            return True

        if not signature_header or not signature_header.startswith("sha256="):
            return False

        expected = (
            "sha256="
            + hmac.new(
                self.webhook_secret.encode("utf-8"),
                payload,
                hashlib.sha256,
            ).hexdigest()
        )

        return hmac.compare_digest(expected, signature_header)

    async def get_authenticated_user(self, access_token: str) -> dict[str, Any]:
        """Fetch profile of authenticated GitHub user."""
        if access_token.startswith("gho_mock"):
            return {"login": "octocat", "id": 583231, "name": "The Octocat"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self.API_BASE_URL}/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Parabox-Backend-Starter",
                },
            )
            response.raise_for_status()
            from typing import cast

            return cast(dict[str, Any], response.json())

    async def list_repositories(self, access_token: str) -> list[dict[str, Any]]:
        """List repositories accessible to the token."""
        if access_token.startswith("gho_mock"):
            return [
                {"id": 1, "name": "demo-repo", "full_name": "octocat/demo-repo", "private": False}
            ]

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self.API_BASE_URL}/user/repos?per_page=100",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Parabox-Backend-Starter",
                },
            )
            response.raise_for_status()
            from typing import cast

            return cast(list[dict[str, Any]], response.json())
