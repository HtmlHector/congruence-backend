"""Storage routes for client-direct uploads, downloads, and artifact management."""

from __future__ import annotations

import re
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src.infrastructure.auth.dependencies import get_current_user, require_workspace_member
from src.infrastructure.auth.jwt import UserClaims
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


@router.post("/upload-url")
async def get_upload_url(
    req: UploadUrlRequest,
    membership: dict = Depends(require_workspace_member),
) -> dict[str, str]:
    """Generate a presigned PUT URL for client-direct uploads to S3/R2."""
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
) -> dict[str, str]:
    """Generate a short-lived presigned GET URL for secure downloading."""
    storage = ObjectStorage()
    download_url = await storage.generate_presigned_url(key=key, expires_in=3600)
    return {"key": key, "download_url": download_url}


@router.delete("/file")
async def delete_file(
    key: str = Query(..., min_length=1),
    user: UserClaims = Depends(get_current_user),
) -> dict[str, bool]:
    """Delete an object from storage."""
    storage = ObjectStorage()
    await storage.delete(key=key)
    return {"deleted": True}
