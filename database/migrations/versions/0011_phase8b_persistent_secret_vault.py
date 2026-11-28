"""Phase 8b schema: persistent AES-256-GCM secret vault (Meta tokens survive restarts).

Revision ID: 0011_phase8b_persistent_secret_vault
Revises: 0010_phase8_social_auth_and_capi
Create Date: 2026-11-28 13:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_phase8b_persistent_secret_vault"
down_revision: str | Sequence[str] | None = "0010_phase8_social_auth_and_capi"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "secret_vault",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("secret_ref", sa.Text(), nullable=False, unique=True),
        sa.Column("namespace", sa.Text(), nullable=False, server_default=sa.text("'meta'")),
        sa.Column("nonce", postgresql.BYTEA(), nullable=False),
        sa.Column("ciphertext", postgresql.BYTEA(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_secret_vault_ws_id"),
    )
    op.create_index("ix_secret_vault_ws", "secret_vault", ["workspace_id"])
    op.execute(
        """
        CREATE TRIGGER trg_secret_vault_updated_at
        BEFORE UPDATE ON public.secret_vault
        FOR EACH ROW
        EXECUTE FUNCTION public.set_updated_at();
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON public.secret_vault TO metuse_app;")
    op.execute("ALTER TABLE public.secret_vault ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE public.secret_vault FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY secret_vault_tenant_isolation ON public.secret_vault
        FOR ALL TO metuse_app
        USING (workspace_id = public.current_app_workspace_id())
        WITH CHECK (workspace_id = public.current_app_workspace_id());
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS secret_vault_tenant_isolation ON public.secret_vault;")
    op.drop_table("secret_vault")
