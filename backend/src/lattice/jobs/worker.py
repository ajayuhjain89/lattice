"""The background worker: runs the syncs administrators request (architecture §1;
ADR-013). A process, not a platform -- a Postgres table with ``FOR UPDATE SKIP
LOCKED``, no broker.

Loop::

    reap     running jobs whose lease expired and have no attempts left → failed
             (``job_abandoned``)
    claim    the next due job (queued, or running with an expired lease and attempts
             left) → running, attempts + 1, lease; SKIP LOCKED, so several workers
             never take the same job
    run      bind the job's organization and sync its source exactly as
             ``lattice sync`` would (same pipeline, same advisory lock, same deletion
             guard -- never ``--allow-mass-deletion``), renewing the lease meanwhile
    finish   succeeded (the run's own status may be ``partial``) | failed (a
             source-level code) | queued again with a backoff (``source_busy``, an
             unexpected error, while attempts remain)

Properties:

* **Configuration is read-only here.** The source's configuration and export path are
  the ones the operator's last ``lattice sync`` recorded; the worker's role can update
  only a source's three sync timestamps, so no job can redirect a sync elsewhere.
* **Tenant-safe.** The worker's role sees every organization's *jobs* (IDs and codes
  only) and nothing else across organizations: every sync statement runs in a
  transaction bound to the job's organization, under forced row-level security, and a
  sync can write only source-origin grants and groups (restrictive policies, 0011).
* **Idempotent and safe to retry.** A sync of unchanged data writes nothing; a retried
  job re-runs the same idempotent sync; an interrupted run is recorded ``aborted`` by the
  next sync of that source. A job finishes at most once (every transition is guarded by
  its current status).
* **Bounded.** One job at a time per worker, attempts capped (``max_attempts``), leases
  expire, finished jobs are deleted after 30 days in batches (the row-level policy
  permits nothing younger).
* **Observable.** ``jobs.*`` log events with IDs and codes, ``lattice_jobs_*`` metrics,
  and each sync's own run row.
* **Live (Phase 11).** A heartbeat -- the ``lattice_worker_heartbeat_timestamp_seconds``
  gauge and, when configured, the modification time of ``LATTICE_WORKER_HEARTBEAT_FILE``
  -- is renewed at every loop iteration (also while the database is unreachable: the
  loop still runs and restarting would not help) and at every lease renewal during a
  job. If the event loop stops making progress (the process hung, or a sync blocks the
  loop), the heartbeat ages; the container health check compares its age with a
  threshold (docs/deployment.md §10). It carries a timestamp only.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sqlalchemy import text

from lattice import metrics
from lattice.db.engine import Database
from lattice.ingestion.sync import SyncError, SyncOptions, SyncResult, sync_source
from lattice.log import log_event
from lattice.providers.embedding import EmbeddingProvider
from lattice.sources.config import ChannelConfig, SourceConfig

_log = logging.getLogger("lattice.jobs")

RETENTION_BATCH: Final = 500
# Backoff before a retry: attempt n waits n times this (source busy, unexpected error).
RETRY_BACKOFF_SECONDS: Final = 60

_REAP_SQL = text(
    "UPDATE jobs SET status = 'failed', error_code = 'job_abandoned', finished_at = now(), "
    "lease_expires_at = NULL "
    "WHERE status = 'running' AND lease_expires_at < now() AND attempts >= max_attempts "
    "RETURNING id, organization_id"
)
_CLAIM_SQL = text(
    """
    WITH next AS (
      SELECT id FROM jobs
      WHERE (status = 'queued' AND run_after <= now())
         OR (status = 'running' AND lease_expires_at < now() AND attempts < max_attempts)
      ORDER BY run_after, created_at, id
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    UPDATE jobs j SET status = 'running', attempts = j.attempts + 1, started_at = now(),
      lease_expires_at = now() + make_interval(secs => :lease), error_code = NULL
    FROM next WHERE j.id = next.id
    RETURNING j.id, j.organization_id, j.kind, j.source_id, j.mode, j.attempts, j.max_attempts
    """
)
_RENEW_SQL = text(
    "UPDATE jobs SET lease_expires_at = now() + make_interval(secs => :lease) "
    "WHERE id = :id AND status = 'running'"
)
_FINISH_SQL = text(
    "UPDATE jobs SET status = :status, error_code = :code, sync_run_id = :run, "
    "finished_at = now(), lease_expires_at = NULL "
    "WHERE id = :id AND status = 'running' AND attempts = :attempt RETURNING id"
)
_RETRY_SQL = text(
    "UPDATE jobs SET status = 'queued', error_code = :code, lease_expires_at = NULL, "
    "run_after = now() + make_interval(secs => :delay) "
    "WHERE id = :id AND status = 'running' AND attempts = :attempt"
)
_WAITING_SQL = text("SELECT count(*) FROM jobs WHERE status = 'queued' AND run_after <= now()")
_RETENTION_SQL = text(
    "DELETE FROM jobs WHERE id IN (SELECT id FROM jobs WHERE status IN ('succeeded', 'failed') "
    "AND finished_at < now() - interval '30 days' ORDER BY finished_at LIMIT :n) RETURNING id"
)
_SOURCE_SQL = text(
    "SELECT key, connector_type, name, config, default_sensitivity, account_scoped, authority, "
    "status FROM sources WHERE organization_id = :o AND id = :s"
)
# An organization suspended by an operator (``lattice org suspend``, D-26) runs no sync.
_ORGANIZATION_STATUS_SQL = text("SELECT status FROM organizations WHERE id = :o")


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    organization_id: uuid.UUID
    kind: str
    source_id: uuid.UUID
    mode: str
    attempt: int
    max_attempts: int


@dataclass(frozen=True)
class JobOutcome:
    job_id: uuid.UUID
    outcome: str  # succeeded | failed | retry
    error_code: str | None
    sync_status: str | None


class _Unrunnable(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def source_config(row: Any) -> tuple[SourceConfig, Path]:
    """Rebuild a source's configuration from its row, as the last CLI sync recorded it
    (``ingestion.store.upsert_source``). The export path is the absolute path recorded
    then; the API cannot change it (the runtime role only reads ``sources``)."""
    stored: dict[str, Any] = dict(row.config or {})
    path = stored.get("path")
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise _Unrunnable("source_config:path_unknown")
    config = SourceConfig(
        key=str(row.key),
        connector_type=row.connector_type,
        name=row.name,
        path=path,
        default_sensitivity=row.default_sensitivity,
        default_departments=list(stored.get("default_departments") or []),
        account_scoped=bool(row.account_scoped),
        authority=row.authority,
        account_refs={int(k): v for k, v in (stored.get("account_refs") or {}).items()},
        channels={k: ChannelConfig(**v) for k, v in (stored.get("channels") or {}).items()},
    )
    return config, Path(path)


class Heartbeat:
    """Proof of progress: the gauge always, the file's mtime when a path is given."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path

    def beat(self) -> None:
        metrics.WORKER_HEARTBEAT.set(time.time())
        if self.path is not None:
            try:
                self.path.touch()
            except OSError as exc:  # a full or read-only /tmp must not stop the worker
                log_event(
                    _log, logging.WARNING, "worker.heartbeat_failed", error_type=type(exc).__name__
                )


class Worker:
    def __init__(
        self,
        db: Database,
        *,
        embedding: EmbeddingProvider | None,
        lease_seconds: int = 300,
        heartbeat: Heartbeat | None = None,
    ) -> None:
        self.db = db
        self.embedding = embedding
        self.lease_seconds = lease_seconds
        self.heartbeat = heartbeat or Heartbeat()

    # --- queue ------------------------------------------------------------------------

    async def reap(self) -> int:
        async with self.db.unscoped_transaction() as s:
            reaped = (await s.execute(_REAP_SQL)).all()
        for job_id, org in reaped:
            metrics.JOBS.labels(kind="source_sync", outcome="abandoned").inc()
            log_event(
                _log,
                logging.WARNING,
                "jobs.abandoned",
                job_id=str(job_id),
                organization_id=str(org),
            )
        return len(reaped)

    async def claim(self) -> ClaimedJob | None:
        async with self.db.unscoped_transaction() as s:
            row = (await s.execute(_CLAIM_SQL, {"lease": self.lease_seconds})).one_or_none()
        if row is None:
            return None
        return ClaimedJob(*row)

    async def waiting(self) -> int:
        async with self.db.unscoped_transaction() as s:
            waiting: int = (await s.execute(_WAITING_SQL)).scalar_one()
        return waiting

    async def cleanup(self) -> int:
        """Delete finished jobs older than 30 days, in bounded batches."""
        removed = 0
        while True:
            async with self.db.unscoped_transaction() as s:
                batch = len((await s.execute(_RETENTION_SQL, {"n": RETENTION_BATCH})).all())
            removed += batch
            if batch < RETENTION_BATCH:
                break
        if removed:
            metrics.MAINTENANCE_ROWS.labels(task="jobs_retention").inc(removed)
            log_event(_log, logging.INFO, "jobs.retention", deleted=removed)
        return removed

    # --- one job ------------------------------------------------------------------------

    async def run_once(self) -> JobOutcome | None:
        """Reap, claim and run at most one job. None when nothing was due."""
        await self.reap()
        job = await self.claim()
        if job is None:
            metrics.WORKER_POLLS.labels(result="empty").inc()
            return None
        metrics.WORKER_POLLS.labels(result="claimed").inc()
        return await self.execute(job)

    async def execute(self, job: ClaimedJob) -> JobOutcome:
        started = time.perf_counter()
        log_event(
            _log,
            logging.INFO,
            "jobs.started",
            job_id=str(job.id),
            organization_id=str(job.organization_id),
            source_id=str(job.source_id),
            kind=job.kind,
            mode=job.mode,
            attempt=job.attempt,
        )
        renew = asyncio.create_task(self._renew(job))
        try:
            outcome = await self._execute(job)
        finally:
            renew.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renew
        elapsed = time.perf_counter() - started
        metrics.JOBS.labels(
            kind=metrics.bounded(job.kind, metrics.JOB_KINDS),
            outcome=metrics.bounded(outcome.outcome, metrics.JOB_OUTCOMES),
        ).inc()
        metrics.JOB_DURATION.labels(kind=metrics.bounded(job.kind, metrics.JOB_KINDS)).observe(
            elapsed
        )
        log_event(
            _log,
            logging.INFO if outcome.outcome == "succeeded" else logging.WARNING,
            "jobs.finished",
            job_id=str(job.id),
            organization_id=str(job.organization_id),
            outcome=outcome.outcome,
            error_code=outcome.error_code,
            sync_status=outcome.sync_status,
            attempt=job.attempt,
            duration_ms=round(elapsed * 1000, 1),
        )
        return outcome

    async def _execute(self, job: ClaimedJob) -> JobOutcome:
        try:
            result = await self._sync(job)
        except _Unrunnable as exc:
            return await self._finish(job, "failed", exc.code, None)
        except SyncError as exc:
            if exc.code == "source_busy":
                return await self._retry(job, exc.code)
            return await self._finish(job, "failed", exc.code, None)
        except Exception as exc:
            # The run (if one opened) is already recorded aborted by the sync itself.
            log_event(
                _log,
                logging.ERROR,
                "jobs.error",
                job_id=str(job.id),
                error_type=type(exc).__name__,
            )
            return await self._retry(job, "internal_error")
        if result.status == "failed":
            return await self._finish(job, "failed", result.error_code, result)
        return await self._finish(job, "succeeded", None, result)

    async def _sync(self, job: ClaimedJob) -> SyncResult:
        if job.kind != "source_sync":
            raise _Unrunnable("job_kind_unsupported")
        async with self.db.tenant_transaction(job.organization_id) as s:
            organization = (
                await s.execute(_ORGANIZATION_STATUS_SQL, {"o": job.organization_id})
            ).scalar_one_or_none()
            row = (
                await s.execute(_SOURCE_SQL, {"o": job.organization_id, "s": job.source_id})
            ).one_or_none()
        if organization != "active":
            raise _Unrunnable("organization_suspended")
        if row is None:
            raise _Unrunnable("source_config:source_missing")
        if row.status != "active":
            raise _Unrunnable(f"source_config:source_{row.status}")
        config, path = source_config(row)
        # The recorded configuration is used as it is: the worker never writes it.
        options = SyncOptions(
            mode="acl_only" if job.mode == "acl_only" else "full", record_config=False
        )
        return await sync_source(
            self.db, job.organization_id, config, path, self.embedding, options
        )

    async def _finish(
        self, job: ClaimedJob, status: str, code: str | None, result: SyncResult | None
    ) -> JobOutcome:
        async with self.db.unscoped_transaction() as s:
            updated = (
                await s.execute(
                    _FINISH_SQL,
                    {
                        "id": job.id,
                        "status": status,
                        "code": _job_code(code),
                        "run": result.run_id if result is not None else None,
                        "attempt": job.attempt,
                    },
                )
            ).one_or_none()
        if updated is None:
            log_event(_log, logging.WARNING, "jobs.lease_lost", job_id=str(job.id))
        return JobOutcome(job.id, status, code, result.status if result else None)

    async def _retry(self, job: ClaimedJob, code: str) -> JobOutcome:
        if job.attempt >= job.max_attempts:
            return await self._finish(job, "failed", code, None)
        async with self.db.unscoped_transaction() as s:
            await s.execute(
                _RETRY_SQL,
                {
                    "id": job.id,
                    "code": _job_code(code),
                    "delay": RETRY_BACKOFF_SECONDS * job.attempt,
                    "attempt": job.attempt,
                },
            )
        return JobOutcome(job.id, "retry", code, None)

    async def _renew(self, job: ClaimedJob) -> None:
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            try:
                async with self.db.unscoped_transaction() as s:
                    await s.execute(_RENEW_SQL, {"id": job.id, "lease": self.lease_seconds})
            except Exception as exc:  # the next renewal may succeed; the lease is long
                log_event(
                    _log,
                    logging.WARNING,
                    "jobs.lease_renewal_failed",
                    job_id=str(job.id),
                    error_type=type(exc).__name__,
                )
            self.heartbeat.beat()

    # --- the loop ------------------------------------------------------------------------

    async def run_forever(
        self, stop: asyncio.Event, *, poll_seconds: float, maintenance_seconds: float = 3600.0
    ) -> None:
        """Poll until ``stop`` is set; a job in progress is finished first."""
        next_maintenance = 0.0
        while not stop.is_set():
            self.heartbeat.beat()
            try:
                if time.monotonic() >= next_maintenance:
                    await self.cleanup()
                    next_maintenance = time.monotonic() + maintenance_seconds
                metrics.JOBS_WAITING.set(await self.waiting())
                metrics.CLOCK_SKEW.set(await self.db.clock_skew())
                if await self.run_once() is not None:
                    continue  # there may be more due; poll again at once
            except Exception as exc:
                # The database may be down: back off and try again, never exit.
                metrics.WORKER_POLLS.labels(result="error").inc()
                metrics.DEPENDENCY_FAILURES.labels(dependency="database", check="worker").inc()
                log_event(_log, logging.WARNING, "jobs.poll_failed", error_type=type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)


def _job_code(code: str | None) -> str | None:
    """Codes are fixed identifiers; anything else is recorded as a generic one."""
    if code is None:
        return None
    ok = code[:1].isalpha() and all(c.isalnum() or c in "_:.-" for c in code)
    return code.lower()[:100] if ok else "internal_error"
