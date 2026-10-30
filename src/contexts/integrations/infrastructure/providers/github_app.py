"""GitHub App authentication: app JWT, installation tokens, and repository access.

A GitHub App is not installed once per workspace — it is installed on an account
or organisation, and each installation mints short-lived tokens that can read (and
push to) the repositories it was granted. ``owner/repo`` resolves to whichever
installation owns it, so project cloning works for private repositories without
ever storing a long-lived personal token.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)

API_BASE_URL = "https://api.github.com"
USER_AGENT = "Congruence"
# GitHub rejects app JWTs that live longer than 10 minutes.
JWT_LIFETIME = timedelta(minutes=9)
# Backdate by a minute so a slightly fast control-plane clock still validates.
JWT_CLOCK_SKEW = timedelta(seconds=60)
HTTP_TIMEOUT = 15.0

_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def repository_slug(repository_id: str) -> str | None:
    """Return ``owner/repo`` for a slug or a GitHub URL, else None."""
    value = repository_id.strip()
    if _SLUG.match(value):
        return value.removesuffix(".git")
    if value.startswith("https://github.com/"):
        value = value.removeprefix("https://github.com/")
    elif value.startswith("git@github.com:"):
        value = value.removeprefix("git@github.com:")
    else:
        return None
    value = value.removesuffix(".git").strip("/")
    if not _SLUG.match(value):
        return None
    return value


class GitHubAppClient:
    """Signs app JWTs and exchanges them for installation access tokens."""

    def __init__(
        self,
        app_id: str | None = None,
        private_key_path: str | Path | None = None,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id or settings.integrations.github_app_id
        self._key_path = Path(private_key_path or settings.integrations.github_private_key_path)
        self._private_key: str | None = None
        self._http = http
        self._tokens: dict[str, tuple[str, datetime]] = {}

    @property
    def configured(self) -> bool:
        """True when an app id and private key are both available."""
        if self.app_id.startswith("gh_app_id_placeholder") or not self.app_id:
            return False
        return self._key_path.is_file()

    def _key(self) -> str:
        if self._private_key is None:
            self._private_key = self._key_path.read_text(encoding="utf-8")
        return self._private_key

    def app_jwt(self, now: datetime | None = None) -> str:
        """Return a short-lived JWT that authenticates as the app itself."""
        issued_at = now or datetime.now(UTC)
        payload = {
            "iat": int((issued_at - JWT_CLOCK_SKEW).timestamp()),
            "exp": int((issued_at + JWT_LIFETIME).timestamp()),
            "iss": str(self.app_id),
        }
        return jwt.encode(payload, self._key(), algorithm="RS256")

    async def _client(self, token: str) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http
        return httpx.AsyncClient(
            base_url=API_BASE_URL,
            timeout=HTTP_TIMEOUT,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def installation_id_for_repository(self, repository_id: str) -> str | None:
        """Return the installation that owns a repository, if any."""
        slug = repository_slug(repository_id)
        if not slug or not self.configured:
            return None

        client = await self._client(self.app_jwt())
        try:
            response = await client.get(f"/repos/{slug}/installation")
            if response.status_code == 404:
                logger.info("github_app_no_installation", repository=slug)
                return None
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return str(data.get("id")) if data.get("id") is not None else None
        except httpx.HTTPError as exc:
            logger.warning("github_app_installation_lookup_failed", repository=slug, error=str(exc))
            return None
        finally:
            await _close(client, self._http)

    async def installation_token(self, installation_id: str) -> str | None:
        """Mint (or reuse) an access token for one installation."""
        cached = self._tokens.get(installation_id)
        if cached and cached[1] > datetime.now(UTC) + timedelta(minutes=2):
            return cached[0]

        client = await self._client(self.app_jwt())
        try:
            response = await client.post(f"/app/installations/{installation_id}/access_tokens")
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        except httpx.HTTPError as exc:
            logger.warning("github_app_token_failed", installation=installation_id, error=str(exc))
            return None
        finally:
            await _close(client, self._http)

        token = data.get("token")
        if not token:
            return None
        expires_at = _parse_time(data.get("expires_at")) or datetime.now(UTC) + timedelta(
            minutes=55
        )
        self._tokens[installation_id] = (str(token), expires_at)
        return str(token)

    async def repository_token(self, repository_id: str) -> str | None:
        """Return an installation token able to clone ``owner/repo``."""
        installation_id = await self.installation_id_for_repository(repository_id)
        if not installation_id:
            return None
        return await self.installation_token(installation_id)

    async def list_repositories(self) -> list[dict[str, Any]]:
        """List every repository the app installation can reach."""
        if not self.configured:
            return []

        client = await self._client(self.app_jwt())
        try:
            installations = await client.get("/app/installations", params={"per_page": 100})
            installations.raise_for_status()
            repositories: list[dict[str, Any]] = []
            for installation in installations.json():
                installation_id = str(installation.get("id"))
                token = await self.installation_token(installation_id)
                if not token:
                    continue
                repos = await client.get(
                    "/installation/repositories",
                    params={"per_page": 100},
                    headers={"Authorization": f"Bearer {token}"},
                )
                repos.raise_for_status()
                for repo in repos.json().get("repositories", []):
                    repositories.append(
                        {
                            "full_name": repo.get("full_name"),
                            "private": repo.get("private"),
                            "default_branch": repo.get("default_branch"),
                            "installation_id": installation_id,
                        }
                    )
            return repositories
        except httpx.HTTPError as exc:
            logger.warning("github_app_repository_list_failed", error=str(exc))
            return []
        finally:
            await _close(client, self._http)


async def _close(client: httpx.AsyncClient, injected: httpx.AsyncClient | None) -> None:
    if injected is None:
        await client.aclose()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
