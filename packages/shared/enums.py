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
    VALIDATION_ERROR = "VALIDATION_ERROR"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
