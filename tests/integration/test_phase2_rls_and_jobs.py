"""Live PostgreSQL Phase 2 integration tests: FORCE RLS, workers, idempotency, usage & budgets."""

from __future__ import annotations

import os
from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, table, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from database.seeds.seed_dev import seed_canonical_roles
from packages.ai_core import (
    execute_specialist_job,
    get_usage_summary,
    get_worker_job,
    list_worker_jobs,
    upsert_workspace_budget,
)
from packages.muse_adapter import MockMuseAdapter
from packages.shared.config import BaseAppSettings, normalize_database_url
from packages.shared.enums import WorkerTaskType, WorkspaceType
from packages.shared.errors import (
    BudgetExceededError,
    ConflictError,
    NotFoundError,
    ProviderDisabledError,
)
from packages.skill_runtime import SkillRegistry
from packages.tenancy import (
    create_client,
    create_session_factory,
    create_workspace,
    tenant_transaction,
)
from packages.tenancy.db import is_supabase_transaction_pooler_url

pytestmark = pytest.mark.integration

_PHASE2_RLS_TABLES = (
    "worker_jobs",
    "model_runs",
    "usage_records",
    "workspace_budgets",
    "research_reports",
    "research_sources",
)


def _get_owner_dsn() -> str:
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return normalize_database_url(env_url)
    return BaseAppSettings().get_sqlalchemy_database_url(use_app_role=False)


def _get_app_dsn() -> str:
    env_url = os.environ.get("APP_DATABASE_URL")
    if env_url:
        return normalize_database_url(env_url)
    settings = BaseAppSettings()
    if (
        settings.app_database_url is not None
        and settings.app_database_url.get_secret_value().strip()
    ):
        return settings.get_sqlalchemy_database_url(use_app_role=True)
    return f"postgresql+psycopg://metuse_app:change-me-app@localhost:{settings.postgres_host_port}/metuse"


@pytest.fixture(scope="module")
def owner_engine() -> Iterator[Engine]:
    engine = create_engine(_get_owner_dsn(), future=True)
    with engine.begin() as conn:
        seed_canonical_roles(conn)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def app_session_factory() -> Iterator[sessionmaker[Session]]:
    dsn = _get_app_dsn()
    connect_args = {"prepare_threshold": None} if is_supabase_transaction_pooler_url(dsn) else {}
    engine = create_engine(dsn, connect_args=connect_args, future=True)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


def _insert_user(owner_engine: Engine, label: str) -> UUID:
    uid = uuid4()
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO public.users (id, email, password_hash, full_name, is_active)
                VALUES (:id, :email, 'scrypt$test$hash', :full_name, true)
                """
            ),
            {"id": uid, "email": f"p2-{label}-{uid.hex[:8]}@metuse.test", "full_name": label},
        )
    return uid


def _cleanup(owner_engine: Engine, *, workspace_ids: list[UUID], user_ids: list[UUID]) -> None:
    with owner_engine.begin() as conn:
        if workspace_ids:
            conn.execute(
                text("DELETE FROM public.workspaces WHERE id = ANY(:ws_ids)"),
                {"ws_ids": workspace_ids},
            )
        if user_ids:
            conn.execute(
                text("DELETE FROM public.users WHERE id = ANY(:u_ids)"),
                {"u_ids": user_ids},
            )


def test_phase2_tables_have_force_rls_enabled(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    """
                    SELECT relname, relrowsecurity, relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relname = ANY(:tables)
                    """
                ),
                {"tables": list(_PHASE2_RLS_TABLES)},
            )
            .mappings()
            .all()
        )
        by_name = {str(r["relname"]): r for r in rows}
        assert set(by_name.keys()) == set(_PHASE2_RLS_TABLES)
        for tbl in _PHASE2_RLS_TABLES:
            assert by_name[tbl]["relrowsecurity"] is True, tbl
            assert by_name[tbl]["relforcerowsecurity"] is True, tbl


def test_phase2_workers_idempotency_budgets_usage_and_cross_tenant_rls(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    created_users: list[UUID] = []
    created_workspaces: list[UUID] = []
    registry = SkillRegistry()
    muse = MockMuseAdapter()

    try:
        user_a = _insert_user(owner_engine, "p2-agency-a")
        user_b = _insert_user(owner_engine, "p2-agency-b")
        created_users.extend([user_a, user_b])

        ws_a = create_workspace(
            app_session_factory,
            user_id=user_a,
            name="Phase2 Agency A",
            workspace_type=WorkspaceType.AGENCY,
        )
        ws_b = create_workspace(
            app_session_factory,
            user_id=user_b,
            name="Phase2 Agency B",
            workspace_type=WorkspaceType.AGENCY,
        )
        created_workspaces.extend([ws_a.id, ws_b.id])

        with tenant_transaction(app_session_factory, user_id=user_a, workspace_id=ws_a.id) as (
            session,
            ctx_a,
        ):
            client_a = create_client(session, ctx_a, name="Brand Alpha")

            # Execute all 4 specialist task types under RLS
            idem_key = f"idem-{uuid4().hex[:8]}"
            research_job = execute_specialist_job(
                session,
                ctx_a,
                client_id=client_a.id,
                task_type=WorkerTaskType.RESEARCH,
                topic="DTC matcha Instagram reels",
                idempotency_key=idem_key,
                skill_registry=registry,
                muse_adapter=muse,
            )
            assert research_job.status.value == "succeeded"

            # Reusing same idempotency key returns identical job without double-billing
            deduped = execute_specialist_job(
                session,
                ctx_a,
                client_id=client_a.id,
                task_type=WorkerTaskType.RESEARCH,
                topic="DTC matcha Instagram reels",
                idempotency_key=idem_key,
                skill_registry=registry,
                muse_adapter=muse,
            )
            assert deduped.id == research_job.id

            # Reusing same idempotency key for a different task type raises ConflictError
            with pytest.raises(ConflictError):
                execute_specialist_job(
                    session,
                    ctx_a,
                    client_id=client_a.id,
                    task_type=WorkerTaskType.WRITING,
                    topic="Different task",
                    idempotency_key=idem_key,
                    skill_registry=registry,
                    muse_adapter=muse,
                )

            seo_job = execute_specialist_job(
                session,
                ctx_a,
                client_id=client_a.id,
                task_type=WorkerTaskType.SEO_AEO,
                topic="Organic matcha brewing",
                skill_registry=registry,
                muse_adapter=muse,
            )
            assert seo_job.status.value == "succeeded"

            media_job = execute_specialist_job(
                session,
                ctx_a,
                client_id=client_a.id,
                task_type=WorkerTaskType.MEDIA_PLAN,
                topic="Matcha morning ritual reel",
                skill_registry=registry,
                muse_adapter=muse,
            )
            assert media_job.status.value == "succeeded"

            # Verify usage & cost attribution (Acceptance #17)
            usage_a = get_usage_summary(session, ctx_a, client_id=client_a.id)
            assert usage_a.total_input_units > 0
            assert usage_a.total_output_units > 0
            assert usage_a.total_estimated_cost_usd > Decimal("0")
            assert sum(item.run_count for item in usage_a.breakdown) == 3

            # Enforce monthly unit budget cap -> BudgetExceededError
            upsert_workspace_budget(
                session,
                ctx_a,
                monthly_unit_limit=1,
                provider_kill_switch=False,
            )
            with pytest.raises(BudgetExceededError):
                execute_specialist_job(
                    session,
                    ctx_a,
                    client_id=client_a.id,
                    task_type=WorkerTaskType.WRITING,
                    topic="Should be blocked by budget",
                    skill_registry=registry,
                    muse_adapter=muse,
                )

            # Enforce kill switch -> ProviderDisabledError
            upsert_workspace_budget(
                session,
                ctx_a,
                monthly_unit_limit=None,
                provider_kill_switch=True,
            )
            with pytest.raises(ProviderDisabledError):
                execute_specialist_job(
                    session,
                    ctx_a,
                    client_id=client_a.id,
                    task_type=WorkerTaskType.WRITING,
                    topic="Should be blocked by kill switch",
                    skill_registry=registry,
                    muse_adapter=muse,
                )

        # Cross-tenant RLS isolation: Workspace B sees ZERO of Workspace A's jobs/usage/reports
        with tenant_transaction(app_session_factory, user_id=user_b, workspace_id=ws_b.id) as (
            session,
            ctx_b,
        ):
            assert list_worker_jobs(session, ctx_b) == []
            with pytest.raises(NotFoundError):
                get_worker_job(session, ctx_b, job_id=research_job.id)
            usage_b = get_usage_summary(session, ctx_b)
            assert usage_b.total_input_units == 0
            assert usage_b.total_estimated_cost_usd == Decimal("0")

            for tbl in _PHASE2_RLS_TABLES:
                cnt_stmt = select(func.count()).select_from(table(tbl, schema="public"))
                cnt = session.execute(cnt_stmt).scalar_one()
                assert cnt == 0, f"Cross-tenant leak detected in {tbl}"
    finally:
        _cleanup(owner_engine, workspace_ids=created_workspaces, user_ids=created_users)
