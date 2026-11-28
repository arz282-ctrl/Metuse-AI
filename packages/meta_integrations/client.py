"""Meta Graph / Unified Login client protocol with Mock and Live adapters (Section 2.4 & 8.3).

Strictly separate from Meta Muse (Model API) — never accepts or reads MUSE_API_KEY.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from packages.meta_integrations.contracts import DiscoveredMetaAsset, MetaTokenExchangeResult
from packages.shared.config import BaseAppSettings
from packages.shared.enums import InstagramAccountType, MetaAssetType, MetaConnectionType
from packages.shared.errors import ExternalServiceError, ProviderDisabledError
from packages.shared.scrubbing import scrub_text


class MetaGraphClient(Protocol):
    """Protocol for Meta Unified Login code exchange and asset discovery."""

    @property
    def mode(self) -> str: ...

    def build_authorization_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: list[str],
        config_id: str,
    ) -> str: ...

    def exchange_code_and_discover(
        self,
        *,
        code: str,
        redirect_uri: str,
        connection_type: MetaConnectionType,
        requested_scopes: list[str],
    ) -> MetaTokenExchangeResult: ...

    def verify_token_health(self, *, access_token: str) -> tuple[bool, str | None]: ...


class MockMetaGraphClient:
    """$0 deterministic offline Meta Unified Login simulator for dev/CI & acceptance tests #3-#6."""

    @property
    def mode(self) -> str:
        return "mock"

    def build_authorization_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: list[str],
        config_id: str,
    ) -> str:
        params = urlencode(
            {
                "client_id": "mock_meta_app_id",
                "redirect_uri": redirect_uri,
                "state": state,
                "response_type": "code",
                "scope": ",".join(scopes),
                "config_id": config_id or "mock_unified_config",
            }
        )
        return f"https://www.facebook.com/v22.0/dialog/oauth?{params}"

    def exchange_code_and_discover(
        self,
        *,
        code: str,
        redirect_uri: str,
        connection_type: MetaConnectionType,
        requested_scopes: list[str],
    ) -> MetaTokenExchangeResult:
        _ = redirect_uri
        cleaned = code.strip()
        expires = datetime.now(UTC) + timedelta(days=60)

        # Acceptance #3: Personal Instagram account (ineligible -> blocked with conversion guidance)
        if cleaned == "mock_personal_ig":
            return MetaTokenExchangeResult(
                access_token="EAA_MOCK_PERSONAL_NEVER_STORED_0001",  # noqa: S106
                token_type="user_long_lived",  # noqa: S106
                expires_at=expires,
                meta_business_id=None,
                granted_scopes=["instagram_basic"],
                discovered_assets=[
                    DiscoveredMetaAsset(
                        asset_type=MetaAssetType.INSTAGRAM_PROFESSIONAL,
                        external_asset_id="ig_personal_9001",
                        display_name="Personal Creator Profile",
                        username="personal_user",
                        instagram_account_type=InstagramAccountType.PERSONAL,
                        is_selected=True,
                    )
                ],
            )

        # Acceptance #6: Missing publish permission (only instagram_basic granted)
        if cleaned == "mock_missing_publish_scope":
            return MetaTokenExchangeResult(
                access_token="EAA_MOCK_PARTIAL_SCOPE_TOKEN_0006",  # noqa: S106
                token_type="user_long_lived",  # noqa: S106
                expires_at=expires,
                meta_business_id=None,
                granted_scopes=["instagram_basic"],
                discovered_assets=[
                    DiscoveredMetaAsset(
                        asset_type=MetaAssetType.INSTAGRAM_PROFESSIONAL,
                        external_asset_id="ig_creator_6001",
                        display_name="Limited Scope Creator",
                        username="limited_creator",
                        instagram_account_type=InstagramAccountType.CREATOR,
                        is_selected=True,
                    )
                ],
            )

        # Acceptance #5 & #16: Agency Business portfolio with selected IG Pro + optional Ad Account
        if cleaned in {"mock_agency_biz", "mock_agency_biz_manage"} or (
            connection_type == MetaConnectionType.AGENCY_BUSINESS
        ):
            granted = list(dict.fromkeys(requested_scopes or ["instagram_basic", "ads_read"]))
            if cleaned == "mock_agency_biz_manage":
                for scope in ("ads_read", "ads_management"):
                    if scope not in granted:
                        granted.append(scope)
            assets = [
                DiscoveredMetaAsset(
                    asset_type=MetaAssetType.INSTAGRAM_PROFESSIONAL,
                    external_asset_id="ig_biz_5001",
                    display_name="Client Brand IG",
                    username="client_brand_official",
                    instagram_account_type=InstagramAccountType.BUSINESS,
                    meta_business_id="biz_portfolio_7701",
                    is_selected=True,
                ),
                DiscoveredMetaAsset(
                    asset_type=MetaAssetType.FACEBOOK_PAGE,
                    external_asset_id="page_5001",
                    display_name="Client Brand Page",
                    meta_business_id="biz_portfolio_7701",
                    is_selected=True,
                ),
                # Unselected asset in portfolio must NOT become available (Acceptance #5)
                DiscoveredMetaAsset(
                    asset_type=MetaAssetType.INSTAGRAM_PROFESSIONAL,
                    external_asset_id="ig_unshared_9999",
                    display_name="Unshared Sister Brand",
                    username="unshared_brand",
                    instagram_account_type=InstagramAccountType.BUSINESS,
                    meta_business_id="biz_portfolio_7701",
                    is_selected=False,
                ),
            ]
            if "ads_read" in granted or "ads_management" in granted:
                assets.append(
                    DiscoveredMetaAsset(
                        asset_type=MetaAssetType.AD_ACCOUNT,
                        external_asset_id="act_5501",
                        display_name="Client Brand Ad Account",
                        meta_business_id="biz_portfolio_7701",
                        is_selected=True,
                    )
                )
            return MetaTokenExchangeResult(
                access_token="EAA_MOCK_AGENCY_SYSTEM_USER_TOKEN_5001",  # noqa: S106
                token_type="system_user",  # noqa: S106
                expires_at=None,
                meta_business_id="biz_portfolio_7701",
                granted_scopes=granted,
                discovered_assets=assets,
            )

        # Acceptance #4: Creator Professional account WITHOUT ad account
        granted = list(
            dict.fromkeys(
                requested_scopes
                or [
                    "instagram_basic",
                    "instagram_content_publish",
                    "instagram_manage_insights",
                ]
            )
        )
        return MetaTokenExchangeResult(
            access_token="EAA_MOCK_CREATOR_PRO_TOKEN_4001",  # noqa: S106
            token_type="user_long_lived",  # noqa: S106
            expires_at=expires,
            meta_business_id=None,
            granted_scopes=granted,
            discovered_assets=[
                DiscoveredMetaAsset(
                    asset_type=MetaAssetType.INSTAGRAM_PROFESSIONAL,
                    external_asset_id="ig_creator_4001",
                    display_name="Studio Creator Pro",
                    username="studio_creator_pro",
                    instagram_account_type=InstagramAccountType.CREATOR,
                    is_selected=True,
                )
            ],
        )

    def verify_token_health(self, *, access_token: str) -> tuple[bool, str | None]:
        if "REVOKED" in access_token:
            return False, "Token revoked or asset moved to another business portfolio"
        return True, None


class LiveMetaGraphClient:
    """Live Meta Graph / Unified Login client (enabled only when META_APP_ID & SECRET are set)."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        graph_base_url: str = "https://graph.facebook.com",
        api_version: str = "v22.0",
        timeout_seconds: float = 20.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._app_id = app_id.strip()
        self._app_secret = app_secret.strip()
        self._graph_base_url = graph_base_url.rstrip("/")
        self._api_version = api_version.strip()
        self._timeout = timeout_seconds
        self._http_client = http_client

    @property
    def mode(self) -> str:
        return "live"

    def _ensure_configured(self) -> None:
        if not self._app_id or not self._app_secret:
            raise ProviderDisabledError(
                "Live Meta Graph integration requires META_APP_ID and META_APP_SECRET"
            )

    def build_authorization_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: list[str],
        config_id: str,
    ) -> str:
        self._ensure_configured()
        params: dict[str, str] = {
            "client_id": self._app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": ",".join(scopes),
        }
        if config_id:
            params["config_id"] = config_id
        return f"https://www.facebook.com/{self._api_version}/dialog/oauth?{urlencode(params)}"

    def exchange_code_and_discover(
        self,
        *,
        code: str,
        redirect_uri: str,
        connection_type: MetaConnectionType,
        requested_scopes: list[str],
    ) -> MetaTokenExchangeResult:
        self._ensure_configured()
        client = self._http_client or httpx.Client(timeout=self._timeout)
        close_after = self._http_client is None
        try:
            token_resp = client.get(
                f"{self._graph_base_url}/{self._api_version}/oauth/access_token",
                params={
                    "client_id": self._app_id,
                    "client_secret": self._app_secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
            )
            if token_resp.status_code >= 400:
                raise ExternalServiceError(
                    scrub_text(f"Meta OAuth code exchange failed (HTTP {token_resp.status_code})")
                )
            token_data: dict[str, Any] = token_resp.json()
            access_token = str(token_data.get("access_token") or "").strip()
            if not access_token:
                raise ExternalServiceError("Meta OAuth response did not include an access_token")

            expires_in = token_data.get("expires_in")
            expires_at = (
                datetime.now(UTC) + timedelta(seconds=int(expires_in))
                if isinstance(expires_in, int | float) and expires_in > 0
                else None
            )

            # Inspect granted permissions server-side
            perm_resp = client.get(
                f"{self._graph_base_url}/{self._api_version}/me/permissions",
                params={"access_token": access_token},
            )
            granted_scopes: list[str] = []
            if perm_resp.status_code == 200:
                for row in perm_resp.json().get("data", []):
                    if row.get("status") == "granted" and row.get("permission"):
                        granted_scopes.append(str(row["permission"]))
            if not granted_scopes:
                granted_scopes = list(requested_scopes)

            # Discover connected Pages & linked Instagram Business/Creator accounts
            pages_resp = client.get(
                f"{self._graph_base_url}/{self._api_version}/me/accounts",
                params={
                    "fields": "id,name,instagram_business_account{id,username,name,account_type}",
                    "access_token": access_token,
                },
            )
            discovered: list[DiscoveredMetaAsset] = []
            if pages_resp.status_code == 200:
                for page in pages_resp.json().get("data", []):
                    page_id = str(page.get("id") or "")
                    if page_id:
                        discovered.append(
                            DiscoveredMetaAsset(
                                asset_type=MetaAssetType.FACEBOOK_PAGE,
                                external_asset_id=page_id,
                                display_name=str(page.get("name") or page_id),
                                is_selected=True,
                            )
                        )
                    ig = page.get("instagram_business_account")
                    if isinstance(ig, dict) and ig.get("id"):
                        raw_type = str(ig.get("account_type") or "business").lower()
                        ig_type = (
                            InstagramAccountType.CREATOR
                            if "creator" in raw_type
                            else InstagramAccountType.BUSINESS
                            if "business" in raw_type
                            else InstagramAccountType.PERSONAL
                        )
                        discovered.append(
                            DiscoveredMetaAsset(
                                asset_type=MetaAssetType.INSTAGRAM_PROFESSIONAL,
                                external_asset_id=str(ig["id"]),
                                display_name=str(ig.get("name") or ig.get("username") or ig["id"]),
                                username=str(ig["username"]) if ig.get("username") else None,
                                instagram_account_type=ig_type,
                                is_selected=True,
                            )
                        )

            return MetaTokenExchangeResult(
                access_token=access_token,
                token_type=(
                    "system_user"
                    if connection_type == MetaConnectionType.AGENCY_BUSINESS
                    else "user_long_lived"
                ),
                expires_at=expires_at,
                granted_scopes=granted_scopes,
                discovered_assets=discovered,
            )
        finally:
            if close_after:
                client.close()

    def verify_token_health(self, *, access_token: str) -> tuple[bool, str | None]:
        self._ensure_configured()
        client = self._http_client or httpx.Client(timeout=self._timeout)
        close_after = self._http_client is None
        try:
            resp = client.get(
                f"{self._graph_base_url}/{self._api_version}/me",
                params={"fields": "id", "access_token": access_token},
            )
            if resp.status_code == 200:
                return True, None
            return False, f"Meta token verification failed (HTTP {resp.status_code})"
        finally:
            if close_after:
                client.close()


def create_meta_graph_client(settings: BaseAppSettings) -> MetaGraphClient:
    """Instantiate MockMetaGraphClient (default $0) or LiveMetaGraphClient."""
    if settings.meta_connections_mode.strip().lower() == "live":
        return LiveMetaGraphClient(
            app_id=settings.meta_app_id,
            app_secret=settings.meta_app_secret.get_secret_value(),
            graph_base_url=settings.meta_graph_base_url,
            api_version=settings.meta_graph_api_version,
        )
    return MockMetaGraphClient()
