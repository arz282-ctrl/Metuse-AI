"""Idempotent development-only seed runner for Metuse AI.

Refuses to run when APP_ENV is production, staging, or any non-development environment.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
from uuid import UUID, uuid5

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from packages.shared.config import (
    DEV_APP_ENVS,
    NON_DEV_APP_ENVS,
    BaseAppSettings,
    normalize_database_url,
)
from packages.shared.enums import (
    ClientStatus,
    IdentityProvider,
    MemberStatus,
    RoleName,
    WorkspaceType,
)

_SEED_NS = UUID("8b7d4c10-2e3f-4b5a-9c6d-1e2f3a4b5c6d")

ROLE_DEFINITIONS: tuple[tuple[RoleName, str, str, list[str]], ...] = (
    (
        RoleName.AGENCY_OWNER,
        "workspace",
        "Full agency workspace ownership including billing and member management",
        ["workspace:*", "members:*", "clients:*", "brand:*", "audit:read"],
    ),
    (
        RoleName.AGENCY_ADMIN,
        "workspace",
        "Agency administrator managing members, clients, and brand profiles",
        ["workspace:read", "members:manage", "clients:*", "brand:*", "audit:read"],
    ),
    (
        RoleName.AGENCY_MEMBER,
        "workspace",
        "Agency staff working on assigned clients and brand profiles",
        ["workspace:read", "clients:read", "brand:write"],
    ),
    (
        RoleName.CREATOR_OWNER,
        "workspace",
        "Solo creator workspace owner (usable without an ad account)",
        ["workspace:*", "clients:*", "brand:*", "audit:read"],
    ),
    (
        RoleName.CLIENT_REVIEWER,
        "client",
        "Client stakeholder able to review and approve deliverables",
        ["client:read", "brand:read", "deliverables:approve"],
    ),
    (
        RoleName.CLIENT_VIEWER,
        "client",
        "Client stakeholder with read-only access",
        ["client:read", "brand:read"],
    ),
)


def _seed_uuid(label: str) -> UUID:
    return uuid5(_SEED_NS, label)


def _dev_password_hash(password: str, salt_label: str) -> str:
    salt = hashlib.sha256(salt_label.encode("utf-8")).digest()[:16]
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1${}${}".format(
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def assert_dev_environment(app_env: str | None = None) -> str:
    """Validate APP_ENV is strictly a local/dev environment; raise RuntimeError otherwise."""
    raw_env = app_env if app_env is not None else os.environ.get("APP_ENV")
    if raw_env is None:
        raw_env = BaseAppSettings().app_env
    normalized = raw_env.strip().lower()
    if normalized in NON_DEV_APP_ENVS or normalized not in DEV_APP_ENVS:
        raise RuntimeError(
            f"Refusing to run dev seed script when APP_ENV={raw_env!r}. "
            f"Allowed dev environments: {sorted(DEV_APP_ENVS)}."
        )
    return normalized


def seed_canonical_roles(conn: Connection) -> dict[str, UUID]:
    """Idempotently upsert the 6 canonical RBAC roles."""
    role_ids: dict[str, UUID] = {}
    for role_name, scope, description, permissions in ROLE_DEFINITIONS:
        role_id = _seed_uuid(f"role:{role_name.value}")
        role_ids[role_name.value] = role_id
        conn.execute(
            text(
                """
                INSERT INTO public.roles (id, name, scope, description, permissions)
                VALUES (:id, :name, :scope, :description, CAST(:permissions AS jsonb))
                ON CONFLICT (name) DO UPDATE
                SET scope = EXCLUDED.scope,
                    description = EXCLUDED.description,
                    permissions = EXCLUDED.permissions
                """
            ),
            {
                "id": role_id,
                "name": role_name.value,
                "scope": scope,
                "description": description,
                "permissions": __import__("json").dumps(permissions),
            },
        )
    return role_ids


def _resolve_role_ids(conn: Connection) -> dict[str, UUID]:
    rows = conn.execute(text("SELECT id, name FROM public.roles")).mappings().all()
    return {str(row["name"]): UUID(str(row["id"])) for row in rows}


def run_seed_dev(database_url: str | None = None, *, app_env: str | None = None) -> dict[str, str]:
    """Execute idempotent development seeds using owner DB connection."""
    env_name = assert_dev_environment(app_env)
    resolved_url = (
        normalize_database_url(database_url)
        if database_url
        else BaseAppSettings().get_sqlalchemy_database_url(use_app_role=False)
    )
    dev_pass = os.environ.get("DEV_SEED_PASSWORD", "metuse-dev-local-only")

    engine = create_engine(resolved_url, future=True)
    try:
        with engine.begin() as conn:
            seed_canonical_roles(conn)
            role_ids = _resolve_role_ids(conn)

            # Demo users (agency owner, agency admin, agency member, creator owner)
            users_spec = [
                ("owner@acme-agency.local", "Acme Agency Owner", "user:acme-owner"),
                ("admin@acme-agency.local", "Acme Agency Admin", "user:acme-admin"),
                ("member@acme-agency.local", "Acme Agency Member", "user:acme-member"),
                ("creator@solo-studio.local", "Solo Creator", "user:solo-creator"),
            ]
            user_ids: dict[str, UUID] = {}
            for email, full_name, label in users_spec:
                uid = _seed_uuid(label)
                user_ids[label] = uid
                conn.execute(
                    text(
                        """
                        INSERT INTO public.users (id, email, password_hash, full_name, is_active)
                        VALUES (:id, :email, :password_hash, :full_name, true)
                        ON CONFLICT (email) DO NOTHING
                        """
                    ),
                    {
                        "id": uid,
                        "email": email,
                        "password_hash": _dev_password_hash(dev_pass, label),
                        "full_name": full_name,
                    },
                )
                persisted_uid = conn.execute(
                    text("SELECT id FROM public.users WHERE email = :email"),
                    {"email": email},
                ).scalar_one()
                user_ids[label] = UUID(str(persisted_uid))

                conn.execute(
                    text(
                        """
                        INSERT INTO public.user_identities (
                            id, user_id, provider, provider_subject, token_secret_ref, metadata
                        )
                        VALUES (
                            :id, :user_id, :provider, :provider_subject, NULL, CAST('{}' AS jsonb)
                        )
                        ON CONFLICT (provider, provider_subject) DO NOTHING
                        """
                    ),
                    {
                        "id": _seed_uuid(f"identity:{email}"),
                        "user_id": user_ids[label],
                        "provider": IdentityProvider.EMAIL.value,
                        "provider_subject": email,
                    },
                )

            # Demo Agency Workspace (3 of 5 members seeded so tests/devs can add 2 more up to 5)
            agency_ws_id = _seed_uuid("workspace:acme-demo-agency")
            conn.execute(
                text(
                    """
                    INSERT INTO public.workspaces (
                        id, name, slug, workspace_type, max_members, created_by_user_id
                    )
                    VALUES (:id, :name, :slug, :workspace_type, 5, :created_by_user_id)
                    ON CONFLICT (slug) DO NOTHING
                    """
                ),
                {
                    "id": agency_ws_id,
                    "name": "Acme Demo Agency",
                    "slug": "acme-demo-agency",
                    "workspace_type": WorkspaceType.AGENCY.value,
                    "created_by_user_id": user_ids["user:acme-owner"],
                },
            )
            agency_ws_id = UUID(
                str(
                    conn.execute(
                        text("SELECT id FROM public.workspaces WHERE slug = :slug"),
                        {"slug": "acme-demo-agency"},
                    ).scalar_one()
                )
            )

            agency_members = [
                ("user:acme-owner", RoleName.AGENCY_OWNER.value),
                ("user:acme-admin", RoleName.AGENCY_ADMIN.value),
                ("user:acme-member", RoleName.AGENCY_MEMBER.value),
            ]
            for user_label, role_name in agency_members:
                conn.execute(
                    text(
                        """
                        INSERT INTO public.workspace_memberships (
                            id, workspace_id, user_id, role_id, status
                        )
                        VALUES (:id, :workspace_id, :user_id, :role_id, :status)
                        ON CONFLICT (workspace_id, user_id) DO NOTHING
                        """
                    ),
                    {
                        "id": _seed_uuid(f"membership:acme:{user_label}"),
                        "workspace_id": agency_ws_id,
                        "user_id": user_ids[user_label],
                        "role_id": role_ids[role_name],
                        "status": MemberStatus.ACTIVE.value,
                    },
                )

            # Demo Client & Sample Brand Profile
            demo_client_id = _seed_uuid("client:nova-athletics")
            conn.execute(
                text(
                    """
                    INSERT INTO public.clients (id, workspace_id, name, slug, status, metadata)
                    VALUES (
                        :id, :workspace_id, :name, :slug, :status,
                        CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (workspace_id, slug) DO NOTHING
                    """
                ),
                {
                    "id": demo_client_id,
                    "workspace_id": agency_ws_id,
                    "name": "Nova Athletics",
                    "slug": "nova-athletics",
                    "status": ClientStatus.ACTIVE.value,
                    "metadata": '{"industry": "sportswear", "seeded": true}',
                },
            )
            demo_client_id = UUID(
                str(
                    conn.execute(
                        text(
                            "SELECT id FROM public.clients "
                            "WHERE workspace_id = :ws AND slug = :slug"
                        ),
                        {"ws": agency_ws_id, "slug": "nova-athletics"},
                    ).scalar_one()
                )
            )

            conn.execute(
                text(
                    """
                    INSERT INTO public.brand_profiles (
                        id, workspace_id, client_id, brand_name,
                        tone_of_voice, target_audience, color_palette, guidelines
                    )
                    VALUES (
                        :id, :workspace_id, :client_id, :brand_name,
                        :tone_of_voice, :target_audience,
                        CAST(:color_palette AS jsonb), :guidelines
                    )
                    ON CONFLICT (workspace_id, client_id) DO NOTHING
                    """
                ),
                {
                    "id": _seed_uuid("brand:nova-athletics"),
                    "workspace_id": agency_ws_id,
                    "client_id": demo_client_id,
                    "brand_name": "Nova Athletics",
                    "tone_of_voice": "Energetic, direct, and performance-focused",
                    "target_audience": "Everyday runners and hybrid strength athletes",
                    "color_palette": '["#111827", "#F97316", "#F8FAFC"]',
                    "guidelines": (
                        "Lead with real training motion in the first 2 seconds; avoid clickbait."
                    ),
                },
            )

            # Demo Creator Workspace (zero ad accounts, zero Meta connections)
            creator_ws_id = _seed_uuid("workspace:solo-creator-studio")
            conn.execute(
                text(
                    """
                    INSERT INTO public.workspaces (
                        id, name, slug, workspace_type, max_members, created_by_user_id
                    )
                    VALUES (:id, :name, :slug, :workspace_type, 1, :created_by_user_id)
                    ON CONFLICT (slug) DO NOTHING
                    """
                ),
                {
                    "id": creator_ws_id,
                    "name": "Solo Creator Studio",
                    "slug": "solo-creator-studio",
                    "workspace_type": WorkspaceType.CREATOR.value,
                    "created_by_user_id": user_ids["user:solo-creator"],
                },
            )
            creator_ws_id = UUID(
                str(
                    conn.execute(
                        text("SELECT id FROM public.workspaces WHERE slug = :slug"),
                        {"slug": "solo-creator-studio"},
                    ).scalar_one()
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO public.workspace_memberships (
                        id, workspace_id, user_id, role_id, status
                    )
                    VALUES (:id, :workspace_id, :user_id, :role_id, :status)
                    ON CONFLICT (workspace_id, user_id) DO NOTHING
                    """
                ),
                {
                    "id": _seed_uuid("membership:solo-creator"),
                    "workspace_id": creator_ws_id,
                    "user_id": user_ids["user:solo-creator"],
                    "role_id": role_ids[RoleName.CREATOR_OWNER.value],
                    "status": MemberStatus.ACTIVE.value,
                },
            )

            # Initial append-only seed audit entry (INSERT ... ON CONFLICT DO NOTHING only)
            conn.execute(
                text(
                    """
                    INSERT INTO public.audit_logs (
                        id, workspace_id, client_id, actor_user_id,
                        action, target_type, target_id, metadata
                    )
                    VALUES (
                        :id, :workspace_id, NULL, :actor_user_id,
                        'workspace.created', 'workspace', :target_id,
                        CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": _seed_uuid("audit:acme-demo-agency-created"),
                    "workspace_id": agency_ws_id,
                    "actor_user_id": user_ids["user:acme-owner"],
                    "target_id": str(agency_ws_id),
                    "metadata": '{"source": "seed_dev", "workspace_type": "agency"}',
                },
            )
    finally:
        engine.dispose()

    return {
        "app_env": env_name,
        "agency_workspace_id": str(agency_ws_id),
        "creator_workspace_id": str(creator_ws_id),
        "demo_client_id": str(demo_client_id),
    }


def main() -> int:
    try:
        summary = run_seed_dev()
    except Exception as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        return 1
    print(
        "Seeded development fixtures successfully: "
        f"env={summary['app_env']} "
        f"agency_ws={summary['agency_workspace_id']} "
        f"creator_ws={summary['creator_workspace_id']} "
        f"client={summary['demo_client_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
