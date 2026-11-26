"""Tenant RLS context managers and Agency/Creator/Client/Brand domain services."""

from packages.tenancy.audit import list_audit_logs, record_audit_log
from packages.tenancy.brand import (
    BrandAssetUploadInit,
    complete_brand_asset_upload,
    get_brand_asset_download_url,
    get_brand_profile,
    initiate_brand_asset_upload,
    list_brand_assets,
    upsert_brand_profile,
)
from packages.tenancy.clients import (
    CLIENT_SCOPED_ROLES,
    assert_client_readable,
    assign_client_member,
    create_client,
    get_client,
    list_clients,
    update_client,
)
from packages.tenancy.db import (
    create_app_engine,
    create_session_factory,
    resolve_tenant_context,
    rls_transaction,
    set_rls_gucs,
    tenant_transaction,
)
from packages.tenancy.workspaces import (
    add_workspace_member,
    create_workspace,
    get_current_workspace,
    list_user_workspaces,
    list_workspace_members,
    slugify,
    update_workspace_member,
)

__all__ = [
    "CLIENT_SCOPED_ROLES",
    "BrandAssetUploadInit",
    "add_workspace_member",
    "assert_client_readable",
    "assign_client_member",
    "complete_brand_asset_upload",
    "create_app_engine",
    "create_client",
    "create_session_factory",
    "create_workspace",
    "get_brand_asset_download_url",
    "get_brand_profile",
    "get_client",
    "get_current_workspace",
    "initiate_brand_asset_upload",
    "list_audit_logs",
    "list_brand_assets",
    "list_clients",
    "list_user_workspaces",
    "list_workspace_members",
    "record_audit_log",
    "resolve_tenant_context",
    "rls_transaction",
    "set_rls_gucs",
    "slugify",
    "tenant_transaction",
    "update_client",
    "update_workspace_member",
    "upsert_brand_profile",
]
