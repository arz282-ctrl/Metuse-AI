"""Meta Data Deletion Request protocol (App Review requirement).

Meta POSTs a `signed_request` to the configured Data Deletion URL when a user
removes the app. The payload is verified with the Meta app secret before any
account data is touched. See https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from packages.shared.config import BaseAppSettings
from packages.shared.errors import BadRequestError, ExternalServiceError


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode((segment + padding).encode("ascii"))


def verify_meta_signed_request(settings: BaseAppSettings, signed_request: str) -> dict[str, Any]:
    """Verify a Meta signed_request and return its JSON payload (fails closed)."""
    app_secret = settings.meta_app_secret.get_secret_value().strip()
    if not app_secret:
        raise ExternalServiceError(
            "META_APP_SECRET must be configured to verify Meta data deletion requests"
        )
    if not signed_request or "." not in signed_request:
        raise BadRequestError("Meta signed_request is missing or malformed")

    encoded_sig, encoded_payload = signed_request.strip().split(".", 1)
    try:
        provided_sig = _b64url_decode(encoded_sig)
        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    except Exception as exc:
        raise BadRequestError("Meta signed_request payload is invalid") from exc

    if not isinstance(payload, dict):
        raise BadRequestError("Meta signed_request payload is invalid")
    if str(payload.get("algorithm", "")).upper() != "HMAC-SHA256":
        raise BadRequestError("Unsupported Meta signed_request algorithm")

    expected_sig = hmac.new(
        app_secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise BadRequestError("Meta signed_request signature verification failed")
    return payload
