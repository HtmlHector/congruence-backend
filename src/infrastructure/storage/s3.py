"""S3-compatible object storage adapter (AWS S3, Cloudflare R2, MinIO, Supabase Storage).

All blocking boto3 calls are offloaded via asyncio.to_thread to keep the async event loop responsive.
"""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
import structlog
from botocore.config import Config

from src.config.settings import settings

logger = structlog.get_logger(__name__)


class ObjectStorage:
    """Async wrapper for S3-compatible object storage."""

    def __init__(self, bucket: str | None = None) -> None:
        self._bucket = bucket or settings.storage.bucket

        config_kwargs: dict[str, Any] = {
            "signature_version": "s3v4",
            "s3": {"addressing_style": "path", "payload_signing_enabled": False},
        }

        scheme = "https" if settings.storage.use_ssl else "http"
        endpoint_url = f"{scheme}://{settings.storage.endpoint}"

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.storage.access_key,
            aws_secret_access_key=settings.storage.secret_key,
            region_name=settings.storage.region,
            config=Config(**config_kwargs),
        )

    async def upload(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        """Upload raw bytes to S3 asynchronously."""

        def _sync_upload() -> None:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )

        await asyncio.to_thread(_sync_upload)
        return self.get_url(key)

    async def upload_file(
        self, key: str, file_path: str, content_type: str = "application/octet-stream"
    ) -> str:
        """Stream a local file directly to S3 asynchronously."""

        def _sync_upload_file() -> None:
            self._client.upload_file(
                file_path,
                self._bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )

        await asyncio.to_thread(_sync_upload_file)
        return self.get_url(key)

    async def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate presigned GET URL for downloading an object."""

        def _sync_presign() -> str:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )

        return await asyncio.to_thread(_sync_presign)

    async def generate_upload_url(
        self, key: str, content_type: str = "application/octet-stream", expires_in: int = 300
    ) -> str:
        """Generate presigned PUT URL for client-direct uploads."""

        def _sync_upload_presign() -> str:
            return self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=expires_in,
            )

        return await asyncio.to_thread(_sync_upload_presign)

    async def download(self, key: str) -> bytes | None:
        """Download object bytes, returning None if missing."""

        def _sync_download() -> bytes | None:
            try:
                resp = self._client.get_object(Bucket=self._bucket, Key=key)
                return resp["Body"].read()
            except self._client.exceptions.ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("NoSuchKey", "404"):
                    return None
                raise

        return await asyncio.to_thread(_sync_download)

    async def exists(self, key: str) -> bool:
        """Check whether key exists."""

        def _sync_exists() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
                return True
            except self._client.exceptions.ClientError:
                return False

        return await asyncio.to_thread(_sync_exists)

    async def delete(self, key: str) -> None:
        """Delete an object from S3."""
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)

    async def list_keys(self, prefix: str = "") -> list[str]:
        """List keys with prefix."""

        def _sync_list() -> list[str]:
            keys: list[str] = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
            return keys

        return await asyncio.to_thread(_sync_list)

    def get_url(self, key: str) -> str:
        scheme = "https" if settings.storage.use_ssl else "http"
        return f"{scheme}://{settings.storage.endpoint}/{self._bucket}/{key}"
