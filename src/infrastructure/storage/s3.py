"""S3 / MinIO object storage primitive — presigned URLs and object management."""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
import structlog
from botocore.config import Config

from src.config.settings import settings

logger = structlog.get_logger(__name__)


class StorageService:
    """S3 and MinIO compatible object storage client."""

    def __init__(self) -> None:
        self._bucket = settings.s3.bucket_name
        self._endpoint_url = settings.s3.endpoint_url
        self._region = settings.s3.region
        self._client = boto3.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=settings.s3.access_key,
            aws_secret_access_key=settings.s3.secret_key,
            region_name=self._region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def generate_presigned_upload_url(
        self,
        object_key: str,
        content_type: str = "application/octet-stream",
        expires_in: int | None = None,
    ) -> dict[str, Any]:
        """Generate a presigned PUT URL allowing clients to upload directly to S3."""
        expiration = expires_in or settings.s3.presigned_url_expiration_seconds
        url = self._client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": self._bucket,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=expiration,
        )
        return {
            "upload_url": url,
            "object_key": object_key,
            "expires_in": expiration,
        }

    def generate_presigned_download_url(
        self,
        object_key: str,
        expires_in: int | None = None,
    ) -> str:
        """Generate a presigned GET URL allowing clients to download an object."""
        expiration = expires_in or settings.s3.presigned_url_expiration_seconds
        return str(
            self._client.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": object_key,
                },
                ExpiresIn=expiration,
            )
        )

    async def upload_bytes(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Upload raw bytes to S3 asynchronously."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.put_object(
                Bucket=self._bucket,
                Key=object_key,
                Body=data,
                ContentType=content_type,
            ),
        )

    async def delete_object(self, object_key: str) -> None:
        """Delete an object from S3 asynchronously."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.delete_object(
                Bucket=self._bucket,
                Key=object_key,
            ),
        )


storage_service = StorageService()
