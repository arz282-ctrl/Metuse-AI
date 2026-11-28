"""Phase 7 Meta Ads Read, Campaign Drafts & Approval-Gated Execution (Sections 8.6, 9, 16 #16).

Strictly separated from organic publishing:
- Read-only ad account discovery & campaign/ad-set/ad hierarchy requires `meta_ads_read`.
- Campaign drafts and validation run locally with zero spend impact.
- Any external campaign creation or budget change requires:
  1. `ads_kill_switch` disabled,
  2. Backend `meta_ads_manage` capability verified,
  3. Explicit version/hash-bound human `Approval` (`campaign_execution` or `budget_change`),
  4. Explicit human confirmation flag for budget mutations (never auto-increases spend),
  5. Remote-state re-read before retrying ambiguous timeouts, and
  6. Append-only `AuditLog` with before/after values and actor attribution.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    AdAccount,
    AdCampaignDraft,
    AdCampaignSnapshot,
    AdSetSnapshot,
    AdSnapshot,
    Approval,
    AuditLog,
    MetaConnectedAsset,
    MetaConnection,
)
from packages.meta_integrations.capabilities import require_meta_capability
from packages.meta_integrations.secret_store import SecretStore
from packages.meta_integrations.service import MetaConnectionService
from packages.shared.config import BaseAppSettings
from packages.shared.enums import (
    AdDraftStatus,
    AdObjective,
    ApprovalStatus,
    ApprovalType,
    AuditAction,
    ErrorCode,
    MetaAssetType,
    MetaConnectionStatus,
)
from packages.shared.errors import (
    ConflictError,
    MetuseError,
    NotFoundError,
    ValidationDomainError,
)


class AmbiguousAdsTimeoutError(RuntimeError):
    """Raised when an external Meta Ads mutation times out with ambiguous remote state."""


@dataclass(frozen=True)
class RemoteAdAccountData:
    external_account_id: str
    name: str
    currency: str
    timezone_name: str
    account_status: str
    spend_cap_cents: int | None
    amount_spent_cents: int


@dataclass(frozen=True)
class RemoteAdData:
    external_ad_id: str
    name: str
    status: str
    creative_summary: dict[str, Any]
    spend_cents: int
    impressions: int
    clicks: int


@dataclass(frozen=True)
class RemoteAdSetData:
    external_ad_set_id: str
    name: str
    status: str
    optimization_goal: str
    daily_budget_cents: int | None
    targeting: dict[str, Any]
    spend_cents: int
    impressions: int
    clicks: int
    ads: tuple[RemoteAdData, ...]


@dataclass(frozen=True)
class RemoteCampaignData:
    external_campaign_id: str
    name: str
    objective: str
    status: str
    daily_budget_cents: int | None
    lifetime_budget_cents: int | None
    spend_cents: int
    impressions: int
    clicks: int
    conversions: int
    ad_sets: tuple[RemoteAdSetData, ...] = ()
    client_ref: str | None = None


class MetaAdsClient(Protocol):
    """Provider-independent Meta Marketing API client boundary."""

    def fetch_ad_account(
        self, *, access_token: str, external_account_id: str
    ) -> RemoteAdAccountData: ...

    def fetch_campaign_hierarchy(
        self, *, access_token: str, external_account_id: str
    ) -> list[RemoteCampaignData]: ...

    def create_campaign(
        self,
        *,
        access_token: str,
        external_account_id: str,
        name: str,
        objective: str,
        daily_budget_cents: int,
        targeting: dict[str, Any],
        client_ref: str,
    ) -> RemoteCampaignData: ...

    def find_campaign_by_client_ref(
        self, *, access_token: str, external_account_id: str, client_ref: str
    ) -> RemoteCampaignData | None: ...

    def update_campaign_daily_budget(
        self,
        *,
        access_token: str,
        external_account_id: str,
        external_campaign_id: str,
        new_daily_budget_cents: int,
    ) -> RemoteCampaignData: ...

    def get_remote_campaign(
        self,
        *,
        access_token: str,
        external_account_id: str,
        external_campaign_id: str,
    ) -> RemoteCampaignData | None: ...


class MockMetaAdsClient:
    """Deterministic zero-cost Meta Marketing API mock with ambiguous-timeout simulation."""

    def __init__(self, *, simulate_ambiguous_timeout_once: bool = False) -> None:
        self._simulate_timeout = simulate_ambiguous_timeout_once
        self._timed_out_create_refs: set[str] = set()
        self._timed_out_budget_keys: set[str] = set()
        self._campaigns_by_account: dict[str, dict[str, RemoteCampaignData]] = {}
        self.create_call_count: int = 0
        self.budget_update_call_count: int = 0
        self.remote_state_check_count: int = 0

    def _seed_account_if_needed(self, external_account_id: str) -> dict[str, RemoteCampaignData]:
        if external_account_id not in self._campaigns_by_account:
            acct_suffix = external_account_id.replace("act_", "")[-4:]
            camp_id = f"cmp_{acct_suffix}_launch_01"
            ad_set_id = f"adset_{acct_suffix}_reels_01"
            ad_id = f"ad_{acct_suffix}_hook_a"
            self._campaigns_by_account[external_account_id] = {
                camp_id: RemoteCampaignData(
                    external_campaign_id=camp_id,
                    name="Q4 Reels Awareness Launch",
                    objective=AdObjective.OUTCOME_AWARENESS.value,
                    status="PAUSED",
                    daily_budget_cents=5000,
                    lifetime_budget_cents=None,
                    spend_cents=12450,
                    impressions=48200,
                    clicks=1390,
                    conversions=42,
                    ad_sets=(
                        RemoteAdSetData(
                            external_ad_set_id=ad_set_id,
                            name="US Creators 21-40",
                            status="PAUSED",
                            optimization_goal="REACH",
                            daily_budget_cents=5000,
                            targeting={"countries": ["US"], "age_min": 21, "age_max": 40},
                            spend_cents=12450,
                            impressions=48200,
                            clicks=1390,
                            ads=(
                                RemoteAdData(
                                    external_ad_id=ad_id,
                                    name="Hook A — Studio Cut",
                                    status="PAUSED",
                                    creative_summary={
                                        "format": "reels_9x16",
                                        "cta": "LEARN_MORE",
                                    },
                                    spend_cents=12450,
                                    impressions=48200,
                                    clicks=1390,
                                ),
                            ),
                        ),
                    ),
                )
            }
        return self._campaigns_by_account[external_account_id]

    def fetch_ad_account(
        self, *, access_token: str, external_account_id: str
    ) -> RemoteAdAccountData:
        _ = access_token
        campaigns = self._seed_account_if_needed(external_account_id)
        total_spent = sum(c.spend_cents for c in campaigns.values())
        return RemoteAdAccountData(
            external_account_id=external_account_id,
            name=f"Metuse Ad Account ({external_account_id})",
            currency="USD",
            timezone_name="America/New_York",
            account_status="active",
            spend_cap_cents=500_000,
            amount_spent_cents=total_spent,
        )

    def fetch_campaign_hierarchy(
        self, *, access_token: str, external_account_id: str
    ) -> list[RemoteCampaignData]:
        _ = access_token
        campaigns = self._seed_account_if_needed(external_account_id)
        return list(campaigns.values())

    def create_campaign(
        self,
        *,
        access_token: str,
        external_account_id: str,
        name: str,
        objective: str,
        daily_budget_cents: int,
        targeting: dict[str, Any],
        client_ref: str,
    ) -> RemoteCampaignData:
        _ = (access_token, targeting)
        self.create_call_count += 1
        campaigns = self._seed_account_if_needed(external_account_id)
        remote_id = f"cmp_live_{hashlib.sha256(client_ref.encode()).hexdigest()[:12]}"
        created = RemoteCampaignData(
            external_campaign_id=remote_id,
            name=name,
            objective=objective,
            status="PAUSED",
            daily_budget_cents=daily_budget_cents,
            lifetime_budget_cents=None,
            spend_cents=0,
            impressions=0,
            clicks=0,
            conversions=0,
            ad_sets=(),
            client_ref=client_ref,
        )
        campaigns[remote_id] = created
        if self._simulate_timeout and client_ref not in self._timed_out_create_refs:
            self._timed_out_create_refs.add(client_ref)
            raise AmbiguousAdsTimeoutError(
                "Simulated Meta Marketing API timeout after remote write"
            )
        return created

    def find_campaign_by_client_ref(
        self, *, access_token: str, external_account_id: str, client_ref: str
    ) -> RemoteCampaignData | None:
        _ = access_token
        self.remote_state_check_count += 1
        campaigns = self._seed_account_if_needed(external_account_id)
        for camp in campaigns.values():
            if camp.client_ref == client_ref:
                return camp
        return None

    def update_campaign_daily_budget(
        self,
        *,
        access_token: str,
        external_account_id: str,
        external_campaign_id: str,
        new_daily_budget_cents: int,
    ) -> RemoteCampaignData:
        _ = access_token
        self.budget_update_call_count += 1
        campaigns = self._seed_account_if_needed(external_account_id)
        existing = campaigns.get(external_campaign_id)
        if existing is None:
            raise NotFoundError(
                "Remote campaign not found",
                details={"external_campaign_id": external_campaign_id},
            )
        updated = RemoteCampaignData(
            external_campaign_id=existing.external_campaign_id,
            name=existing.name,
            objective=existing.objective,
            status=existing.status,
            daily_budget_cents=new_daily_budget_cents,
            lifetime_budget_cents=existing.lifetime_budget_cents,
            spend_cents=existing.spend_cents,
            impressions=existing.impressions,
            clicks=existing.clicks,
            conversions=existing.conversions,
            ad_sets=existing.ad_sets,
            client_ref=existing.client_ref,
        )
        campaigns[external_campaign_id] = updated
        timeout_key = f"{external_campaign_id}:{new_daily_budget_cents}"
        if self._simulate_timeout and timeout_key not in self._timed_out_budget_keys:
            self._timed_out_budget_keys.add(timeout_key)
            raise AmbiguousAdsTimeoutError("Simulated Meta budget mutation timeout after write")
        return updated

    def get_remote_campaign(
        self,
        *,
        access_token: str,
        external_account_id: str,
        external_campaign_id: str,
    ) -> RemoteCampaignData | None:
        _ = access_token
        self.remote_state_check_count += 1
        campaigns = self._seed_account_if_needed(external_account_id)
        return campaigns.get(external_campaign_id)


# ---------------------------------------------------------------------------
# Response contracts
# ---------------------------------------------------------------------------


class AdAccountView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    meta_connection_id: UUID
    external_account_id: str
    name: str
    currency: str
    timezone_name: str
    account_status: str
    spend_cap_cents: int | None = None
    amount_spent_cents: int
    last_synced_at: datetime | None = None


class AdSnapshotView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    external_ad_id: str
    name: str
    status: str
    creative_summary: dict[str, Any] = Field(default_factory=dict)
    spend_cents: int
    impressions: int
    clicks: int


class AdSetSnapshotView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    external_ad_set_id: str
    name: str
    status: str
    optimization_goal: str
    daily_budget_cents: int | None = None
    targeting: dict[str, Any] = Field(default_factory=dict)
    spend_cents: int
    impressions: int
    clicks: int
    ads: list[AdSnapshotView] = Field(default_factory=list)


class AdCampaignSnapshotView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    ad_account_id: UUID
    external_campaign_id: str
    name: str
    objective: str
    status: str
    daily_budget_cents: int | None = None
    lifetime_budget_cents: int | None = None
    spend_cents: int
    impressions: int
    clicks: int
    conversions: int
    captured_at: datetime
    ad_sets: list[AdSetSnapshotView] = Field(default_factory=list)


class AdDraftView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    ad_account_id: UUID
    name: str
    objective: str
    status: str
    daily_budget_cents: int
    targeting: dict[str, Any] = Field(default_factory=dict)
    creative_refs: list[str] = Field(default_factory=list)
    version_number: int
    content_hash: str
    validation_passed: bool
    validation_report: dict[str, Any] = Field(default_factory=dict)
    approval_id: UUID | None = None
    remote_campaign_id: str | None = None
    executed_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class AdApprovalRequestView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    approval_type: str
    target_type: str
    target_id: UUID
    target_version_sha256: str
    status: str


class AdExecutionResultView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: AdDraftView
    campaign: AdCampaignSnapshotView
    approval_id: UUID
    verified_via_remote_check: bool


class BudgetMutationResultView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign: AdCampaignSnapshotView
    previous_daily_budget_cents: int | None = None
    new_daily_budget_cents: int
    approval_id: UUID
    verified_via_remote_check: bool


class AdsSyncSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounts_synced: int
    campaigns_synced: int
    ad_sets_synced: int
    ads_synced: int
    accounts: list[AdAccountView] = Field(default_factory=list)


def compute_draft_content_hash(
    *,
    ad_account_id: UUID,
    name: str,
    objective: str,
    daily_budget_cents: int,
    targeting: dict[str, Any],
    creative_refs: list[str],
) -> str:
    """Compute canonical SHA-256 fingerprint binding human approvals to exact draft parameters."""
    payload = {
        "ad_account_id": str(ad_account_id),
        "name": name.strip(),
        "objective": objective,
        "daily_budget_cents": daily_budget_cents,
        "targeting": targeting,
        "creative_refs": sorted(creative_refs),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compute_budget_change_hash(
    *,
    campaign_id: UUID,
    previous_daily_budget_cents: int | None,
    new_daily_budget_cents: int,
) -> str:
    """Compute canonical SHA-256 binding a budget_change approval to exact before/after spend."""
    payload = {
        "campaign_id": str(campaign_id),
        "previous_daily_budget_cents": previous_daily_budget_cents,
        "new_daily_budget_cents": new_daily_budget_cents,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _account_view(row: AdAccount) -> AdAccountView:
    return AdAccountView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        meta_connection_id=row.meta_connection_id,
        external_account_id=row.external_account_id,
        name=row.name,
        currency=row.currency,
        timezone_name=row.timezone_name,
        account_status=row.account_status,
        spend_cap_cents=row.spend_cap_cents,
        amount_spent_cents=row.amount_spent_cents,
        last_synced_at=row.last_synced_at,
    )


def _draft_view(row: AdCampaignDraft) -> AdDraftView:
    return AdDraftView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        ad_account_id=row.ad_account_id,
        name=row.name,
        objective=row.objective,
        status=row.status,
        daily_budget_cents=row.daily_budget_cents,
        targeting=dict(row.targeting_json or {}),
        creative_refs=list(row.creative_refs_json or []),
        version_number=row.version_number,
        content_hash=row.content_hash,
        validation_passed=row.validation_passed,
        validation_report=dict(row.validation_report or {}),
        approval_id=row.approval_id,
        remote_campaign_id=row.remote_campaign_id,
        executed_at=row.executed_at,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class AdsOperationsService:
    """Tenant-scoped Meta Ads discovery, draft validation & approval-gated execution service."""

    def __init__(
        self,
        *,
        settings: BaseAppSettings,
        meta_service: MetaConnectionService,
        secret_store: SecretStore,
        ads_client: MetaAdsClient | None = None,
    ) -> None:
        self._settings = settings
        self._meta_service = meta_service
        self._secret_store = secret_store
        self._ads_client: MetaAdsClient = ads_client or MockMetaAdsClient()

    def _require_connection_capability(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        capability: str,
    ) -> tuple[MetaConnection, str]:
        conn = session.execute(
            select(MetaConnection).where(
                MetaConnection.workspace_id == workspace_id,
                MetaConnection.id == connection_id,
            )
        ).scalar_one_or_none()
        if conn is None:
            raise NotFoundError(
                "Meta connection not found", details={"meta_connection_id": str(connection_id)}
            )
        if conn.status != MetaConnectionStatus.ACTIVE.value:
            raise MetuseError(
                f"Meta connection is '{conn.status}'; reconnect before running Ads operations.",
                code=ErrorCode.META_RECONNECT_REQUIRED,
                status_code=403,
            )
        require_meta_capability(
            capabilities=dict(conn.capabilities or {}),
            granted_scopes=list(conn.granted_scopes or []),
            required_capability=capability,
        )
        token = self._secret_store.get_secret(secret_ref=conn.token_secret_ref)
        return conn, token

    def _get_ad_account_row(
        self, session: Session, *, workspace_id: UUID, ad_account_id: UUID
    ) -> AdAccount:
        row = session.execute(
            select(AdAccount).where(
                AdAccount.workspace_id == workspace_id,
                AdAccount.id == ad_account_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                "Ad account not found", details={"ad_account_id": str(ad_account_id)}
            )
        return row

    def _get_draft_row(
        self, session: Session, *, workspace_id: UUID, draft_id: UUID
    ) -> AdCampaignDraft:
        row = session.execute(
            select(AdCampaignDraft).where(
                AdCampaignDraft.workspace_id == workspace_id,
                AdCampaignDraft.id == draft_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Ad campaign draft not found", details={"draft_id": str(draft_id)})
        return row

    def _get_campaign_row(
        self, session: Session, *, workspace_id: UUID, campaign_id: UUID
    ) -> AdCampaignSnapshot:
        row = session.execute(
            select(AdCampaignSnapshot).where(
                AdCampaignSnapshot.workspace_id == workspace_id,
                AdCampaignSnapshot.id == campaign_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                "Ad campaign snapshot not found", details={"campaign_id": str(campaign_id)}
            )
        return row

    # ------------------------------------------------------------------
    # 7.1 Read-Only Discovery & Performance Hierarchy
    # ------------------------------------------------------------------

    def sync_ad_accounts_and_snapshots(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        client_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> AdsSyncSummaryView:
        """Discover selected ad accounts & sync read-only campaign/ad-set/ad snapshots."""
        stmt = select(MetaConnection).where(
            MetaConnection.workspace_id == workspace_id,
            MetaConnection.status == MetaConnectionStatus.ACTIVE.value,
        )
        if client_id is not None:
            stmt = stmt.where(MetaConnection.client_id == client_id)
        connections = session.execute(stmt).scalars().all()

        eligible_assets: list[tuple[MetaConnection, str, MetaConnectedAsset]] = []
        saw_connection_without_ads_read = False

        for conn in connections:
            caps = dict(conn.capabilities or {})
            if not caps.get("meta_ads_read", False):
                saw_connection_without_ads_read = True
                continue
            assets = (
                session.execute(
                    select(MetaConnectedAsset).where(
                        MetaConnectedAsset.workspace_id == workspace_id,
                        MetaConnectedAsset.connection_id == conn.id,
                        MetaConnectedAsset.asset_type == MetaAssetType.AD_ACCOUNT.value,
                        MetaConnectedAsset.is_selected.is_(True),
                        MetaConnectedAsset.status == "active",
                    )
                )
                .scalars()
                .all()
            )
            if not assets:
                continue
            token = self._secret_store.get_secret(secret_ref=conn.token_secret_ref)
            for asset in assets:
                eligible_assets.append((conn, token, asset))

        if not eligible_assets:
            if saw_connection_without_ads_read:
                raise MetuseError(
                    "Meta capability 'meta_ads_read' is disabled. Reconnect Meta with 'ads_read' "
                    "and select an ad account to enable read-only Ads reporting.",
                    code=ErrorCode.META_PERMISSION_MISSING,
                    status_code=403,
                    details={"required_capability": "meta_ads_read"},
                )
            return AdsSyncSummaryView(
                accounts_synced=0,
                campaigns_synced=0,
                ad_sets_synced=0,
                ads_synced=0,
                accounts=[],
            )

        now = datetime.now(UTC)
        synced_accounts: list[AdAccountView] = []
        campaigns_synced = 0
        ad_sets_synced = 0
        ads_synced = 0

        for conn, token, asset in eligible_assets:
            remote_acct = self._ads_client.fetch_ad_account(
                access_token=token, external_account_id=asset.external_asset_id
            )
            acct_row = session.execute(
                select(AdAccount).where(
                    AdAccount.workspace_id == workspace_id,
                    AdAccount.external_account_id == remote_acct.external_account_id,
                )
            ).scalar_one_or_none()
            if acct_row is None:
                acct_row = AdAccount(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    client_id=asset.client_id or conn.client_id,
                    meta_connection_id=conn.id,
                    external_account_id=remote_acct.external_account_id,
                    name=remote_acct.name,
                )
                session.add(acct_row)

            acct_row.meta_connection_id = conn.id
            acct_row.name = remote_acct.name
            acct_row.currency = remote_acct.currency
            acct_row.timezone_name = remote_acct.timezone_name
            acct_row.account_status = remote_acct.account_status
            acct_row.spend_cap_cents = remote_acct.spend_cap_cents
            acct_row.amount_spent_cents = remote_acct.amount_spent_cents
            acct_row.last_synced_at = now
            session.flush()

            remote_campaigns = self._ads_client.fetch_campaign_hierarchy(
                access_token=token, external_account_id=remote_acct.external_account_id
            )
            for r_camp in remote_campaigns:
                camp_row = self._upsert_campaign_snapshot(
                    session, acct_row=acct_row, remote=r_camp, captured_at=now
                )
                campaigns_synced += 1
                for r_set in r_camp.ad_sets:
                    set_row = session.execute(
                        select(AdSetSnapshot).where(
                            AdSetSnapshot.workspace_id == workspace_id,
                            AdSetSnapshot.ad_account_id == acct_row.id,
                            AdSetSnapshot.external_ad_set_id == r_set.external_ad_set_id,
                        )
                    ).scalar_one_or_none()
                    if set_row is None:
                        set_row = AdSetSnapshot(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            client_id=acct_row.client_id,
                            ad_account_id=acct_row.id,
                            campaign_snapshot_id=camp_row.id,
                            external_ad_set_id=r_set.external_ad_set_id,
                            name=r_set.name,
                        )
                        session.add(set_row)
                    set_row.campaign_snapshot_id = camp_row.id
                    set_row.name = r_set.name
                    set_row.status = r_set.status
                    set_row.optimization_goal = r_set.optimization_goal
                    set_row.daily_budget_cents = r_set.daily_budget_cents
                    set_row.targeting_json = dict(r_set.targeting)
                    set_row.spend_cents = r_set.spend_cents
                    set_row.impressions = r_set.impressions
                    set_row.clicks = r_set.clicks
                    set_row.captured_at = now
                    session.flush()
                    ad_sets_synced += 1

                    for r_ad in r_set.ads:
                        ad_row = session.execute(
                            select(AdSnapshot).where(
                                AdSnapshot.workspace_id == workspace_id,
                                AdSnapshot.ad_account_id == acct_row.id,
                                AdSnapshot.external_ad_id == r_ad.external_ad_id,
                            )
                        ).scalar_one_or_none()
                        if ad_row is None:
                            ad_row = AdSnapshot(
                                id=uuid4(),
                                workspace_id=workspace_id,
                                client_id=acct_row.client_id,
                                ad_account_id=acct_row.id,
                                ad_set_snapshot_id=set_row.id,
                                external_ad_id=r_ad.external_ad_id,
                                name=r_ad.name,
                            )
                            session.add(ad_row)
                        ad_row.ad_set_snapshot_id = set_row.id
                        ad_row.name = r_ad.name
                        ad_row.status = r_ad.status
                        ad_row.creative_summary_json = dict(r_ad.creative_summary)
                        ad_row.spend_cents = r_ad.spend_cents
                        ad_row.impressions = r_ad.impressions
                        ad_row.clicks = r_ad.clicks
                        ad_row.captured_at = now
                        ads_synced += 1

            synced_accounts.append(_account_view(acct_row))

        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.AD_INSIGHTS_SYNCED.value,
                target_type="ad_accounts",
                target_id=str(workspace_id),
                request_id=request_id,
                metadata_json={
                    "accounts_synced": len(synced_accounts),
                    "campaigns_synced": campaigns_synced,
                    "ad_sets_synced": ad_sets_synced,
                    "ads_synced": ads_synced,
                },
            )
        )
        session.flush()
        return AdsSyncSummaryView(
            accounts_synced=len(synced_accounts),
            campaigns_synced=campaigns_synced,
            ad_sets_synced=ad_sets_synced,
            ads_synced=ads_synced,
            accounts=synced_accounts,
        )

    def _upsert_campaign_snapshot(
        self,
        session: Session,
        *,
        acct_row: AdAccount,
        remote: RemoteCampaignData,
        captured_at: datetime,
    ) -> AdCampaignSnapshot:
        camp_row = session.execute(
            select(AdCampaignSnapshot).where(
                AdCampaignSnapshot.workspace_id == acct_row.workspace_id,
                AdCampaignSnapshot.ad_account_id == acct_row.id,
                AdCampaignSnapshot.external_campaign_id == remote.external_campaign_id,
            )
        ).scalar_one_or_none()
        if camp_row is None:
            camp_row = AdCampaignSnapshot(
                id=uuid4(),
                workspace_id=acct_row.workspace_id,
                client_id=acct_row.client_id,
                ad_account_id=acct_row.id,
                external_campaign_id=remote.external_campaign_id,
                name=remote.name,
                objective=remote.objective,
            )
            session.add(camp_row)
        camp_row.name = remote.name
        camp_row.objective = remote.objective
        camp_row.status = remote.status
        camp_row.daily_budget_cents = remote.daily_budget_cents
        camp_row.lifetime_budget_cents = remote.lifetime_budget_cents
        camp_row.spend_cents = remote.spend_cents
        camp_row.impressions = remote.impressions
        camp_row.clicks = remote.clicks
        camp_row.conversions = remote.conversions
        camp_row.captured_at = captured_at
        session.flush()
        return camp_row

    def list_ad_accounts(
        self, session: Session, *, workspace_id: UUID, client_id: UUID | None = None
    ) -> list[AdAccountView]:
        stmt = select(AdAccount).where(AdAccount.workspace_id == workspace_id)
        if client_id is not None:
            stmt = stmt.where(AdAccount.client_id == client_id)
        rows = session.execute(stmt.order_by(AdAccount.created_at.desc())).scalars().all()
        return [_account_view(r) for r in rows]

    def list_campaign_hierarchy(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        ad_account_id: UUID | None = None,
        client_id: UUID | None = None,
    ) -> list[AdCampaignSnapshotView]:
        stmt = select(AdCampaignSnapshot).where(AdCampaignSnapshot.workspace_id == workspace_id)
        if ad_account_id is not None:
            stmt = stmt.where(AdCampaignSnapshot.ad_account_id == ad_account_id)
        if client_id is not None:
            stmt = stmt.where(AdCampaignSnapshot.client_id == client_id)
        campaigns = (
            session.execute(stmt.order_by(AdCampaignSnapshot.captured_at.desc())).scalars().all()
        )
        results: list[AdCampaignSnapshotView] = []
        for camp in campaigns:
            ad_sets = (
                session.execute(
                    select(AdSetSnapshot)
                    .where(
                        AdSetSnapshot.workspace_id == workspace_id,
                        AdSetSnapshot.campaign_snapshot_id == camp.id,
                    )
                    .order_by(AdSetSnapshot.name.asc())
                )
                .scalars()
                .all()
            )
            set_views: list[AdSetSnapshotView] = []
            for s in ad_sets:
                ads = (
                    session.execute(
                        select(AdSnapshot)
                        .where(
                            AdSnapshot.workspace_id == workspace_id,
                            AdSnapshot.ad_set_snapshot_id == s.id,
                        )
                        .order_by(AdSnapshot.name.asc())
                    )
                    .scalars()
                    .all()
                )
                set_views.append(
                    AdSetSnapshotView(
                        id=s.id,
                        external_ad_set_id=s.external_ad_set_id,
                        name=s.name,
                        status=s.status,
                        optimization_goal=s.optimization_goal,
                        daily_budget_cents=s.daily_budget_cents,
                        targeting=dict(s.targeting_json or {}),
                        spend_cents=s.spend_cents,
                        impressions=s.impressions,
                        clicks=s.clicks,
                        ads=[
                            AdSnapshotView(
                                id=a.id,
                                external_ad_id=a.external_ad_id,
                                name=a.name,
                                status=a.status,
                                creative_summary=dict(a.creative_summary_json or {}),
                                spend_cents=a.spend_cents,
                                impressions=a.impressions,
                                clicks=a.clicks,
                            )
                            for a in ads
                        ],
                    )
                )
            results.append(
                AdCampaignSnapshotView(
                    id=camp.id,
                    workspace_id=camp.workspace_id,
                    client_id=camp.client_id,
                    ad_account_id=camp.ad_account_id,
                    external_campaign_id=camp.external_campaign_id,
                    name=camp.name,
                    objective=camp.objective,
                    status=camp.status,
                    daily_budget_cents=camp.daily_budget_cents,
                    lifetime_budget_cents=camp.lifetime_budget_cents,
                    spend_cents=camp.spend_cents,
                    impressions=camp.impressions,
                    clicks=camp.clicks,
                    conversions=camp.conversions,
                    captured_at=camp.captured_at,
                    ad_sets=set_views,
                )
            )
        return results

    # ------------------------------------------------------------------
    # 7.2 Campaign Drafts & Validation (Zero External Spend)
    # ------------------------------------------------------------------

    def create_draft(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        ad_account_id: UUID,
        name: str,
        objective: AdObjective,
        daily_budget_cents: int,
        targeting: dict[str, Any],
        creative_refs: list[str],
        request_id: UUID | None = None,
    ) -> AdDraftView:
        acct = self._get_ad_account_row(
            session, workspace_id=workspace_id, ad_account_id=ad_account_id
        )
        if daily_budget_cents <= 0:
            raise ValidationDomainError("daily_budget_cents must be greater than zero")

        content_hash = compute_draft_content_hash(
            ad_account_id=acct.id,
            name=name,
            objective=objective.value,
            daily_budget_cents=daily_budget_cents,
            targeting=targeting,
            creative_refs=creative_refs,
        )
        draft = AdCampaignDraft(
            id=uuid4(),
            workspace_id=workspace_id,
            client_id=acct.client_id,
            ad_account_id=acct.id,
            created_by_user_id=actor_user_id,
            name=name.strip(),
            objective=objective.value,
            status=AdDraftStatus.DRAFT.value,
            daily_budget_cents=daily_budget_cents,
            targeting_json=dict(targeting),
            creative_refs_json=list(creative_refs),
            version_number=1,
            content_hash=content_hash,
            validation_passed=False,
            validation_report={},
        )
        session.add(draft)
        session.flush()
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=acct.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.AD_DRAFT_CREATED.value,
                target_type="ad_campaign_draft",
                target_id=str(draft.id),
                request_id=request_id,
                metadata_json={
                    "ad_account_id": str(acct.id),
                    "daily_budget_cents": daily_budget_cents,
                    "content_hash": content_hash,
                },
            )
        )
        session.flush()
        return _draft_view(draft)

    def list_drafts(
        self, session: Session, *, workspace_id: UUID, ad_account_id: UUID | None = None
    ) -> list[AdDraftView]:
        stmt = select(AdCampaignDraft).where(AdCampaignDraft.workspace_id == workspace_id)
        if ad_account_id is not None:
            stmt = stmt.where(AdCampaignDraft.ad_account_id == ad_account_id)
        rows = session.execute(stmt.order_by(AdCampaignDraft.created_at.desc())).scalars().all()
        return [_draft_view(r) for r in rows]

    def update_draft(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        draft_id: UUID,
        actor_user_id: UUID,
        name: str | None = None,
        daily_budget_cents: int | None = None,
        targeting: dict[str, Any] | None = None,
        creative_refs: list[str] | None = None,
        request_id: UUID | None = None,
    ) -> AdDraftView:
        """Edit a draft, bump version/hash, and invalidate any prior approval (Section 9)."""
        draft = self._get_draft_row(session, workspace_id=workspace_id, draft_id=draft_id)
        if draft.status == AdDraftStatus.EXECUTED.value:
            raise ConflictError(
                "Cannot edit a campaign draft after execution",
                code=ErrorCode.INVALID_STATE_TRANSITION,
            )

        before = {
            "name": draft.name,
            "daily_budget_cents": draft.daily_budget_cents,
            "targeting": dict(draft.targeting_json or {}),
            "creative_refs": list(draft.creative_refs_json or []),
            "version_number": draft.version_number,
            "content_hash": draft.content_hash,
        }

        if name is not None:
            draft.name = name.strip()
        if daily_budget_cents is not None:
            if daily_budget_cents <= 0:
                raise ValidationDomainError("daily_budget_cents must be greater than zero")
            draft.daily_budget_cents = daily_budget_cents
        if targeting is not None:
            draft.targeting_json = dict(targeting)
        if creative_refs is not None:
            draft.creative_refs_json = list(creative_refs)

        new_hash = compute_draft_content_hash(
            ad_account_id=draft.ad_account_id,
            name=draft.name,
            objective=draft.objective,
            daily_budget_cents=draft.daily_budget_cents,
            targeting=dict(draft.targeting_json or {}),
            creative_refs=list(draft.creative_refs_json or []),
        )
        draft.version_number += 1
        draft.content_hash = new_hash
        draft.validation_passed = False
        draft.validation_report = {}
        draft.status = AdDraftStatus.DRAFT.value

        # Invalidate any existing pending/approved approvals bound to this draft
        now = datetime.now(UTC)
        existing_approvals = (
            session.execute(
                select(Approval).where(
                    Approval.workspace_id == workspace_id,
                    Approval.target_type == "ad_campaign_draft",
                    Approval.target_id == draft.id,
                    Approval.status.in_(
                        [ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value]
                    ),
                )
            )
            .scalars()
            .all()
        )
        for appr in existing_approvals:
            appr.status = ApprovalStatus.INVALIDATED.value
            appr.invalidated_reason = "Campaign draft modified after approval request"
            appr.invalidated_at = now
            session.add(
                AuditLog(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    client_id=draft.client_id,
                    actor_user_id=actor_user_id,
                    action=AuditAction.APPROVAL_INVALIDATED.value,
                    target_type="approval",
                    target_id=str(appr.id),
                    request_id=request_id,
                    metadata_json={
                        "draft_id": str(draft.id),
                        "old_hash": before["content_hash"],
                        "new_hash": new_hash,
                    },
                )
            )
        draft.approval_id = None

        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=draft.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.AD_DRAFT_UPDATED.value,
                target_type="ad_campaign_draft",
                target_id=str(draft.id),
                request_id=request_id,
                metadata_json={
                    "before": before,
                    "after": {
                        "name": draft.name,
                        "daily_budget_cents": draft.daily_budget_cents,
                        "version_number": draft.version_number,
                        "content_hash": new_hash,
                    },
                },
            )
        )
        session.flush()
        return _draft_view(draft)

    def validate_draft(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        draft_id: UUID,
        actor_user_id: UUID,
        request_id: UUID | None = None,
    ) -> AdDraftView:
        """Run deterministic pre-flight validation on a campaign draft without spending budget."""
        draft = self._get_draft_row(session, workspace_id=workspace_id, draft_id=draft_id)
        acct = self._get_ad_account_row(
            session, workspace_id=workspace_id, ad_account_id=draft.ad_account_id
        )

        errors: list[str] = []
        warnings: list[str] = []

        if len(draft.name.strip()) < 3:
            errors.append("Campaign name must be at least 3 characters.")
        if draft.daily_budget_cents < 100:
            errors.append("Daily budget must be at least $1.00 (100 cents).")
        if draft.daily_budget_cents > self._settings.ads_max_daily_budget_cents:
            errors.append(
                f"Daily budget ({draft.daily_budget_cents} cents) exceeds workspace safety "
                f"ceiling ({self._settings.ads_max_daily_budget_cents} cents)."
            )
        if acct.spend_cap_cents is not None and draft.daily_budget_cents > acct.spend_cap_cents:
            errors.append("Daily budget exceeds connected ad account spend cap.")

        targeting = dict(draft.targeting_json or {})
        countries = targeting.get("countries")
        if not isinstance(countries, list) or len(countries) == 0:
            errors.append("Targeting must include at least one country code in 'countries'.")
        age_min = targeting.get("age_min", 18)
        if not isinstance(age_min, int) or age_min < 18:
            errors.append("Targeting age_min must be an integer >= 18.")

        if not draft.creative_refs_json:
            errors.append("At least one approved creative reference is required.")

        conn = session.execute(
            select(MetaConnection).where(
                MetaConnection.workspace_id == workspace_id,
                MetaConnection.id == acct.meta_connection_id,
            )
        ).scalar_one_or_none()
        if conn is None or conn.status != MetaConnectionStatus.ACTIVE.value:
            errors.append("Underlying Meta connection is not active.")
        elif not dict(conn.capabilities or {}).get("meta_ads_manage", False):
            warnings.append(
                "Connection currently lacks 'meta_ads_manage' (ads_management). "
                "Draft can be reviewed, but execution requires reconnecting with ads_management."
            )

        passed = len(errors) == 0
        report = {
            "passed": passed,
            "errors": errors,
            "warnings": warnings,
            "checked_at": datetime.now(UTC).isoformat(),
            "content_hash": draft.content_hash,
            "safety_max_daily_budget_cents": self._settings.ads_max_daily_budget_cents,
        }
        draft.validation_passed = passed
        draft.validation_report = report
        if passed and draft.status == AdDraftStatus.DRAFT.value:
            draft.status = AdDraftStatus.VALIDATED.value

        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=draft.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.AD_DRAFT_VALIDATED.value,
                target_type="ad_campaign_draft",
                target_id=str(draft.id),
                request_id=request_id,
                metadata_json={"passed": passed, "error_count": len(errors)},
            )
        )
        session.flush()
        return _draft_view(draft)

    # ------------------------------------------------------------------
    # 7.3 Approval-Gated Campaign Execution (Acceptance #16)
    # ------------------------------------------------------------------

    def request_draft_approval(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        draft_id: UUID,
        actor_user_id: UUID,
        request_id: UUID | None = None,
    ) -> AdApprovalRequestView:
        """Create a version-bound campaign_execution Approval for a validated draft."""
        draft = self._get_draft_row(session, workspace_id=workspace_id, draft_id=draft_id)
        if not draft.validation_passed:
            raise MetuseError(
                "Campaign draft must pass validation before requesting execution approval.",
                code=ErrorCode.ADS_DRAFT_VALIDATION_FAILED,
                status_code=422,
            )

        appr = Approval(
            id=uuid4(),
            workspace_id=workspace_id,
            client_id=draft.client_id,
            project_id=None,
            creative_version_id=None,
            approval_type=ApprovalType.CAMPAIGN_EXECUTION.value,
            target_type="ad_campaign_draft",
            target_id=draft.id,
            target_version_sha256=draft.content_hash,
            status=ApprovalStatus.PENDING.value,
            requested_by_user_id=actor_user_id,
        )
        session.add(appr)
        session.flush()
        draft.approval_id = appr.id
        draft.status = AdDraftStatus.PENDING_APPROVAL.value

        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=draft.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.AD_DRAFT_APPROVAL_REQUESTED.value,
                target_type="ad_campaign_draft",
                target_id=str(draft.id),
                request_id=request_id,
                metadata_json={
                    "approval_id": str(appr.id),
                    "content_hash": draft.content_hash,
                    "daily_budget_cents": draft.daily_budget_cents,
                },
            )
        )
        session.flush()
        return AdApprovalRequestView(
            approval_id=appr.id,
            approval_type=appr.approval_type,
            target_type=appr.target_type,
            target_id=appr.target_id,
            target_version_sha256=appr.target_version_sha256,
            status=appr.status,
        )

    def execute_draft(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        draft_id: UUID,
        actor_user_id: UUID,
        idempotency_key: str | None = None,
        request_id: UUID | None = None,
    ) -> AdExecutionResultView:
        """Execute an explicitly approved campaign draft with remote-state verification & audit."""
        if self._settings.ads_kill_switch:
            raise MetuseError(
                "Meta Ads mutations are disabled by ADS_KILL_SWITCH.",
                code=ErrorCode.ADS_KILL_SWITCH_ACTIVE,
                status_code=503,
            )

        draft = self._get_draft_row(session, workspace_id=workspace_id, draft_id=draft_id)
        acct = self._get_ad_account_row(
            session, workspace_id=workspace_id, ad_account_id=draft.ad_account_id
        )

        # Idempotent replay check
        if idempotency_key:
            existing = session.execute(
                select(AdCampaignDraft).where(
                    AdCampaignDraft.workspace_id == workspace_id,
                    AdCampaignDraft.idempotency_key == idempotency_key,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.id != draft.id:
                    raise ConflictError(
                        "Idempotency-Key already used for a different ad campaign draft",
                        code=ErrorCode.IDEMPOTENCY_CONFLICT,
                    )
                if existing.status == AdDraftStatus.EXECUTED.value and existing.remote_campaign_id:
                    camp = session.execute(
                        select(AdCampaignSnapshot).where(
                            AdCampaignSnapshot.workspace_id == workspace_id,
                            AdCampaignSnapshot.ad_account_id == acct.id,
                            AdCampaignSnapshot.external_campaign_id == existing.remote_campaign_id,
                        )
                    ).scalar_one()
                    return AdExecutionResultView(
                        draft=_draft_view(existing),
                        campaign=self._campaign_single_view(camp),
                        approval_id=existing.approval_id or uuid4(),
                        verified_via_remote_check=True,
                    )

        if not draft.validation_passed:
            raise MetuseError(
                "Campaign draft must be validated before execution.",
                code=ErrorCode.ADS_DRAFT_VALIDATION_FAILED,
                status_code=422,
            )

        # Mandatory explicit human approval gate (Acceptance #16)
        if draft.approval_id is None:
            raise MetuseError(
                "Explicit campaign_execution approval is required before executing any ad draft.",
                code=ErrorCode.ADS_APPROVAL_REQUIRED,
                status_code=403,
            )
        appr = session.execute(
            select(Approval).where(
                Approval.workspace_id == workspace_id,
                Approval.id == draft.approval_id,
            )
        ).scalar_one_or_none()
        if (
            appr is None
            or appr.approval_type != ApprovalType.CAMPAIGN_EXECUTION.value
            or appr.target_type != "ad_campaign_draft"
            or appr.target_id != draft.id
        ):
            raise MetuseError(
                "Linked approval is not a valid campaign_execution approval for this draft.",
                code=ErrorCode.ADS_APPROVAL_REQUIRED,
                status_code=403,
            )
        if appr.status == ApprovalStatus.INVALIDATED.value or (
            appr.target_version_sha256 != draft.content_hash
        ):
            raise MetuseError(
                "Campaign draft approval is stale or invalidated; "
                "re-validate and obtain a new approval.",
                code=ErrorCode.ADS_APPROVAL_STALE,
                status_code=403,
            )
        if appr.status != ApprovalStatus.APPROVED.value:
            raise MetuseError(
                f"Campaign draft approval is '{appr.status}', not 'approved'.",
                code=ErrorCode.ADS_APPROVAL_REQUIRED,
                status_code=403,
            )

        # Backend Meta capability gate (ads_management)
        _, token = self._require_connection_capability(
            session,
            workspace_id=workspace_id,
            connection_id=acct.meta_connection_id,
            capability="meta_ads_manage",
        )

        client_ref = f"metuse:draft:{draft.id}:v{draft.version_number}"
        verified_via_remote = False
        before_state = {
            "draft_status": draft.status,
            "remote_campaign_id": draft.remote_campaign_id,
            "daily_budget_cents": draft.daily_budget_cents,
        }

        try:
            remote_camp = self._ads_client.create_campaign(
                access_token=token,
                external_account_id=acct.external_account_id,
                name=draft.name,
                objective=draft.objective,
                daily_budget_cents=draft.daily_budget_cents,
                targeting=dict(draft.targeting_json or {}),
                client_ref=client_ref,
            )
        except AmbiguousAdsTimeoutError:
            # Section 8.6: Re-read remote state before retrying ambiguous operations!
            existing_remote = self._ads_client.find_campaign_by_client_ref(
                access_token=token,
                external_account_id=acct.external_account_id,
                client_ref=client_ref,
            )
            if existing_remote is not None:
                remote_camp = existing_remote
                verified_via_remote = True
            else:
                remote_camp = self._ads_client.create_campaign(
                    access_token=token,
                    external_account_id=acct.external_account_id,
                    name=draft.name,
                    objective=draft.objective,
                    daily_budget_cents=draft.daily_budget_cents,
                    targeting=dict(draft.targeting_json or {}),
                    client_ref=client_ref,
                )

        now = datetime.now(UTC)
        camp_row = self._upsert_campaign_snapshot(
            session, acct_row=acct, remote=remote_camp, captured_at=now
        )
        draft.status = AdDraftStatus.EXECUTED.value
        draft.remote_campaign_id = remote_camp.external_campaign_id
        draft.executed_at = now
        draft.error_message = None
        if idempotency_key:
            draft.idempotency_key = idempotency_key

        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=draft.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.AD_CAMPAIGN_EXECUTED.value,
                target_type="ad_campaign_draft",
                target_id=str(draft.id),
                request_id=request_id,
                metadata_json={
                    "approval_id": str(appr.id),
                    "approved_by_user_id": str(appr.decided_by_user_id)
                    if appr.decided_by_user_id
                    else None,
                    "before": before_state,
                    "after": {
                        "draft_status": draft.status,
                        "remote_campaign_id": remote_camp.external_campaign_id,
                        "daily_budget_cents": remote_camp.daily_budget_cents,
                        "campaign_status": remote_camp.status,
                    },
                    "verified_via_remote_check": verified_via_remote,
                },
            )
        )
        session.flush()
        return AdExecutionResultView(
            draft=_draft_view(draft),
            campaign=self._campaign_single_view(camp_row),
            approval_id=appr.id,
            verified_via_remote_check=verified_via_remote,
        )

    # ------------------------------------------------------------------
    # 7.4 Budget Changes with Explicit Confirmation & Audit Trail
    # ------------------------------------------------------------------

    def request_budget_change_approval(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        actor_user_id: UUID,
        new_daily_budget_cents: int,
        request_id: UUID | None = None,
    ) -> AdApprovalRequestView:
        """Create an explicit budget_change Approval binding exact before/after daily spend."""
        camp = self._get_campaign_row(session, workspace_id=workspace_id, campaign_id=campaign_id)
        if new_daily_budget_cents < 100:
            raise ValidationDomainError("new_daily_budget_cents must be at least 100 cents")
        if new_daily_budget_cents > self._settings.ads_max_daily_budget_cents:
            raise MetuseError(
                f"Proposed daily budget ({new_daily_budget_cents} cents) exceeds safety cap "
                f"({self._settings.ads_max_daily_budget_cents} cents).",
                code=ErrorCode.ADS_SPEND_CAP_EXCEEDED,
                status_code=422,
            )

        budget_hash = compute_budget_change_hash(
            campaign_id=camp.id,
            previous_daily_budget_cents=camp.daily_budget_cents,
            new_daily_budget_cents=new_daily_budget_cents,
        )
        appr = Approval(
            id=uuid4(),
            workspace_id=workspace_id,
            client_id=camp.client_id,
            project_id=None,
            creative_version_id=None,
            approval_type=ApprovalType.BUDGET_CHANGE.value,
            target_type="ad_campaign_budget",
            target_id=camp.id,
            target_version_sha256=budget_hash,
            status=ApprovalStatus.PENDING.value,
            requested_by_user_id=actor_user_id,
        )
        session.add(appr)
        session.flush()
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=camp.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.AD_BUDGET_APPROVAL_REQUESTED.value,
                target_type="ad_campaign_snapshot",
                target_id=str(camp.id),
                request_id=request_id,
                metadata_json={
                    "approval_id": str(appr.id),
                    "before": {"daily_budget_cents": camp.daily_budget_cents},
                    "proposed": {"daily_budget_cents": new_daily_budget_cents},
                    "budget_hash": budget_hash,
                },
            )
        )
        session.flush()
        return AdApprovalRequestView(
            approval_id=appr.id,
            approval_type=appr.approval_type,
            target_type=appr.target_type,
            target_id=appr.target_id,
            target_version_sha256=appr.target_version_sha256,
            status=appr.status,
        )

    def apply_budget_change(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        actor_user_id: UUID,
        approval_id: UUID,
        new_daily_budget_cents: int,
        explicit_human_confirmation: bool,
        request_id: UUID | None = None,
    ) -> BudgetMutationResultView:
        """Apply an explicitly approved and confirmed campaign budget change with audit."""
        if self._settings.ads_kill_switch:
            raise MetuseError(
                "Meta Ads mutations are disabled by ADS_KILL_SWITCH.",
                code=ErrorCode.ADS_KILL_SWITCH_ACTIVE,
                status_code=503,
            )
        if not explicit_human_confirmation:
            raise MetuseError(
                "Budget mutations require explicit_human_confirmation=true; "
                "Metuse AI never automatically increases ad spend.",
                code=ErrorCode.ADS_BUDGET_CONFIRMATION_REQUIRED,
                status_code=403,
            )
        if new_daily_budget_cents < 100:
            raise ValidationDomainError("new_daily_budget_cents must be at least 100 cents")
        if new_daily_budget_cents > self._settings.ads_max_daily_budget_cents:
            raise MetuseError(
                "Proposed daily budget exceeds safety ceiling.",
                code=ErrorCode.ADS_SPEND_CAP_EXCEEDED,
                status_code=422,
            )

        camp = self._get_campaign_row(session, workspace_id=workspace_id, campaign_id=campaign_id)
        acct = self._get_ad_account_row(
            session, workspace_id=workspace_id, ad_account_id=camp.ad_account_id
        )

        appr = session.execute(
            select(Approval).where(
                Approval.workspace_id == workspace_id,
                Approval.id == approval_id,
            )
        ).scalar_one_or_none()
        if (
            appr is None
            or appr.approval_type != ApprovalType.BUDGET_CHANGE.value
            or appr.target_type != "ad_campaign_budget"
            or appr.target_id != camp.id
        ):
            raise MetuseError(
                "A valid budget_change approval for this campaign is required.",
                code=ErrorCode.ADS_APPROVAL_REQUIRED,
                status_code=403,
            )
        if appr.status != ApprovalStatus.APPROVED.value:
            raise MetuseError(
                f"Budget change approval is '{appr.status}', not 'approved'.",
                code=ErrorCode.ADS_APPROVAL_REQUIRED,
                status_code=403,
            )

        expected_hash = compute_budget_change_hash(
            campaign_id=camp.id,
            previous_daily_budget_cents=camp.daily_budget_cents,
            new_daily_budget_cents=new_daily_budget_cents,
        )
        if appr.target_version_sha256 != expected_hash:
            raise MetuseError(
                "Budget approval hash mismatch (campaign budget changed or amount differs).",
                code=ErrorCode.ADS_APPROVAL_STALE,
                status_code=403,
            )

        _, token = self._require_connection_capability(
            session,
            workspace_id=workspace_id,
            connection_id=acct.meta_connection_id,
            capability="meta_ads_manage",
        )

        previous_budget = camp.daily_budget_cents
        verified_via_remote = False
        try:
            remote_camp = self._ads_client.update_campaign_daily_budget(
                access_token=token,
                external_account_id=acct.external_account_id,
                external_campaign_id=camp.external_campaign_id,
                new_daily_budget_cents=new_daily_budget_cents,
            )
        except AmbiguousAdsTimeoutError:
            # Section 8.6: Re-read remote state before retrying ambiguous budget operations!
            remote_check = self._ads_client.get_remote_campaign(
                access_token=token,
                external_account_id=acct.external_account_id,
                external_campaign_id=camp.external_campaign_id,
            )
            if (
                remote_check is not None
                and remote_check.daily_budget_cents == new_daily_budget_cents
            ):
                remote_camp = remote_check
                verified_via_remote = True
            else:
                remote_camp = self._ads_client.update_campaign_daily_budget(
                    access_token=token,
                    external_account_id=acct.external_account_id,
                    external_campaign_id=camp.external_campaign_id,
                    new_daily_budget_cents=new_daily_budget_cents,
                )

        camp.daily_budget_cents = remote_camp.daily_budget_cents
        camp.captured_at = datetime.now(UTC)

        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=camp.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.AD_BUDGET_CHANGED.value,
                target_type="ad_campaign_snapshot",
                target_id=str(camp.id),
                request_id=request_id,
                metadata_json={
                    "approval_id": str(appr.id),
                    "approved_by_user_id": str(appr.decided_by_user_id)
                    if appr.decided_by_user_id
                    else None,
                    "external_campaign_id": camp.external_campaign_id,
                    "explicit_human_confirmation": True,
                    "before": {"daily_budget_cents": previous_budget},
                    "after": {"daily_budget_cents": remote_camp.daily_budget_cents},
                    "verified_via_remote_check": verified_via_remote,
                },
            )
        )
        session.flush()
        return BudgetMutationResultView(
            campaign=self._campaign_single_view(camp),
            previous_daily_budget_cents=previous_budget,
            new_daily_budget_cents=remote_camp.daily_budget_cents or new_daily_budget_cents,
            approval_id=appr.id,
            verified_via_remote_check=verified_via_remote,
        )

    @staticmethod
    def _campaign_single_view(camp: AdCampaignSnapshot) -> AdCampaignSnapshotView:
        return AdCampaignSnapshotView(
            id=camp.id,
            workspace_id=camp.workspace_id,
            client_id=camp.client_id,
            ad_account_id=camp.ad_account_id,
            external_campaign_id=camp.external_campaign_id,
            name=camp.name,
            objective=camp.objective,
            status=camp.status,
            daily_budget_cents=camp.daily_budget_cents,
            lifetime_budget_cents=camp.lifetime_budget_cents,
            spend_cents=camp.spend_cents,
            impressions=camp.impressions,
            clicks=camp.clicks,
            conversions=camp.conversions,
            captured_at=camp.captured_at,
            ad_sets=[],
        )
