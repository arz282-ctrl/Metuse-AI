"""Meta Account Connection service: Unified Login, Eligibility, Secret Refs & Webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import AuditLog, Client, MetaConnectedAsset, MetaConnection, MetaWebhookEvent
from packages.auth.jwt import decode_hs256_token, encode_hs256_token, resolve_jwt_secret
from packages.meta_integrations.capabilities import (
    PERMISSION_MATRIX,
    PERSONAL_IG_CONVERSION_STEPS,
    compute_capabilities,
    default_scopes_for_connection,
    require_meta_capability,
)
from packages.meta_integrations.client import MetaGraphClient
from packages.meta_integrations.contracts import (
    MetaCapabilitiesView,
    MetaConnectedAssetView,
    MetaConnectionView,
    MetaWebhookReceipt,
    PermissionMatrixEntry,
    UnifiedLoginStartResponse,
)
from packages.meta_integrations.secret_store import SecretStore
from packages.shared.config import BaseAppSettings
from packages.shared.enums import (
    AuditAction,
    ErrorCode,
    InstagramAccountType,
    MetaAssetType,
    MetaConnectionStatus,
    MetaConnectionType,
)
from packages.shared.errors import MetuseError, NotFoundError
from packages.shared.scrubbing import scrub_secrets


def _connection_view(row: MetaConnection, *, has_secret: bool = True) -> MetaConnectionView:
    return MetaConnectionView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        connection_type=MetaConnectionType(row.connection_type),
        meta_business_id=row.meta_business_id,
        config_id=row.config_id,
        token_configured=has_secret and row.status == MetaConnectionStatus.ACTIVE.value,
        token_type=row.token_type,
        granted_scopes=list(row.granted_scopes),
        instagram_account_ids=list(row.instagram_account_ids),
        page_ids=list(row.page_ids),
        ad_account_ids=list(row.ad_account_ids),
        capabilities=dict(row.capabilities),
        status=MetaConnectionStatus(row.status),
        last_verified_at=row.last_verified_at,
        expires_at=row.expires_at,
        reconnect_required_at=row.reconnect_required_at,
        reconnect_reason=row.reconnect_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _asset_view(row: MetaConnectedAsset) -> MetaConnectedAssetView:
    return MetaConnectedAssetView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        connection_id=row.connection_id,
        asset_type=MetaAssetType(row.asset_type),
        external_asset_id=row.external_asset_id,
        display_name=row.display_name,
        username=row.username,
        instagram_account_type=row.instagram_account_type,
        meta_business_id=row.meta_business_id,
        is_selected=row.is_selected,
        is_eligible=row.is_eligible,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class MetaConnectionService:
    """Manages tenant-isolated Meta Unified Login connections with zero plaintext token storage."""

    def __init__(
        self,
        *,
        settings: BaseAppSettings,
        graph_client: MetaGraphClient,
        secret_store: SecretStore,
    ) -> None:
        self._settings = settings
        self._graph = graph_client
        self._secrets = secret_store

    def get_permission_matrix(self) -> list[PermissionMatrixEntry]:
        return list(PERMISSION_MATRIX)

    def _validate_redirect_uri(self, redirect_uri: str | None) -> str:
        allowed = self._settings.get_meta_allowed_redirect_uris()
        resolved = (redirect_uri or self._settings.meta_redirect_uri).strip()
        if not resolved or resolved not in allowed:
            raise MetuseError(
                "Redirect URI is not in the configured Meta OAuth allowlist",
                code=ErrorCode.META_REDIRECT_URI_NOT_ALLOWED,
                status_code=400,
                details={"redirect_uri": resolved},
            )
        return resolved

    def _assert_client_owned(
        self, session: Session, *, workspace_id: UUID, client_id: UUID
    ) -> None:
        client = session.execute(
            select(Client).where(Client.workspace_id == workspace_id, Client.id == client_id)
        ).scalar_one_or_none()
        if client is None:
            raise NotFoundError("Client not found", details={"client_id": str(client_id)})

    def start_unified_login(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        connection_type: MetaConnectionType,
        client_id: UUID | None = None,
        redirect_uri: str | None = None,
        include_ads: bool = False,
        include_comments: bool = False,
        include_messages: bool = False,
        request_id: UUID | None = None,
    ) -> UnifiedLoginStartResponse:
        if client_id is not None:
            self._assert_client_owned(session, workspace_id=workspace_id, client_id=client_id)

        validated_redirect = self._validate_redirect_uri(redirect_uri)
        is_agency = connection_type == MetaConnectionType.AGENCY_BUSINESS
        scopes = default_scopes_for_connection(
            include_ads=include_ads,
            include_comments=include_comments,
            include_messages=include_messages,
            is_agency=is_agency,
        )
        config_id = self._settings.meta_config_id.strip() or "metuse_unified_login_default"
        secret = resolve_jwt_secret(self._settings)
        state_token = encode_hs256_token(
            {
                "sub": str(actor_user_id),
                "ws": str(workspace_id),
                "cid": str(client_id) if client_id else None,
                "ctype": connection_type.value,
                "redirect_uri": validated_redirect,
                "scopes": scopes,
                "config_id": config_id,
                "nonce": uuid4().hex,
            },
            secret=secret,
            ttl_seconds=600,
            token_use="meta_unified_login_state",  # noqa: S106
        )
        auth_url = self._graph.build_authorization_url(
            state=state_token,
            redirect_uri=validated_redirect,
            scopes=scopes,
            config_id=config_id,
        )
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.META_LOGIN_STARTED.value,
                target_type="meta_connection",
                target_id=None,
                request_id=request_id,
                metadata_json={"connection_type": connection_type.value, "scopes": scopes},
            )
        )
        session.flush()
        return UnifiedLoginStartResponse(
            authorization_url=auth_url,
            state=state_token,
            redirect_uri=validated_redirect,
            connection_type=connection_type,
            requested_scopes=scopes,
            config_id=config_id,
            mode=self._graph.mode,
        )

    def complete_unified_login(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        code: str,
        state: str,
        redirect_uri: str | None = None,
        request_id: UUID | None = None,
    ) -> MetaConnectionView:
        secret = resolve_jwt_secret(self._settings)
        try:
            claims = decode_hs256_token(
                state, secret=secret, expected_use="meta_unified_login_state"
            )
        except MetuseError as exc:
            raise MetuseError(
                "Invalid or expired Meta OAuth state token",
                code=ErrorCode.META_OAUTH_STATE_INVALID,
                status_code=400,
            ) from exc

        if str(claims.get("ws")) != str(workspace_id) or str(claims.get("sub")) != str(
            actor_user_id
        ):
            raise MetuseError(
                "Meta OAuth state does not match authenticated user or workspace",
                code=ErrorCode.META_OAUTH_STATE_INVALID,
                status_code=403,
            )

        state_redirect = str(claims.get("redirect_uri") or "")
        validated_redirect = self._validate_redirect_uri(redirect_uri or state_redirect)
        if validated_redirect != state_redirect:
            raise MetuseError(
                "Meta OAuth redirect_uri does not match login start state",
                code=ErrorCode.META_REDIRECT_URI_NOT_ALLOWED,
                status_code=400,
            )

        raw_cid = claims.get("cid")
        client_id = UUID(str(raw_cid)) if raw_cid else None
        if client_id is not None:
            self._assert_client_owned(session, workspace_id=workspace_id, client_id=client_id)

        connection_type = MetaConnectionType(str(claims["ctype"]))
        requested_scopes = [str(s) for s in (claims.get("scopes") or [])]
        config_id = str(claims.get("config_id") or "")

        exchanged = self._graph.exchange_code_and_discover(
            code=code,
            redirect_uri=validated_redirect,
            connection_type=connection_type,
            requested_scopes=requested_scopes,
        )

        # Acceptance #3: Block personal Instagram accounts cleanly; store ZERO connection or token!
        selected_assets = [a for a in exchanged.discovered_assets if a.is_selected]
        ig_assets = [
            a for a in selected_assets if a.asset_type == MetaAssetType.INSTAGRAM_PROFESSIONAL
        ]
        eligible_ig_assets = [
            a
            for a in ig_assets
            if a.instagram_account_type
            in {InstagramAccountType.BUSINESS, InstagramAccountType.CREATOR}
        ]
        personal_ig_assets = [
            a for a in ig_assets if a.instagram_account_type == InstagramAccountType.PERSONAL
        ]

        if personal_ig_assets and not eligible_ig_assets:
            session.add(
                AuditLog(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    client_id=client_id,
                    actor_user_id=actor_user_id,
                    action=AuditAction.META_PERSONAL_ACCOUNT_BLOCKED.value,
                    target_type="meta_connection",
                    target_id=None,
                    request_id=request_id,
                    metadata_json={
                        "reason": "personal_instagram_account",
                        "connection_type": connection_type.value,
                    },
                )
            )
            session.flush()
            raise MetuseError(
                "Personal Instagram accounts are not supported by the Instagram Platform API. "
                "Convert to a Professional Creator or Business account and retry.",
                code=ErrorCode.META_PERSONAL_ACCOUNT_INELIGIBLE,
                status_code=422,
                details={
                    "account_type": "personal",
                    "eligible_types": ["creator", "business"],
                    "conversion_steps": list(PERSONAL_IG_CONVERSION_STEPS),
                },
            )

        # Store raw token ONLY inside SecretStore -> keep only `secret://meta/...` ref in Postgres
        secret_ref = self._secrets.put_secret(
            workspace_id=workspace_id,
            namespace="meta",
            secret_value=exchanged.access_token,
        )

        eligible_selected = [
            a
            for a in selected_assets
            if not (
                a.asset_type == MetaAssetType.INSTAGRAM_PROFESSIONAL
                and a.instagram_account_type == InstagramAccountType.PERSONAL
            )
        ]
        capabilities, _ = compute_capabilities(
            granted_scopes=exchanged.granted_scopes,
            assets=eligible_selected,
        )

        ig_ids = [
            a.external_asset_id
            for a in eligible_selected
            if a.asset_type == MetaAssetType.INSTAGRAM_PROFESSIONAL
        ]
        page_ids = [
            a.external_asset_id
            for a in eligible_selected
            if a.asset_type == MetaAssetType.FACEBOOK_PAGE
        ]
        ad_ids = [
            a.external_asset_id
            for a in eligible_selected
            if a.asset_type == MetaAssetType.AD_ACCOUNT
        ]

        now = datetime.now(UTC)
        conn = MetaConnection(
            id=uuid4(),
            workspace_id=workspace_id,
            client_id=client_id,
            connection_type=connection_type.value,
            meta_business_id=exchanged.meta_business_id,
            config_id=config_id,
            token_secret_ref=secret_ref,
            token_type=exchanged.token_type,
            granted_scopes=list(exchanged.granted_scopes),
            instagram_account_ids=ig_ids,
            page_ids=page_ids,
            ad_account_ids=ad_ids,
            capabilities=capabilities,
            status=MetaConnectionStatus.ACTIVE.value,
            last_verified_at=now,
            expires_at=exchanged.expires_at,
            created_by_user_id=actor_user_id,
        )
        session.add(conn)
        session.flush()

        # Store ONLY selected & eligible discovered assets (Acceptance #5)
        for asset in eligible_selected:
            session.add(
                MetaConnectedAsset(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    client_id=client_id,
                    connection_id=conn.id,
                    asset_type=asset.asset_type.value,
                    external_asset_id=asset.external_asset_id,
                    display_name=asset.display_name,
                    username=asset.username,
                    instagram_account_type=(
                        asset.instagram_account_type.value if asset.instagram_account_type else None
                    ),
                    meta_business_id=asset.meta_business_id or exchanged.meta_business_id,
                    is_selected=True,
                    is_eligible=True,
                    status="active",
                    metadata_json=dict(asset.metadata),
                )
            )

        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.META_CONNECTION_CREATED.value,
                target_type="meta_connection",
                target_id=str(conn.id),
                request_id=request_id,
                metadata_json={
                    "connection_type": conn.connection_type,
                    "instagram_count": len(ig_ids),
                    "page_count": len(page_ids),
                    "ad_account_count": len(ad_ids),
                    "granted_scopes": list(conn.granted_scopes),
                },
            )
        )
        session.flush()
        return _connection_view(conn, has_secret=True)

    def _get_connection_row(
        self, session: Session, *, workspace_id: UUID, connection_id: UUID
    ) -> MetaConnection:
        row = session.execute(
            select(MetaConnection).where(
                MetaConnection.workspace_id == workspace_id,
                MetaConnection.id == connection_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                "Meta connection not found", details={"connection_id": str(connection_id)}
            )
        return row

    def list_connections(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        client_id: UUID | None = None,
    ) -> list[MetaConnectionView]:
        stmt = select(MetaConnection).where(MetaConnection.workspace_id == workspace_id)
        if client_id is not None:
            stmt = stmt.where(MetaConnection.client_id == client_id)
        stmt = stmt.order_by(MetaConnection.created_at.desc())
        rows = session.execute(stmt).scalars().all()
        return [_connection_view(r) for r in rows]

    def list_connection_assets(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        include_deselected: bool = False,
    ) -> list[MetaConnectedAssetView]:
        self._get_connection_row(session, workspace_id=workspace_id, connection_id=connection_id)
        stmt = select(MetaConnectedAsset).where(
            MetaConnectedAsset.workspace_id == workspace_id,
            MetaConnectedAsset.connection_id == connection_id,
        )
        if not include_deselected:
            stmt = stmt.where(MetaConnectedAsset.is_selected.is_(True))
        rows = (
            session.execute(
                stmt.order_by(
                    MetaConnectedAsset.asset_type.asc(), MetaConnectedAsset.display_name.asc()
                )
            )
            .scalars()
            .all()
        )
        return [_asset_view(r) for r in rows]

    def update_asset_selection(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        actor_user_id: UUID,
        selections: dict[UUID, bool],
        request_id: UUID | None = None,
    ) -> MetaConnectionView:
        """Update which persisted assets are selected; recompute ids + capabilities + audit."""
        if not selections:
            raise MetuseError(
                "At least one asset selection is required",
                code=ErrorCode.BAD_REQUEST,
                status_code=400,
            )
        conn = self._get_connection_row(
            session, workspace_id=workspace_id, connection_id=connection_id
        )
        if conn.status == MetaConnectionStatus.DISCONNECTED.value:
            raise MetuseError(
                "Cannot update assets on a disconnected Meta connection; reconnect first.",
                code=ErrorCode.META_RECONNECT_REQUIRED,
                status_code=403,
            )

        rows = (
            session.execute(
                select(MetaConnectedAsset).where(
                    MetaConnectedAsset.workspace_id == workspace_id,
                    MetaConnectedAsset.connection_id == connection_id,
                )
            )
            .scalars()
            .all()
        )
        by_id = {row.id: row for row in rows}
        changed: list[str] = []
        for asset_id, selected in selections.items():
            row = by_id.get(asset_id)
            if row is None:
                raise NotFoundError(
                    "Meta connected asset not found",
                    details={"asset_id": str(asset_id)},
                )
            if row.is_selected != selected:
                row.is_selected = selected
                changed.append(f"{row.asset_type}:{row.external_asset_id}={selected!s}")
        session.flush()

        active_rows = [r for r in rows if r.is_selected and r.is_eligible]
        before = {
            "instagram_count": len(conn.instagram_account_ids),
            "page_count": len(conn.page_ids),
            "ad_account_count": len(conn.ad_account_ids),
        }
        conn.instagram_account_ids = [
            r.external_asset_id
            for r in active_rows
            if r.asset_type == MetaAssetType.INSTAGRAM_PROFESSIONAL.value
        ]
        conn.page_ids = [
            r.external_asset_id
            for r in active_rows
            if r.asset_type == MetaAssetType.FACEBOOK_PAGE.value
        ]
        conn.ad_account_ids = [
            r.external_asset_id
            for r in active_rows
            if r.asset_type == MetaAssetType.AD_ACCOUNT.value
        ]
        capabilities, _ = compute_capabilities(
            granted_scopes=list(conn.granted_scopes),
            has_instagram_professional=len(conn.instagram_account_ids) > 0,
            has_ad_account=len(conn.ad_account_ids) > 0,
        )
        conn.capabilities = capabilities
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=conn.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.META_ASSETS_UPDATED.value,
                target_type="meta_connection",
                target_id=str(conn.id),
                request_id=request_id,
                metadata_json={
                    "before": before,
                    "after": {
                        "instagram_count": len(conn.instagram_account_ids),
                        "page_count": len(conn.page_ids),
                        "ad_account_count": len(conn.ad_account_ids),
                    },
                    "changed": changed,
                },
            )
        )
        session.flush()
        return _connection_view(conn, has_secret=True)

    def get_connection_capabilities(
        self, session: Session, *, workspace_id: UUID, connection_id: UUID
    ) -> MetaCapabilitiesView:
        conn = self._get_connection_row(
            session, workspace_id=workspace_id, connection_id=connection_id
        )
        caps, missing = compute_capabilities(
            granted_scopes=conn.granted_scopes,
            has_instagram_professional=len(conn.instagram_account_ids) > 0,
            has_ad_account=len(conn.ad_account_ids) > 0,
        )
        is_reconnect = conn.status in {
            MetaConnectionStatus.RECONNECT_REQUIRED.value,
            MetaConnectionStatus.REVOKED.value,
            MetaConnectionStatus.DISCONNECTED.value,
        }
        if is_reconnect:
            caps = {k: False for k in caps}
        return MetaCapabilitiesView(
            connection_id=conn.id,
            status=MetaConnectionStatus(conn.status),
            granted_scopes=list(conn.granted_scopes),
            capabilities=caps,
            missing_permissions_by_capability=missing,
            reconnect_required=is_reconnect,
            reconnect_reason=conn.reconnect_reason,
        )

    def assert_capability(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        required_capability: str,
    ) -> MetaConnectionView:
        """Backend enforcement of Meta capabilities & connection health (Acceptance #6)."""
        conn = self._get_connection_row(
            session, workspace_id=workspace_id, connection_id=connection_id
        )
        if conn.status != MetaConnectionStatus.ACTIVE.value:
            raise MetuseError(
                "Meta connection requires reconnection before performing this action",
                code=ErrorCode.META_RECONNECT_REQUIRED,
                status_code=403,
                details={
                    "connection_id": str(conn.id),
                    "status": conn.status,
                    "reason": conn.reconnect_reason,
                },
            )
        require_meta_capability(
            capabilities=dict(conn.capabilities),
            granted_scopes=list(conn.granted_scopes),
            required_capability=required_capability,
        )
        return _connection_view(conn)

    def mark_reconnect_required(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        reason: str,
        actor_user_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> MetaConnectionView:
        """Stop retrying on revoked/moved assets and require user reconnection (Section 2.2)."""
        conn = self._get_connection_row(
            session, workspace_id=workspace_id, connection_id=connection_id
        )
        self._secrets.delete_secret(secret_ref=conn.token_secret_ref)
        conn.status = MetaConnectionStatus.RECONNECT_REQUIRED.value
        conn.reconnect_required_at = datetime.now(UTC)
        conn.reconnect_reason = reason
        conn.capabilities = {k: False for k in conn.capabilities}
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=conn.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.META_CONNECTION_REVOKED.value,
                target_type="meta_connection",
                target_id=str(conn.id),
                request_id=request_id,
                metadata_json={"reason": reason},
            )
        )
        session.flush()
        return _connection_view(conn, has_secret=False)

    def reconnect_connection(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        actor_user_id: UUID,
        redirect_uri: str | None = None,
        request_id: UUID | None = None,
    ) -> UnifiedLoginStartResponse:
        conn = self._get_connection_row(
            session, workspace_id=workspace_id, connection_id=connection_id
        )
        include_ads = "ads_read" in conn.granted_scopes or len(conn.ad_account_ids) > 0
        resp = self.start_unified_login(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            connection_type=MetaConnectionType(conn.connection_type),
            client_id=conn.client_id,
            redirect_uri=redirect_uri,
            include_ads=include_ads,
            request_id=request_id,
        )
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=conn.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.META_CONNECTION_RECONNECTED.value,
                target_type="meta_connection",
                target_id=str(conn.id),
                request_id=request_id,
                metadata_json={"mode": resp.mode},
            )
        )
        session.flush()
        return resp

    def disconnect_connection(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        actor_user_id: UUID,
        request_id: UUID | None = None,
    ) -> MetaConnectionView:
        conn = self._get_connection_row(
            session, workspace_id=workspace_id, connection_id=connection_id
        )
        self._secrets.delete_secret(secret_ref=conn.token_secret_ref)
        conn.status = MetaConnectionStatus.DISCONNECTED.value
        conn.capabilities = {k: False for k in conn.capabilities}
        conn.reconnect_reason = "Disconnected by workspace user"
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=conn.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.META_CONNECTION_DISCONNECTED.value,
                target_type="meta_connection",
                target_id=str(conn.id),
                request_id=request_id,
                metadata_json={},
            )
        )
        session.flush()
        return _connection_view(conn, has_secret=False)

    def verify_webhook_challenge(
        self,
        *,
        hub_mode: str,
        hub_verify_token: str,
        hub_challenge: str,
    ) -> str:
        expected = self._settings.meta_webhook_verify_token.get_secret_value().strip()
        if not expected:
            expected = "metuse-dev-meta-verify-token"
        if hub_mode == "subscribe" and hmac.compare_digest(hub_verify_token, expected):
            return hub_challenge
        raise MetuseError(
            "Invalid Meta webhook verification token",
            code=ErrorCode.WEBHOOK_SIGNATURE_INVALID,
            status_code=403,
        )

    def ingest_webhook(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        raw_body: bytes,
        headers: dict[str, str],
        request_id: UUID | None = None,
    ) -> MetaWebhookReceipt:
        """Verify X-Hub-Signature-256 HMAC, deduplicate event, and handle revocations."""
        app_secret = self._settings.meta_app_secret.get_secret_value().strip()
        if not app_secret:
            if not self._settings.is_dev_env():
                raise MetuseError(
                    "META_APP_SECRET is required to verify Meta webhook signatures",
                    code=ErrorCode.WEBHOOK_SIGNATURE_INVALID,
                    status_code=503,
                )
            app_secret = "metuse-dev-meta-app-secret"  # noqa: S105

        lower_headers = {k.lower(): v for k, v in headers.items()}
        sig_header = lower_headers.get("x-hub-signature-256", "").strip()
        expected = (
            "sha256=" + hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        )
        if not sig_header or not hmac.compare_digest(sig_header, expected):
            raise MetuseError(
                "Invalid Meta webhook X-Hub-Signature-256 signature",
                code=ErrorCode.WEBHOOK_SIGNATURE_INVALID,
                status_code=401,
            )

        try:
            payload: dict[str, Any] = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MetuseError(
                "Malformed Meta webhook JSON payload",
                code=ErrorCode.BAD_REQUEST,
                status_code=400,
            ) from exc

        event_id = str(payload.get("event_id") or hashlib.sha256(raw_body).hexdigest()[:32]).strip()
        existing = session.execute(
            select(MetaWebhookEvent).where(
                MetaWebhookEvent.workspace_id == workspace_id,
                MetaWebhookEvent.provider_event_id == event_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return MetaWebhookReceipt(
                accepted=True,
                deduplicated=True,
                provider_event_id=event_id,
                affected_connection_ids=(
                    [existing.connection_id] if existing.connection_id else []
                ),
            )

        object_type = str(payload.get("object") or "instagram")
        event_field = str(payload.get("field") or "unknown")
        affected: list[UUID] = []

        # Handle permission revocation / deauthorization events -> mark reconnect_required
        if object_type == "permissions" or event_field in {"deauthorize", "revoked", "asset_moved"}:
            target_asset_id = str(payload.get("external_asset_id") or "").strip()
            active_conns = (
                session.execute(
                    select(MetaConnection).where(
                        MetaConnection.workspace_id == workspace_id,
                        MetaConnection.status == MetaConnectionStatus.ACTIVE.value,
                    )
                )
                .scalars()
                .all()
            )
            for conn in active_conns:
                all_ids = (
                    set(conn.instagram_account_ids) | set(conn.page_ids) | set(conn.ad_account_ids)
                )
                if not target_asset_id or target_asset_id in all_ids:
                    self.mark_reconnect_required(
                        session,
                        workspace_id=workspace_id,
                        connection_id=conn.id,
                        reason=f"Meta webhook revocation event ({event_field})",
                        request_id=request_id,
                    )
                    affected.append(conn.id)

        scrubbed_payload = scrub_secrets(payload)
        safe_payload = scrubbed_payload if isinstance(scrubbed_payload, dict) else {}
        session.add(
            MetaWebhookEvent(
                id=uuid4(),
                workspace_id=workspace_id,
                connection_id=affected[0] if affected else None,
                provider_event_id=event_id,
                object_type=object_type,
                event_field=event_field,
                signature_verified=True,
                payload=safe_payload,
                processed_at=datetime.now(UTC),
            )
        )
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=None,
                actor_user_id=None,
                action=AuditAction.META_WEBHOOK_RECEIVED.value,
                target_type="meta_webhook_event",
                target_id=event_id,
                request_id=request_id,
                metadata_json={"object_type": object_type, "event_field": event_field},
            )
        )
        session.flush()
        return MetaWebhookReceipt(
            accepted=True,
            deduplicated=False,
            provider_event_id=event_id,
            affected_connection_ids=affected,
        )
