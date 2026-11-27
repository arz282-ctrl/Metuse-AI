"""Shared domain enums for Metuse AI."""

from enum import StrEnum


class WorkspaceType(StrEnum):
    """Supported workspace tenancy modes."""

    AGENCY = "agency"
    CREATOR = "creator"


class RoleName(StrEnum):
    """Canonical workspace and client RBAC role names."""

    AGENCY_OWNER = "agency_owner"
    AGENCY_ADMIN = "agency_admin"
    AGENCY_MEMBER = "agency_member"
    CREATOR_OWNER = "creator_owner"
    CLIENT_REVIEWER = "client_reviewer"
    CLIENT_VIEWER = "client_viewer"


class MemberStatus(StrEnum):
    """Workspace membership lifecycle states."""

    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"
    REMOVED = "removed"


class ClientStatus(StrEnum):
    """Client account status within an agency or creator workspace."""

    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class IdentityProvider(StrEnum):
    """External or local user identity provider."""

    EMAIL = "email"
    META = "meta"
    SUPABASE = "supabase"


class WorkerTaskType(StrEnum):
    """Supported Phase 2 specialist worker task types."""

    RESEARCH = "research"
    WRITING = "writing"
    SEO_AEO = "seo_aeo"
    MEDIA_PLAN = "media_plan"


class WorkerJobStatus(StrEnum):
    """Lifecycle states for specialist AI worker jobs."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class MuseCapability(StrEnum):
    """Capabilities actually exposed by the Meta Model API / Muse layer (no video generation)."""

    REASONING_TOOL_USE = "reasoning_tool_use"
    REASONING_STRUCTURED_OUTPUT = "reasoning_structured_output"
    IMAGE_GENERATION = "image_generation"
    IMAGE_EDIT = "image_edit"
    AUDIO_TRANSCRIPTION = "audio_transcription"
    SEGMENTATION = "segmentation"


class BrandAssetType(StrEnum):
    """Categorized brand asset types stored in private object storage."""

    LOGO = "logo"
    PALETTE = "palette"
    FONT = "font"
    GUIDELINE = "guideline"
    PRODUCT_REFERENCE = "product_reference"
    OTHER = "other"


class AuditAction(StrEnum):
    """Canonical append-only audit log action identifiers."""

    AUTH_REGISTER = "auth.register"
    AUTH_LOGIN = "auth.login"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_LOGOUT = "auth.logout"
    AUTH_META_LOGIN_START = "auth.meta_login_start"
    AUTH_META_LOGIN_CALLBACK = "auth.meta_login_callback"
    WORKSPACE_CREATED = "workspace.created"
    WORKSPACE_UPDATED = "workspace.updated"
    WORKSPACE_MEMBER_ADDED = "workspace.member_added"
    WORKSPACE_MEMBER_UPDATED = "workspace.member_updated"
    WORKSPACE_MEMBER_REMOVED = "workspace.member_removed"
    CLIENT_CREATED = "client.created"
    CLIENT_UPDATED = "client.updated"
    BRAND_PROFILE_UPSERTED = "brand_profile.upserted"
    BRAND_ASSET_CREATED = "brand_asset.created"
    BRAND_ASSET_ACCESSED = "brand_asset.accessed"
    WORKER_JOB_CREATED = "worker_job.created"
    WORKER_JOB_COMPLETED = "worker_job.completed"
    WORKER_JOB_FAILED = "worker_job.failed"
    WORKSPACE_BUDGET_UPDATED = "workspace_budget.updated"


class ErrorCode(StrEnum):
    """Canonical UPPER_SNAKE_CASE API and domain error codes."""

    BAD_REQUEST = "BAD_REQUEST"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    UNAUTHORIZED = "UNAUTHORIZED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    WORKSPACE_CONTEXT_REQUIRED = "WORKSPACE_CONTEXT_REQUIRED"
    WORKSPACE_ACCESS_DENIED = "WORKSPACE_ACCESS_DENIED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    EMAIL_ALREADY_EXISTS = "EMAIL_ALREADY_EXISTS"
    WORKSPACE_MEMBER_LIMIT_EXCEEDED = "WORKSPACE_MEMBER_LIMIT_EXCEEDED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CAPABILITY_NOT_SUPPORTED = "CAPABILITY_NOT_SUPPORTED"
    PROVIDER_DISABLED = "PROVIDER_DISABLED"
    INVALID_TASK_TYPE = "INVALID_TASK_TYPE"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
