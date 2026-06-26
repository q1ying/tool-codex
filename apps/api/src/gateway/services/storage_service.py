from __future__ import annotations

from pathlib import Path
from typing import Iterator

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ..config import Settings
from .path_security import safe_join


class StorageService:
    """S3-compatible object storage used by both local MinIO and production OSS."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache_root = settings.data_dir / ".object-cache"
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.object_storage_endpoint,
            aws_access_key_id=settings.object_storage_access_key_id,
            aws_secret_access_key=settings.object_storage_secret_access_key,
            region_name=settings.object_storage_region,
            config=Config(
                signature_version=settings.object_storage_signature_version,
                s3={"addressing_style": settings.object_storage_addressing_style},
            ),
        )

    @property
    def backend_name(self) -> str:
        return "s3-compatible"

    @property
    def bucket(self) -> str:
        return self.settings.object_storage_bucket

    def put_path(self, source_path: Path, object_key: str, content_type: str | None = None) -> None:
        self._ensure_bucket()
        extra_args = {"ContentType": content_type} if content_type else {}
        self.client.upload_file(str(source_path), self.bucket, object_key, ExtraArgs=extra_args)

    def copy_to_path(self, object_key: str, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, object_key, str(target_path))

    def object_size(self, object_key: str) -> int:
        response = self.client.head_object(Bucket=self.bucket, Key=object_key)
        return int(response.get("ContentLength") or 0)

    def resolve_path(self, object_key: str) -> Path:
        target = safe_join(self.cache_root, object_key)
        self.copy_to_path(object_key, target)
        return target

    def exists(self, object_key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=object_key)
            return True
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return False
            raise

    def delete_object(self, object_key: str) -> None:
        if not object_key:
            return
        try:
            self.client.delete_object(Bucket=self.bucket, Key=object_key)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return
            raise

    def iter_bytes(self, object_key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        response = self.client.get_object(Bucket=self.bucket, Key=object_key)
        body = response["Body"]
        try:
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            body.close()

    def presign_download(self, object_key: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=self.settings.object_storage_presign_expires_seconds,
        )

    def presign_upload(self, object_key: str, content_type: str | None = None) -> str:
        params = {"Bucket": self.bucket, "Key": object_key}
        if content_type:
            params["ContentType"] = content_type
        return self.client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=self.settings.object_storage_presign_expires_seconds,
        )

    def _ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status not in {404, 400} or not self.settings.object_storage_auto_create_bucket:
                raise
        self.client.create_bucket(Bucket=self.bucket)
