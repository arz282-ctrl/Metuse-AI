"""Tenant and request execution context models."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from packages.shared.enums import RoleName


class TenantContext(BaseModel):
    """Immutable tenant/actor scope bound to a single unit of work / DB transaction."""

    model_config = ConfigDict(frozen=True)

    workspace_id: UUID | None = None
    user_id: UUID | None = None
    request_id: UUID = Field(default_factory=uuid4)
    role_name: RoleName | None = None


class RequestContext(BaseModel):
    """Request metadata propagated across API, tenancy, and audit layers."""

    model_config = ConfigDict(frozen=True)

    request_id: UUID = Field(default_factory=uuid4)
    user_id: UUID | None = None
    workspace_id: UUID | None = None
    role_name: RoleName | None = None
    idempotency_key: str | None = None
