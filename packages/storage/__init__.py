"""Private multi-tenant object storage abstraction for Metuse AI."""

from packages.storage.keys import (
    build_brand_asset_key,
    build_tenant_object_key,
    sanitize_filename,
    validate_storage_key,
)
from packages.storage.protocol import (
    ObjectMetadata,
    PresignedDownloadUrl,
    PresignedUploadUrl,
    StorageBackend,
)
from packages.storage.s3 import InMemoryStorageBackend, S3StorageBackend
from packages.storage.validation import (
    ALLOWED_BRAND_MIME_TYPES,
    MAX_BRAND_ASSET_BYTES,
    detect_mime_from_magic_bytes,
    validate_magic_bytes,
    validate_upload_metadata,
)

__all__ = [
    "ALLOWED_BRAND_MIME_TYPES",
    "MAX_BRAND_ASSET_BYTES",
    "InMemoryStorageBackend",
    "ObjectMetadata",
    "PresignedDownloadUrl",
    "PresignedUploadUrl",
    "S3StorageBackend",
    "StorageBackend",
    "build_brand_asset_key",
    "build_tenant_object_key",
    "detect_mime_from_magic_bytes",
    "sanitize_filename",
    "validate_magic_bytes",
    "validate_storage_key",
    "validate_upload_metadata",
]
