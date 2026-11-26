"""Harden all PostgreSQL functions with SET search_path = pg_catalog, public.

Revision ID: 0002_harden_functions_search_path
Revises: 0001_initial_schema
Create Date: 2026-11-25 22:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_harden_functions_search_path"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTIONS = (
    "public.current_app_workspace_id()",
    "public.current_app_user_id()",
    "public.set_updated_at()",
    "public.enforce_workspace_member_limit()",
    "public.prevent_audit_log_mutation()",
)


def upgrade() -> None:
    for fn_signature in _FUNCTIONS:
        op.execute(f"ALTER FUNCTION {fn_signature} SET search_path = pg_catalog, public;")

    # Reaffirm BEFORE INSERT OR UPDATE coverage on the atomic workspace member cap trigger.
    op.execute(
        "DROP TRIGGER IF EXISTS trg_workspace_memberships_member_limit "
        "ON public.workspace_memberships;"
    )
    op.execute(
        """
        CREATE TRIGGER trg_workspace_memberships_member_limit
        BEFORE INSERT OR UPDATE ON public.workspace_memberships
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_workspace_member_limit();
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER FUNCTION public.enforce_workspace_member_limit() SET search_path = public, pg_temp;"
    )
    for fn_signature in (
        "public.current_app_workspace_id()",
        "public.current_app_user_id()",
        "public.set_updated_at()",
        "public.prevent_audit_log_mutation()",
    ):
        op.execute(f"ALTER FUNCTION {fn_signature} RESET search_path;")
