"""Meta Conversions API (CAPI) server-event sender with PII hashing (Phase 8).

Strictly separated from browser-pixel flows: the backend hashes user identifiers
(SHA-256) and sends server events for optimization and measurement. Test events
(`test_event_code`) never affect delivery and require `ads:write`; live events
require `ads:execute`. Audit entries never contain raw or hashed user data keys
beyond the event name and test-mode flag.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import AdAccount, AuditLog, MetaConnection
from packages.meta_integrations.capabilities import require_meta_capability
from packages.meta_integrations.secret_store import SecretStore
from packages.shared.config import BaseAppSettings
from packages.shared.enums import AuditAction, ErrorCode, MetaConnectionStatus
from packages.shared.errors import ExternalServiceError, MetuseError, NotFoundError
from packages.shared.scrubbing import scrub_text

_SUPPORTED_ACTION_SOURCES = frozenset(
    {
        "website",
        "app",
        "phone_call",
        "chat",
        "physical_store",
        "system_generated",
        "other",
    }
)

_NON_DIGIT_RE = re.compile(r"\D+")


def hash_capi_email(email: str) -> str:
    """Normalize (trim + lowercase) and SHA-256 hash an email for CAPI matching."""
    normalized = email.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def hash_capi_phone(phone: str) -> str:
    """Normalize (digits only, strip leading zeros) and SHA-256 hash a phone number."""
    digits = _NON_DIGIT_RE.sub("", phone.strip()).lstrip("0")
    return hashlib.sha256(digits.encode("utf-8")).hexdigest()


def build_capi_user_data(
    *,
    email: str | None = None,
    phone: str | None = None,
    client_ip_address: str | None = None,
    client_user_agent: str | None = None,
    fbp: str | None = None,
    fbc: str | None = None,
    external_id: str | None = None,
) -> dict[str, Any]:
    """Build a CAPI user_data object with PII hashed and browser ids passed through."""
    user_data: dict[str, Any] = {}
    if email and email.strip():
        user_data["em"] = [hash_capi_email(email)]
    if phone and phone.strip():
        user_data["ph"] = [hash_capi_phone(phone)]
    if client_ip_address and client_ip_address.strip():
        user_data["client_ip_address"] = client_ip_address.strip()
    if client_user_agent and client_user_agent.strip():
        user_data["client_user_agent"] = client_user_agent.strip()[:512]
    if fbp and fbp.strip():
        user_data["fbp"] = fbp.strip()
    if fbc and fbc.strip():
        user_data["fbc"] = fbc.strip()
    if external_id and external_id.strip():
        user_data["external_id"] = [external_id.strip()]
    return user_data


class CapiSendResult(BaseModel):
    """Raw client-level send receipt (normalized across mock and live clients)."""

    model_config = ConfigDict(extra="forbid")

    events_received: int
    fbtrace_id: str | None = None
    test_mode: bool


class CapiEventReceiptView(BaseModel):
    """Serialized CAPI event receipt — never includes user identifiers."""

    model_config = ConfigDict(extra="forbid")

    pixel_dataset_id: str
    event_name: str
    event_id: str
    event_time: int
    action_source: str
    test_mode: bool
    events_received: int
    fbtrace_id: str | None = None


class CapiClient(Protocol):
    """Provider boundary for Conversions API event delivery."""

    def send_events(
        self,
        *,
        pixel_id: str,
        events: list[dict[str, Any]],
        access_token: str,
        test_event_code: str | None = None,
    ) -> CapiSendResult: ...


class MockCapiClient:
    """Deterministic zero-cost CAPI simulator for dev/CI (no network, no delivery)."""

    def send_events(
        self,
        *,
        pixel_id: str,
        events: list[dict[str, Any]],
        access_token: str,
        test_event_code: str | None = None,
    ) -> CapiSendResult:
        _ = (pixel_id, access_token)
        test_mode = bool(test_event_code and test_event_code.strip())
        return CapiSendResult(
            events_received=len(events),
            fbtrace_id=f"mock_trace_{uuid4().hex[:12]}",
            test_mode=test_mode,
        )


class LiveCapiClient:
    """Live Meta Conversions API client (POST /{pixel_id}/events)."""

    def __init__(
        self,
        *,
        graph_base_url: str = "https://graph.facebook.com",
        api_version: str = "v22.0",
        timeout_seconds: float = 20.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._graph_base_url = graph_base_url.rstrip("/")
        self._api_version = api_version.strip()
        self._timeout = timeout_seconds
        self._http_client = http_client

    def send_events(
        self,
        *,
        pixel_id: str,
        events: list[dict[str, Any]],
        access_token: str,
        test_event_code: str | None = None,
    ) -> CapiSendResult:
        client = self._http_client or httpx.Client(timeout=self._timeout)
        close_after = self._http_client is None
        try:
            payload: dict[str, Any] = {"data": events}
            if test_event_code and test_event_code.strip():
                payload["test_event_code"] = test_event_code.strip()
            resp = client.post(
                f"{self._graph_base_url}/{self._api_version}/{pixel_id.strip()}/events",
                params={"access_token": access_token},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceError("Conversions API network request failed") from exc
        finally:
            if close_after:
                client.close()
        if resp.status_code >= 400:
            raise ExternalServiceError(
                scrub_text(f"Conversions API delivery failed (HTTP {resp.status_code})")
            )
        data = resp.json()
        if isinstance(data.get("error"), dict):
            raise ExternalServiceError("Conversions API returned an error payload")
        return CapiSendResult(
            events_received=int(data.get("events_received", 0) or 0),
            fbtrace_id=str(data.get("fbtrace_id") or "") or None,
            test_mode=bool(test_event_code and test_event_code.strip()),
        )


def create_capi_client(settings: BaseAppSettings) -> CapiClient:
    """Instantiate MockCapiClient (default $0) or LiveCapiClient from settings."""
    if settings.meta_connections_mode.strip().lower() == "live":
        return LiveCapiClient(
            graph_base_url=settings.meta_graph_base_url,
            api_version=settings.meta_graph_api_version,
        )
    return MockCapiClient()


class CapiService:
    """Tenant-scoped Conversions API sender with capability gating and audit."""

    def __init__(
        self,
        *,
        settings: BaseAppSettings,
        secret_store: SecretStore,
        capi_client: CapiClient | None = None,
    ) -> None:
        self._settings = settings
        self._secret_store = secret_store
        self._capi_client: CapiClient = capi_client or MockCapiClient()

    def send_event(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        ad_account_id: UUID,
        event_name: str,
        user_data: dict[str, Any],
        custom_data: dict[str, Any] | None = None,
        event_source_url: str | None = None,
        action_source: str = "website",
        event_id: str | None = None,
        test_event_code: str | None = None,
        request_id: UUID | None = None,
    ) -> CapiEventReceiptView:
        """Send one server event to the ad account's bound pixel/dataset."""
        cleaned_name = event_name.strip()
        if not cleaned_name:
            raise MetuseError(
                "CAPI event_name must not be empty",
                code=ErrorCode.BAD_REQUEST,
                status_code=400,
            )
        normalized_source = action_source.strip().lower() or "website"
        if normalized_source not in _SUPPORTED_ACTION_SOURCES:
            raise MetuseError(
                f"Unsupported CAPI action_source '{action_source}'",
                code=ErrorCode.BAD_REQUEST,
                status_code=400,
            )
        if not user_data:
            raise MetuseError(
                "At least one user identifier (email, phone, fbp, fbc, external_id) "
                "or client context is required for CAPI matching",
                code=ErrorCode.BAD_REQUEST,
                status_code=400,
            )

        acct = session.execute(
            select(AdAccount).where(
                AdAccount.workspace_id == workspace_id,
                AdAccount.id == ad_account_id,
            )
        ).scalar_one_or_none()
        if acct is None:
            raise NotFoundError(
                "Ad account not found in the active workspace",
                details={"ad_account_id": str(ad_account_id)},
            )
        if not acct.pixel_dataset_id:
            raise MetuseError(
                "No Conversions API pixel is bound to this ad account yet. "
                "Set pixel_dataset_id on the ad account first.",
                code=ErrorCode.BAD_REQUEST,
                status_code=422,
            )
        conn = session.execute(
            select(MetaConnection).where(
                MetaConnection.workspace_id == workspace_id,
                MetaConnection.id == acct.meta_connection_id,
            )
        ).scalar_one_or_none()
        if conn is None or conn.status != MetaConnectionStatus.ACTIVE.value:
            raise MetuseError(
                "Underlying Meta connection is not active.",
                code=ErrorCode.META_RECONNECT_REQUIRED,
                status_code=403,
            )
        require_meta_capability(
            capabilities=dict(conn.capabilities or {}),
            granted_scopes=list(conn.granted_scopes or []),
            required_capability="meta_ads_manage",
        )
        token = self._secret_store.get_secret(secret_ref=conn.token_secret_ref)

        resolved_event_id = event_id.strip() if event_id and event_id.strip() else uuid4().hex
        event_time = int(time.time())
        event: dict[str, Any] = {
            "event_name": cleaned_name,
            "event_time": event_time,
            "event_id": resolved_event_id,
            "action_source": normalized_source,
            "user_data": user_data,
        }
        if custom_data:
            event["custom_data"] = dict(custom_data)
        if event_source_url and event_source_url.strip():
            event["event_source_url"] = event_source_url.strip()

        result = self._capi_client.send_events(
            pixel_id=acct.pixel_dataset_id,
            events=[event],
            access_token=token,
            test_event_code=test_event_code,
        )
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=acct.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.AD_CAPI_EVENT_SENT.value,
                target_type="ad_account",
                target_id=str(acct.id),
                request_id=request_id,
                metadata_json={
                    "event_name": cleaned_name,
                    "event_id": resolved_event_id,
                    "action_source": normalized_source,
                    "test_mode": result.test_mode,
                    "events_received": result.events_received,
                },
            )
        )
        session.flush()
        return CapiEventReceiptView(
            pixel_dataset_id=acct.pixel_dataset_id,
            event_name=cleaned_name,
            event_id=resolved_event_id,
            event_time=event_time,
            action_source=normalized_source,
            test_mode=result.test_mode,
            events_received=result.events_received,
            fbtrace_id=result.fbtrace_id,
        )
