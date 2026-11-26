"""Additive, reversible Supabase profile & identity compatibility migration.

Revision ID: 0003_supabase_compat
Revises: 0002_harden_functions_search_path
Create Date: 2026-11-26 16:45:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_supabase_compat"
down_revision: str | Sequence[str] | None = "0002_harden_functions_search_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add nullable avatar_url to canonical public.users (maps Phase 1 profiles.avatar_url)
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))

    # 2. Expand user_identities.provider check constraint to include 'supabase'
    op.drop_constraint("ck_user_identities_provider", "user_identities", type_="check")
    op.create_check_constraint(
        "ck_user_identities_provider",
        "user_identities",
        "provider IN ('email', 'meta', 'supabase')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_user_identities_provider", "user_identities", type_="check")
    op.create_check_constraint(
        "ck_user_identities_provider",
        "user_identities",
        "provider IN ('email', 'meta')",
    )
    op.drop_column("users", "avatar_url")
