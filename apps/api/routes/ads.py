"""Tenant-scoped Meta Ads Read, Campaign Drafts & Controlled Execution routes (Section 8.6)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from apps.api.deps import (
    AppContainer,
    AuthenticatedActor,
    get_container,
    get_current_actor,
    get_request_id,
    get_session_factory,
)
from packages.auth import require_permission
from packages.meta_integrations import CapiEventReceiptView, build_capi_user_data
from packages.meta_integrations.ads import (
    AdAccountView,
    AdApprovalRequestView,
    AdCampaignSnapshotView,
    AdDraftView,
    AdExecutionResultView,
    AdsSyncSummaryView,
    BudgetMutationResultView,
)
from packages.shared.context import TenantContext
from packages.shared.enums import AdObjective
from packages.shared.errors import ValidationDomainError, WorkspaceAccessDeniedError
from packages.tenancy import tenant_transaction

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/ads", tags=["ads"])


class CreateAdDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ad_account_id: UUID
    name: str = Field(min_length=1, max_length=160)
    objective: AdObjective = AdObjective.OUTCOME_AWARENESS
    daily_budget_cents: int = Field(gt=0)
    targeting: dict[str, Any] = Field(default_factory=dict)
    creative_refs: list[str] = Field(default_factory=list)


class UpdateAdDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    daily_budget_cents: int | None = Field(default=None, gt=0)
    targeting: dict[str, Any] | None = None
    creative_refs: list[str] | None = None


class CreatePromptDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ad_account_id: UUID
    page_asset_id: UUID | None = None
    prompt: str = Field(min_length=8, max_length=2000)
    creative_refs: list[str] = Field(default_factory=list, max_length=10)


class UpdatePixelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pixel_dataset_id: str | None = Field(default=None, max_length=32)


class SendCapiEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ad_account_id: UUID
    event_name: str = Field(min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    client_ip_address: str | None = Field(default=None, max_length=64)
    client_user_agent: str | None = Field(default=None, max_length=512)
    fbp: str | None = Field(default=None, max_length=128)
    fbc: str | None = Field(default=None, max_length=256)
    external_id: str | None = Field(default=None, max_length=128)
    custom_data: dict[str, Any] = Field(default_factory=dict)
    event_source_url: str | None = Field(default=None, max_length=1024)
    action_source: str = Field(default="website", max_length=32)
    event_id: str | None = Field(default=None, max_length=128)
    test_event_code: str | None = Field(default=None, max_length=64)


class RequestBudgetChangePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_daily_budget_cents: int = Field(gt=0)


class ApplyBudgetChangePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    new_daily_budget_cents: int = Field(gt=0)
    explicit_human_confirmation: bool = False


def _require_ws_perm(ctx: TenantContext, permission: str) -> None:
    if ctx.role_name is None:
        raise WorkspaceAccessDeniedError("Access to the requested workspace is denied")
    require_permission(ctx.role_name, permission)


@router.get("/accounts", response_model=list[AdAccountView])
def get_ad_accounts(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    client_id: Annotated[UUID | None, Query()] = None,
) -> list[AdAccountView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:read")
        return container.ads_service.list_ad_accounts(
            session, workspace_id=workspace_id, client_id=client_id
        )


@router.post("/sync", response_model=AdsSyncSummaryView)
def post_sync_ads(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    client_id: Annotated[UUID | None, Query()] = None,
) -> AdsSyncSummaryView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:read")
        return container.ads_service.sync_ad_accounts_and_snapshots(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            client_id=client_id,
            request_id=request_id,
        )


@router.get("/campaigns", response_model=list[AdCampaignSnapshotView])
def get_ad_campaigns(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    ad_account_id: Annotated[UUID | None, Query()] = None,
    client_id: Annotated[UUID | None, Query()] = None,
) -> list[AdCampaignSnapshotView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:read")
        return container.ads_service.list_campaign_hierarchy(
            session,
            workspace_id=workspace_id,
            ad_account_id=ad_account_id,
            client_id=client_id,
        )


@router.get("/drafts", response_model=list[AdDraftView])
def get_ad_drafts(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    ad_account_id: Annotated[UUID | None, Query()] = None,
) -> list[AdDraftView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:read")
        return container.ads_service.list_drafts(
            session, workspace_id=workspace_id, ad_account_id=ad_account_id
        )


@router.post("/drafts", response_model=AdDraftView, status_code=status.HTTP_201_CREATED)
def post_create_ad_draft(
    workspace_id: UUID,
    body: CreateAdDraftPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AdDraftView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:write")
        return container.ads_service.create_draft(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            ad_account_id=body.ad_account_id,
            name=body.name,
            objective=body.objective,
            daily_budget_cents=body.daily_budget_cents,
            targeting=body.targeting,
            creative_refs=body.creative_refs,
            request_id=request_id,
        )


@router.post("/prompt-drafts", response_model=AdDraftView, status_code=status.HTTP_201_CREATED)
def post_create_prompt_draft(
    workspace_id: UUID,
    body: CreatePromptDraftPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AdDraftView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:write")
        return container.ads_service.create_prompt_draft(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            ad_account_id=body.ad_account_id,
            prompt=body.prompt,
            page_asset_id=body.page_asset_id,
            creative_refs=body.creative_refs,
            muse_adapter=container.muse_adapter,
            request_id=request_id,
        )


@router.patch("/accounts/{account_id}", response_model=AdAccountView)
def patch_ad_account_pixel(
    workspace_id: UUID,
    account_id: UUID,
    body: UpdatePixelPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AdAccountView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:write")
        return container.ads_service.update_ad_account_pixel(
            session,
            workspace_id=workspace_id,
            ad_account_id=account_id,
            actor_user_id=actor.user_id,
            pixel_dataset_id=body.pixel_dataset_id,
            request_id=request_id,
        )


@router.patch("/drafts/{draft_id}", response_model=AdDraftView)
def patch_ad_draft(
    workspace_id: UUID,
    draft_id: UUID,
    body: UpdateAdDraftPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AdDraftView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:write")
        return container.ads_service.update_draft(
            session,
            workspace_id=workspace_id,
            draft_id=draft_id,
            actor_user_id=actor.user_id,
            name=body.name,
            daily_budget_cents=body.daily_budget_cents,
            targeting=body.targeting,
            creative_refs=body.creative_refs,
            request_id=request_id,
        )


@router.post("/drafts/{draft_id}/validate", response_model=AdDraftView)
def post_validate_ad_draft(
    workspace_id: UUID,
    draft_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AdDraftView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:write")
        return container.ads_service.validate_draft(
            session,
            workspace_id=workspace_id,
            draft_id=draft_id,
            actor_user_id=actor.user_id,
            request_id=request_id,
        )


@router.post(
    "/drafts/{draft_id}/request-approval",
    response_model=AdApprovalRequestView,
    status_code=status.HTTP_201_CREATED,
)
def post_request_draft_approval(
    workspace_id: UUID,
    draft_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AdApprovalRequestView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:write")
        return container.ads_service.request_draft_approval(
            session,
            workspace_id=workspace_id,
            draft_id=draft_id,
            actor_user_id=actor.user_id,
            request_id=request_id,
        )


@router.post("/drafts/{draft_id}/execute", response_model=AdExecutionResultView)
def post_execute_ad_draft(
    workspace_id: UUID,
    draft_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AdExecutionResultView:
    cleaned_key = idempotency_key.strip() if idempotency_key else None
    if cleaned_key and len(cleaned_key) > 128:
        raise ValidationDomainError("Idempotency-Key must not exceed 128 characters")
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:execute")
        return container.ads_service.execute_draft(
            session,
            workspace_id=workspace_id,
            draft_id=draft_id,
            actor_user_id=actor.user_id,
            idempotency_key=cleaned_key,
            request_id=request_id,
        )


@router.post(
    "/campaigns/{campaign_id}/budget/request-approval",
    response_model=AdApprovalRequestView,
    status_code=status.HTTP_201_CREATED,
)
def post_request_budget_change_approval(
    workspace_id: UUID,
    campaign_id: UUID,
    body: RequestBudgetChangePayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AdApprovalRequestView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:write")
        return container.ads_service.request_budget_change_approval(
            session,
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            actor_user_id=actor.user_id,
            new_daily_budget_cents=body.new_daily_budget_cents,
            request_id=request_id,
        )


@router.post("/campaigns/{campaign_id}/budget/apply", response_model=BudgetMutationResultView)
def post_apply_budget_change(
    workspace_id: UUID,
    campaign_id: UUID,
    body: ApplyBudgetChangePayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> BudgetMutationResultView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "ads:execute")
        return container.ads_service.apply_budget_change(
            session,
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            actor_user_id=actor.user_id,
            approval_id=body.approval_id,
            new_daily_budget_cents=body.new_daily_budget_cents,
            explicit_human_confirmation=body.explicit_human_confirmation,
            request_id=request_id,
        )


@router.post("/capi/events", response_model=CapiEventReceiptView)
def post_send_capi_event(
    workspace_id: UUID,
    body: SendCapiEventPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> CapiEventReceiptView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        is_test = bool(body.test_event_code and body.test_event_code.strip())
        _require_ws_perm(ctx, "ads:write" if is_test else "ads:execute")
        return container.capi_service.send_event(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            ad_account_id=body.ad_account_id,
            event_name=body.event_name,
            user_data=build_capi_user_data(
                email=body.email,
                phone=body.phone,
                client_ip_address=body.client_ip_address,
                client_user_agent=body.client_user_agent,
                fbp=body.fbp,
                fbc=body.fbc,
                external_id=body.external_id,
            ),
            custom_data=body.custom_data,
            event_source_url=body.event_source_url,
            action_source=body.action_source,
            event_id=body.event_id,
            test_event_code=body.test_event_code,
            request_id=request_id,
        )
