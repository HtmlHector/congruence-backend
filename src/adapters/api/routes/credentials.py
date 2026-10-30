"""Agent credential endpoints (per-user, write-only secrets)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.contexts.credentials.application.service import CredentialError, CredentialService
from src.contexts.credentials.domain.entities import AgentCredential, normalize_provider
from src.contexts.terminals.application.service import TerminalError, TerminalService
from src.contexts.terminals.domain.entities import TerminalKind
from src.contexts.worktrees.application.service import WorktreeService
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

router = APIRouter(prefix="/credentials", tags=["Credentials"])

_AGENT_FOR_PROVIDER = {"anthropic": "claude", "openai": "codex"}


class StoreApiKeyRequest(BaseModel):
    provider: str
    value: str
    label: str | None = None


class StartLoginRequest(BaseModel):
    worktree_id: UUID


class CaptureRequest(BaseModel):
    token: str
    secret: Any


class CredentialResponse(BaseModel):
    provider: str
    kind: str
    status: str
    label: str | None
    last_verified_at: str | None
    created_at: str


class LoginResponse(BaseModel):
    terminal_id: str
    agent: str
    capture_token: str
    instructions: str


def _serialize(credential: AgentCredential) -> CredentialResponse:
    return CredentialResponse(
        provider=credential.provider.value,
        kind=credential.kind.value,
        status=credential.status.value,
        label=credential.label,
        last_verified_at=(
            credential.last_verified_at.isoformat() if credential.last_verified_at else None
        ),
        created_at=credential.created_at.isoformat(),
    )


def _require_workspace(tenant: TenantContext) -> UUID:
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )
    return tenant.workspace_uuid


@router.get("", response_model=list[CredentialResponse])
async def list_credentials(
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """List the caller's agent credentials without secrets."""
    workspace_id = _require_workspace(tenant)
    service = CredentialService(session)
    credentials = await service.list_for_user(workspace_id, tenant.user_id or "")
    return [_serialize(credential) for credential in credentials]


@router.post("", response_model=CredentialResponse, status_code=status.HTTP_201_CREATED)
async def store_api_key(
    payload: StoreApiKeyRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Encrypt and store an API key for the caller."""
    workspace_id = _require_workspace(tenant)
    service = CredentialService(session)
    try:
        credential = await service.store_api_key(
            workspace_id=workspace_id,
            user_id=tenant.user_id or "system",
            provider=payload.provider,
            value=payload.value,
            label=payload.label,
        )
    except (CredentialError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _serialize(credential)


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_credential(
    provider: str,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Revoke the caller's credential for a provider."""
    workspace_id = _require_workspace(tenant)
    try:
        normalized = normalize_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    revoked = await CredentialService(session).revoke(
        workspace_id=workspace_id,
        user_id=tenant.user_id or "",
        provider=normalized.value,
        actor_id=tenant.user_id or "system",
    )
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")


@router.post("/{provider}/login", response_model=LoginResponse)
async def start_login(
    provider: str,
    payload: StartLoginRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Open a non-recording login terminal and return its capture token."""
    workspace_id = _require_workspace(tenant)
    try:
        normalized = normalize_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    agent = _AGENT_FOR_PROVIDER.get(normalized.value)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported provider")

    worktree = await WorktreeService(session).get(payload.worktree_id, workspace_id)
    if not worktree:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worktree not found")

    terminal_service = TerminalService(session)
    try:
        terminal = await terminal_service.create(
            worktree,
            tenant.user_id or "system",
            kind=TerminalKind.LOGIN,
            agent=agent,
            title=f"Connect {agent}",
        )
    except TerminalError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    capture_token = await terminal_service.capture_token(terminal, normalized.value)
    return LoginResponse(
        terminal_id=str(terminal.id),
        agent=agent,
        capture_token=capture_token,
        instructions=(
            f"Run `{agent} login` in this terminal, then complete the browser flow. "
            "Congruence captures the credential automatically."
        ),
    )


@router.post("/{provider}/capture", response_model=CredentialResponse)
async def capture_credential(
    provider: str,
    payload: CaptureRequest,
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Internal: box-agent reports a credential captured from a login terminal."""
    try:
        credential = await CredentialService(session).capture_from_login(
            token=payload.token,
            provider=provider,
            secret_payload=payload.secret,
        )
    except CredentialError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    return _serialize(credential)
