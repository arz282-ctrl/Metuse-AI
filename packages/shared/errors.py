"""Canonical API error envelope and domain exception hierarchy."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from packages.shared.enums import ErrorCode
from packages.shared.scrubbing import scrub_secrets, scrub_text


class ErrorDetail(BaseModel):
    """Inner error payload matching Metuse API error specification."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str


class ErrorEnvelope(BaseModel):
    """Canonical top-level JSON error wrapper."""

    error: ErrorDetail


class MetuseError(Exception):
    """Base domain exception that serializes cleanly to ErrorEnvelope."""

    default_code: ErrorCode = ErrorCode.INTERNAL_ERROR
    default_status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code or self.default_code
        self.status_code = status_code if status_code is not None else self.default_status_code
        scrubbed_details = scrub_secrets(details or {})
        self.details: dict[str, Any] = (
            scrubbed_details if isinstance(scrubbed_details, dict) else {}
        )
        self.message = scrub_text(message)
        super().__init__(self.message)

    def to_envelope(self, request_id: str | UUID | None = None) -> ErrorEnvelope:
        """Build a secret-scrubbed canonical ErrorEnvelope."""
        req_id = str(request_id) if request_id is not None else str(uuid4())
        scrubbed_details = scrub_secrets(self.details)
        safe_details = scrubbed_details if isinstance(scrubbed_details, dict) else {}
        return ErrorEnvelope(
            error=ErrorDetail(
                code=self.code.value,
                message=scrub_text(self.message),
                details=safe_details,
                request_id=req_id,
            )
        )


class BadRequestError(MetuseError):
    default_code = ErrorCode.BAD_REQUEST
    default_status_code = 400


class AuthenticationError(MetuseError):
    default_code = ErrorCode.UNAUTHORIZED
    default_status_code = 401


class InvalidCredentialsError(AuthenticationError):
    default_code = ErrorCode.INVALID_CREDENTIALS
    default_status_code = 401


class PermissionDeniedError(MetuseError):
    default_code = ErrorCode.PERMISSION_DENIED
    default_status_code = 403


class WorkspaceContextRequiredError(PermissionDeniedError):
    default_code = ErrorCode.WORKSPACE_CONTEXT_REQUIRED
    default_status_code = 403


class WorkspaceAccessDeniedError(PermissionDeniedError):
    default_code = ErrorCode.WORKSPACE_ACCESS_DENIED
    default_status_code = 403


class NotFoundError(MetuseError):
    default_code = ErrorCode.NOT_FOUND
    default_status_code = 404


class ConflictError(MetuseError):
    default_code = ErrorCode.CONFLICT
    default_status_code = 409


class WorkspaceMemberLimitExceededError(ConflictError):
    default_code = ErrorCode.WORKSPACE_MEMBER_LIMIT_EXCEEDED
    default_status_code = 409

    def __init__(
        self,
        message: str = "Workspace has reached the maximum limit of 5 members",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code=ErrorCode.WORKSPACE_MEMBER_LIMIT_EXCEEDED,
            status_code=409,
            details=details,
        )


class ValidationDomainError(MetuseError):
    default_code = ErrorCode.VALIDATION_ERROR
    default_status_code = 422


class ExternalServiceError(MetuseError):
    default_code = ErrorCode.EXTERNAL_SERVICE_ERROR
    default_status_code = 502


class ServiceUnavailableError(MetuseError):
    default_code = ErrorCode.SERVICE_UNAVAILABLE
    default_status_code = 503
