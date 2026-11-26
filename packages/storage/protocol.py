"""Cloud-agnostic object storage protocol and typed DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PresignedUploadUrl:
    """Short-lived signed PUT URL contract for private object uploads."""

    url: str
    method: str
    headers: dict[str, str]
    bucket: str
    key: str
    expires_in: int


@dataclass(frozen=True)
class PresignedDownloadUrl:
    """Short-lived signed GET URL contract for private object downloads."""

    url: str
    bucket: str
    key: str
    expires_in: int


@dataclass(frozen=True)
class ObjectMetadata:
    """Verified metadata returned from object storage HEAD/PUT calls."""

    bucket: str
    key: str
    size_bytes: int
    content_type: str
    etag: str | None = None


class StorageBackend(Protocol):
    """Provider-independent private object storage interface (S3/MinIO/GCS)."""

    @property
    def bucket_name(self) -> str: ...

    def create_presigned_put_url(
        self,
        *,
        key: str,
        content_type: str,
        content_length: int,
        expires_in: int | None = None,
    ) -> PresignedUploadUrl: ...

    def create_presigned_get_url(
        *,
        self,
        key: str,
        expires_in: int | None = None,
        download_filename: str | None = None,
    ) -> PresignedDownloadUrl: ...

    def head_object(self, *, key: str) -> ObjectMetadata: ...

    def read_object_prefix(self, *, key: str, num_bytes: int = 64) -> bytes: ...

    def put_object_bytes(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
    ) -> ObjectMetadata: ...

    def delete_object(self, *, key: str) -> None: ...

    def check_health(self) -> bool: ...
