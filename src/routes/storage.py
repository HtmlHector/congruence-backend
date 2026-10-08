"""Storage routes for client-direct uploads, downloads, and artifact management."""

from __future__ import annotations

import re
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.auth.dependencies import (
    check_workspace_membership,
    get_current_user,
)
from src.infrastructure.auth.jwt import UserClaims
from src.infrastructure.database.session import get_db
from src.infrastructure.storage.s3 import ObjectStorage

router = APIRouter(prefix="/storage", tags=["storage"])


class UploadUrlRequest(BaseModel):
    workspace_id: str
    file_name: str = Field(..., min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream")
    folder: str = Field(default="assets", max_length=50)


def sanitize_filename(name: str) -> str:
    clean = re.sub(r"[^\w\.\-]", "_", name)
    return clean.strip("._") or "file.bin"


async def _require_key_workspace_access(
    db: AsyncSession, user: UserClaims, key: str
) -> None:
    """Require membership of the workspace that owns ``key``.

    Keys are minted as ``workspaces/{workspace_id}/...``; anything else is
    outside the tenant namespace and rejected.
    """
    parts = key.split("/")
    if len(parts) < 2 or parts[0] != "workspaces" or not parts[1]:
        raise HTTPException(status_code=403, detail="Object key is not workspace-scoped")
    await check_workspace_membership(db, user, parts[1])


@router.post("/upload-url")
async def get_upload_url(
    req: UploadUrlRequest,
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Generate a presigned PUT URL for client-direct uploads to S3/R2.

    Membership is checked against the ``workspace_id`` in the request body —
    the key prefix must belong to a workspace the caller is a member of.
    """
    await check_workspace_membership(db, user, req.workspace_id)
    safe_name = sanitize_filename(req.file_name)
    key = f"workspaces/{req.workspace_id}/{req.folder}/{uuid4().hex[:12]}_{safe_name}"

    storage = ObjectStorage()
    upload_url = await storage.generate_upload_url(
        key=key,
        content_type=req.content_type,
        expires_in=600,
    )
    file_url = storage.get_url(key)

    return {
        "key": key,
        "upload_url": upload_url,
        "file_url": file_url,
    }


@router.get("/download-url")
async def get_download_url(
    key: str = Query(..., min_length=1),
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Generate a short-lived presigned GET URL for secure downloading.

    The object key must belong to a workspace the caller is a member of.
    """
    await _require_key_workspace_access(db, user, key)
    storage = ObjectStorage()
    download_url = await storage.generate_presigned_url(key=key, expires_in=3600)
    return {"key": key, "download_url": download_url}


@router.delete("/file")
async def delete_file(
    key: str = Query(..., min_length=1),
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Delete an object from storage (workspace-scoped via key prefix)."""
    await _require_key_workspace_access(db, user, key)
    storage = ObjectStorage()
    await storage.delete(key=key)
    return {"deleted": True}
