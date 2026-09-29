"""The job queue and the worker (Phase 8; ADR-013): requested syncs run as
``lattice_worker`` through row-level security, idempotently, with bounded retries and
leases, and without touching another organization or an administrator's grants.

Each Halden source is first synced by the operator command (as ``lattice_owner``, which
records the source and its export path), exactly as in a deployment."""

import asyncio
import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text

from lattice import metrics
from lattice.db.engine import Database, create_engine
from lattice.jobs.queue import SourceNotActive, SourceNotFound, request_sync
from lattice.jobs.worker import RETRY_BACKOFF_SECONDS, Worker
from lattice.providers.embedding import FakeEmbeddingProvider

from ..factories import create_source
from .conftest import Org, row_versions, sync

FAKE = FakeEmbeddingProvider()


async def _source(db: Database, org: Org, key: str) -> uuid.UUID:
    async with db.tenant_transaction(org.id) as s:
        value: uuid.UUID = (
            await s.execute(
                text("SELECT id FROM sources WHERE organization_id = :o AND key = :k"),
                {"o": org.id, "k": key},
            )
        ).scalar_one()
    return value


async def _admin(db: Database, org: Org) -> uuid.UUID:
    async with db.tenant_transaction(org.id) as s:
        value: uuid.UUID = (
            await s.execute(
                text("SELECT id FROM memberships WHERE organization_id = :o AND is_admin LIMIT 1"),
                {"o": org.id},
            )
        ).scalar_one()
    return value


async def _job(db: Database, org: Org, job_id: uuid.UUID) -> dict[str, object]:
    async with db.tenant_transaction(org.id) as s:
        row = (
            (await s.execute(text("SELECT * FROM jobs WHERE id = :j"), {"j": job_id}))
            .mappings()
            .one()
        )
    return dict(row)


@pytest.fixture
async def synced(owner_db: Database, halden: Org) -> Org:
    await sync(owner_db, halden, "drive", FAKE)
    return halden


def _sample(name: str, **labels: str) -> float:
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


async def test_a_requested_sync_runs_as_the_worker_and_is_idempotent(
    owner_db: Database, app_db: Database, worker_db: Database, synced: Org
) -> None:
    source = await _source(owner_db, synced, "drive")
    admin = await _admin(owner_db, synced)
    before = await row_versions(owner_db, synced.id)

    job, created = await request_sync(
        app_db, organization_id=synced.id, membership_id=admin, source_id=source
    )
    again, created_again = await request_sync(
        app_db, organization_id=synced.id, membership_id=admin, source_id=source
    )
    assert created
    assert not created_again
    assert again.id == job.id  # one active job per source

    succeeded = _sample("lattice_jobs_total", kind="source_sync", outcome="succeeded")
    worker = Worker(worker_db, embedding=FAKE)
    outcome = await worker.run_once()
    assert outcome is not None
    assert (outcome.outcome, outcome.sync_status) == ("succeeded", "succeeded")
    assert await worker.run_once() is None  # nothing else is due

    row = await _job(owner_db, synced, job.id)
    assert row["status"] == "succeeded"
    assert row["attempts"] == 1
    assert row["sync_run_id"] is not None
    assert row["lease_expires_at"] is None
    # The export did not change: the requested sync wrote no document, chunk or grant
    # (only the source's last-sync timestamps moved).
    after = await row_versions(owner_db, synced.id)
    assert {t: v for t, v in after.items() if t != "sources"} == {
        t: v for t, v in before.items() if t != "sources"
    }
    assert _sample("lattice_jobs_total", kind="source_sync", outcome="succeeded") == succeeded + 1

    # A new request after the first finished is a new job.
    third, created_third = await request_sync(
        app_db, organization_id=synced.id, membership_id=admin, source_id=source
    )
    assert created_third
    assert third.id != job.id


async def test_a_requested_sync_applies_changes(
    owner_db: Database, app_db: Database, worker_db: Database, synced: Org
) -> None:
    folder = synced.path("drive")
    doc = sorted(folder.rglob("*.md"))[0]
    doc.unlink()
    source = await _source(owner_db, synced, "drive")
    await request_sync(
        app_db,
        organization_id=synced.id,
        membership_id=await _admin(owner_db, synced),
        source_id=source,
    )
    outcome = await Worker(worker_db, embedding=FAKE).run_once()
    assert outcome is not None
    assert outcome.outcome == "succeeded"
    async with owner_db.tenant_transaction(synced.id) as s:
        deleted: int = (
            await s.execute(
                text(
                    "SELECT deleted FROM source_sync_runs WHERE source_id = :s "
                    "ORDER BY started_at DESC LIMIT 1"
                ),
                {"s": source},
            )
        ).scalar_one()
    assert deleted == 1


async def test_requests_are_confined_to_the_organization(
    owner_db: Database, app_db: Database, synced: Org
) -> None:
    veridian = await seed_veridian_source(owner_db)
    admin = await _admin(owner_db, synced)
    with pytest.raises(SourceNotFound):
        await request_sync(
            app_db, organization_id=synced.id, membership_id=admin, source_id=veridian
        )
    with pytest.raises(SourceNotFound):
        await request_sync(
            app_db, organization_id=synced.id, membership_id=admin, source_id=uuid.uuid4()
        )


async def seed_veridian_source(owner_db: Database) -> uuid.UUID:
    async with owner_db.unscoped_transaction() as s:
        org_id: uuid.UUID = (
            await s.execute(text("SELECT id FROM organizations WHERE slug = 'veridian'"))
        ).scalar_one()
    return await create_source(owner_db, org_id, "veridian-drive")


async def test_a_paused_source_accepts_no_request(
    owner_db: Database, app_db: Database, synced: Org
) -> None:
    source = await _source(owner_db, synced, "drive")
    async with owner_db.tenant_transaction(synced.id) as s:
        await s.execute(text("UPDATE sources SET status = 'paused' WHERE id = :s"), {"s": source})
    with pytest.raises(SourceNotActive):
        await request_sync(
            app_db,
            organization_id=synced.id,
            membership_id=await _admin(owner_db, synced),
            source_id=source,
        )


async def test_a_missing_export_fails_the_job_without_retry(
    owner_db: Database, app_db: Database, worker_db: Database, synced: Org
) -> None:
    shutil.rmtree(synced.path("drive"))
    source = await _source(owner_db, synced, "drive")
    job, _ = await request_sync(
        app_db,
        organization_id=synced.id,
        membership_id=await _admin(owner_db, synced),
        source_id=source,
    )
    outcome = await Worker(worker_db, embedding=FAKE).run_once()
    assert outcome is not None
    assert outcome.outcome == "failed"
    row = await _job(owner_db, synced, job.id)
    assert row["status"] == "failed"
    assert row["error_code"] == "source_unavailable:missing_export"
    assert row["sync_run_id"] is not None  # the run records the failure too


@asynccontextmanager
async def _hold_source_lock(owner_db: Database, org: Org, key: str) -> AsyncIterator[None]:
    name = f"lattice-sync:{org.id}:{key}"
    async with owner_db.engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(hashtextextended(:n, 0))"), {"n": name})
        await conn.commit()
        try:
            yield
        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:n, 0))"), {"n": name}
            )
            await conn.commit()


async def test_a_busy_source_is_retried_later(
    owner_db: Database, app_db: Database, worker_db: Database, synced: Org
) -> None:
    source = await _source(owner_db, synced, "drive")
    job, _ = await request_sync(
        app_db,
        organization_id=synced.id,
        membership_id=await _admin(owner_db, synced),
        source_id=source,
    )
    worker = Worker(worker_db, embedding=FAKE)
    async with _hold_source_lock(owner_db, synced, "drive"):  # an operator's CLI sync
        outcome = await worker.run_once()
    assert outcome is not None
    assert outcome.outcome == "retry"
    row = await _job(owner_db, synced, job.id)
    assert row["status"] == "queued"
    assert row["error_code"] == "source_busy"
    assert await worker.run_once() is None  # backing off, not due yet
    async with owner_db.tenant_transaction(synced.id) as s:
        delay: float = (
            await s.execute(
                text("SELECT extract(epoch FROM run_after - now()) FROM jobs WHERE id = :j"),
                {"j": job.id},
            )
        ).scalar_one()
        assert RETRY_BACKOFF_SECONDS - 5 < delay <= RETRY_BACKOFF_SECONDS
        await s.execute(text("UPDATE jobs SET run_after = now() WHERE id = :j"), {"j": job.id})
    outcome = await worker.run_once()
    assert outcome is not None
    assert outcome.outcome == "succeeded"
    assert (await _job(owner_db, synced, job.id))["attempts"] == 2


async def test_an_expired_lease_is_retried_then_abandoned(
    owner_db: Database, app_db: Database, worker_db: Database, synced: Org
) -> None:
    """A worker that dies mid-job leaves it running; its lease expires and another poll
    retries it, until the attempts are spent."""
    source = await _source(owner_db, synced, "drive")
    job, _ = await request_sync(
        app_db,
        organization_id=synced.id,
        membership_id=await _admin(owner_db, synced),
        source_id=source,
    )
    worker = Worker(worker_db, embedding=FAKE)

    async def die_holding_it() -> None:
        claimed = await worker.claim()
        assert claimed is not None
        assert claimed.id == job.id
        async with owner_db.tenant_transaction(synced.id) as s:
            await s.execute(
                text(
                    "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :j"
                ),
                {"j": job.id},
            )

    for _ in range(3):  # max_attempts
        await die_holding_it()
    abandoned = _sample("lattice_jobs_total", kind="source_sync", outcome="abandoned")
    assert await worker.run_once() is None
    row = await _job(owner_db, synced, job.id)
    assert (row["status"], row["error_code"], row["attempts"]) == ("failed", "job_abandoned", 3)
    assert _sample("lattice_jobs_total", kind="source_sync", outcome="abandoned") == abandoned + 1


async def test_an_interrupted_run_is_recorded_aborted_by_the_next_sync(
    owner_db: Database, synced: Org
) -> None:
    source = await _source(owner_db, synced, "drive")
    async with owner_db.tenant_transaction(synced.id) as s:
        stale: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO source_sync_runs (organization_id, source_id, mode) "
                    "VALUES (:o, :s, 'full') RETURNING id"
                ),
                {"o": synced.id, "s": source},
            )
        ).scalar_one()
    await sync(owner_db, synced, "drive", FAKE)
    async with owner_db.tenant_transaction(synced.id) as s:
        status, code = (
            await s.execute(
                text("SELECT status, error_code FROM source_sync_runs WHERE id = :r"), {"r": stale}
            )
        ).one()
    assert (status, code) == ("aborted", "internal_error:interrupted")


async def test_an_unexpected_error_is_retried_then_fails(
    owner_db: Database,
    app_db: Database,
    worker_db: Database,
    synced: Org,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("connection reset by peer, for example")

    monkeypatch.setattr("lattice.jobs.worker.sync_source", explode)
    source = await _source(owner_db, synced, "drive")
    job, _ = await request_sync(
        app_db,
        organization_id=synced.id,
        membership_id=await _admin(owner_db, synced),
        source_id=source,
    )
    worker = Worker(worker_db, embedding=FAKE)
    outcomes = []
    for _ in range(3):
        outcome = await worker.run_once()
        assert outcome is not None
        outcomes.append(outcome.outcome)
        async with owner_db.tenant_transaction(synced.id) as s:
            await s.execute(text("UPDATE jobs SET run_after = now() WHERE id = :j"), {"j": job.id})
    assert outcomes == ["retry", "retry", "failed"]
    row = await _job(owner_db, synced, job.id)
    assert (row["status"], row["error_code"]) == ("failed", "internal_error")


async def test_finished_jobs_are_deleted_after_30_days_only(
    owner_db: Database, app_db: Database, worker_db: Database, synced: Org
) -> None:
    source = await _source(owner_db, synced, "drive")
    admin = await _admin(owner_db, synced)
    worker = Worker(worker_db, embedding=FAKE)
    ids = []
    for _ in range(2):
        job, _ = await request_sync(
            app_db, organization_id=synced.id, membership_id=admin, source_id=source
        )
        await worker.run_once()
        ids.append(job.id)
    old, recent = ids
    async with owner_db.tenant_transaction(synced.id) as s:
        await s.execute(
            text("UPDATE jobs SET finished_at = now() - interval '31 days' WHERE id = :j"),
            {"j": old},
        )
    assert await worker.cleanup() == 1
    assert await worker.cleanup() == 0  # idempotent
    async with owner_db.tenant_transaction(synced.id) as s:
        remaining: set[uuid.UUID] = set((await s.execute(text("SELECT id FROM jobs"))).scalars())
    assert remaining == {recent}
    # The worker's role cannot delete a younger job, even directly.
    async with worker_db.unscoped_transaction() as s:
        deleted = await s.execute(
            text("DELETE FROM jobs WHERE id = :j RETURNING id"), {"j": recent}
        )
        assert deleted.first() is None


async def test_two_workers_never_run_the_same_job(
    owner_db: Database, app_db: Database, worker_db: Database, synced: Org
) -> None:
    source = await _source(owner_db, synced, "drive")
    await request_sync(
        app_db,
        organization_id=synced.id,
        membership_id=await _admin(owner_db, synced),
        source_id=source,
    )
    first, second = Worker(worker_db, embedding=FAKE), Worker(worker_db, embedding=FAKE)
    claims = await asyncio.gather(first.claim(), second.claim())
    assert sum(c is not None for c in claims) == 1


async def test_the_worker_loop_stops_on_request(worker_db: Database) -> None:
    stop = asyncio.Event()
    worker = Worker(worker_db, embedding=FAKE)
    task = asyncio.create_task(worker.run_forever(stop, poll_seconds=0.05))
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=5)


async def test_the_worker_loop_survives_a_database_outage() -> None:
    down = Database(
        create_engine(
            "postgresql+asyncpg://lattice_worker:x@127.0.0.1:1/none",
            application_name="lattice-tests-worker-down",
            connect_timeout_seconds=0.5,
        )
    )
    errors = _sample("lattice_worker_polls_total", result="error")
    stop = asyncio.Event()
    task = asyncio.create_task(Worker(down, embedding=FAKE).run_forever(stop, poll_seconds=0.05))
    await asyncio.sleep(0.5)
    stop.set()
    await asyncio.wait_for(task, timeout=5)  # never exits on its own, never raises
    await down.dispose()
    assert _sample("lattice_worker_polls_total", result="error") > errors
