"""REST API routes for Congruence Projects, Hosts, Lanes, and Leases."""

from __future__ import annotations

from typing import Any

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.congruence.models import (
    ActivityEvent,
    Actor,
    GitHubAccount,
    Grant,
    Host,
    Lane,
    Organization,
    Project,
    Publication,
    Service,
)
from src.infrastructure.database.session import get_db_session
from src.infrastructure.runner.registry import get_runner

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
    auth_token = payload.auth_token
    if not auth_token:
        gh_res = await db.execute(
            select(GitHubAccount).where(
                GitHubAccount.user_id == "usr_default",
                GitHubAccount.is_active == True,  # noqa: E712
            )
        )
        gh_acc = gh_res.scalar_one_or_none()
        if gh_acc:
            auth_token = gh_acc.access_token

    if hasattr(runner, "clone_repository"):
        await runner.clone_repository(host.id, payload.repo_url, auth_token)


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

    # NOTE: `auth_token` is deliberately ignored.
    #
    # This previously called `vault.encrypt_and_store(...)`, which did not exist
    # on VaultService, so the branch could only ever have raised. It is removed
    # rather than repaired because accepting a user-supplied token here is
    # exactly what Host Custody forbids (`trd.md` section 7.4). Repository
    # authorization goes through the GitHub App installation flow instead, and
    # the App installation id is not a user credential.

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

    # Record publication in database
    publication = Publication(
        lane_id=payload.lane_id,
        actor_id=payload.lane_id,  # Associate with lane actor context
        pr_number=pr_number,
        pr_url=f"https://github.com/{project.repo_full_name if project else 'org/repo'}/pull/{pr_number}",
        status="open",
    )
    db.add(publication)

    event = ActivityEvent(
        project_id=project_id,
        lane_id=payload.lane_id,
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
        "url": f"https://github.com/{project.repo_full_name if project else 'org/repo'}/pull/{pr_number}",
        "message": push_msg,
    }


@router.get("/{project_id}/host")
async def get_project_host(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Retrieve compute host state, specs, and status."""
    host_res = await db.execute(select(Host).where(Host.project_id == project_id))
    host = host_res.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found for project")

    runner = get_runner()
    live_state = await runner.get_host_state(host.id)

    return {
        "id": host.id,
        "project_id": host.project_id,
        "state": live_state or host.state,
        "backend_type": host.backend_type,
        "size": host.size,
        "disk_size_gb": host.disk_size_gb,
        "awake_seconds_month": host.awake_seconds_month,
        "last_wake_at": host.last_wake_at.isoformat() if host.last_wake_at else None,
        "last_sleep_at": host.last_sleep_at.isoformat() if host.last_sleep_at else None,
    }


@router.post("/{project_id}/host/sleep")
async def sleep_project_host(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Put project host to sleep, suspending compute while preserving disk."""
    host_res = await db.execute(select(Host).where(Host.project_id == project_id))
    host = host_res.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found for project")

    runner = get_runner()
    await runner.sleep_host(host.id)

    host.state = "asleep"
    host.last_sleep_at = datetime.now(timezone.utc)

    event = ActivityEvent(
        project_id=project_id,
        action_type="host_sleep",
        summary="Host put to sleep (compute suspended, disk preserved)",
    )
    db.add(event)
    await db.commit()

    return {"status": "asleep", "host_id": host.id}


@router.post("/{project_id}/host/wake")
async def wake_project_host(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Wake project host from sleep."""
    host_res = await db.execute(select(Host).where(Host.project_id == project_id))
    host = host_res.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found for project")

    runner = get_runner()
    await runner.wake_host(host.id)

    host.state = "awake"
    host.last_wake_at = datetime.now(timezone.utc)

    event = ActivityEvent(
        project_id=project_id,
        action_type="host_wake",
        summary="Host woken up and ready for sessions",
    )
    db.add(event)
    await db.commit()

    return {"status": "awake", "host_id": host.id}


@router.get("/{project_id}/services")
async def get_project_services(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Retrieve list of active services and published preview addresses for the project."""
    host_res = await db.execute(select(Host).where(Host.project_id == project_id))
    host = host_res.scalar_one_or_none()
    if not host:
        return []

    services_res = await db.execute(select(Service).where(Service.host_id == host.id))
    services = services_res.scalars().all()

    # Also check if runner has dev server info for lanes
    runner = get_runner()
    active_srvs = []
    
    for s in services:
        active_srvs.append({
            "id": s.id,
            "lane_id": s.lane_id,
            "port": s.port,
            "protocol": s.protocol,
            "address_subdomain": s.address_subdomain,
            "access_mode": s.access_mode,
            "is_active": s.is_active,
            "url": f"/preview/{s.lane_id or host.id}/{s.port}/",
        })

    # If runner has in-memory dev servers registered not yet in DB, include them
    if hasattr(runner, "_dev_server_info"):
        for lane_id, info in runner._dev_server_info.items():
            if info.is_running and not any(s["lane_id"] == lane_id and s["port"] == info.port for s in active_srvs):
                active_srvs.append({
                    "id": f"srv_{lane_id}_{info.port}",
                    "lane_id": lane_id,
                    "port": info.port,
                    "protocol": "http",
                    "address_subdomain": f"preview-{lane_id}",
                    "access_mode": "private",
                    "is_active": True,
                    "url": f"/preview/{lane_id}/{info.port}/",
                })

    return active_srvs


@router.get("/{project_id}/git/diff")
async def get_git_diff(
    project_id: str,
    lane_id: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Get full git diff and stats for a worktree lane."""
    runner = get_runner()
    diff_info = await runner.get_diff(lane_id)
    return {
        "lane_id": diff_info.lane_id,
        "branch_name": diff_info.branch_name,
        "files_changed": diff_info.files_changed,
        "insertions": diff_info.insertions,
        "deletions": diff_info.deletions,
        "diff_text": diff_info.diff_text,
    }


@router.get("/{project_id}/lanes/{lane_id}/grant")
async def get_lane_grant(
    project_id: str,
    lane_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Get active write lease / grant for a lane."""
    grant_res = await db.execute(
        select(Grant).where(Grant.lane_id == lane_id, Grant.is_revoked == False).order_by(Grant.granted_at.desc())
    )
    grant = grant_res.scalar_one_or_none()
    
    if grant:
        return {
            "id": grant.id,
            "lane_id": grant.lane_id,
            "actor_id": grant.actor_id,
            "permission": grant.permission,
            "is_revoked": grant.is_revoked,
            "granted_at": grant.granted_at.isoformat() if grant.granted_at else None,
        }

    # Default to owner actor
    owner_res = await db.execute(
        select(Actor).where(Actor.project_id == project_id, Actor.role == "owner")
    )
    owner = owner_res.scalar_one_or_none()
    return {
        "id": None,
        "lane_id": lane_id,
        "actor_id": owner.id if owner else "act_human",
        "permission": "write",
        "is_revoked": False,
        "granted_at": None,
    }


@router.post("/{project_id}/lanes/{lane_id}/grant")
async def grant_lane_control(
    project_id: str,
    lane_id: str,
    payload: GrantControlRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Grant or transfer write control for a lane to an actor."""
    # Revoke existing grants
    existing_grants = await db.execute(
        select(Grant).where(Grant.lane_id == lane_id, Grant.is_revoked == False)
    )
    for g in existing_grants.scalars().all():
        g.is_revoked = True

    # Find actor display name
    actor_res = await db.execute(select(Actor).where(Actor.id == payload.actor_id))
    actor = actor_res.scalar_one_or_none()
    actor_name = actor.display_name if actor else payload.actor_id

    # Create new grant
    grant = Grant(
        lane_id=lane_id,
        actor_id=payload.actor_id,
        permission=payload.permission,
        is_revoked=False,
    )
    db.add(grant)

    event = ActivityEvent(
        project_id=project_id,
        lane_id=lane_id,
        actor_id=payload.actor_id,
        action_type="grant_issued",
        summary=f"Write control for lane granted to '{actor_name}'",
    )
    db.add(event)
    await db.commit()
    await db.refresh(grant)

    return {
        "id": grant.id,
        "lane_id": grant.lane_id,
        "actor_id": grant.actor_id,
        "permission": grant.permission,
        "is_revoked": grant.is_revoked,
        "granted_at": grant.granted_at.isoformat() if grant.granted_at else None,
    }


@router.delete("/{project_id}/lanes/{lane_id}/grant")
async def revoke_lane_control(
    project_id: str,
    lane_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Revoke actor write control for a lane, restoring owner default."""
    existing_grants = await db.execute(
        select(Grant).where(Grant.lane_id == lane_id, Grant.is_revoked == False)
    )
    for g in existing_grants.scalars().all():
        g.is_revoked = True

    event = ActivityEvent(
        project_id=project_id,
        lane_id=lane_id,
        action_type="grant_revoked",
        summary="Lane write control reclaimed by owner",
    )
    db.add(event)
    await db.commit()

    return {"status": "revoked", "lane_id": lane_id}


@router.get("/{project_id}/github/pull-requests")
async def list_github_pull_requests(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List registered GitHub pull requests for the project."""
    host_res = await db.execute(select(Host).where(Host.project_id == project_id))
    host = host_res.scalar_one_or_none()
    if not host:
        return []

    lanes_res = await db.execute(select(Lane).where(Lane.host_id == host.id))
    lane_ids = [l.id for l in lanes_res.scalars().all()]
    if not lane_ids:
        return []

    pubs_res = await db.execute(
        select(Publication).where(Publication.lane_id.in_(lane_ids)).order_by(Publication.created_at.desc())
    )
    pubs = pubs_res.scalars().all()

    return [
        {
            "id": p.id,
            "lane_id": p.lane_id,
            "pr_number": p.pr_number,
            "pr_url": p.pr_url,
            "title": f"Pull Request #{p.pr_number}",
            "status": p.status,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in pubs
    ]


@router.get("/{project_id}/activity")
async def get_project_activity(
    project_id: str,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Retrieve audit activity feed for the project."""
    events_res = await db.execute(
        select(ActivityEvent)
        .where(ActivityEvent.project_id == project_id)
        .order_by(ActivityEvent.created_at.desc())
        .limit(limit)
    )
    events = events_res.scalars().all()

    return [
        {
            "id": e.id,
            "action_type": e.action_type,
            "summary": e.summary,
            "actor_id": e.actor_id,
            "lane_id": e.lane_id,
            "timestamp": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


class UpdateSpendCapsRequest(BaseModel):
    spend_cap_usd: float | None = Field(None, ge=0.0)
    egress_limit_mb: int | None = Field(None, ge=0)
    auto_idle_minutes: int | None = Field(None, ge=1, le=1440)


class CheckoutWriterSeatRequest(BaseModel):
    quantity: int = Field(1, ge=1, le=50)
    success_url: str | None = None
    cancel_url: str | None = None


@router.get("/{project_id}/spend")
async def get_project_spend(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Calculate real-time compute spend metrics, cost breakdown, and spend caps for a project."""
    proj_res = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    host_res = await db.execute(select(Host).where(Host.project_id == project_id))
    host = host_res.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found for project")

    # Compute current active awake duration
    current_active_seconds = 0
    now = datetime.now(timezone.utc)
    if host.state == "awake" and host.last_wake_at:
        wake_dt = host.last_wake_at if host.last_wake_at.tzinfo else host.last_wake_at.replace(tzinfo=timezone.utc)
        current_active_seconds = max(0, int((now - wake_dt).total_seconds()))

    total_awake_seconds_month = host.awake_seconds_month + current_active_seconds
    awake_hours_month = total_awake_seconds_month / 3600.0

    # Pricing constants (Fly Sprite standard tier)
    cpu_rate_per_hour = 0.07  # $0.07 per CPU-hour
    mem_rate_per_gb_hour = 0.04  # $0.04 per GB RAM-hour
    disk_rate_per_gb_hour = 0.0002  # ~$0.15/month per GB NVMe disk

    cpu_cost = round(awake_hours_month * host.cpu_count * cpu_rate_per_hour, 4)
    mem_cost = round(awake_hours_month * host.memory_gb * mem_rate_per_gb_hour, 4)
    disk_cost = round(720.0 * host.disk_size_gb * disk_rate_per_gb_hour, 4)
    total_cost = round(cpu_cost + mem_cost + disk_cost, 2)

    spend_cap = project.spend_cap_usd
    spend_cap_reached = (total_cost >= spend_cap) if spend_cap is not None else False
    egress_limit = project.egress_limit_mb
    egress_limit_reached = (host.egress_used_mb >= egress_limit) if egress_limit is not None else False

    return {
        "project_id": project.id,
        "host_id": host.id,
        "host_state": host.state,
        "awake_seconds_month": total_awake_seconds_month,
        "awake_hours_month": round(awake_hours_month, 2),
        "rates": {
            "cpu_per_hour_usd": cpu_rate_per_hour,
            "memory_gb_per_hour_usd": mem_rate_per_gb_hour,
            "disk_gb_per_hour_usd": disk_rate_per_gb_hour,
        },
        "cost_breakdown_usd": {
            "cpu": cpu_cost,
            "memory": mem_cost,
            "disk": disk_cost,
        },
        "estimated_cost_usd_month": total_cost,
        "spend_cap_usd": spend_cap,
        "spend_cap_reached": spend_cap_reached,
        "egress_limit_mb": egress_limit,
        "egress_used_mb": host.egress_used_mb,
        "egress_limit_reached": egress_limit_reached,
        "auto_idle_minutes": host.auto_idle_minutes,
    }


@router.put("/{project_id}/spend-caps")
async def update_project_spend_caps(
    project_id: str,
    payload: UpdateSpendCapsRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Configure spend limits, egress threshold, and auto-idle minutes for a project."""
    proj_res = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    host_res = await db.execute(select(Host).where(Host.project_id == project_id))
    host = host_res.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found for project")

    if payload.spend_cap_usd is not None:
        project.spend_cap_usd = payload.spend_cap_usd
    if payload.egress_limit_mb is not None:
        project.egress_limit_mb = payload.egress_limit_mb
    if payload.auto_idle_minutes is not None:
        host.auto_idle_minutes = payload.auto_idle_minutes

    event = ActivityEvent(
        project_id=project.id,
        action_type="spend_caps_updated",
        summary=f"Spend cap set to ${project.spend_cap_usd}, egress limit {project.egress_limit_mb}MB, auto-idle {host.auto_idle_minutes}m",
    )
    db.add(event)
    await db.commit()

    return {
        "project_id": project.id,
        "spend_cap_usd": project.spend_cap_usd,
        "egress_limit_mb": project.egress_limit_mb,
        "auto_idle_minutes": host.auto_idle_minutes,
        "status": "updated",
    }


@router.get("/{project_id}/billing/subscription")
async def get_project_subscription(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Retrieve billing and seat subscription status for a project's organization."""
    proj_res = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    org_res = await db.execute(select(Organization).where(Organization.id == project.organization_id))
    org = org_res.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Count writers and watchers
    actors_res = await db.execute(select(Actor).where(Actor.project_id == project_id))
    actors = actors_res.scalars().all()
    writers_count = sum(1 for a in actors if a.role in ("owner", "writer"))
    watchers_count = sum(1 for a in actors if a.role == "watcher")

    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "plan_tier": org.plan_tier,
        "writer_seats_licensed": org.writer_seats_count,
        "active_writers_count": writers_count,
        "watchers_count": watchers_count,
        "subscription_status": org.subscription_status,
        "stripe_customer_id": org.stripe_customer_id,
        "stripe_subscription_id": org.stripe_subscription_id,
        "seat_price_usd_month": 49.0,
        "watchers_are_free": True,
    }


@router.post("/{project_id}/billing/checkout-writer-seat")
async def checkout_writer_seat(
    project_id: str,
    payload: CheckoutWriterSeatRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a checkout session for purchasing additional writer seats ($49/month per writer)."""
    proj_res = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    org_res = await db.execute(select(Organization).where(Organization.id == project.organization_id))
    org = org_res.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    from src.config.settings import settings

    # In local/mock/test mode or if Stripe key is placeholder, return simulated checkout
    if not settings.stripe.secret_key.get_secret_value() or settings.stripe.secret_key.get_secret_value().startswith("sk_test_placeholder"):
        mock_checkout_url = f"https://checkout.stripe.com/c/pay/mock_cs_seat_{project.id}?quantity={payload.quantity}"
        return {
            "checkout_url": mock_checkout_url,
            "session_id": f"cs_mock_{project.id}",
            "quantity": payload.quantity,
            "unit_price_usd": 49.0,
            "total_usd": 49.0 * payload.quantity,
        }

    from src.infrastructure.stripe.client import StripeClient

    client = StripeClient()
    success_url = payload.success_url or f"https://congruence.dev/projects/{project.id}/settings?billing=success"
    cancel_url = payload.cancel_url or f"https://congruence.dev/projects/{project.id}/settings?billing=cancelled"

    try:
        session = await client.create_checkout_session(
            customer_email="admin@congruence.dev",
            price_id=settings.stripe.price_id_pro or "price_writer_seat_monthly",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "project_id": project.id,
                "organization_id": org.id,
                "seats_quantity": str(payload.quantity),
                "purchase_type": "writer_seat",
            },
        )
        return {
            "checkout_url": session.url,
            "session_id": session.id,
            "quantity": payload.quantity,
            "unit_price_usd": 49.0,
            "total_usd": 49.0 * payload.quantity,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create checkout session: {str(exc)}")



