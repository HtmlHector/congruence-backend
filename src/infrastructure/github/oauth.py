"""GitHub OAuth 2.0 authorization and code exchange handler."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx

from src.config.settings import settings

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"


class GitHubOAuthError(Exception):
    """GitHub OAuth returned an error."""


class GitHubOAuth:
    """Handles GitHub OAuth 2.0 authorization code flow."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
    ) -> None:
        self._client_id = client_id or settings.github.client_id
        self._client_secret = (
            client_secret or settings.github.client_secret.get_secret_value()
        )
        self._redirect_uri = redirect_uri or settings.github.redirect_uri

    def get_authorize_url(self, state: str | None = None) -> str:
        """Generate the GitHub OAuth authorization URL."""
        state = state or secrets.token_urlsafe(32)
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": "repo,read:user,user:email",
            "state": state,
        }
        return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        """Exchange authorization code for an access token."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.post(
                GITHUB_TOKEN_URL,
                json={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise GitHubOAuthError(data.get("error_description", data["error"]))
            return data

    async def revoke_token(self, access_token: str) -> None:
        """Revoke a user grant/token."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as http:
            await http.delete(
                f"https://api.github.com/applications/{self._client_id}/grant",
                json={"access_token": access_token},
                auth=(self._client_id, self._client_secret),
                headers={"Accept": "application/json"},
            )
