"""Tenant-scoped Brand Profile routes wired to packages.tenancy.brand."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from apps.api.deps import (
    AuthenticatedActor,
    get_current_actor,
    get_request_id,
    get_session_factory,
)
from packages.shared.schemas import BrandProfileOut
from packages.tenancy import get_brand_profile, tenant_transaction, upsert_brand_profile

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/clients/{client_id}/brand",
    tags=["brand"],
)


class UpsertBrandProfileRequest(BaseModel):
    brand_name: str = Field(min_length=1, max_length=120)
    tone_of_voice: str | None = Field(default=None, max_length=500)
    target_audience: str | None = Field(default=None, max_length=500)
    color_palette: list[str] | dict[str, Any] = Field(default_factory=list)
    guidelines: str | None = Field(default=None, max_length=4000)


@router.get("", response_model=BrandProfileOut)
def get_client_brand_profile(
    workspace_id: UUID,
    client_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> BrandProfileOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return get_brand_profile(session, ctx, client_id=client_id)


@router.put("", response_model=BrandProfileOut)
def put_client_brand_profile(
    workspace_id: UUID,
    client_id: UUID,
    body: UpsertBrandProfileRequest,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> BrandProfileOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return upsert_brand_profile(
            session,
            ctx,
            client_id=client_id,
            brand_name=body.brand_name,
            tone_of_voice=body.tone_of_voice,
            target_audience=body.target_audience,
            color_palette=body.color_palette,
            guidelines=body.guidelines,
        )
