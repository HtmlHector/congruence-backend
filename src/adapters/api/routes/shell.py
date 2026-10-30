"""Interactive devbox shell: ticket minting and the streaming WebSocket."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.config.settings import settings
from src.contexts.devbox.application.service import DevboxError, DevboxService
from src.contexts.devbox.domain.entities import DevboxStatus
from src.contexts.devbox.infrastructure.repository import DevboxRepository
from src.contexts.devbox.infrastructure.shell import (
    SESSION_PATTERN,
    kill_session,
    stream_shell,
)
from src.contexts.projects.application.service import ProjectService
from src.contexts.projects.infrastructure.models import ProjectModel
from src.contexts.worktrees.application.service import WorktreeService
from src.contexts.worktrees.domain.entities import slugify
from src.contexts.worktrees.infrastructure.models import WorktreeModel
from src.infrastructure.database.engine import async_session_factory
from src.infrastructure.database.rls import set_rls_workspace
from src.infrastructure.database.session import get_db
from src.security.tickets import TicketError, sign_ticket, verify_ticket
from src.shared.tenant_context import TenantContext

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["Terminals"])

SHELL_PURPOSE = "devbox_shell"


class ShellTicketRequest(BaseModel):
    worktree_id: str | None = None
    # Stable per terminal tab, so reconnecting resumes the same tmux session.
    session: str | None = Field(default=None, pattern=SESSION_PATTERN.pattern)
    cols: int = Field(default=120, ge=20, le=400)
    rows: int = Field(default=32, ge=5, le=200)


class ShellTicketResponse(BaseModel):
    ticket: str
    ws_url: str
    expires_in: int


def _require_workspace(tenant: TenantContext) -> UUID:
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )
    return tenant.workspace_uuid


@router.post("/projects/{project_id}/shell-ticket", response_model=ShellTicketResponse)
async def create_shell_ticket(
    project_id: UUID,
    payload: ShellTicketRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Mint a short-lived ticket for an interactive shell in the project devbox."""
    workspace_id = _require_workspace(tenant)

    project = await ProjectService(session).get_project(project_id, workspace_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    worktree_id: str | None = None
    if payload.worktree_id:
        try:
            worktree_uuid = UUID(payload.worktree_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid worktree id"
            ) from exc
        worktree = await WorktreeService(session).get(worktree_uuid, workspace_id, project_id)
        if worktree is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worktree not found")
        worktree_id = str(worktree.id)

    box = await DevboxService(session).get_live(project_id, workspace_id)
    if box is None or not box.provider_box_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "BOX_UNAVAILABLE", "message": "Project devbox is not running"},
        )

    ticket = sign_ticket(
        {
            "purpose": SHELL_PURPOSE,
            "project_id": str(project_id),
            "workspace_id": str(workspace_id),
            "user_id": tenant.user_id or "system",
            "worktree_id": worktree_id,
            "box_id": box.provider_box_id,
            "session": payload.session,
            "cols": payload.cols,
            "rows": payload.rows,
        },
        secret=settings.gateway.ticket_secret,
        ttl_seconds=settings.gateway.ticket_ttl_seconds,
    )
    return ShellTicketResponse(
        ticket=ticket,
        ws_url=f"{settings.app.api_prefix}/projects/{project_id}/shell",
        expires_in=settings.gateway.ticket_ttl_seconds,
    )


@router.delete(
    "/projects/{project_id}/shell-sessions/{name}", status_code=status.HTTP_204_NO_CONTENT
)
async def close_shell_session(
    project_id: UUID,
    name: str,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Stop the tmux session behind a closed terminal tab.

    Tabs persist across reloads, so closing one must end its processes instead of
    leaving an unreachable agent running in the devbox.
    """
    if not SESSION_PATTERN.match(name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid session name"
        )
    workspace_id = _require_workspace(tenant)

    project = await ProjectService(session).get_project(project_id, workspace_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    box = await DevboxService(session).get_live(project_id, workspace_id)
    if box is not None and box.provider_box_id:
        await kill_session(box.provider_box_id, name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.websocket("/projects/{project_id}/shell")
async def stream_project_shell(
    websocket: WebSocket,
    project_id: UUID,
    ticket: str = Query(...),
) -> None:
    """Stream an interactive shell from the project's devbox container."""
    try:
        verified = verify_ticket(ticket, secret=settings.gateway.ticket_secret)
    except TicketError as exc:
        logger.info("shell_ticket_rejected", project_id=str(project_id), reason=str(exc))
        await websocket.close(code=4401, reason="Invalid or expired ticket")
        return

    claims = verified.claims
    if claims.get("purpose") != SHELL_PURPOSE or claims.get("project_id") != str(project_id):
        await websocket.close(code=4403, reason="Ticket does not match this project")
        return

    try:
        workspace_id = UUID(str(claims.get("workspace_id")))
    except (TypeError, ValueError):
        await websocket.close(code=4401, reason="Invalid ticket")
        return

    container: str | None = None
    cwd = ""
    async with async_session_factory() as session:
        await set_rls_workspace(session, str(workspace_id))
        project = (
            await session.execute(
                select(ProjectModel).where(
                    ProjectModel.id == project_id,
                    ProjectModel.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if project is None:
            await websocket.close(code=4404, reason="Project not found")
            return

        box = await DevboxRepository(session).get_live_for_project(project_id, workspace_id)
        if box is None or not box.provider_box_id:
            await websocket.close(code=4404, reason="Devbox is not running")
            return

        if box.status is DevboxStatus.PAUSED:
            try:
                box = await DevboxService(session).resume(
                    box, actor_id=str(claims.get("user_id") or "system")
                )
            except DevboxError:
                await websocket.close(code=4409, reason="Could not wake the devbox")
                return

        container = box.provider_box_id
        cwd = f"/workspaces/{slugify(project.name)}"
        worktree_id = claims.get("worktree_id")
        if worktree_id:
            worktree = (
                await session.execute(
                    select(WorktreeModel).where(
                        WorktreeModel.id == UUID(str(worktree_id)),
                        WorktreeModel.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if worktree is not None:
                cwd = worktree.path
        await session.commit()

    if not container:
        await websocket.close(code=4404, reason="Devbox is not running")
        return

    await websocket.accept()
    await stream_shell(
        websocket,
        container=container,
        cwd=cwd,
        cols=int(claims.get("cols") or 120),
        rows=int(claims.get("rows") or 32),
        session=claims.get("session"),
    )
