"""Bitbucket Cloud integration provider — OAuth and repository access.

Bitbucket Cloud has no equivalent of a GitHub App, so repository access comes
from the OAuth token the workspace authorizes. Tokens expire after two hours,
which is why the refresh token is stored alongside the access token.
"""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlencode

import httpx
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)

# Bitbucket declares scopes on the OAuth consumer rather than per request, so the
# consumer registered for this app needs: repository, pullrequest, webhook.
HTTP_TIMEOUT = 10.0
MAX_PAGES = 5


class BitbucketProvider:
    """Bitbucket Cloud OAuth and REST API client."""

    AUTH_URL = "https://bitbucket.org/site/oauth2/authorize"
    TOKEN_URL = "https://bitbucket.org/site/oauth2/access_token"
    API_BASE_URL = "https://api.bitbucket.org/2.0"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self.client_id = client_id or settings.integrations.bitbucket_client_id
        self.client_secret = client_secret or settings.integrations.bitbucket_client_secret

    @property
    def configured(self) -> bool:
        """True when real OAuth consumer credentials are present."""
        return not self.client_id.startswith("bitbucket_client_id_placeholder")

    def get_authorize_url(
        self, redirect_uri: str, state: str, scopes: list[str] | None = None
    ) -> str:
        """Construct Bitbucket authorization URL.

        ``scopes`` is accepted for a uniform provider interface but not sent:
        Bitbucket validates scopes against the registered OAuth consumer.
        """
        _ = scopes
        query = urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "state": state,
                "redirect_uri": redirect_uri,
            }
        )
        return f"{self.AUTH_URL}?{query}"

    async def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange temporary code for an access and refresh token.

        Bitbucket requires HTTP Basic client authentication on the token endpoint
        instead of credentials in the body.
        """
        if not self.configured:
            return {
                "access_token": "bbmock-oauth-access-token",
                "token_type": "bearer",
                "expires_in": 7200,
                "refresh_token": "bbmock-oauth-refresh-token",
                "scopes": "repository pullrequest webhook",
            }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                self.TOKEN_URL,
                auth=(self.client_id, self.client_secret),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            response.raise_for_status()
            data = response.json()

        if "error" in data:
            raise ValueError(f"Bitbucket OAuth error: {data.get('error_description') or data['error']}")
        return cast(dict[str, Any], data)

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """Rotate an expired access token using its refresh token."""
        if not self.configured:
            return {
                "access_token": "bbmock-refreshed-access-token",
                "token_type": "bearer",
                "expires_in": 7200,
                "refresh_token": "bbmock-rotated-refresh-token",
                "scopes": "repository pullrequest webhook",
            }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                self.TOKEN_URL,
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            )
            response.raise_for_status()
            data = response.json()

        if "error" in data:
            raise ValueError(f"Bitbucket token refresh error: {data.get('error')}")
        return cast(dict[str, Any], data)

    async def get_authenticated_user(self, access_token: str) -> dict[str, Any]:
        """Fetch the profile of the user who authorized the app."""
        if not self.configured or access_token.startswith("bbmock-"):
            return {
                "username": "mock-bitbucket-user",
                "uuid": "{00000000-0000-0000-0000-000000000001}",
                "display_name": "Mock Bitbucket User",
            }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                f"{self.API_BASE_URL}/user",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    async def list_repositories(self, access_token: str) -> list[dict[str, Any]]:
        """List repositories the user can reach, following Bitbucket's cursor paging."""
        if not self.configured or access_token.startswith("bbmock-"):
            return [{"full_name": "acme/demo", "private": True, "default_branch": "main"}]

        repositories: list[dict[str, Any]] = []
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, headers={"Authorization": f"Bearer {access_token}"}
        ) as client:
            url: str | None = f"{self.API_BASE_URL}/repositories"
            params: dict[str, Any] | None = {"role": "member", "pagelen": 100}
            for _ in range(MAX_PAGES):
                if not url:
                    break
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = cast(dict[str, Any], response.json())
                for repository in data.get("values", []):
                    default_branch = (repository.get("mainbranch") or {}).get("name")
                    repositories.append(
                        {
                            "full_name": repository.get("full_name"),
                            "private": bool(repository.get("is_private")),
                            "default_branch": default_branch or "main",
                        }
                    )
                # The next page URL already carries the cursor; do not resend params.
                url = data.get("next")
                params = None

        return repositories
