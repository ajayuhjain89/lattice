"""Phase 11 observability (P11.6, plan §16): membership lifecycle by actor, session
revocations by cause, the worker's backlog, database size and WAL-archiver gauges. Closed
label sets; no identifier ever becomes a label."""

import httpx
import pytest
from prometheus_client import generate_latest
from sqlalchemy import text

from lattice import metrics
from lattice.db.engine import Database
from lattice.identity import admin
from lattice.jobs.worker import Worker

from .conftest import CSRF
from .factories import create_member, create_org, create_source, login


def _value(name: str, **labels: str) -> float:
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.mark.parametrize(
    ("before", "after", "change"),
    [
        (None, "active", "created"),
        (None, "suspended", "created"),
        ("active", "suspended", "suspended"),
        ("suspended", "active", "reactivated"),
        ("removed", "active", "reactivated"),
        ("active", "removed", "removed"),
    ],
)
def test_member_changes_are_named_by_transition(
    before: str | None, after: str, change: str
) -> None:
    start = _value("lattice_member_lifecycle_total", change=change, actor_kind="scim")
    metrics.member_changed(before, after, "scim")
    assert _value("lattice_member_lifecycle_total", change=change, actor_kind="scim") == start + 1


def test_unknown_labels_are_bounded_and_nothing_is_counted_without_a_change() -> None:
    other = _value("lattice_member_lifecycle_total", change="suspended", actor_kind="other")
    metrics.member_changed("active", "suspended", "someone@example.com")
    metrics.member_changed("active", "active", "scim")
    assert (
        _value("lattice_member_lifecycle_total", change="suspended", actor_kind="other")
        == other + 1
    )
    revoked = _value("lattice_sessions_revoked_total", reason="other")
    metrics.sessions_revoked("a-free-text-reason", 3)
    metrics.sessions_revoked("logout", 0)
    assert _value("lattice_sessions_revoked_total", reason="other") == revoked + 3
    assert "someone@example.com" not in generate_latest(metrics.REGISTRY).decode()


async def test_logout_and_operator_actions_are_counted(
    owner_db: Database, client: httpx.AsyncClient
) -> None:
    org = await create_org(owner_db, "halden")
    await create_member(owner_db, org, "maya@halden.example")
    logouts = _value("lattice_sessions_revoked_total", reason="logout")
    assert (await login(client, "maya@halden.example")).status_code == 200
    assert (await client.post("/v1/auth/logout", headers=CSRF)).status_code == 204
    assert _value("lattice_sessions_revoked_total", reason="logout") == logouts + 1

    assert (await login(client, "maya@halden.example")).status_code == 200
    suspended = _value("lattice_member_lifecycle_total", change="suspended", actor_kind="operator")
    by_suspension = _value("lattice_sessions_revoked_total", reason="member_suspended")
    async with owner_db.tenant_transaction(org) as s:
        await admin.suspend_member(
            s, org, email="maya@halden.example", reason="r", actor="operator:test"
        )
    assert (
        _value("lattice_member_lifecycle_total", change="suspended", actor_kind="operator")
        == suspended + 1
    )
    assert _value("lattice_sessions_revoked_total", reason="member_suspended") == by_suspension + 1


async def test_the_worker_reports_backlog_and_database_size(
    owner_db: Database, app_db: Database, worker_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    member = await create_member(owner_db, org, "lena@halden.example", is_admin=True)
    source = await create_source(owner_db, org)
    async with owner_db.tenant_transaction(org) as s:
        await s.execute(
            text(
                "INSERT INTO jobs (organization_id, kind, source_id, requested_by, run_after) "
                "VALUES (:o, 'source_sync', :s, :m, now() - interval '5 minutes')"
            ),
            {"o": org, "s": source, "m": member.membership_id},
        )
    worker = Worker(worker_db, embedding=None)
    assert await worker.waiting() == 1
    assert 299 <= _value("lattice_jobs_oldest_waiting_seconds") < 400
    await worker.database_stats()
    assert _value("lattice_database_size_bytes") > 1_000_000
    async with owner_db.tenant_transaction(org) as s:
        await s.execute(text("DELETE FROM jobs"))
    assert await worker.waiting() == 0
    assert _value("lattice_jobs_oldest_waiting_seconds") == 0
