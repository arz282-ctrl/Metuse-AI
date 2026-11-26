"""RBAC permission matrix and evaluator for workspace and client roles."""

from __future__ import annotations

from collections.abc import Iterable

from packages.shared.enums import RoleName
from packages.shared.errors import PermissionDeniedError

ROLE_PERMISSIONS: dict[RoleName, frozenset[str]] = {
    RoleName.AGENCY_OWNER: frozenset(
        {"workspace:*", "members:*", "clients:*", "brand:*", "audit:read"}
    ),
    RoleName.AGENCY_ADMIN: frozenset(
        {"workspace:read", "members:manage", "clients:*", "brand:*", "audit:read"}
    ),
    RoleName.AGENCY_MEMBER: frozenset(
        {"workspace:read", "clients:read", "brand:read", "brand:write"}
    ),
    RoleName.CREATOR_OWNER: frozenset({"workspace:*", "clients:*", "brand:*", "audit:read"}),
    RoleName.CLIENT_REVIEWER: frozenset({"client:read", "brand:read", "deliverables:approve"}),
    RoleName.CLIENT_VIEWER: frozenset({"client:read", "brand:read"}),
}


def get_permissions_for_role(role_name: RoleName | str) -> frozenset[str]:
    """Return canonical permission strings granted by a role."""
    resolved = role_name if isinstance(role_name, RoleName) else RoleName(role_name)
    return ROLE_PERMISSIONS[resolved]


def _matches_permission(granted: str, required: str) -> bool:
    if granted == "*" or granted == required:
        return True
    if granted.endswith(":*"):
        prefix = granted[:-1]
        if required.startswith(prefix):
            return True
    if granted == "members:manage" and required.startswith("members:"):
        return True
    if granted == "brand:write" and required in {"brand:write", "brand:read"}:
        return True
    return False


def has_permission(
    role_name: RoleName | str,
    required: str,
    *,
    extra_permissions: Iterable[str] | None = None,
) -> bool:
    """Evaluate whether a role (and optional DB permissions) satisfies a required permission."""
    granted_set = set(get_permissions_for_role(role_name))
    if extra_permissions:
        granted_set.update(extra_permissions)
    return any(_matches_permission(grant, required) for grant in granted_set)


def require_permission(
    role_name: RoleName | str,
    required: str,
    *,
    extra_permissions: Iterable[str] | None = None,
) -> None:
    """Raise PermissionDeniedError if role does not hold the required permission."""
    if not has_permission(role_name, required, extra_permissions=extra_permissions):
        raise PermissionDeniedError(
            f"Role '{role_name}' lacks required permission '{required}'",
            details={"required_permission": required},
        )
