"""GitHub App authentication: JWT signing, installation tokens, and repo slugs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.contexts.integrations.infrastructure.providers.github import GitHubProvider
from src.contexts.integrations.infrastructure.providers.github_app import (
    API_BASE_URL,
    GitHubAppClient,
    repository_slug,
)


def _private_key(tmp_path) -> tuple[object, object]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "github-app.pem"
    path.write_bytes(pem)
    return path, key.public_key()


def test_repository_slug_accepts_slugs_and_urls() -> None:
    assert repository_slug("acme/widgets") == "acme/widgets"
    assert repository_slug("acme/widgets.git") == "acme/widgets"
    assert repository_slug("https://github.com/acme/widgets.git") == "acme/widgets"
    assert repository_slug("git@github.com:acme/widgets.git") == "acme/widgets"


def test_repository_slug_rejects_other_hosts_and_paths() -> None:
    assert repository_slug("https://gitlab.com/acme/widgets") is None
    assert repository_slug("not a repo") is None
    assert repository_slug("") is None


def test_app_is_unconfigured_with_the_placeholder_app_id(tmp_path) -> None:
    path, _ = _private_key(tmp_path)
    assert (
        GitHubAppClient(app_id="gh_app_id_placeholder", private_key_path=path).configured is False
    )


def test_app_is_unconfigured_without_a_key_file(tmp_path) -> None:
    client = GitHubAppClient(app_id="5278115", private_key_path=tmp_path / "missing.pem")
    assert client.configured is False


def test_app_jwt_is_signed_for_the_app_and_expires_within_ten_minutes(tmp_path) -> None:
    path, public_key = _private_key(tmp_path)
    client = GitHubAppClient(app_id="5278115", private_key_path=path)
    assert client.configured is True

    now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    token = client.app_jwt(now=now)

    payload = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert payload["iss"] == "5278115"
    # Backdated by a minute, capped under GitHub's 10 minute ceiling.
    assert payload["iat"] == int(now.timestamp()) - 60
    assert payload["exp"] == int(now.timestamp()) + 540


@pytest.mark.asyncio
async def test_installation_token_is_cached_per_installation(tmp_path) -> None:
    path, _ = _private_key(tmp_path)
    calls: list[str] = []
    expires = (datetime.now(UTC) + timedelta(minutes=50)).isoformat().replace("+00:00", "Z")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/repos/acme/widgets/installation":
            return httpx.Response(200, json={"id": 42})
        if request.url.path == "/app/installations/42/access_tokens":
            return httpx.Response(200, json={"token": "ghs_installation", "expires_at": expires})
        return httpx.Response(404, json={"message": "Not Found"})

    async with httpx.AsyncClient(
        base_url=API_BASE_URL, transport=httpx.MockTransport(handler)
    ) as http:
        client = GitHubAppClient(app_id="5278115", private_key_path=path, http=http)
        assert await client.repository_token("acme/widgets") == "ghs_installation"
        assert await client.repository_token("acme/widgets") == "ghs_installation"

    assert calls.count("POST /app/installations/42/access_tokens") == 1


@pytest.mark.asyncio
async def test_repository_token_is_missing_when_the_app_is_not_installed(tmp_path) -> None:
    path, _ = _private_key(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with httpx.AsyncClient(
        base_url=API_BASE_URL, transport=httpx.MockTransport(handler)
    ) as http:
        client = GitHubAppClient(app_id="5278115", private_key_path=path, http=http)
        assert await client.repository_token("acme/widgets") is None


def test_authorize_url_encodes_a_redirect_uri_with_its_own_query() -> None:
    provider = GitHubProvider(client_id="Iv23liTest", client_secret="secret")
    url = provider.get_authorize_url(
        redirect_uri="http://localhost:3000/oauth/callback?provider=github",
        state="signed-state",
    )

    assert "client_id=Iv23liTest" in url
    # The callback's own query must survive inside the encoded redirect_uri.
    assert (
        "redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Foauth%2Fcallback%3Fprovider%3Dgithub" in url
    )
    assert "scope=repo+read%3Auser+user%3Aemail" in url
    assert "state=signed-state" in url
