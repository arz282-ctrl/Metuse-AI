"""Phase 8 unit tests: social auth (Meta/Google), data deletion, CAPI, prompt drafts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from apps.api.main import create_app
from packages.ai_core.contracts import AdCampaignPlanArtifact
from packages.auth import (
    InMemorySessionStore,
    exchange_google_code,
    resolve_google_auth_mode,
    start_google_oauth,
    start_meta_oauth,
    verify_meta_signed_request,
)
from packages.auth import google_oauth as google_oauth_module
from packages.meta_integrations import (
    LiveCapiClient,
    LiveMetaAdsClient,
    LiveMetaGraphClient,
    MockCapiClient,
    build_capi_user_data,
    hash_capi_email,
    hash_capi_phone,
)
from packages.muse_adapter import MockMuseAdapter, MuseStructuredRequest
from packages.shared.config import BaseAppSettings
from packages.shared.enums import MetaConnectionType, MuseCapability
from packages.shared.errors import BadRequestError, ExternalServiceError

pytestmark = pytest.mark.unit


def _test_settings(**overrides: Any) -> BaseAppSettings:
    kwargs: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "app_jwt_secret": SecretStr("phase8-unit-test-secret-32-bytes-ok"),
    }
    kwargs.update(overrides)
    return BaseAppSettings(**kwargs)


# ---------------------------------------------------------------------------
# Google OAuth (mock mode mirrors Meta identity guarantees)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_env", ["production", "prod", "staging", "stage", "local", "dev", ""])
def test_google_oauth_blocks_mock_mode_outside_development_and_test(bad_env: str) -> None:
    settings = _test_settings(app_env=bad_env, google_auth_mode="mock_dev")
    with pytest.raises(ExternalServiceError, match="development/test"):
        resolve_google_auth_mode(settings)


def test_google_oauth_mock_roundtrip_with_single_use_nonce() -> None:
    settings = _test_settings(
        google_auth_mode="mock_dev",
        google_redirect_uri="http://localhost:3000/auth/google/callback",
    )
    store = InMemorySessionStore()
    start = start_google_oauth(settings, session_store=store)
    assert start.mode == "mock_dev"
    assert start.authorization_url.startswith("http://localhost:3000/auth/google/callback?")

    with pytest.raises(BadRequestError, match="allowlist"):
        start_google_oauth(
            settings,
            redirect_uri="https://evil.example.com/steal",
            session_store=store,
        )

    identity = exchange_google_code(
        settings,
        code="mock-google-code-1",
        state=start.state,
        mock_email="creator@example.com",
        session_store=store,
    )
    assert identity.email == "creator@example.com"
    assert identity.token_secret_ref.startswith("env://google-oauth/")

    with pytest.raises(BadRequestError, match="already consumed"):
        exchange_google_code(
            settings,
            code="mock-google-code-1",
            state=start.state,
            session_store=store,
        )


def test_google_oauth_has_zero_meta_coupling() -> None:
    source = inspect.getsource(google_oauth_module)
    assert "meta_integrations" not in source
    assert "facebook" not in source.lower()
    assert "ads_management" not in source


def test_google_live_exchange_rejects_unverified_email() -> None:
    settings = _test_settings(
        google_auth_mode="live",
        google_client_id="test-google-client-id",
        google_client_secret=SecretStr("test-google-client-secret"),
        google_redirect_uri="http://localhost:3000/auth/google/callback",
    )
    start = start_google_oauth(settings)
    assert "accounts.google.com" in start.authorization_url

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "ya29.test-token"})
        return httpx.Response(
            200,
            json={
                "sub": "google-sub-1",
                "email": "unverified@example.com",
                "email_verified": False,
                "name": "Unverified User",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ExternalServiceError, match="not verified"):
        exchange_google_code(settings, code="live-code", state=start.state, http_client=client)


def test_meta_identity_login_uses_graph_v22_in_live_mode() -> None:
    settings = _test_settings(
        meta_auth_mode="live",
        meta_app_id="meta-app-id-123",
        meta_redirect_uri="http://localhost:3000/auth/meta/callback",
    )
    start = start_meta_oauth(settings)
    assert start.mode == "live"
    assert start.authorization_url.startswith("https://www.facebook.com/v22.0/dialog/oauth?")


# ---------------------------------------------------------------------------
# Social start + legal routes (no database required)
# ---------------------------------------------------------------------------


def test_social_start_and_privacy_policy_routes() -> None:
    settings = _test_settings(
        meta_auth_mode="mock_dev",
        google_auth_mode="mock_dev",
        meta_redirect_uri="http://localhost:3000/auth/meta/callback",
        google_redirect_uri="http://localhost:3000/auth/google/callback",
    )
    app = create_app(settings=settings, session_factory=None, enable_db=False)
    client = TestClient(app)

    meta_start = client.get("/api/v1/auth/meta/start")
    assert meta_start.status_code == 200
    meta_body = meta_start.json()
    assert meta_body["mode"] == "mock_dev"
    assert meta_body["authorization_url"].startswith("http://localhost:3000/auth/meta/callback?")

    google_start = client.get("/api/v1/auth/google/start")
    assert google_start.status_code == 200
    google_body = google_start.json()
    assert google_body["mode"] == "mock_dev"
    assert google_body["authorization_url"].startswith(
        "http://localhost:3000/auth/google/callback?"
    )

    evil = client.get(
        "/api/v1/auth/meta/start", params={"redirect_uri": "https://evil.example.com/x"}
    )
    assert evil.status_code == 400

    policy = client.get("/legal/privacy-policy")
    assert policy.status_code == 200
    assert "Privacy Policy" in policy.text
    assert "Data Deletion" in policy.text


# ---------------------------------------------------------------------------
# Meta data-deletion signed_request verification
# ---------------------------------------------------------------------------


def _signed_request(payload: dict[str, object], secret: str) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    sig = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    encoded_sig = base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    return f"{encoded_sig}.{encoded}"


def test_verify_meta_signed_request_roundtrip_and_tamper_rejection() -> None:
    secret = "meta-app-secret-for-unit-tests"
    settings = _test_settings(meta_app_secret=SecretStr(secret))
    payload = {"algorithm": "HMAC-SHA256", "issued_at": 1793980000, "user_id": "meta-user-42"}
    verified = verify_meta_signed_request(settings, _signed_request(payload, secret))
    assert verified["user_id"] == "meta-user-42"

    tampered_payload = {"algorithm": "HMAC-SHA256", "issued_at": 1793980000, "user_id": "attacker"}
    tampered_raw = (
        _signed_request(payload, secret).split(".", 1)[0]
        + "."
        + base64.urlsafe_b64encode(json.dumps(tampered_payload).encode()).decode()
    )
    with pytest.raises(BadRequestError, match="signature"):
        verify_meta_signed_request(settings, tampered_raw)

    with pytest.raises(BadRequestError, match="algorithm"):
        verify_meta_signed_request(
            settings, _signed_request({"algorithm": "none", "user_id": "x"}, secret)
        )

    no_secret_settings = _test_settings(meta_app_secret=SecretStr(""))
    with pytest.raises(ExternalServiceError, match="META_APP_SECRET"):
        verify_meta_signed_request(no_secret_settings, _signed_request(payload, secret))


# ---------------------------------------------------------------------------
# CAPI hashing + clients
# ---------------------------------------------------------------------------


def test_capi_hashing_vectors_and_user_data_builder() -> None:
    assert hash_capi_email("  Test@Example.COM ") == hashlib.sha256(b"test@example.com").hexdigest()
    assert hash_capi_phone("+1 (555) 010-2030") == hashlib.sha256(b"15550102030").hexdigest()

    user_data = build_capi_user_data(
        email="buyer@shop.com",
        phone="+44 7700 900123",
        client_ip_address="203.0.113.7",
        fbp="fb.1.123.456",
        external_id=str(uuid4()),
    )
    assert user_data["em"] == [hash_capi_email("buyer@shop.com")]
    assert user_data["ph"] == [hash_capi_phone("+44 7700 900123")]
    assert user_data["client_ip_address"] == "203.0.113.7"
    assert user_data["fbp"] == "fb.1.123.456"
    # Raw PII must never appear in the built payload
    assert "buyer@shop.com" not in json.dumps(user_data)
    assert "+44 7700 900123" not in json.dumps(user_data)


def test_mock_capi_client_marks_test_mode() -> None:
    client = MockCapiClient()
    test_receipt = client.send_events(
        pixel_id="123",
        events=[{"event_name": "Lead"}],
        access_token="token",
        test_event_code="TEST123",
    )
    assert test_receipt.test_mode is True
    assert test_receipt.events_received == 1

    live_receipt = client.send_events(
        pixel_id="123", events=[{"event_name": "Lead"}], access_token="token"
    )
    assert live_receipt.test_mode is False


def test_live_capi_client_posts_to_pixel_events_endpoint() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"events_received": 1, "fbtrace_id": "trace_abc"})

    client = LiveCapiClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    receipt = client.send_events(
        pixel_id="987654",
        events=[{"event_name": "Purchase", "event_time": 1793980000}],
        access_token="EAA_TEST",
        test_event_code="TESTCODE1",
    )
    assert str(seen["url"]).startswith("https://graph.facebook.com/v22.0/987654/events")
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["test_event_code"] == "TESTCODE1"
    assert body["data"][0]["event_name"] == "Purchase"
    assert receipt.events_received == 1
    assert receipt.fbtrace_id == "trace_abc"
    assert receipt.test_mode is True


def test_live_capi_client_surfaces_error_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"message": "Invalid pixel"}})

    client = LiveCapiClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ExternalServiceError, match="error payload"):
        client.send_events(pixel_id="bad", events=[{"event_name": "Lead"}], access_token="token")


# ---------------------------------------------------------------------------
# Prompt campaign plan artifact + mock Muse payload
# ---------------------------------------------------------------------------


def test_ad_campaign_plan_artifact_validation() -> None:
    plan = AdCampaignPlanArtifact.model_validate(
        {
            "name": "Creator Launch",
            "objective": "OUTCOME_TRAFFIC",
            "daily_budget_cents": 5000,
            "countries": ["us", "gb"],
            "age_min": 21,
            "age_max": 45,
            "creative_brief": {
                "headline": "Edit reels faster",
                "primary_text": "Metuse turns clips into reels.",
            },
        }
    )
    assert plan.countries == ["US", "GB"]
    assert plan.objective.value == "OUTCOME_TRAFFIC"

    with pytest.raises(ValidationError):
        AdCampaignPlanArtifact.model_validate(
            {
                "name": "Bad",
                "objective": "OUTCOME_TRAFFIC",
                "daily_budget_cents": 5000,
                "countries": ["USA"],
                "age_min": 21,
                "creative_brief": {"headline": "h", "primary_text": "p"},
            }
        )

    with pytest.raises(ValidationError):
        AdCampaignPlanArtifact.model_validate(
            {
                "name": "Bad window",
                "objective": "OUTCOME_TRAFFIC",
                "daily_budget_cents": 5000,
                "countries": ["US"],
                "age_min": 40,
                "age_max": 30,
                "creative_brief": {"headline": "h", "primary_text": "p"},
            }
        )


def test_mock_muse_campaign_plan_payload_validates() -> None:
    adapter = MockMuseAdapter()
    response = adapter.invoke_structured(
        MuseStructuredRequest(
            capability=MuseCapability.REASONING_STRUCTURED_OUTPUT,
            system_policy="plan",
            user_prompt="launch traffic campaign",
            output_schema_id="metuse.ad_campaign_plan.v1",
            metadata={"topic": "reels service", "brand_name": "Acme"},
        )
    )
    plan = AdCampaignPlanArtifact.model_validate(response.content_json)
    assert plan.daily_budget_cents >= 100
    assert plan.countries == ["US"]


# ---------------------------------------------------------------------------
# Live Marketing API client over mocked transport
# ---------------------------------------------------------------------------


def _marketing_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/act_5501") and request.method == "GET":
        return httpx.Response(
            200,
            json={
                "id": "act_5501",
                "name": "Client Brand Ad Account",
                "currency": "USD",
                "timezone_name": "America/New_York",
                "account_status": "active",
                "spend_cap": "100000",
                "amount_spent": "2500",
            },
        )
    if path.endswith("/act_5501/campaigns") and request.method == "POST":
        return httpx.Response(200, json={"id": "cmp_9001"})
    if path.endswith("/act_5501/adsets") and request.method == "POST":
        return httpx.Response(200, json={"id": "adset_9001"})
    if path.endswith("/cmp_9001") and request.method == "GET":
        return httpx.Response(
            200,
            json={
                "id": "cmp_9001",
                "name": "Launch [metuse:abcdef12]",
                "objective": "OUTCOME_TRAFFIC",
                "status": "PAUSED",
                "daily_budget": "5000",
            },
        )
    if path.endswith("/act_5501/campaigns") and request.method == "GET":
        return httpx.Response(
            200, json={"data": [{"id": "cmp_9001", "name": "Launch [metuse:abcdef12]"}]}
        )
    if path.endswith("/insights"):
        return httpx.Response(
            200,
            json={"data": [{"spend": "12.50", "impressions": "1000", "clicks": "25"}]},
        )
    return httpx.Response(404, json={"error": {"message": "not found"}})


def test_live_ads_client_fetch_and_paused_create_flow() -> None:
    client = LiveMetaAdsClient(
        http_client=httpx.Client(transport=httpx.MockTransport(_marketing_handler))
    )
    acct = client.fetch_ad_account(access_token="EAA_TEST", external_account_id="act_5501")
    assert acct.name == "Client Brand Ad Account"
    assert acct.spend_cap_cents == 100000
    assert acct.amount_spent_cents == 2500

    created = client.create_campaign(
        access_token="EAA_TEST",
        external_account_id="5501",
        name="Launch",
        objective="OUTCOME_TRAFFIC",
        daily_budget_cents=5000,
        targeting={"countries": ["US"], "age_min": 21},
        client_ref="metuse:draft:abcdef12-0000-4000-8000-000000000000:v1",
    )
    assert created.external_campaign_id == "cmp_9001"
    assert created.status == "PAUSED"

    found = client.find_campaign_by_client_ref(
        access_token="EAA_TEST",
        external_account_id="act_5501",
        client_ref="metuse:draft:abcdef12-0000-4000-8000-000000000000:v1",
    )
    assert found is not None
    assert found.external_campaign_id == "cmp_9001"


def test_live_graph_client_discovers_ad_accounts_when_ads_scope_granted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth/access_token"):
            return httpx.Response(200, json={"access_token": "EAA_LIVE", "expires_in": 3600})
        if path.endswith("/me/permissions"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"permission": "instagram_basic", "status": "granted"},
                        {"permission": "ads_read", "status": "granted"},
                    ]
                },
            )
        if path.endswith("/me/accounts"):
            return httpx.Response(200, json={"data": []})
        if path.endswith("/me/adaccounts"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "act_7701",
                            "name": "Live Ad Account",
                            "currency": "USD",
                            "timezone_name": "UTC",
                            "account_status": "active",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": {"message": "not found"}})

    graph = LiveMetaGraphClient(
        app_id="app-id",
        app_secret="app-secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = graph.exchange_code_and_discover(
        code="live-code",
        redirect_uri="http://localhost:3000/connections/callback",
        connection_type=MetaConnectionType.AGENCY_BUSINESS,
        requested_scopes=["instagram_basic", "ads_read"],
    )
    ad_assets = [a for a in result.discovered_assets if a.asset_type.value == "ad_account"]
    assert len(ad_assets) == 1
    assert ad_assets[0].external_asset_id == "act_7701"
    assert "ads_read" in result.granted_scopes
