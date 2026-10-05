# ruff: noqa: S608 - fixed test statements; ages are module constants
"""Retention (Phase 11, P11.4; plan §12, deployment.md §13): the worker deletes exactly
the rows past each class's rule, across organizations; the database refuses anything
younger than the floor even to a direct statement as ``lattice_worker``; the latest sync
run of each source is kept; audit events and tombstones are never touched; a repeated
or concurrent run deletes nothing twice; every task is metered."""

import asyncio
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from lattice import metrics
from lattice.db.engine import Database
from lattice.jobs.retention import TASKS, RetentionPolicy, run_retention, run_task

from ..factories import create_member, create_oidc_connection, create_org, create_source

OLD = "now() - interval '400 days'"
# 32 random bytes without pgcrypto.
RANDOM_32 = "decode(md5(random()::text) || md5(random()::text), 'hex')"
YOUNG = "now() - interval '10 days'"


@dataclass
class Tenant:
    org: uuid.UUID
    member: uuid.UUID
    user: uuid.UUID
    source: uuid.UUID
    connection: uuid.UUID


async def _tenant(owner_db: Database, slug: str) -> Tenant:
    org = await create_org(owner_db, slug)
    member = await create_member(owner_db, org, f"maya@{slug}.example")
    return Tenant(
        org,
        member.membership_id,
        member.user_id,
        await create_source(owner_db, org),
        await create_oidc_connection(owner_db, org),
    )


async def _rows(owner_db: Database, tenant: Tenant, age: str, *, run_count: int = 1) -> None:
    """One row of every retained table (and an audit event) created at ``age``."""
    p = {
        "o": tenant.org,
        "m": tenant.member,
        "u": tenant.user,
        "s": tenant.source,
        "c": tenant.connection,
    }
    async with owner_db.tenant_transaction(tenant.org) as s:
        await s.execute(
            text(
                "INSERT INTO query_traces (organization_id, membership_id, created_at, "
                "question_text, question_sensitivity, authz_snapshot, retrieval, egress, "
                f"validation, status, timings_ms) VALUES (:o, :m, {age}, 'q', 'internal', "
                "'{}', '{}', '{}', '{}', 'answered', '{}')"
            ),
            p,
        )
        await s.execute(
            text(
                "INSERT INTO sessions (token_hash, user_id, organization_id, created_at, "
                f"last_seen_at, expires_at) VALUES ({RANDOM_32}, :u, :o, {age}, "
                f"{age}, {age} + interval '7 days')"
            ),
            p,
        )
        await s.execute(
            text(
                "INSERT INTO oidc_login_attempts (organization_id, connection_id, state_hash, "
                "binding_hash, nonce, code_verifier, created_at, expires_at) VALUES (:o, :c, "
                f"{RANDOM_32}, {RANDOM_32}, repeat('n', 22), repeat('v', 43), {age}, "
                f"{age} + interval '10 minutes')"
            ),
            p,
        )
        await s.execute(
            text(
                "INSERT INTO rate_limit_counters (organization_id, bucket, subject, "
                f"window_start, count) VALUES (:o, 'ask_minute', gen_random_uuid(), {age}, 1)"
            ),
            p,
        )
        await s.execute(
            text(
                "INSERT INTO ask_slots (organization_id, membership_id, slot, lease_id, "
                f"expires_at) VALUES (:o, :m, (SELECT count(*) FROM ask_slots), "
                f"gen_random_uuid(), {age})"
            ),
            p,
        )
        for _ in range(run_count):
            await s.execute(
                text(
                    "INSERT INTO source_sync_runs (organization_id, source_id, mode, status, "
                    f"started_at, finished_at) VALUES (:o, :s, 'full', 'succeeded', "
                    f"{age} + random() * interval '1 hour', {age} + interval '2 hours')"
                ),
                p,
            )
        await s.execute(
            text(
                "INSERT INTO jobs (organization_id, kind, source_id, mode, requested_by, "
                "status, attempts, created_at, run_after, started_at, finished_at) VALUES "
                f"(:o, 'source_sync', :s, 'full', :m, 'succeeded', 1, {age}, {age}, {age}, "
                f"{age})"
            ),
            p,
        )
        await s.execute(
            text(
                "INSERT INTO audit_events (organization_id, created_at, actor, action, target, "
                f"reason) VALUES (:o, {age}, 'test', 'provider.configured', '{{}}', 'r')"
            ),
            p,
        )
    # The non-tenant counters: no login role may write them directly (forced RLS).
    async with SUPERUSER.engine.begin() as conn:
        await conn.execute(
            text(f"INSERT INTO pre_auth_rate_limits VALUES ('sign_in', {RANDOM_32}, {age}, 1)")
        )


_COUNTED = (
    "query_traces",
    "sessions",
    "oidc_login_attempts",
    "rate_limit_counters",
    "ask_slots",
    "source_sync_runs",
    "jobs",
    "audit_events",
)


async def _counts(owner_db: Database, tenant: Tenant) -> dict[str, int]:
    found: dict[str, int] = {}
    async with owner_db.tenant_transaction(tenant.org) as s:
        for table in _COUNTED:
            found[table] = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
    async with SUPERUSER.engine.connect() as conn:
        found["pre_auth_rate_limits"] = (
            await conn.execute(text("SELECT count(*) FROM pre_auth_rate_limits"))
        ).scalar_one()
    return found


class _Superuser:
    engine: AsyncEngine


SUPERUSER = _Superuser()


@pytest.fixture
async def tenants(owner_db: Database, superuser_engine: AsyncEngine) -> tuple[Tenant, Tenant]:
    SUPERUSER.engine = superuser_engine
    return await _tenant(owner_db, "halden"), await _tenant(owner_db, "veridian")


async def test_retention_deletes_exactly_the_eligible_rows_across_organizations(
    owner_db: Database, worker_db: Database, tenants: tuple[Tenant, Tenant]
) -> None:
    halden, veridian = tenants
    for tenant in tenants:
        await _rows(owner_db, tenant, OLD, run_count=2)  # two old runs of one source
        await _rows(owner_db, tenant, YOUNG)
    before = {t.org: await _counts(owner_db, t) for t in tenants}

    results = await run_retention(worker_db, RetentionPolicy())

    assert set(results) == set(TASKS)
    for tenant in tenants:
        after = await _counts(owner_db, tenant)
        assert after["audit_events"] == before[tenant.org]["audit_events"] == 2  # never
        # Ten days old: kept by the long classes...
        for table in ("query_traces", "sessions", "jobs"):
            assert after[table] == 1, table
        # ...and both old runs superseded by the young one go; the young one stays.
        assert after["source_sync_runs"] == 1
        # ...but past the short ones (1 day, 2 days, 1 hour).
        for table in ("oidc_login_attempts", "rate_limit_counters", "ask_slots"):
            assert after[table] == 0, table
    assert results == {
        "jobs": 2,
        "query_traces": 2,
        "sessions": 2,
        "oidc_login_attempts": 4,
        "rate_limit_counters": 4,
        "ask_slots": 4,
        "pre_auth_rate_limits": 4,
        "source_sync_runs": 4,
    }

    # A repeated run finds nothing left.
    again = await run_retention(worker_db, RetentionPolicy())
    assert all(count == 0 for count in again.values()), again
    assert halden.org != veridian.org


async def test_the_latest_run_of_a_source_is_kept_however_old(
    owner_db: Database, worker_db: Database, tenants: tuple[Tenant, Tenant]
) -> None:
    halden, _ = tenants
    await _rows(owner_db, halden, OLD, run_count=3)
    assert await run_task(worker_db, "source_sync_runs", RetentionPolicy()) == 2
    assert (await _counts(owner_db, halden))["source_sync_runs"] == 1


async def test_a_longer_trace_retention_keeps_more_and_a_shorter_one_is_refused(
    owner_db: Database, worker_db: Database, tenants: tuple[Tenant, Tenant]
) -> None:
    halden, _ = tenants
    await _rows(owner_db, halden, OLD)
    assert await run_task(worker_db, "query_traces", RetentionPolicy(trace_days=500)) == 0
    assert await run_task(worker_db, "query_traces", RetentionPolicy(trace_days=90)) == 1
    with pytest.raises(ValueError, match="at least 90"):
        RetentionPolicy(trace_days=30)


@pytest.mark.parametrize(
    ("table", "age"),
    [
        ("query_traces", "now() - interval '89 days'"),
        ("sessions", "now() - interval '36 days'"),  # expired 29 days ago
        ("oidc_login_attempts", "now() - interval '23 hours'"),
        ("rate_limit_counters", "now() - interval '47 hours'"),
        ("ask_slots", "now() - interval '50 minutes'"),
        ("source_sync_runs", "now() - interval '179 days'"),
        ("jobs", "now() - interval '29 days'"),
    ],
)
async def test_the_database_refuses_younger_rows_even_with_the_organization_bound(
    owner_db: Database, worker_db: Database, tenants: tuple[Tenant, Tenant], table: str, age: str
) -> None:
    halden, _ = tenants
    await _rows(owner_db, halden, age, run_count=2)
    async with worker_db.tenant_transaction(halden.org) as s:
        deleted = len((await s.execute(text(f"DELETE FROM {table} RETURNING 1"))).all())
    assert deleted == 0
    async with worker_db.unscoped_transaction() as s:
        deleted = len((await s.execute(text(f"DELETE FROM {table} RETURNING 1"))).all())
    assert deleted == 0
    assert (await _counts(owner_db, halden))[table] >= 1


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM audit_events",
        "DELETE FROM directory_identities",
        "DELETE FROM scim_tokens",
        "DELETE FROM oidc_connections",
        "DELETE FROM provider_credentials",
        "DELETE FROM pre_auth_rate_limits",
        "SELECT question_text FROM query_traces",
        "SELECT token_hash FROM sessions",
        "SELECT nonce FROM oidc_login_attempts",
    ],
)
async def test_the_worker_never_reaches_audit_tombstones_or_content(
    worker_db: Database, tenants: tuple[Tenant, Tenant], sql: str
) -> None:
    halden, _ = tenants
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with worker_db.tenant_transaction(halden.org) as s:
            await s.execute(text(sql))


async def test_concurrent_runs_delete_each_row_once(
    owner_db: Database, worker_db: Database, tenants: tuple[Tenant, Tenant]
) -> None:
    """Two workers at once: nothing fails, nothing is deleted twice, the latest run of
    each source survives. A batch that met the other worker's rows may stop early; the
    next run deletes what is left."""
    for tenant in tenants:
        for _ in range(3):
            await _rows(owner_db, tenant, OLD)
    first, second = await asyncio.gather(
        run_retention(worker_db, RetentionPolicy(batch=2)),
        run_retention(worker_db, RetentionPolicy(batch=2)),
    )
    after = await run_retention(worker_db, RetentionPolicy(batch=2))
    runs = (first, second, after)
    assert all(run[task] is not None for run in runs for task in TASKS)
    total = {task: sum(run[task] or 0 for run in runs) for task in TASKS}
    assert total == {
        "jobs": 6,
        "query_traces": 6,
        "sessions": 6,
        "oidc_login_attempts": 6,
        "rate_limit_counters": 6,
        "ask_slots": 6,
        "pre_auth_rate_limits": 6,
        "source_sync_runs": 4,  # the latest per source stays
    }
    for tenant in tenants:
        assert (await _counts(owner_db, tenant))["source_sync_runs"] == 1


async def test_retention_is_metered_per_task(
    owner_db: Database, worker_db: Database, tenants: tuple[Tenant, Tenant]
) -> None:
    halden, _ = tenants
    await _rows(owner_db, halden, OLD)

    def rows(task: str) -> float:
        return (
            metrics.REGISTRY.get_sample_value("lattice_maintenance_rows_total", {"task": task}) or 0
        )

    before = rows("sessions")
    await run_retention(worker_db, RetentionPolicy())
    assert rows("sessions") == before + 1
    for task in TASKS:
        stamp = metrics.REGISTRY.get_sample_value(
            "lattice_maintenance_last_success_timestamp_seconds", {"task": task}
        )
        assert stamp is not None, task
