"""Project pull-request publication route tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.contexts.congruence.models import Actor, GitHubAccount, Lane, Project
from src.infrastructure.auth.jwt import UserClaims
from src.routes.projects import CreateGitHubPRRequest, create_github_pull_request


class PRDb:
    def __init__(self, project, lane, account, actor=None):
        self.project = project
        self.lane = lane
        self.account = account
        self.actor = actor
        self.added = []
        self.committed = False
        self.flushed = False

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "congruence_projects" in sql:
            return SimpleNamespace(scalar_one_or_none=lambda: self.project)
        if "congruence_lanes" in sql:
            return SimpleNamespace(scalar_one_or_none=lambda: self.lane)
        if "congruence_github_accounts" in sql:
            return SimpleNamespace(scalar_one_or_none=lambda: self.account)
        if "congruence_actors" in sql:
            return SimpleNamespace(scalar_one_or_none=lambda: self.actor)
        raise AssertionError(f"Unexpected SQL: {sql}")

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        self.flushed = True
        for instance in self.added:
            if isinstance(instance, Actor) and instance.id is None:
                instance.id = "act_test_writer"

    async def commit(self):
        self.committed = True


class PushRunner:
    def __init__(self, result):
        self.result = result

    async def push_worktree_branch(self, lane_id):
        return self.result


@pytest.mark.asyncio
async def test_pr_route_creates_and_persists_real_github_response():
    project = Project(id="proj_test", organization_id="org_test", name="Demo", slug="demo", repo_full_name="acme/demo")
    lane = Lane(id="lane_feature", host_id="host_test", name="Feature", slug="feature", worktree_path="/tmp/worktree", branch_name="feature/real", is_pair_lane=False)
    account = GitHubAccount(id="gh_test", user_id="usr_owner", access_token="ghp_secret", is_active=True)
    db = PRDb(project, lane, account)
    user = UserClaims(user_id="usr_owner", email="owner@example.com")
    pr_result = {"number": 321, "html_url": "https://github.com/acme/demo/pull/321", "state": "open"}

    with (
        patch("src.routes.projects.get_runner", return_value=PushRunner((True, "pushed"))),
        patch("src.routes.projects.GitHubClient") as github_cls,
    ):
        github_cls.return_value.create_pull_request = AsyncMock(return_value=pr_result)
        github_cls.return_value.close = AsyncMock()
        result = await create_github_pull_request(
            "proj_test",
            CreateGitHubPRRequest(lane_id="lane_feature", title="Feature", body="Details", target_branch="main"),
            user,
            db,
        )

    assert result["pr_number"] == 321
    assert result["url"] == pr_result["html_url"]
    assert result["pushed"] is True
    github_cls.return_value.create_pull_request.assert_awaited_once_with(
        owner="acme", repo="demo", title="Feature", head="feature/real", base="main", body="Details"
    )
    assert any(type(row).__name__ == "Publication" and row.pr_number == 321 for row in db.added)
    assert db.committed


@pytest.mark.asyncio
async def test_pr_route_stops_if_branch_push_fails():
    project = Project(id="proj_test", organization_id="org_test", name="Demo", slug="demo", repo_full_name="acme/demo")
    lane = Lane(id="lane_feature", host_id="host_test", name="Feature", slug="feature", worktree_path="/tmp/worktree", branch_name="feature/real", is_pair_lane=False)
    account = GitHubAccount(id="gh_test", user_id="usr_owner", access_token="ghp_secret", is_active=True)
    db = PRDb(project, lane, account)

    with (
        patch("src.routes.projects.get_runner", return_value=PushRunner((False, "push failed"))),
        patch("src.routes.projects.GitHubClient") as github_cls,pytest.raises(HTTPException) as exc
    ):
        await create_github_pull_request(
            "proj_test", CreateGitHubPRRequest(lane_id="lane_feature", title="Feature"),
            UserClaims(user_id="usr_owner", email="owner@example.com"), db,
        )
    assert exc.value.status_code == 502
    github_cls.assert_not_called()
    assert not db.committed


@pytest.mark.asyncio
async def test_pr_route_requires_connected_github_account():
    project = Project(id="proj_test", organization_id="org_test", name="Demo", slug="demo", repo_full_name="acme/demo")
    lane = Lane(id="lane_feature", host_id="host_test", name="Feature", slug="feature", worktree_path="/tmp/worktree", branch_name="feature/real", is_pair_lane=False)
    db = PRDb(project, lane, None)

    with (
        patch("src.routes.projects.GitHubClient") as github_cls,
        pytest.raises(HTTPException) as exc,
    ):
        await create_github_pull_request(
            "proj_test", CreateGitHubPRRequest(lane_id="lane_feature", title="Feature"),
            UserClaims(user_id="usr_owner", email="owner@example.com"), db,
        )
    assert exc.value.status_code == 409
    github_cls.assert_not_called()
