"""Phase 7 unit tests: Ads RBAC, hash binding, ambiguous timeout mock, kill switch, routes."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from apps.api.main import create_app
from packages.auth.rbac import has_permission
from packages.meta_integrations import (
    AmbiguousAdsTimeoutError,
    MockMetaAdsClient,
    compute_budget_change_hash,
    compute_draft_content_hash,
)
from packages.shared.config import BaseAppSettings
from packages.shared.enums import AdObjective, RoleName

pytestmark = pytest.mark.unit


def test_phase7_ads_rbac_separation_of_duties() -> None:
    """Only agency/creator owners and admins hold ads:execute; members can read/draft only."""
    assert has_permission(RoleName.AGENCY_OWNER, "ads:read")
    assert has_permission(RoleName.AGENCY_OWNER, "ads:write")
    assert has_permission(RoleName.AGENCY_OWNER, "ads:execute")
    assert has_permission(RoleName.AGENCY_ADMIN, "ads:execute")

    assert has_permission(RoleName.AGENCY_MEMBER, "ads:read")
    assert has_permission(RoleName.AGENCY_MEMBER, "ads:write")
    assert not has_permission(RoleName.AGENCY_MEMBER, "ads:execute")

    assert has_permission(RoleName.CLIENT_REVIEWER, "ads:read")
    assert has_permission(RoleName.CLIENT_REVIEWER, "approvals:decide")
    assert not has_permission(RoleName.CLIENT_REVIEWER, "ads:write")
    assert not has_permission(RoleName.CLIENT_REVIEWER, "ads:execute")

    assert has_permission(RoleName.CLIENT_VIEWER, "ads:read")
    assert not has_permission(RoleName.CLIENT_VIEWER, "ads:execute")


def test_draft_and_budget_hashes_change_on_any_parameter_edit() -> None:
    acct_id = uuid4()
    h1 = compute_draft_content_hash(
        ad_account_id=acct_id,
        name="Launch A",
        objective=AdObjective.OUTCOME_AWARENESS.value,
        daily_budget_cents=5000,
        targeting={"countries": ["US"], "age_min": 21},
        creative_refs=["cv-1"],
    )
    h2 = compute_draft_content_hash(
        ad_account_id=acct_id,
        name="Launch A",
        objective=AdObjective.OUTCOME_AWARENESS.value,
        daily_budget_cents=7500,
        targeting={"countries": ["US"], "age_min": 21},
        creative_refs=["cv-1"],
    )
    assert h1 != h2

    camp_id = uuid4()
    b1 = compute_budget_change_hash(
        campaign_id=camp_id, previous_daily_budget_cents=5000, new_daily_budget_cents=8000
    )
    b2 = compute_budget_change_hash(
        campaign_id=camp_id, previous_daily_budget_cents=5000, new_daily_budget_cents=9000
    )
    b3 = compute_budget_change_hash(
        campaign_id=camp_id, previous_daily_budget_cents=6000, new_daily_budget_cents=8000
    )
    assert len({b1, b2, b3}) == 3


def test_mock_ads_client_ambiguous_timeout_remote_state_recovery() -> None:
    client = MockMetaAdsClient(simulate_ambiguous_timeout_once=True)
    acct = client.fetch_ad_account(access_token="tok", external_account_id="act_9001")
    assert acct.external_account_id == "act_9001"
    hierarchy = client.fetch_campaign_hierarchy(access_token="tok", external_account_id="act_9001")
    assert len(hierarchy) == 1
    assert len(hierarchy[0].ad_sets) == 1
    assert len(hierarchy[0].ad_sets[0].ads) == 1

    # Simulated ambiguous timeout on create writes remotely, then remote-state lookup recovers it
    with pytest.raises(AmbiguousAdsTimeoutError):
        client.create_campaign(
            access_token="tok",
            external_account_id="act_9001",
            name="Approved Launch",
            objective=AdObjective.OUTCOME_LEADS.value,
            daily_budget_cents=4000,
            targeting={"countries": ["US"]},
            client_ref="ref-1",
        )
    assert client.create_call_count == 1
    recovered = client.find_campaign_by_client_ref(
        access_token="tok", external_account_id="act_9001", client_ref="ref-1"
    )
    assert recovered is not None
    assert recovered.name == "Approved Launch"
    assert client.remote_state_check_count == 1

    # Simulated ambiguous timeout on budget update writes remotely, then remote check recovers
    with pytest.raises(AmbiguousAdsTimeoutError):
        client.update_campaign_daily_budget(
            access_token="tok",
            external_account_id="act_9001",
            external_campaign_id=recovered.external_campaign_id,
            new_daily_budget_cents=6500,
        )
    assert client.budget_update_call_count == 1
    remote_after = client.get_remote_campaign(
        access_token="tok",
        external_account_id="act_9001",
        external_campaign_id=recovered.external_campaign_id,
    )
    assert remote_after is not None
    assert remote_after.daily_budget_cents == 6500


def test_ads_routes_registered_and_fail_closed_unauthenticated() -> None:
    settings = BaseAppSettings(
        _env_file=None,
        app_env="test",
        app_jwt_secret=SecretStr("unit-test-phase7-jwt-secret"),
        ads_kill_switch=False,
    )
    assert settings.safe_summary()["ads_kill_switch"] is False
    assert settings.safe_summary()["ads_max_daily_budget_cents"] == 500_000

    app = create_app(settings=settings, session_factory=None, enable_db=False)
    client = TestClient(app)
    ws_id = uuid4()
    resp = client.get(f"/api/v1/workspaces/{ws_id}/ads/accounts")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
