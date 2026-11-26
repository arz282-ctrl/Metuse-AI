"""S3/MinIO private storage backend with separate internal/public SigV4 endpoints."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from packages.shared.config import BaseAppSettings
from packages.shared.errors import ExternalServiceError, NotFoundError
from packages.storage.keys import sanitize_filename
from packages.storage.protocol import ObjectMetadata, PresignedDownloadUrl, PresignedUploadUrl

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

_MIN_PRESIGN_TTL = 60
_MAX_PRESIGN_TTL = 900


def _clamp_ttl(ttl: int) -> int:
    return min(max(int(ttl), _MIN_PRESIGN_TTL), _MAX_PRESIGN_TTL)


class S3StorageBackend:
    """Private S3/MinIO storage adapter with distinct internal and public endpoints."""

    def __init__(
        self,
        *,
        bucket: str,
        internal_endpoint_url: str,
        public_endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        default_presign_ttl_seconds: int = 900,
        internal_client: S3Client | None = None,
        public_client: S3Client | None = None,
    ) -> None:
        self._bucket = bucket.strip()
        if not self._bucket:
            raise ValueError("S3 bucket name must not be empty")
        self._internal_endpoint_url = internal_endpoint_url.strip().rstrip("/")
        self._public_endpoint_url = public_endpoint_url.strip().rstrip("/")
        self._default_ttl = _clamp_ttl(default_presign_ttl_seconds)

        boto_cfg = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 2, "mode": "standard"},
        )
        self._internal_client: S3Client = internal_client or boto3.client(
            "s3",
            endpoint_url=self._internal_endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=boto_cfg,
        )
        self._public_client: S3Client = public_client or boto3.client(
            "s3",
            endpoint_url=self._public_endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=boto_cfg,
        )

    @classmethod
    def from_settings(cls, settings: BaseAppSettings) -> S3StorageBackend:
        """Instantiate S3StorageBackend from BaseAppSettings."""
        return cls(
            bucket=settings.s3_bucket,
            internal_endpoint_url=settings.get_s3_internal_endpoint_url(),
            public_endpoint_url=settings.get_s3_public_endpoint_url(),
            access_key=settings.s3_access_key.get_secret_value() or "minioadmin",
            secret_key=settings.s3_secret_key.get_secret_value() or "minioadmin",
            region=settings.s3_region,
            default_presign_ttl_seconds=settings.s3_presign_ttl_seconds,
        )

    @property
    def bucket_name(self) -> str:
        return self._bucket

    @property
    def internal_endpoint_url(self) -> str:
        return self._internal_endpoint_url

    @property
    def public_endpoint_url(self) -> str:
        return self._public_endpoint_url

    def create_presigned_put_url(
        self,
        *,
        key: str,
        content_type: str,
        content_length: int,
        expires_in: int | None = None,
    ) -> PresignedUploadUrl:
        ttl = _clamp_ttl(self._default_ttl if expires_in is None else expires_in)
        try:
            url = self._public_client.generate_presigned_url(
                ClientMethod="put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ContentType": content_type,
                    "ContentLength": content_length,
                },
                ExpiresIn=ttl,
                HttpMethod="PUT",
            )
        except (ClientError, BotoCoreError) as exc:
            raise ExternalServiceError("Failed to generate presigned upload URL") from exc

        return PresignedUploadUrl(
            url=url,
            method="PUT",
            headers={
                "Content-Type": content_type,
                "Content-Length": str(content_length),
            },
            bucket=self._bucket,
            key=key,
            expires_in=ttl,
        )

    def create_presigned_get_url(
        self,
        *,
        key: str,
        expires_in: int | None = None,
        download_filename: str | None = None,
    ) -> PresignedDownloadUrl:
        ttl = _clamp_ttl(self._default_ttl if expires_in is None else expires_in)
        params: dict[str, str] = {"Bucket": self._bucket, "Key": key}
        if download_filename:
            safe_name = sanitize_filename(download_filename)
            params["ResponseContentDisposition"] = f'attachment; filename="{safe_name}"'
        else:
            params["ResponseContentDisposition"] = "attachment"

        try:
            url = self._public_client.generate_presigned_url(
                ClientMethod="get_object",
                Params=params,
                ExpiresIn=ttl,
                HttpMethod="GET",
            )
        except (ClientError, BotoCoreError) as exc:
            raise ExternalServiceError("Failed to generate presigned download URL") from exc

        return PresignedDownloadUrl(
            url=url,
            bucket=self._bucket,
            key=key,
            expires_in=ttl,
        )

    def head_object(self, *, key: str) -> ObjectMetadata:
        try:
            resp = self._internal_client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if status == 404 or error_code in {"404", "NoSuchKey", "NotFound"}:
                raise NotFoundError("Requested object does not exist in storage") from exc
            raise ExternalServiceError("Failed to inspect object metadata in storage") from exc
        except BotoCoreError as exc:
            raise ExternalServiceError("Storage service communication failed") from exc

        etag_raw = resp.get("ETag")
        etag = etag_raw.strip('"') if isinstance(etag_raw, str) else None
        return ObjectMetadata(
            bucket=self._bucket,
            key=key,
            size_bytes=int(resp.get("ContentLength", 0)),
            content_type=str(resp.get("ContentType", "application/octet-stream")),
            etag=etag,
        )

    def read_object_prefix(self, *, key: str, num_bytes: int = 64) -> bytes:
        byte_count = max(1, min(int(num_bytes), 4096))
        try:
            resp = self._internal_client.get_object(
                Bucket=self._bucket,
                Key=key,
                Range=f"bytes=0-{byte_count - 1}",
            )
            body = resp["Body"]
            try:
                return body.read(byte_count)
            finally:
                body.close()
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if status == 404 or error_code in {"404", "NoSuchKey", "NotFound"}:
                raise NotFoundError("Requested object does not exist in storage") from exc
            if status == 416 or error_code == "InvalidRange":
                return b""
            raise ExternalServiceError("Failed to read object bytes from storage") from exc
        except BotoCoreError as exc:
            raise ExternalServiceError("Storage service communication failed") from exc

    def put_object_bytes(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
    ) -> ObjectMetadata:
        try:
            resp = self._internal_client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except (ClientError, BotoCoreError) as exc:
            raise ExternalServiceError("Failed to write object to storage") from exc

        etag_raw = resp.get("ETag")
        etag = etag_raw.strip('"') if isinstance(etag_raw, str) else None
        return ObjectMetadata(
            bucket=self._bucket,
            key=key,
            size_bytes=len(data),
            content_type=content_type,
            etag=etag,
        )

    def delete_object(self, *, key: str) -> None:
        try:
            self._internal_client.delete_object(Bucket=self._bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise ExternalServiceError("Failed to delete object from storage") from exc

    def check_health(self) -> bool:
        try:
            self._internal_client.head_bucket(Bucket=self._bucket)
            return True
        except (ClientError, BotoCoreError):
            return False


class InMemoryStorageBackend:
    """In-memory StorageBackend implementation for deterministic unit and integration tests."""

    def __init__(
        self,
        *,
        bucket: str = "metuse-test",
        public_endpoint_url: str = "http://localhost:9000",
    ) -> None:
        self._bucket = bucket
        self._public_endpoint_url = public_endpoint_url.rstrip("/")
        self._objects: dict[str, tuple[bytes, str]] = {}

    @property
    def bucket_name(self) -> str:
        return self._bucket

    def create_presigned_put_url(
        self,
        *,
        key: str,
        content_type: str,
        content_length: int,
        expires_in: int | None = None,
    ) -> PresignedUploadUrl:
        ttl = _clamp_ttl(900 if expires_in is None else expires_in)
        encoded_key = quote(key, safe="/")
        url = (
            f"{self._public_endpoint_url}/{self._bucket}/{encoded_key}"
            f"?X-Amz-Expires={ttl}&X-Amz-Signature=inmemory"
        )
        return PresignedUploadUrl(
            url=url,
            method="PUT",
            headers={
                "Content-Type": content_type,
                "Content-Length": str(content_length),
            },
            bucket=self._bucket,
            key=key,
            expires_in=ttl,
        )

    def create_presigned_get_url(
        self,
        *,
        key: str,
        expires_in: int | None = None,
        download_filename: str | None = None,
    ) -> PresignedDownloadUrl:
        ttl = _clamp_ttl(900 if expires_in is None else expires_in)
        encoded_key = quote(key, safe="/")
        disposition = (
            f"attachment;filename={sanitize_filename(download_filename)}"
            if download_filename
            else "attachment"
        )
        url = (
            f"{self._public_endpoint_url}/{self._bucket}/{encoded_key}"
            f"?X-Amz-Expires={ttl}&response-content-disposition={quote(disposition)}"
        )
        return PresignedDownloadUrl(
            url=url,
            bucket=self._bucket,
            key=key,
            expires_in=ttl,
        )

    def head_object(self, *, key: str) -> ObjectMetadata:
        entry = self._objects.get(key)
        if entry is None:
            raise NotFoundError("Requested object does not exist in storage")
        data, content_type = entry
        return ObjectMetadata(
            bucket=self._bucket,
            key=key,
            size_bytes=len(data),
            content_type=content_type,
            etag=hashlib.sha256(data).hexdigest()[:32],
        )

    def read_object_prefix(self, *, key: str, num_bytes: int = 64) -> bytes:
        entry = self._objects.get(key)
        if entry is None:
            raise NotFoundError("Requested object does not exist in storage")
        return entry[0][: max(1, num_bytes)]

    def put_object_bytes(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
    ) -> ObjectMetadata:
        self._objects[key] = (data, content_type)
        return self.head_object(key=key)

    def delete_object(self, *, key: str) -> None:
        self._objects.pop(key, None)

    def check_health(self) -> bool:
        return True
