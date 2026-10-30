"""Third-party integration and webhook endpoints."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.config.settings import settings
from src.contexts.integrations.application.service import IntegrationsService
from src.contexts.integrations.domain.entities import CredentialType, IntegrationProvider
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/integrations", tags=["Integrations"])

# Providers that host git repositories a project can bind to.
GIT_REPOSITORY_PROVIDERS = frozenset(
    {IntegrationProvider.GITHUB, IntegrationProvider.GITLAB, IntegrationProvider.BITBUCKET}
)


def _repository_summaries(repositories: list[dict[str, Any]]) -> list[RepositorySummary]:
    """Normalize raw provider repositories into the API's repository shape."""
    return [
        RepositorySummary(
            full_name=str(repo.get("full_name") or ""),
            private=bool(repo.get("private")),
            default_branch=repo.get("default_branch"),
        )
        for repo in repositories
        if repo.get("full_name")
    ]


def oauth_callback_url(provider: IntegrationProvider) -> str:
    """Return this API's callback URL, the one registered on the provider."""
    base = settings.app.public_api_url.rstrip("/")
    return f"{base}{settings.app.api_prefix}/integrations/{provider.value}/callback"


def resolve_callback_uri(provider: IntegrationProvider, requested: str | None) -> str:
    """Return the callback to use, ignoring any value the provider would reject.

    GitHub validates ``redirect_uri`` against the app's registered callbacks, so a
    stale client sending its own URL used to fail the whole handshake. Only an
    exact repeat of the configured callback is honored.
    """
    registered = oauth_callback_url(provider)
    if requested and requested.rstrip("/") != registered:
        logger.warning(
            "oauth_callback_override_ignored",
            provider=provider.value,
            requested=requested,
            using=registered,
        )
    return registered


def _web_app_redirect(
    provider: IntegrationProvider, reason: str | None = None
) -> RedirectResponse:
    """Send the browser back to the web app with a machine-readable outcome."""
    params = {"provider": provider.value, "status": "error" if reason else "connected"}
    if reason:
        params["reason"] = reason
    url = f"{settings.app.web_app_url.rstrip('/')}/integrations?{urlencode(params)}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


class RepositorySummary(BaseModel):
    full_name: str
    private: bool
    default_branch: str | None = None


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


@router.get("/github/repositories", summary="List GitHub repositories")
async def list_github_repositories(
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """List repositories the workspace's GitHub App installation can reach."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    svc = IntegrationsService(session)
    return _repository_summaries(await svc.list_github_repositories())


@router.get("/{provider}/repositories", summary="List repositories for a git provider")
async def list_repositories(
    provider: IntegrationProvider,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> list[RepositorySummary]:
    """List repositories the workspace can connect on GitHub, GitLab, or Bitbucket."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )
    if provider not in GIT_REPOSITORY_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{provider.value} does not host git repositories",
        )

    svc = IntegrationsService(session)
    repositories = await svc.list_repositories(provider, tenant.workspace_uuid)
    return _repository_summaries(repositories)


@router.get("/{provider}/authorize", summary="Start OAuth handshake")
async def authorize_integration(
    provider: IntegrationProvider,
    redirect_uri: str | None = Query(
        None, description="Callback URL registered with the OAuth provider; defaults to this API"
    ),
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Generate provider authorization URL with signed state token."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    callback = resolve_callback_uri(provider, redirect_uri)
    svc = IntegrationsService(session)
    try:
        url = svc.get_authorization_url(
            workspace_id=tenant.workspace_uuid,
            user_id=tenant.user_id or "system",
            provider=provider,
            redirect_uri=callback,
        )
        return {"authorization_url": url, "provider": provider.value, "redirect_uri": callback}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/{provider}/callback", summary="Complete OAuth handshake")
async def oauth_callback(
    provider: IntegrationProvider,
    code: str | None = Query(None, description="Temporary code returned by provider"),
    state: str | None = Query(None, description="Signed cryptographic state parameter"),
    redirect_uri: str | None = Query(
        None, description="Must match the URL used to authorize; defaults to this API"
    ),
    error: str | None = Query(None, description="Provider error when the user denies access"),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Complete the handshake and return the browser to the web app.

    The provider sends the browser here directly, so this endpoint answers with a
    redirect rather than JSON. Failures redirect too, with a machine-readable
    ``reason``; details stay in the logs instead of the URL.
    """
    callback = resolve_callback_uri(provider, redirect_uri)
    if error:
        logger.info("oauth_authorization_denied", provider=provider.value, error=error)
        return _web_app_redirect(provider, reason="access_denied")
    if not code or not state:
        return _web_app_redirect(provider, reason="invalid_request")

    svc = IntegrationsService(session)
    try:
        cred = await svc.handle_oauth_callback(state=state, code=code, redirect_uri=callback)
        if cred.provider != provider:
            raise ValueError(f"State provider mismatch: expected {provider}, got {cred.provider}")
    except Exception as exc:
        logger.warning("oauth_callback_failed", provider=provider.value, error=str(exc))
        return _web_app_redirect(provider, reason="oauth_failed")

    logger.info(
        "oauth_callback_connected",
        provider=cred.provider.value,
        workspace_id=str(cred.workspace_id),
    )
    return _web_app_redirect(provider)


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
