"""Phase 8 schema: Google social identity provider + CAPI pixel per ad account.

Revision ID: 0010_phase8_social_auth_and_capi
Revises: 0009_phase7_ads_and_controlled_execution
Create Date: 2026-11-28 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_phase8_social_auth_and_capi"
down_revision: str | Sequence[str] | None = "0009_phase7_ads_and_controlled_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Allow 'google' as a social identity provider alongside email/meta/supabase.
    op.drop_constraint("ck_user_identities_provider", "user_identities", type_="check")
    op.create_check_constraint(
        "ck_user_identities_provider",
        "user_identities",
        "provider IN ('email', 'meta', 'supabase', 'google')",
    )

    # 2. Optional Conversions API pixel/dataset id bound to each synced ad account.
    op.add_column(
        "ad_accounts",
        sa.Column("pixel_dataset_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ad_accounts", "pixel_dataset_id")

    op.drop_constraint("ck_user_identities_provider", "user_identities", type_="check")
    op.create_check_constraint(
        "ck_user_identities_provider",
        "user_identities",
        "provider IN ('email', 'meta', 'supabase')",
    )
