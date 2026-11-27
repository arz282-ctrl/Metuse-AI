"""Phase 5 unit tests: AES-GCM SecretStore, Meta capability gating, webhooks & credential safety."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from packages.meta_integrations import (
    DiscoveredMetaAsset,
    EncryptedSecretStore,
    LiveMetaGraphClient,
    MetaConnectionService,
    MockMetaGraphClient,
    compute_capabilities,
    require_meta_capability,
)
from packages.shared.config import BaseAppSettings
from packages.shared.enums import ErrorCode, InstagramAccountType, MetaAssetType
from packages.shared.errors import MetuseError, NotFoundError, ProviderDisabledError

pytestmark = pytest.mark.unit


def test_encrypted_secret_store_roundtrip_and_deletion() -> None:
    """Raw Meta tokens are encrypted with AES-256-GCM behind opaque secret:// references."""
    store = EncryptedSecretStore(master_key_material="unit-test-master-key-32-bytes-ok")
    ws_id = uuid4()
    raw_token = "EAA_SECRET_META_SYSTEM_USER_TOKEN_XYZ"
    ref = store.put_secret(workspace_id=ws_id, namespace="meta", secret_value=raw_token)
    assert ref.startswith(f"secret://meta/{ws_id}/")
    assert raw_token not in ref
    assert store.get_secret(secret_ref=ref) == raw_token

    store.delete_secret(secret_ref=ref)
    with pytest.raises(NotFoundError):
        store.get_secret(secret_ref=ref)


def test_meta_capability_matrix_and_permission_denial() -> None:
    """Acceptance #6: capabilities require BOTH granted scope AND eligible selected asset."""
    ig_asset = DiscoveredMetaAsset(
        asset_type=MetaAssetType.INSTAGRAM_PROFESSIONAL,
        external_asset_id="ig_123",
        display_name="Creator Pro",
        instagram_account_type=InstagramAccountType.CREATOR,
        is_selected=True,
    )
    caps, missing = compute_capabilities(
        granted_scopes=["instagram_basic", "instagram_manage_insights"],
        assets=[ig_asset],
    )
    assert caps["instagram_insights"] is True
    assert caps["instagram_publish"] is False
    assert caps["meta_ads_read"] is False
    assert "instagram_content_publish" in missing["instagram_publish"]

    with pytest.raises(MetuseError) as exc_info:
        require_meta_capability(
            capabilities=caps,
            granted_scopes=["instagram_basic", "instagram_manage_insights"],
            required_capability="instagram_publish",
        )
    assert exc_info.value.code == ErrorCode.META_PERMISSION_MISSING
    assert exc_info.value.status_code == 403
    assert "instagram_content_publish" in exc_info.value.details["missing_permissions"]


def test_meta_live_client_disabled_without_credentials_and_webhook_challenge() -> None:
    """Live Meta Graph client refuses calls without credentials; webhook challenge verifies."""
    live = LiveMetaGraphClient(app_id="", app_secret="")
    with pytest.raises(ProviderDisabledError):
        live.build_authorization_url(
            state="s", redirect_uri="https://app.local/cb", scopes=["instagram_basic"], config_id=""
        )

    settings = BaseAppSettings(
        _env_file=None,
        meta_app_secret=SecretStr("meta_app_secret_super_private"),
        meta_webhook_verify_token=SecretStr("verify_token_12345"),
        meta_secret_encryption_key=SecretStr("enc_key_super_private_value"),
    )
    summary = settings.safe_summary()
    assert "meta_app_secret_super_private" not in str(summary)
    assert "enc_key_super_private_value" not in str(summary)
    assert summary["meta_secret_encryption_key_configured"] is True

    svc = MetaConnectionService(
        settings=settings,
        graph_client=MockMetaGraphClient(),
        secret_store=EncryptedSecretStore.from_settings(settings),
    )
    assert (
        svc.verify_webhook_challenge(
            hub_mode="subscribe",
            hub_verify_token="verify_token_12345",
            hub_challenge="challenge_ok",
        )
        == "challenge_ok"
    )
    with pytest.raises(MetuseError) as bad_wh:
        svc.verify_webhook_challenge(
            hub_mode="subscribe",
            hub_verify_token="wrong",
            hub_challenge="challenge_ok",
        )
    assert bad_wh.value.code == ErrorCode.WEBHOOK_SIGNATURE_INVALID
