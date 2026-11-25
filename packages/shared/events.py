"""Shared domain event and audit payload models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from packages.shared.enums import AuditAction
from packages.shared.scrubbing import scrub_secrets


class DomainEvent(BaseModel):
    """Scrubbed domain event envelope for cross-module and workflow boundaries."""

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    workspace_id: UUID | None = None
    client_id: UUID | None = None
    actor_user_id: UUID | None = None
    request_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload", mode="before")
    @classmethod
    def _scrub_payload(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        scrubbed = scrub_secrets(value)
        return scrubbed if isinstance(scrubbed, dict) else {"value": scrubbed}


class AuditEventPayload(BaseModel):
    """Validated, secret-scrubbed payload written to append-only audit_logs."""

    action: AuditAction
    workspace_id: UUID | None = None
    client_id: UUID | None = None
    actor_user_id: UUID | None = None
    target_type: str
    target_id: str | None = None
    request_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def _scrub_metadata(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        scrubbed = scrub_secrets(value)
        return scrubbed if isinstance(scrubbed, dict) else {"value": scrubbed}
