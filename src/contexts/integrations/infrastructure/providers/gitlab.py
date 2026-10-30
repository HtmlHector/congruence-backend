"""GitLab integration provider — OAuth, project access, and webhook verification."""

from __future__ import annotations

import hmac
from typing import Any, cast
from urllib.parse import urlencode

import httpx
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)

DEFAULT_SCOPES = ["api", "read_user", "read_repository", "write_repository"]
# Enough for a few thousand projects without paginating forever on a large account.
MAX_PAGES = 5
HTTP_TIMEOUT = 10.0


class GitLabProvider:
    """GitLab.com OAuth and REST API client."""

    AUTH_URL = "https://gitlab.com/oauth/authorize"
    TOKEN_URL = "https://gitlab.com/oauth/token"
    API_BASE_URL = "https://gitlab.com/api/v4"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        self.client_id = client_id or settings.integrations.gitlab_client_id
        self.client_secret = client_secret or settings.integrations.gitlab_client_secret
        self.webhook_secret = webhook_secret or settings.integrations.gitlab_webhook_secret

    @property
    def configured(self) -> bool:
        """True when real OAuth application credentials are present."""
        return not self.client_id.startswith("gitlab_client_id_placeholder")

    def get_authorize_url(
        self, redirect_uri: str, state: str, scopes: list[str] | None = None
    ) -> str:
        """Construct GitLab OAuth authorization URL."""
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes or DEFAULT_SCOPES),
                "state": state,
            }
        )
        return f"{self.AUTH_URL}?{query}"

    async def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange temporary code for an access and refresh token."""
        if not self.configured:
            return {
                "access_token": "glmock-oauth-access-token",
                "token_type": "bearer",
                "expires_in": 7200,
                "refresh_token": "glmock-oauth-refresh-token",
                "scope": "api read_user read_repository write_repository",
            }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            response.raise_for_status()
            data = response.json()

        if "error" in data:
            raise ValueError(f"GitLab OAuth error: {data.get('error_description') or data['error']}")
        return cast(dict[str, Any], data)

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """Rotate an expired access token using its refresh token."""
        if not self.configured:
            return {
                "access_token": "glmock-refreshed-access-token",
                "token_type": "bearer",
                "expires_in": 7200,
                "refresh_token": "glmock-rotated-refresh-token",
                "scope": "api read_user read_repository write_repository",
            }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "redirect_uri": "",
                },
            )
            response.raise_for_status()
            data = response.json()

        if "error" in data:
            raise ValueError(f"GitLab token refresh error: {data.get('error')}")
        return cast(dict[str, Any], data)

    def verify_webhook_token(self, token_header: str) -> bool:
        """Verify the shared secret GitLab sends in ``X-Gitlab-Token``."""
        if self.webhook_secret.startswith(
            "gitlab_webhook_secret_placeholder"
        ) and settings.app.environment in ("development", "test"):
            return True

        if not token_header:
            return False
        return hmac.compare_digest(self.webhook_secret, token_header)

    async def get_authenticated_user(self, access_token: str) -> dict[str, Any]:
        """Fetch the profile of the user who authorized the app."""
        if not self.configured or access_token.startswith("glmock-"):
            return {"username": "mock-gitlab-user", "id": 1001, "name": "Mock GitLab User"}

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                f"{self.API_BASE_URL}/user",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    async def list_repositories(self, access_token: str) -> list[dict[str, Any]]:
        """List projects the user can push to, normalized to the repository shape."""
        if not self.configured or access_token.startswith("glmock-"):
            return [{"full_name": "acme/demo", "private": True, "default_branch": "main"}]

        repositories: list[dict[str, Any]] = []
        async with httpx.AsyncClient(
            base_url=self.API_BASE_URL,
            timeout=HTTP_TIMEOUT,
            headers={"Authorization": f"Bearer {access_token}"},
        ) as client:
            for page in range(1, MAX_PAGES + 1):
                response = await client.get(
                    "/projects",
                    params={
                        "membership": "true",
                        "simple": "true",
                        "per_page": 100,
                        "page": page,
                        "order_by": "last_activity_at",
                    },
                )
                response.raise_for_status()
                projects = cast(list[dict[str, Any]], response.json())
                if not projects:
                    break
                for project in projects:
                    repositories.append(
                        {
                            "full_name": project.get("path_with_namespace"),
                            "private": project.get("visibility") != "public",
                            "default_branch": project.get("default_branch") or "main",
                        }
                    )
                if len(projects) < 100:
                    break

        return repositories
