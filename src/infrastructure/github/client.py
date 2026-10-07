from __future__ import annotations

import types
from typing import Any, Self

import httpx

GITHUB_API_BASE = "https://api.github.com"


class GitHubClientError(Exception):
    """GitHub API client error."""


class GitHubClient:
    """Async client for GitHub REST API using a personal or OAuth access token."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=httpx.Timeout(20.0),
        )

    async def get_user(self) -> dict[str, Any]:
        """Fetch the authenticated GitHub user profile."""
        response = await self._client.get("/user")
        if response.status_code >= 400:
            raise GitHubClientError(f"Failed to fetch GitHub user: {response.text}")
        return response.json()

    async def list_user_repos(self, per_page: int = 100) -> list[dict[str, Any]]:
        """List repositories accessible to the authenticated user."""
        response = await self._client.get(
            "/user/repos",
            params={
                "sort": "updated",
                "direction": "desc",
                "per_page": per_page,
                "affiliation": "owner,collaborator,organization_member",
            },
        )
        if response.status_code >= 400:
            raise GitHubClientError(f"Failed to list user repositories: {response.text}")
        
        repos = response.json()
        if not isinstance(repos, list):
            return []

        cleaned: list[dict[str, Any]] = []
        for r in repos:
            owner_info = r.get("owner") or {}
            cleaned.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "full_name": r.get("full_name"),
                "owner": owner_info.get("login"),
                "owner_avatar": owner_info.get("avatar_url"),
                "private": r.get("private", False),
                "html_url": r.get("html_url"),
                "clone_url": r.get("clone_url"),
                "default_branch": r.get("default_branch", "main"),
                "description": r.get("description"),
                "updated_at": r.get("updated_at"),
                "pushed_at": r.get("pushed_at"),
                "stargazers_count": r.get("stargazers_count", 0),
                "fork": r.get("fork", False),
            })
        return cleaned

    async def list_pull_requests(
        self, owner: str, repo: str, state: str = "open"
    ) -> list[dict[str, Any]]:
        """List pull requests for a given repository."""
        response = await self._client.get(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "sort": "updated", "direction": "desc"},
        )
        if response.status_code >= 400:
            raise GitHubClientError(f"Failed to list PRs for {owner}/{repo}: {response.text}")
        return response.json()

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str = "main",
        body: str = "",
    ) -> dict[str, Any]:
        """Create a new pull request."""
        response = await self._client.post(
            f"/repos/{owner}/{repo}/pulls",
            json={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
            },
        )
        if response.status_code >= 400:
            raise GitHubClientError(f"Failed to create PR in {owner}/{repo}: {response.text}")
        return response.json()

    async def close(self) -> None:
        """Close the underlying HTTP client session."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        await self.close()
