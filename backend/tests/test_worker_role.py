"""The worker's runtime role (``lattice_worker``, migration 0011, ADR-013) and the jobs
table's privileges. The worker writes ingestion tables, so its role is the narrowest
that a sync needs -- and nothing else: no identities, audit events, provider credentials
or settings; source-origin grants and groups only; one organization at a time for
everything but the queue. Retention (0015, P11.4) adds DELETE of aged rows behind
database floors, with SELECT of identifying and timestamp columns only -- never the
content of a session, trace or sign-in attempt (tests/ingestion/test_retention.py)."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from lattice.api.app import create_app
from lattice.db.engine import Database, create_engine
from lattice.db.role_check import UnsafeDatabaseRoleError, inspect_runtime_role

from .conftest import APP_URL, WORKER_URL, make_settings
from .factories import create_member, create_org, create_source

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")

# Table-level privileges of lattice_worker (column-level grants are checked separately).
WORKER_TABLES = {
    "organizations": {"SELECT"},
    "users": {"SELECT"},
    "memberships": {"SELECT"},
    "departments": {"SELECT"},
    "membership_departments": {"SELECT"},
    "accounts": {"SELECT"},
    "account_assignments": {"SELECT"},
    "access_groups": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "access_group_members": {"SELECT", "INSERT", "DELETE"},
    "documents": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "document_grants": {"SELECT", "INSERT", "DELETE"},
    "chunks": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "sources": {"SELECT"},
    "source_sync_runs": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "sync_record_failures": {"SELECT", "INSERT"},
    "org_settings": {"SELECT"},
    "model_providers": {"SELECT", "INSERT"},
    "jobs": {"SELECT", "DELETE"},
    "external_identities": set(),
    "sessions": {"DELETE"},
    "query_traces": {"DELETE"},
    "audit_events": set(),
    "provider_credentials": set(),
    # Phase 10: identity, provisioning and limits are none of a sync's business.
    "oidc_connections": set(),
    "oidc_login_attempts": {"DELETE"},
    "directory_identities": set(),
    "scim_tokens": set(),
    "rate_limit_counters": {"DELETE"},
    # Phase 11 (P11.4): retention only.
    "ask_slots": {"DELETE"},
    "pre_auth_rate_limits": set(),
    # Phase 12 (P12.1): the worker presents no organization credential (C3.4) and never
    # reads outbound registrations.
    "encrypted_secrets": set(),
    "outbound_targets": set(),
    # Phase 12 (P12.3): a sync resolves references through the mappings, read-only.
    "account_external_refs": {"SELECT"},
}


async def _held(engine: AsyncEngine, role: str, table: str) -> set[str]:
    async with engine.connect() as conn:
        return {
            p
            for p in PRIVILEGES
            if (
                await conn.execute(
                    text("SELECT has_table_privilege(:r, :t, :p)"), {"r": role, "t": table, "p": p}
                )
            ).scalar_one()
        }


@pytest.mark.parametrize(("table", "expected"), sorted(WORKER_TABLES.items()))
async def test_worker_table_privileges_are_exactly_what_a_sync_needs(
    superuser_engine: AsyncEngine, table: str, expected: set[str]
) -> None:
    assert await _held(superuser_engine, "lattice_worker", table) == expected


async def test_every_table_is_in_the_worker_inventory(superuser_engine: AsyncEngine) -> None:
    async with superuser_engine.connect() as conn:
        tables: set[str] = set(
            (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
                        "AND c.relkind IN ('r', 'p') AND c.relname <> 'alembic_version'"
                    )
                )
            ).scalars()
        )
    assert tables == set(WORKER_TABLES), "new table? decide the worker's privileges on it"


async def test_column_grants(superuser_engine: AsyncEngine) -> None:
    async with superuser_engine.connect() as conn:

        async def column(role: str, table: str, col: str, privilege: str) -> bool:
            held: bool = (
                await conn.execute(
                    text("SELECT has_column_privilege(:r, :t, :c, :p)"),
                    {"r": role, "t": table, "c": col, "p": privilege},
                )
            ).scalar_one()
            return held

        assert await column("lattice_worker", "memberships", "authz_updated_at", "UPDATE")
        for col in ("last_synced_at", "last_succeeded_at", "acl_synced_at"):
            assert await column("lattice_worker", "sources", col, "UPDATE"), col
        for col in ("config", "name", "status", "default_sensitivity", "account_scoped"):
            assert not await column("lattice_worker", "sources", col, "UPDATE"), col
        for col in ("clearance", "account_scope", "is_admin", "status"):
            assert not await column("lattice_worker", "memberships", col, "UPDATE"), col
        for col in ("status", "attempts", "lease_expires_at", "error_code", "sync_run_id"):
            assert await column("lattice_worker", "jobs", col, "UPDATE"), col
        for col in ("organization_id", "source_id", "requested_by", "kind", "mode"):
            assert not await column("lattice_worker", "jobs", col, "UPDATE"), col
        # The API queues jobs: it may insert the request columns, never change a job.
        for col in ("organization_id", "kind", "source_id", "mode", "requested_by"):
            assert await column("lattice_app", "jobs", col, "INSERT"), col
        for col in ("status", "attempts", "error_code", "finished_at"):
            assert not await column("lattice_app", "jobs", col, "INSERT"), col
        assert await _held(superuser_engine, "lattice_app", "jobs") == {"SELECT"}


async def test_the_worker_role_passes_the_runtime_safety_check() -> None:
    engine = create_engine(WORKER_URL, application_name="lattice-tests-worker-role", pool_size=1)
    try:
        report = await inspect_runtime_role(engine)
    finally:
        await engine.dispose()
    assert report.role_name == "lattice_worker"
    assert report.problems == ()


async def test_the_api_refuses_the_worker_credential() -> None:
    engine = create_engine(WORKER_URL, application_name="lattice-tests-worker-role", pool_size=1)
    try:
        report = await inspect_runtime_role(engine, forbid_content_writes=True)
    finally:
        await engine.dispose()
    assert any("can write documents" in p for p in report.problems)
    app = create_app(make_settings(database_url=WORKER_URL))
    with pytest.raises(UnsafeDatabaseRoleError, match="can write documents"):
        async with app.router.lifespan_context(app):
            pass


async def test_the_api_role_writes_no_content() -> None:
    engine = create_engine(APP_URL, application_name="lattice-tests-app-role", pool_size=1)
    try:
        report = await inspect_runtime_role(engine, forbid_content_writes=True)
    finally:
        await engine.dispose()
    assert report.problems == ()


# --- policies ----------------------------------------------------------------------------


@pytest.fixture
async def world(owner_db: Database) -> dict[str, uuid.UUID]:
    a = await create_org(owner_db, "org-a")
    b = await create_org(owner_db, "org-b")
    member = await create_member(owner_db, a, "admin@a.example")
    source_a = await create_source(owner_db, a)
    await create_source(owner_db, b)
    async with owner_db.tenant_transaction(a) as s:
        doc: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO documents (organization_id, source_id, external_id, "
                    "document_kind, title, authority, source_updated_at, content_hash, "
                    "access_hash, body, sensitivity, access_mode) VALUES (:o, :s, 'x', 'doc', "
                    "'T', 'working', now(), sha256('x'), sha256('x'), 'b', 'internal', 'policy') "
                    "RETURNING id"
                ),
                {"o": a, "s": source_a},
            )
        ).scalar_one()
        group: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO access_groups (organization_id, key, origin, name) "
                    "VALUES (:o, 'admins-own', 'admin', 'Admin group') RETURNING id"
                ),
                {"o": a},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO document_grants (organization_id, document_id, principal_type, "
                "membership_id, origin) VALUES (:o, :d, 'membership', :m, 'admin')"
            ),
            {"o": a, "d": doc, "m": member.membership_id},
        )
    return {
        "a": a,
        "b": b,
        "source_a": source_a,
        "doc": doc,
        "group": group,
        "member": member.membership_id,
    }


async def test_the_worker_cannot_touch_an_administrators_grant_or_group(
    worker_db: Database, world: dict[str, uuid.UUID]
) -> None:
    a = world["a"]
    async with worker_db.tenant_transaction(a) as s:
        gone = await s.execute(
            text("DELETE FROM document_grants WHERE origin = 'admin' RETURNING id")
        )
        assert gone.first() is None
        gone = await s.execute(
            text("DELETE FROM access_groups WHERE id = :g RETURNING id"), {"g": world["group"]}
        )
        assert gone.first() is None
    for sql in (
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "membership_id, origin) VALUES (:o, :d, 'membership', :m, 'admin')",
        "INSERT INTO access_groups (organization_id, key, origin, name) "
        "VALUES (:o, 'forged', 'admin', 'Forged')",
        "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
        "VALUES (:o, :g, :m)",
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            async with worker_db.tenant_transaction(a) as s:
                await s.execute(
                    text(sql),
                    {"o": a, "d": world["doc"], "m": world["member"], "g": world["group"]},
                )


async def test_the_worker_records_embedding_providers_approved_for_internal_only(
    worker_db: Database, world: dict[str, uuid.UUID]
) -> None:
    a = world["a"]
    for purpose, level in (("generation", "internal"), ("embedding", "confidential")):
        with pytest.raises(DBAPIError, match="row-level security"):
            async with worker_db.tenant_transaction(a) as s:
                await s.execute(
                    text(
                        "INSERT INTO model_providers (organization_id, purpose, provider, model, "
                        "deployment, approved_for_level, retention_mode, training_opt_out) "
                        "VALUES (:o, :p, 'x', 'm', 'local', :l, 'none', true)"
                    ),
                    {"o": a, "p": purpose, "l": level},
                )


async def test_the_worker_writes_one_organization_at_a_time(
    worker_db: Database, world: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with worker_db.tenant_transaction(world["a"]) as s:
            await s.execute(
                text(
                    "INSERT INTO source_sync_runs (organization_id, source_id, mode) "
                    "VALUES (:o, :s, 'full')"
                ),
                {"o": world["b"], "s": world["source_a"]},
            )
    # Without a bound organization it sees no tenant rows at all (the queue aside).
    async with worker_db.unscoped_transaction() as s:
        for table in ("documents", "sources", "memberships", "chunks"):
            count: int = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == 0, table


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM sessions",
        "SELECT * FROM query_traces",
        "SELECT * FROM provider_credentials",
        "SELECT * FROM audit_events",
        "SELECT * FROM external_identities",
        "UPDATE memberships SET is_admin = true",
        "UPDATE org_settings SET external_egress_level = 'restricted'",
        "INSERT INTO jobs (organization_id, kind, source_id, requested_by) "
        "VALUES (gen_random_uuid(), 'source_sync', gen_random_uuid(), gen_random_uuid())",
    ],
)
async def test_the_worker_has_no_privilege_outside_ingestion(
    worker_db: Database, world: dict[str, uuid.UUID], sql: str
) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with worker_db.tenant_transaction(world["a"]) as s:
            await s.execute(text(sql))
