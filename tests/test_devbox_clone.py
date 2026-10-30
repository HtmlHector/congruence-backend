"""Devbox repository cloning: credential installation and token lookup."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.devbox.application.service import DevboxService, clone_url
from src.contexts.devbox.domain.entities import Devbox, DevboxStatus
from src.contexts.devbox.infrastructure.runner import MemoryRunner
from tests.conftest import TEST_WORKSPACE_ID


@dataclass
class _Project:
    """Minimal ProjectLike for cloning tests."""

    id: UUID
    workspace_id: UUID | None
    name: str = "widgets"
    default_branch: str = "main"
    base_image_ref: str | None = None
    repository_id: str | None = "acme/widgets"
    repo_provider: str = "github"
    setup_command: str | None = None


def _box() -> Devbox:
    return Devbox(
        id=uuid4(),
        workspace_id=UUID(TEST_WORKSPACE_ID),
        project_id=uuid4(),
        provider="memory",
        provider_box_id="cng-test",
        status=DevboxStatus.AWAKE,
    )


@pytest.mark.asyncio
async def test_git_credentials_are_installed_without_leaking_the_token_in_the_url(
    db_session: AsyncSession,
) -> None:
    runner = MemoryRunner()
    service = DevboxService(db_session, runner=runner)
    box = _box()

    await service._install_git_credentials(box, "ghs_installation_token")

    commands = runner.commands[str(box.id)]
    script = commands[-1][-1]
    assert "git config --global credential.helper store" in script
    assert "/root/.git-credentials" in script
    # The token arrives through the environment, not the command line or the URL.
    assert "ghs_installation_token" not in " ".join(commands[-1])


@pytest.mark.asyncio
async def test_repository_token_is_none_without_a_connected_provider(
    db_session: AsyncSession,
) -> None:
    service = DevboxService(db_session, runner=MemoryRunner())
    gitlab_project = _Project(
        id=uuid4(),
        workspace_id=UUID(TEST_WORKSPACE_ID),
        repository_id="acme/widgets",
        repo_provider="gitlab",
    )

    assert await service._repository_token(gitlab_project) is None


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("github", "https://github.com/acme/widgets.git"),
        ("gitlab", "https://gitlab.com/acme/widgets.git"),
        ("bitbucket", "https://bitbucket.org/acme/widgets.git"),
    ],
)
def test_clone_urls_follow_the_repository_provider(provider: str, expected: str) -> None:
    assert clone_url("acme/widgets", provider) == expected


def test_clone_url_passes_through_an_explicit_remote() -> None:
    remote = "git@gitlab.com:acme/widgets.git"
    assert clone_url(remote, "gitlab") == remote


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "credential"),
    [
        ("gitlab", "oauth2:%s@gitlab.com"),
        ("bitbucket", "x-token-auth:%s@bitbucket.org"),
    ],
)
async def test_git_credentials_use_the_provider_username(
    db_session: AsyncSession, provider: str, credential: str
) -> None:
    runner = MemoryRunner()
    service = DevboxService(db_session, runner=runner)
    box = _box()

    await service._install_git_credentials(box, "provider-access-token", provider)

    commands = runner.commands[str(box.id)]
    assert credential in commands[-1][-1]
    assert "provider-access-token" not in " ".join(commands[-1])
