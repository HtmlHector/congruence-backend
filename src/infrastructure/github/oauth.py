"""GitHub OAuth 2.0 authorization and code exchange handler."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode

import httpx

from src.config.settings import settings

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"

_OAUTH_STATE_TTL_SECONDS = 600


def _state_secret() -> bytes:
    return settings.auth.supabase_jwt_secret.encode("utf-8")


def issue_oauth_state(user_id: str, ttl_seconds: int = _OAUTH_STATE_TTL_SECONDS) -> str:
    """Mint a signed, short-lived OAuth state binding a callback to ``user_id``.

    The GitHub OAuth callback arrives as a bare browser redirect with no
    Authorization header, so the initiating user's identity must travel in
    the state parameter — signed so it cannot be forged to store a token
    under someone else's account.
    """
    body = f"{user_id}.{int(time.time()) + ttl_seconds}"
    sig = hmac.new(_state_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{body}.{sig}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def verify_oauth_state(state: str | None) -> str | None:
    """Return the bound user_id when ``state`` is valid and unexpired."""
    if not state:
        return None
    try:
        padded = state + "=" * (-len(state) % 4)
        raw = base64.urlsafe_b64decode(padded).decode("utf-8")
        user_id, expiry, sig = raw.rsplit(".", 2)
    except Exception:
        return None

    body = f"{user_id}.{expiry}"
    expected = hmac.new(_state_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        if int(expiry) < int(time.time()):
            return None
    except ValueError:
        return None
    return user_id or None


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
