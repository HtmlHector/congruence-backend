"""FastAPI dependency injection providers."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.identity.application.service import IdentityService
from src.contexts.identity.domain.entities import User
from src.infrastructure.auth.clerk import ClerkClaims, clerk_service
from src.infrastructure.database.rls import bind_rls_workspace
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext, set_current_tenant

security_bearer = HTTPBearer(auto_error=False)


async def get_current_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_bearer),
) -> ClerkClaims:
    """Verify Bearer token and return verified Clerk claims."""
    if not credentials:
        # Development fallback
        claims = await clerk_service.verify_token("mock_token")
        return claims

    try:
        claims = await clerk_service.verify_token(credentials.credentials)
        return claims
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {exc}",
        )


async def get_current_user(
    claims: ClerkClaims = Depends(get_current_claims),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Resolve active user via JIT provisioning."""
    identity_svc = IdentityService(session)
    return await identity_svc.get_or_provision_user(claims)


async def get_active_tenant(
    request: Request,
    claims: ClerkClaims = Depends(get_current_claims),
    session: AsyncSession = Depends(get_db),
    x_workspace_id: str | None = Header(None, alias="X-Workspace-Id"),
) -> TenantContext:
    """Resolve and bind active workspace tenant context to active DB transaction."""
    workspace_id = x_workspace_id or claims.org_id or getattr(request.state, "workspace_id", None)
    if not workspace_id:
        workspace_id = "00000000-0000-0000-0000-000000000001"

    tenant = TenantContext(
        workspace_id=workspace_id,
        user_id=claims.user_id,
    )
    set_current_tenant(tenant)
    await bind_rls_workspace(session, tenant)
    return tenant
