"""Meta Permission Matrix, Capability Gating, and Eligibility Guidance (Sections 2.1, 2.3, 8.4)."""

from __future__ import annotations

from collections.abc import Iterable

from packages.meta_integrations.contracts import DiscoveredMetaAsset, PermissionMatrixEntry
from packages.shared.enums import ErrorCode, InstagramAccountType, MetaAssetType
from packages.shared.errors import MetuseError

PERSONAL_IG_CONVERSION_STEPS: tuple[str, ...] = (
    "Open the Instagram mobile app and go to Settings and privacy -> Account type and tools.",
    "Select 'Switch to professional account' and choose either Creator or Business.",
    "Confirm your category and contact details (no ad account required for organic creators).",
    "Return to Metuse AI Connections and click Retry Meta Login.",
)

PERMISSION_MATRIX: tuple[PermissionMatrixEntry, ...] = (
    PermissionMatrixEntry(
        capability="page_list",
        permission="pages_show_list",
        description="List managed Pages so linked Instagram Pro accounts can be discovered.",
        required_for_creator_organic=True,
        required_for_agency_ads=True,
    ),
    PermissionMatrixEntry(
        capability="instagram_read",
        permission="instagram_basic",
        description="Read connected Instagram Professional profile and media metadata.",
        required_for_creator_organic=True,
    ),
    PermissionMatrixEntry(
        capability="instagram_publish",
        permission="instagram_content_publish",
        description="Publish approved Reels, images, and carousels to Instagram Pro accounts.",
        required_for_creator_organic=True,
    ),
    PermissionMatrixEntry(
        capability="instagram_insights",
        permission="instagram_manage_insights",
        description="Read organic account and media performance insights.",
        required_for_creator_organic=True,
    ),
    PermissionMatrixEntry(
        capability="instagram_comments",
        permission="instagram_manage_comments",
        description="Read and moderate comments on connected Instagram posts.",
    ),
    PermissionMatrixEntry(
        capability="instagram_messages",
        permission="instagram_manage_messages",
        description="Read and reply to Instagram direct messages where enabled.",
    ),
    PermissionMatrixEntry(
        capability="meta_ads_read",
        permission="ads_read",
        description="Read-only ad account discovery, campaign structure, and ad insights.",
        required_for_agency_ads=True,
    ),
    PermissionMatrixEntry(
        capability="meta_ads_manage",
        permission="ads_management",
        description="Create or update campaigns only after explicit human approval.",
        required_for_agency_ads=True,
    ),
    PermissionMatrixEntry(
        capability="business_portfolio",
        permission="business_management",
        description="Discover shared business portfolio assets for agency client onboarding.",
        required_for_agency_ads=True,
    ),
)

CAPABILITY_REQUIRED_SCOPES: dict[str, tuple[str, ...]] = {
    "page_list": ("pages_show_list",),
    "instagram_publish": ("instagram_basic", "instagram_content_publish"),
    "instagram_insights": ("instagram_basic", "instagram_manage_insights"),
    "instagram_comments": ("instagram_basic", "instagram_manage_comments"),
    "instagram_messages": ("instagram_basic", "instagram_manage_messages"),
    "meta_ads_read": ("ads_read",),
    "meta_ads_manage": ("ads_read", "ads_management"),
}


def default_scopes_for_connection(
    *,
    include_ads: bool = False,
    include_comments: bool = False,
    include_messages: bool = False,
    is_agency: bool = False,
) -> list[str]:
    """Compute minimal least-privilege Meta scopes for onboarding (Section 2.3)."""
    scopes = [
        "pages_show_list",
        "instagram_basic",
        "instagram_content_publish",
        "instagram_manage_insights",
    ]
    if is_agency:
        scopes.append("business_management")
    if include_comments:
        scopes.append("instagram_manage_comments")
    if include_messages:
        scopes.append("instagram_manage_messages")
    if include_ads:
        scopes.append("ads_read")
    return scopes


def compute_capabilities(
    *,
    granted_scopes: Iterable[str],
    assets: Iterable[DiscoveredMetaAsset] | None = None,
    has_instagram_professional: bool | None = None,
    has_ad_account: bool | None = None,
) -> tuple[dict[str, bool], dict[str, list[str]]]:
    """Compute exact Section 8.4 capability flags from granted scopes AND eligible assets."""
    scope_set = {s.strip() for s in granted_scopes if s.strip()}

    ig_ready = has_instagram_professional
    ads_ready = has_ad_account
    if assets is not None:
        asset_list = list(assets)
        ig_ready = any(
            a.asset_type == MetaAssetType.INSTAGRAM_PROFESSIONAL
            and a.is_selected
            and a.instagram_account_type
            in {InstagramAccountType.BUSINESS, InstagramAccountType.CREATOR}
            for a in asset_list
        )
        ads_ready = any(
            a.asset_type == MetaAssetType.AD_ACCOUNT and a.is_selected for a in asset_list
        )

    ig_ready_bool = bool(ig_ready)
    ads_ready_bool = bool(ads_ready)

    capabilities: dict[str, bool] = {}
    missing_map: dict[str, list[str]] = {}
    for cap_name, required_scopes in CAPABILITY_REQUIRED_SCOPES.items():
        missing = [s for s in required_scopes if s not in scope_set]
        asset_ok = ads_ready_bool if cap_name.startswith("meta_ads_") else ig_ready_bool
        enabled = len(missing) == 0 and asset_ok
        capabilities[cap_name] = enabled
        if missing:
            missing_map[cap_name] = missing
        elif not asset_ok:
            missing_map[cap_name] = [
                "connected_ad_account"
                if cap_name.startswith("meta_ads_")
                else "connected_instagram_professional_account"
            ]

    return capabilities, missing_map


def require_meta_capability(
    *,
    capabilities: dict[str, bool],
    granted_scopes: Iterable[str],
    required_capability: str,
) -> None:
    """Enforce backend Meta capability gating (Acceptance #6 — never rely on hiding UI buttons)."""
    if capabilities.get(required_capability, False):
        return

    required_scopes = CAPABILITY_REQUIRED_SCOPES.get(required_capability, ())
    scope_set = set(granted_scopes)
    missing_scopes = [s for s in required_scopes if s not in scope_set]
    raise MetuseError(
        f"Meta capability '{required_capability}' is disabled for this connection. "
        "Reconnect Meta and grant the missing permission(s) or asset access.",
        code=ErrorCode.META_PERMISSION_MISSING,
        status_code=403,
        details={
            "required_capability": required_capability,
            "missing_permissions": missing_scopes,
            "action": "reconnect_meta_and_grant_permissions",
        },
    )
