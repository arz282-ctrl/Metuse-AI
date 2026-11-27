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


class VideoGenerationMode(StrEnum):
    """Supported unified video generation gateway modes."""

    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"


class VideoJobStatus(StrEnum):
    """Provider-independent video generation job state machine states."""

    QUEUED = "queued"
    VALIDATING = "validating"
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VideoFailureCategory(StrEnum):
    """Normalized provider-independent video generation failure categories."""

    VALIDATION = "validation"
    CAPABILITY = "capability"
    RATE_LIMITED = "rate_limited"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    BUDGET = "budget"


class AssetKind(StrEnum):
    """Canonical media asset kinds stored in private tenant object storage."""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"


class AssetSourceType(StrEnum):
    """Provenance of a tenant-owned asset record."""

    UPLOAD = "upload"
    VIDEO_GENERATION = "video_generation"
    BRAND = "brand"
    RENDER = "render"


class RenderKind(StrEnum):
    """Cloud editing render output classification."""

    PREVIEW = "preview"
    FINAL = "final"


class RenderJobStatus(StrEnum):
    """Lifecycle states for isolated cloud editing render jobs."""

    QUEUED = "queued"
    VALIDATING = "validating"
    RENDERING = "rendering"
    QA_CHECKING = "qa_checking"
    SUCCEEDED = "succeeded"
    QA_FAILED = "qa_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalType(StrEnum):
    """Mandatory human-approval gate types (Section 9)."""

    STRATEGY = "strategy"
    CREATIVE = "creative"
    PUBLISHING = "publishing"
    CAMPAIGN_EXECUTION = "campaign_execution"
    BUDGET_CHANGE = "budget_change"


class ApprovalStatus(StrEnum):
    """Version-bound approval lifecycle states."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"
    INVALIDATED = "invalidated"


class MetaConnectionType(StrEnum):
    """Supported Meta Unified Login onboarding connection types (Section 8.2)."""

    AGENCY_BUSINESS = "agency_business"
    CREATOR_PROFESSIONAL = "creator_professional"
    PAGE_ONLY = "page_only"
    ADS_ONLY = "ads_only"


class MetaConnectionStatus(StrEnum):
    """Lifecycle states for a Meta account connection."""

    ACTIVE = "active"
    RECONNECT_REQUIRED = "reconnect_required"
    REVOKED = "revoked"
    DISCONNECTED = "disconnected"


class MetaAssetType(StrEnum):
    """Discovered external Meta asset types."""

    INSTAGRAM_PROFESSIONAL = "instagram_professional"
    FACEBOOK_PAGE = "facebook_page"
    AD_ACCOUNT = "ad_account"


class InstagramAccountType(StrEnum):
    """Instagram account tier returned during eligibility inspection."""

    BUSINESS = "business"
    CREATOR = "creator"
    PERSONAL = "personal"


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
    PROJECT_CREATED = "project.created"
    ASSET_CREATED = "asset.created"
    ASSET_UPLOAD_URL_CREATED = "asset.upload_url_created"
    ASSET_UPLOAD_COMPLETED = "asset.upload_completed"
    VIDEO_JOB_CREATED = "video_job.created"
    VIDEO_JOB_COMPLETED = "video_job.completed"
    VIDEO_JOB_FAILED = "video_job.failed"
    VIDEO_JOB_CANCELLED = "video_job.cancelled"
    EDITING_PLAN_CREATED = "editing_plan.created"
    RENDER_JOB_CREATED = "render_job.created"
    RENDER_JOB_COMPLETED = "render_job.completed"
    RENDER_JOB_FAILED = "render_job.failed"
    APPROVAL_CREATED = "approval.created"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_REVISION_REQUESTED = "approval.revision_requested"
    APPROVAL_INVALIDATED = "approval.invalidated"
    META_LOGIN_STARTED = "meta.login_started"
    META_CONNECTION_CREATED = "meta.connection_created"
    META_CONNECTION_RECONNECTED = "meta.connection_reconnected"
    META_CONNECTION_DISCONNECTED = "meta.connection_disconnected"
    META_CONNECTION_REVOKED = "meta.connection_revoked"
    META_PERSONAL_ACCOUNT_BLOCKED = "meta.personal_account_blocked"
    META_WEBHOOK_RECEIVED = "meta.webhook_received"


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
    NO_ELIGIBLE_PROVIDER = "NO_ELIGIBLE_PROVIDER"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    WEBHOOK_SIGNATURE_INVALID = "WEBHOOK_SIGNATURE_INVALID"
    MEDIA_QA_FAILED = "MEDIA_QA_FAILED"
    APPROVAL_INVALIDATED = "APPROVAL_INVALIDATED"
    UNSUPPORTED_TIMELINE_OPERATION = "UNSUPPORTED_TIMELINE_OPERATION"
    META_PERSONAL_ACCOUNT_INELIGIBLE = "META_PERSONAL_ACCOUNT_INELIGIBLE"
    META_PERMISSION_MISSING = "META_PERMISSION_MISSING"
    META_RECONNECT_REQUIRED = "META_RECONNECT_REQUIRED"
    META_OAUTH_STATE_INVALID = "META_OAUTH_STATE_INVALID"
    META_REDIRECT_URI_NOT_ALLOWED = "META_REDIRECT_URI_NOT_ALLOWED"
    INVALID_TASK_TYPE = "INVALID_TASK_TYPE"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
