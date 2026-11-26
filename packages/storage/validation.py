"""Magic-byte signature inspection and upload validation (never trusts extensions alone)."""

from __future__ import annotations

from packages.shared.errors import ValidationDomainError

MAX_BRAND_ASSET_BYTES = 25 * 1024 * 1024  # 25 MiB

ALLOWED_BRAND_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/pdf",
        "font/woff2",
        "font/woff",
        "font/ttf",
        "font/otf",
        "video/mp4",
    }
)

_BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".svg",
        ".svgz",
        ".html",
        ".htm",
        ".xhtml",
        ".js",
        ".mjs",
        ".cjs",
        ".exe",
        ".dll",
        ".sh",
        ".bat",
        ".cmd",
        ".ps1",
        ".php",
        ".py",
    }
)

_DISALLOWED_TEXT_SIGNATURES: tuple[bytes, ...] = (
    b"<svg",
    b"<?xml",
    b"<!doctype html",
    b"<html",
    b"<script",
    b"#!/",
)


def _normalize_mime(mime_type: str) -> str:
    cleaned = mime_type.split(";", 1)[0].strip().lower()
    if cleaned == "image/jpg":
        return "image/jpeg"
    if cleaned == "application/font-woff":
        return "font/woff"
    if cleaned == "application/font-woff2":
        return "font/woff2"
    return cleaned


def validate_upload_metadata(
    *,
    filename: str,
    declared_mime: str,
    size_bytes: int,
    max_size_bytes: int = MAX_BRAND_ASSET_BYTES,
) -> str:
    """Validate pre-upload metadata before issuing a presigned PUT URL."""
    lowered_name = filename.strip().lower()
    for ext in _BLOCKED_EXTENSIONS:
        if lowered_name.endswith(ext):
            raise ValidationDomainError(
                f"File extension '{ext}' is not permitted for private asset uploads"
            )

    normalized_mime = _normalize_mime(declared_mime)
    if normalized_mime not in ALLOWED_BRAND_MIME_TYPES:
        raise ValidationDomainError(
            f"Unsupported MIME type '{declared_mime}'",
            details={"allowed_mime_types": sorted(ALLOWED_BRAND_MIME_TYPES)},
        )

    if size_bytes <= 0:
        raise ValidationDomainError("Uploaded asset size must be greater than 0 bytes")
    if size_bytes > max_size_bytes:
        raise ValidationDomainError(
            f"Uploaded asset exceeds maximum allowed size of {max_size_bytes} bytes",
            details={"max_size_bytes": max_size_bytes},
        )
    return normalized_mime


def detect_mime_from_magic_bytes(header: bytes) -> str | None:
    """Detect canonical MIME type from leading file magic bytes."""
    if not header:
        return None

    stripped_lower = header.lstrip().lower()
    for blocked in _DISALLOWED_TEXT_SIGNATURES:
        if stripped_lower.startswith(blocked):
            return None

    # Reject PE/ELF executables explicitly
    if header.startswith((b"MZ", b"\x7fELF")):
        return None

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith(b"%PDF-"):
        return "application/pdf"
    if header.startswith(b"wOF2"):
        return "font/woff2"
    if header.startswith(b"wOFF"):
        return "font/woff"
    if header.startswith((b"\x00\x01\x00\x00", b"true")):
        return "font/ttf"
    if header.startswith(b"OTTO"):
        return "font/otf"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/mp4"
    return None


def validate_magic_bytes(*, header: bytes, declared_mime: str) -> str:
    """Verify object magic bytes match an allowlisted MIME type and the declared MIME."""
    normalized_declared = _normalize_mime(declared_mime)
    if normalized_declared not in ALLOWED_BRAND_MIME_TYPES:
        raise ValidationDomainError(f"Unsupported MIME type '{declared_mime}'")

    detected = detect_mime_from_magic_bytes(header)
    if detected is None:
        raise ValidationDomainError(
            "Uploaded file failed magic-byte signature verification",
            details={"declared_mime": normalized_declared},
        )
    if detected != normalized_declared:
        raise ValidationDomainError(
            "Uploaded file magic bytes do not match declared MIME type",
            details={"declared_mime": normalized_declared, "detected_mime": detected},
        )
    return detected
