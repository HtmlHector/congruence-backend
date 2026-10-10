"""Object storage presigned URL generation endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.adapters.api.dependencies import get_active_tenant
from src.infrastructure.storage.s3 import storage_service
from src.shared.tenant_context import TenantContext

router = APIRouter(prefix="/storage", tags=["Storage"])


class PresignedUploadRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"


class PresignedDownloadRequest(BaseModel):
    object_key: str


@router.post("/upload-url")
async def get_upload_url(
    payload: PresignedUploadRequest,
    tenant: TenantContext = Depends(get_active_tenant),
) -> Any:
    """Generate presigned S3/MinIO upload URL namespaced by workspace."""
    if not tenant.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    object_key = f"workspaces/{tenant.workspace_id}/{payload.filename}"
    return storage_service.generate_presigned_upload_url(
        object_key=object_key,
        content_type=payload.content_type,
    )


@router.post("/download-url")
async def get_download_url(
    payload: PresignedDownloadRequest,
    tenant: TenantContext = Depends(get_active_tenant),
) -> Any:
    """Generate presigned S3/MinIO download URL."""
    expected_prefix = f"workspaces/{tenant.workspace_id}/"
    if not payload.object_key.startswith(expected_prefix):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access to object denied")

    download_url = storage_service.generate_presigned_download_url(object_key=payload.object_key)
    return {"download_url": download_url, "object_key": payload.object_key}
