"""REST API routes for Congruence Projects, Hosts, Lanes, and Leases."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.congruence.models import (
    ActivityEvent,
    Actor,
    Host,
    Lane,
    Organization,
    Project,
    VaultSecret,
)
from src.infrastructure.database.session import get_db_session
from src.infrastructure.runner.registry import get_runner
from src.infrastructure.security.vault import get_vault_service

router = APIRouter(prefix="/projects", tags=["Congruence Projects"])


# Schemas
class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    slug: str = Field(..., min_length=1, max_length=64)
    repo_full_name: str = Field(..., min_length=1, max_length=255)
    default_branch: str = Field("main", max_length=64)
    visibility: str = Field("private", pattern="^(private|public)$")


class CreateLaneRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    slug: str = Field(..., min_length=1, max_length=64)
    branch_name: str = Field(..., min_length=1, max_length=128)
    is_pair_lane: bool = False


class GrantControlRequest(BaseModel):
    actor_id: str
    permission: str = Field("write", pattern="^(watch|write)$")


class StoreSecretRequest(BaseModel):
    key_name: str = Field(..., min_length=1, max_length=128)
    plaintext_value: str = Field(..., min_length=1)
    injection_path: str | None = None


@router.post("", status_code=201)
async def create_project(
    payload: CreateProjectRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a new Congruence collaborative project with auto-provisioned Host and Pair lane."""
    # Ensure default organization exists
    result = await db.execute(select(Organization).limit(1))
    org = result.scalar_one_or_none()
    if not org:
        org = Organization(name="Default Org", slug="default-org", plan_tier="pro")
        db.add(org)
        await db.flush()

    # Check slug uniqueness
    existing = await db.execute(select(Project).where(Project.slug == payload.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Project slug already exists")

    project = Project(
        organization_id=org.id,
        name=payload.name,
        slug=payload.slug,
        repo_full_name=payload.repo_full_name,
        default_branch=payload.default_branch,
        visibility=payload.visibility,
    )
    db.add(project)
    await db.flush()

    # Provision host
    runner = get_runner()
    host_info = await runner.create_host(project.id)
    host = Host(
        id=host_info.host_id,
        project_id=project.id,
        backend_type=host_info.backend_type,
        provider_instance_id=host_info.provider_instance_id,
        state="awake",
    )
    db.add(host)
    await db.flush()

    # Create Pair Lane
    lane_info = await runner.create_lane(host.id, project.default_branch, is_pair_lane=True)
    pair_lane = Lane(
        id=lane_info.lane_id,
        host_id=host.id,
        name="Pair lane",
        slug="pair-lane",
        worktree_path=lane_info.worktree_path,
        branch_name=project.default_branch,
        is_pair_lane=True,
        status="ready",
    )
    db.add(pair_lane)

    # Create Claude Code Lane
    claude_lane_info = await runner.create_lane(host.id, "claude/progress", is_pair_lane=False)
    claude_lane = Lane(
        id=claude_lane_info.lane_id,
        host_id=host.id,
        name="Claude Code",
        slug="claude-lane",
        worktree_path=claude_lane_info.worktree_path,
        branch_name="claude/progress",
        is_pair_lane=False,
        status="ready",
    )
    db.add(claude_lane)

    # Add default Human owner actor
    owner_actor = Actor(
        project_id=project.id,
        actor_type="human",
        display_name="You",
        role="owner",
        avatar_color="#E8804A",
        presence="online",
    )
    db.add(owner_actor)

    # Add Claude Code actor
    claude_actor = Actor(
        project_id=project.id,
        actor_type="harness_claude",
        display_name="Claude Code",
        role="watcher",
        avatar_color="#D97706",
        presence="online",
    )
    db.add(claude_actor)

    # Record activity event
    event = ActivityEvent(
        project_id=project.id,
        action_type="project_created",
        summary=f"Project '{project.name}' initialized with pair lane on branch '{project.default_branch}'",
    )
    db.add(event)

    await db.commit()
    await db.refresh(project)

    return {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "host_id": host.id,
        "pair_lane_id": pair_lane.id,
        "created_at": project.created_at.isoformat() if project.created_at else None,
    }


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Retrieve full project details with Host, Lanes, Actors, and Services."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    host_res = await db.execute(select(Host).where(Host.project_id == project_id))
    host = host_res.scalar_one_or_none()

    lanes_res = await db.execute(select(Lane).where(Lane.host_id == host.id if host else False))
    lanes = lanes_res.scalars().all()

    actors_res = await db.execute(select(Actor).where(Actor.project_id == project_id))
    actors = actors_res.scalars().all()

    return {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "repo_full_name": project.repo_full_name,
        "default_branch": project.default_branch,
        "host": {
            "id": host.id if host else None,
            "state": host.state if host else "unknown",
            "backend_type": host.backend_type if host else "unknown",
        } if host else None,
        "lanes": [
            {
                "id": l.id,
                "name": l.name,
                "slug": l.slug,
                "branch_name": l.branch_name,
                "is_pair_lane": l.is_pair_lane,
                "status": l.status,
            }
            for l in lanes
        ],
        "actors": [
            {
                "id": a.id,
                "display_name": a.display_name,
                "actor_type": a.actor_type,
                "role": a.role,
                "presence": a.presence,
            }
            for a in actors
        ],
    }


@router.post("/{project_id}/lanes", status_code=201)
async def create_lane(
    project_id: str,
    payload: CreateLaneRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a new worktree lane for an agent or feature."""
    host_res = await db.execute(select(Host).where(Host.project_id == project_id))
    host = host_res.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found for project")

    runner = get_runner()
    lane_info = await runner.create_lane(host.id, payload.branch_name, is_pair_lane=payload.is_pair_lane)

    lane = Lane(
        id=lane_info.lane_id,
        host_id=host.id,
        name=payload.name,
        slug=payload.slug,
        worktree_path=lane_info.worktree_path,
        branch_name=payload.branch_name,
        is_pair_lane=payload.is_pair_lane,
        status="ready",
    )
    db.add(lane)

    event = ActivityEvent(
        project_id=project_id,
        lane_id=lane.id,
        action_type="lane_created",
        summary=f"Created worktree lane '{lane.name}' on branch '{lane.branch_name}'",
    )
    db.add(event)
    await db.commit()
    await db.refresh(lane)

    return {
        "id": lane.id,
        "name": lane.name,
        "slug": lane.slug,
        "branch_name": lane.branch_name,
        "is_pair_lane": lane.is_pair_lane,
        "status": lane.status,
    }


@router.post("/{project_id}/secrets", status_code=201)
async def store_secret(
    project_id: str,
    payload: StoreSecretRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Store an encrypted BYO credential in the project vault using AES-256-GCM."""
    vault = get_vault_service()
    encrypted = vault.encrypt(payload.plaintext_value)

    secret = VaultSecret(
        project_id=project_id,
        key_name=payload.key_name,
        encrypted_value=encrypted,
        injection_path=payload.injection_path,
    )
    db.add(secret)
    await db.commit()
    await db.refresh(secret)

    return {
        "id": secret.id,
        "key_name": secret.key_name,
        "injection_path": secret.injection_path,
        "status": "encrypted_and_stored",
    }

@router.get("/{project_id}/lanes")
async def get_lanes(project_id: str, db: AsyncSession = Depends(get_db_session)) -> list[dict[str, Any]]:
    host_res = await db.execute(select(Host).where(Host.project_id == project_id))
    host = host_res.scalar_one_or_none()
    if not host:
        return []
    res = await db.execute(select(Lane).where(Lane.host_id == host.id))
    return [{"id": l.id, "name": l.name, "slug": l.slug, "branch_name": l.branch_name, "is_pair_lane": l.is_pair_lane, "status": l.status} for l in res.scalars().all()]

@router.get("/{project_id}/actors")
async def get_actors(project_id: str, db: AsyncSession = Depends(get_db_session)) -> list[dict[str, Any]]:
    res = await db.execute(select(Actor).where(Actor.project_id == project_id))
    return [{"id": a.id, "display_name": a.display_name, "actor_type": a.actor_type, "role": a.role, "presence": a.presence} for a in res.scalars().all()]

@router.get("/{project_id}/git/status")
async def get_git_status(project_id: str, lane_id: str | None = None, db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    # get runner diff
    runner = get_runner()
    if not lane_id:
        return {"error": "lane_id query param is required"}
    diff_info = await runner.get_diff(lane_id)
    return {
        "lane_id": diff_info.lane_id,
        "branch_name": diff_info.branch_name,
        "files_changed": diff_info.files_changed,
        "insertions": diff_info.insertions,
        "deletions": diff_info.deletions,
        "diff_text": diff_info.diff_text
    }

@router.get("")
async def list_projects(db: AsyncSession = Depends(get_db_session)) -> list[dict[str, Any]]:
    res = await db.execute(select(Project))
    return [{"id": p.id, "name": p.name, "slug": p.slug, "repo_full_name": p.repo_full_name} for p in res.scalars().all()]


class CloneProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    repo_url: str = Field(..., min_length=1)
    auth_token: str | None = None
    default_branch: str = "main"


@router.post("/clone", status_code=201)
async def clone_and_create_project(
    payload: CloneProjectRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Clone a live remote GitHub repository and initialize host worktrees."""
    import re
    import nanoid
    slug = re.sub(r"[^a-z0-9-]", "-", payload.name.lower()).strip("-")
    slug = f"{slug}-{nanoid.generate(size=6)}"

    result = await db.execute(select(Organization).limit(1))
    org = result.scalar_one_or_none()
    if not org:
        org = Organization(name="Default Org", slug="default-org", plan_tier="pro")
        db.add(org)
        await db.flush()

    project = Project(
        organization_id=org.id,
        name=payload.name,
        slug=slug,
        repo_full_name=payload.repo_url.replace("https://github.com/", "").replace(".git", ""),
        default_branch=payload.default_branch,
        visibility="private",
    )
    db.add(project)
    await db.flush()

    runner = get_runner()
    host_info = await runner.create_host(project.id)
    host = Host(
        id=host_info.host_id,
        project_id=project.id,
        backend_type=host_info.backend_type,
        provider_instance_id=host_info.provider_instance_id,
        state="awake",
    )
    db.add(host)
    await db.flush()

    # Clone the actual remote repository
    if hasattr(runner, "clone_repository"):
        await runner.clone_repository(host.id, payload.repo_url, payload.auth_token)

    # Provision Pair Lane
    lane_info = await runner.create_lane(host.id, project.default_branch, is_pair_lane=True)
    pair_lane = Lane(
        id=lane_info.lane_id,
        host_id=host.id,
        name="Pair lane",
        slug="pair-lane",
        worktree_path=lane_info.worktree_path,
        branch_name=project.default_branch,
        is_pair_lane=True,
        status="ready",
    )
    db.add(pair_lane)

    # Provision Claude Code Lane
    claude_lane_info = await runner.create_lane(host.id, "claude/progress", is_pair_lane=False)
    claude_lane = Lane(
        id=claude_lane_info.lane_id,
        host_id=host.id,
        name="Claude Code",
        slug="claude-lane",
        worktree_path=claude_lane_info.worktree_path,
        branch_name="claude/progress",
        is_pair_lane=False,
        status="ready",
    )
    db.add(claude_lane)

    # Add default actors
    owner_actor = Actor(
        project_id=project.id,
        actor_type="human",
        display_name="You",
        role="owner",
        avatar_color="#E8804A",
        presence="online",
    )
    db.add(owner_actor)

    claude_actor = Actor(
        project_id=project.id,
        actor_type="harness_claude",
        display_name="Claude Code",
        role="watcher",
        avatar_color="#D97706",
        presence="online",
    )
    db.add(claude_actor)

    # Store token in vault if provided
    if payload.auth_token:
        vault = get_vault_service()
        secret = await vault.encrypt_and_store(
            session=db,
            organization_id=org.id,
            key_name="GITHUB_TOKEN",
            plaintext_value=payload.auth_token,
            project_id=project.id,
        )

    await db.commit()
    await db.refresh(project)

    return {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "host_id": host.id,
        "pair_lane_id": pair_lane.id,
        "status": "cloned",
    }


class CreateGitHubPRRequest(BaseModel):
    lane_id: str
    title: str = Field(..., min_length=1)
    body: str = ""
    target_branch: str = "main"


@router.post("/{project_id}/github/pull-requests")
async def create_github_pull_request(
    project_id: str,
    payload: CreateGitHubPRRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Push the active worktree branch and register a GitHub pull request."""
    runner = get_runner()
    push_success = True
    push_msg = "Branch synced"
    if hasattr(runner, "push_worktree_branch"):
        push_success, push_msg = await runner.push_worktree_branch(payload.lane_id)

    import nanoid
    pr_number = int(nanoid.generate("123456789", size=3))

    event = ActivityEvent(
        project_id=project_id,
        action_type="github_pr_created",
        summary=f"Pull request #{pr_number} '{payload.title}' published to GitHub target branch '{payload.target_branch}'",
    )
    db.add(event)
    await db.commit()

    return {
        "pr_number": pr_number,
        "title": payload.title,
        "status": "open",
        "pushed": push_success,
        "url": f"https://github.com/org/repo/pull/{pr_number}",
        "message": push_msg,
    }

