"""Third-party integration and webhook endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.contexts.integrations.application.service import IntegrationsService
from src.contexts.integrations.domain.entities import CredentialType, IntegrationProvider
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

router = APIRouter(prefix="/integrations", tags=["Integrations"])


class CustomApiKeyRequest(BaseModel):
    provider: IntegrationProvider = IntegrationProvider.CUSTOM_API_KEY
    api_key: str
    credential_type: CredentialType = CredentialType.API_KEY
    metadata: dict[str, Any] = {}


@router.get("", summary="List workspace integrations")
async def list_integrations(
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """List all connected integrations for active workspace."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    svc = IntegrationsService(session)
    return await svc.list_workspace_integrations(tenant.workspace_uuid)


@router.get("/{provider}/authorize", summary="Start OAuth handshake")
async def authorize_integration(
    provider: IntegrationProvider,
    redirect_uri: str = Query(..., description="Callback URL registered with the OAuth provider"),
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Generate provider authorization URL with signed state token."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    svc = IntegrationsService(session)
    try:
        url = svc.get_authorization_url(
            workspace_id=tenant.workspace_uuid,
            user_id=tenant.user_id or "system",
            provider=provider,
            redirect_uri=redirect_uri,
        )
        return {"authorization_url": url, "provider": provider.value}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/{provider}/callback", summary="Complete OAuth handshake")
async def oauth_callback(
    provider: IntegrationProvider,
    code: str = Query(..., description="Temporary code returned by provider"),
    state: str = Query(..., description="Signed cryptographic state parameter"),
    redirect_uri: str = Query(..., description="Callback URL used during authorization"),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Validate signed state token, exchange code for access token, and persist encrypted credential."""
    svc = IntegrationsService(session)
    try:
        cred = await svc.handle_oauth_callback(state=state, code=code, redirect_uri=redirect_uri)
        if cred.provider != provider:
            raise ValueError(f"State provider mismatch: expected {provider}, got {cred.provider}")
        return {
            "status": "connected",
            "provider": cred.provider.value,
            "workspace_id": str(cred.workspace_id),
            "scopes": cred.scopes,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"OAuth callback failed: {exc}"
        )


@router.delete(
    "/{provider}", status_code=status.HTTP_204_NO_CONTENT, summary="Disconnect integration"
)
async def disconnect_integration(
    provider: IntegrationProvider,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Revoke and delete credentials for an integration provider."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    svc = IntegrationsService(session)
    success = await svc.disconnect_integration(
        workspace_id=tenant.workspace_uuid,
        provider=provider,
        actor_id=tenant.user_id or "system",
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration not found")


@router.post(
    "/custom-api-key", status_code=status.HTTP_201_CREATED, summary="Store custom API token"
)
async def store_custom_key(
    payload: CustomApiKeyRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Store custom third-party API key encrypted at rest."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    svc = IntegrationsService(session)
    cred = await svc.store_custom_credential(
        workspace_id=tenant.workspace_uuid,
        provider=payload.provider,
        raw_secret=payload.api_key,
        credential_type=payload.credential_type,
        metadata=payload.metadata,
        actor_id=tenant.user_id or "system",
    )
    return {
        "id": str(cred.id),
        "provider": cred.provider.value,
        "credential_type": cred.credential_type.value,
    }


# Webhook receiver router
webhook_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/webhooks/{provider}", summary="Inbound provider webhook receiver")
async def receive_provider_webhook(
    provider: IntegrationProvider,
    request: Request,
    session: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
) -> Any:
    """Validate provider signature and process inbound webhook event."""
    body = await request.body()
    headers = dict(request.headers)
    if x_hub_signature_256:
        headers["x-hub-signature-256"] = x_hub_signature_256

    svc = IntegrationsService(session)
    try:
        return await svc.handle_inbound_webhook(provider=provider, headers=headers, body=body)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Webhook processing error: {exc}"
        )
