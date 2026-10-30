"""Terminal endpoints: create sessions and mint attach tickets."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.config.settings import settings
from src.contexts.terminals.application.service import TerminalError, TerminalService
from src.contexts.terminals.domain.entities import (
    Terminal,
    TerminalKind,
    TerminalStatus,
)
from src.contexts.worktrees.application.service import WorktreeService
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

router = APIRouter(tags=["Terminals"])

_STATUS_CODE_MAP = {
    "TERMINAL_LIMIT": status.HTTP_429_TOO_MANY_REQUESTS,
    "CREDENTIAL_MISSING": status.HTTP_409_CONFLICT,
    "UNSUPPORTED_AGENT": status.HTTP_400_BAD_REQUEST,
    "TERMINAL_CLOSED": status.HTTP_409_CONFLICT,
    "TERMINAL_NOT_READY": status.HTTP_400_BAD_REQUEST,
    "INVALID_TERMINAL": status.HTTP_400_BAD_REQUEST,
}


class CreateTerminalRequest(BaseModel):
    kind: TerminalKind = TerminalKind.SHELL
    agent: str | None = None
    title: str | None = None
    cols: int = Field(default=120, ge=20, le=400)
    rows: int = Field(default=32, ge=5, le=200)


class TerminalResponse(BaseModel):
    id: str
    worktree_id: str
    devbox_id: str
    tmux_session: str | None
    kind: TerminalKind
    agent: str | None
    title: str | None
    status: TerminalStatus
    created_by: str
    recording_enabled: bool
    cols: int
    rows: int
    created_at: str


class AttachResponse(BaseModel):
    ticket: str
    ws_url: str
    expires_in: int


def serialize(terminal: Terminal) -> TerminalResponse:
    return TerminalResponse(
        id=str(terminal.id),
        worktree_id=str(terminal.worktree_id),
        devbox_id=str(terminal.devbox_id),
        tmux_session=terminal.tmux_session,
        kind=terminal.kind,
        agent=terminal.agent,
        title=terminal.title,
        status=terminal.status,
        created_by=terminal.created_by,
        recording_enabled=terminal.recording_enabled,
        cols=terminal.cols,
        rows=terminal.rows,
        created_at=terminal.created_at.isoformat(),
    )


def require_workspace(tenant: TenantContext) -> UUID:
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )
    return tenant.workspace_uuid


def raise_terminal_error(exc: TerminalError) -> None:
    raise HTTPException(
        status_code=_STATUS_CODE_MAP.get(exc.code, status.HTTP_400_BAD_REQUEST),
        detail={"code": exc.code, "message": str(exc)},
    )


@router.post(
    "/worktrees/{worktree_id}/terminals",
    response_model=TerminalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_terminal(
    worktree_id: UUID,
    payload: CreateTerminalRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Open a shell, agent, or login terminal in a worktree."""
    workspace_id = require_workspace(tenant)
    worktree = await WorktreeService(session).get(worktree_id, workspace_id)
    if not worktree:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worktree not found")

    try:
        terminal = await TerminalService(session).create(
            worktree,
            tenant.user_id or "system",
            kind=payload.kind,
            agent=payload.agent,
            title=payload.title,
            cols=payload.cols,
            rows=payload.rows,
        )
    except TerminalError as exc:
        raise_terminal_error(exc)
    return serialize(terminal)


@router.get("/worktrees/{worktree_id}/terminals", response_model=list[TerminalResponse])
async def list_terminals(
    worktree_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """List live terminals for a worktree."""
    workspace_id = require_workspace(tenant)
    worktree = await WorktreeService(session).get(worktree_id, workspace_id)
    if not worktree:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worktree not found")
    terminals = await TerminalService(session).list_for_worktree(worktree_id, workspace_id)
    return [serialize(terminal) for terminal in terminals]


@router.post("/terminals/{terminal_id}/attach", response_model=AttachResponse)
async def attach_terminal(
    terminal_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Mint a short-lived ticket for the terminal WebSocket gateway."""
    workspace_id = require_workspace(tenant)
    service = TerminalService(session)
    terminal = await service.get(terminal_id, workspace_id)
    if not terminal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Terminal not found")

    try:
        ticket = await service.attach(terminal, tenant.user_id or "system")
    except TerminalError as exc:
        raise_terminal_error(exc)
    return AttachResponse(
        ticket=ticket,
        ws_url=f"{settings.app.api_prefix}/terminals/{terminal_id}/stream",
        expires_in=settings.gateway.ticket_ttl_seconds,
    )


@router.delete("/terminals/{terminal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close_terminal(
    terminal_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Close a terminal session."""
    workspace_id = require_workspace(tenant)
    service = TerminalService(session)
    terminal = await service.get(terminal_id, workspace_id)
    if not terminal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Terminal not found")
    await service.close(terminal, tenant.user_id or "system")


@router.post("/terminals/{terminal_id}/restart", response_model=TerminalResponse)
async def restart_terminal(
    terminal_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Recreate a lost terminal session."""
    workspace_id = require_workspace(tenant)
    service = TerminalService(session)
    terminal = await service.get(terminal_id, workspace_id)
    if not terminal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Terminal not found")
    restarted = await service.restart(terminal, tenant.user_id or "system")
    return serialize(restarted)
