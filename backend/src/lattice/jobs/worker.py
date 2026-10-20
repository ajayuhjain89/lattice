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

* **Configuration is read-only here.** An operator-managed source's configuration and
  export path are the ones the operator's last ``lattice sync`` recorded; an
  organization-managed source (P12.6) syncs the import its job names -- resolved by ID
  through the import store, never a path from the database -- and only while that import
  is ``ready`` or ``superseded`` and belongs to the job's source. The worker's role can
  update only a source's three sync timestamps, so no job can redirect a sync elsewhere.
* **Checks** (``import_check``, P12.6): the same pipeline as a dry run (no writes, no
  provider call) of the job's import; its counts and codes are recorded on the import
  through ``lattice_record_import_check`` (validated key by key in the database).
* **Import retention** (P12.6): every minute, ``lattice_expire_imports`` marks deleted
  what may go (rejected, superseded and not read by an active job, abandoned uploads,
  imports of purged sources) and the worker removes their files; directories without a
  live import, older than an hour, are removed too (the API may have died mid-upload).
* **Tenant-safe.** The worker's role sees every organization's *jobs* (IDs and codes
  only) and nothing else across organizations: every sync statement runs in a
  transaction bound to the job's organization, under forced row-level security, and a
  sync can write only source-origin grants and groups (restrictive policies, 0011).
* **Idempotent and safe to retry.** A sync of unchanged data writes nothing; a retried
  job re-runs the same idempotent sync; an interrupted run is recorded ``aborted`` by the
  next sync of that source. A job finishes at most once (every transition is guarded by
  its current status).
* **Bounded.** One job at a time per worker, attempts capped (``max_attempts``), leases
  expire. Hourly maintenance runs retention (``lattice.jobs.retention``, P11.4): finished
  jobs after 30 days, traces, sync runs, ended sessions, login attempts, counters and Ask
  slots per their class, each refused by the database below its floor.
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
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice import metrics
from lattice.db.engine import Database
from lattice.imports.store import ImportStore, ImportUnavailable
from lattice.ingestion.sync import SyncError, SyncOptions, SyncResult, sync_source
from lattice.jobs.retention import RetentionPolicy, run_retention
from lattice.log import log_event
from lattice.providers.embedding import EmbeddingProvider
from lattice.sources.config import ChannelConfig, SourceConfig

_log = logging.getLogger("lattice.jobs")

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
    RETURNING j.id, j.organization_id, j.kind, j.source_id, j.mode, j.attempts, j.max_attempts,
      j.import_id
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
_WAITING_SQL = text(
    "SELECT count(*), coalesce(extract(epoch FROM now() - min(run_after)), 0) "
    "FROM jobs WHERE status = 'queued' AND run_after <= now()"
)
# Database-wide health the worker can see without any tenant data (P11.6): size, and the
# WAL archiver's state (PostgreSQL's own statistics; a platform may hide it).
_DATABASE_SIZE_SQL = text("SELECT pg_database_size(current_database())")
_ARCHIVER_SQL = text("SELECT last_archived_time, last_failed_time FROM pg_stat_archiver")
STATS_INTERVAL_SECONDS: Final = 60.0
_SOURCE_SQL = text(
    "SELECT key, connector_type, name, config, default_sensitivity, account_scoped, authority, "
    "status, managed_by FROM sources WHERE organization_id = :o AND id = :s"
)
_IMPORT_SQL = text(
    "SELECT status, source_id FROM source_imports WHERE organization_id = :o AND id = :i"
)
_RECORD_CHECK_SQL = text("SELECT lattice_record_import_check(:i, CAST(:r AS jsonb), :e)")
_EXPIRE_IMPORTS_SQL = text("SELECT organization_id, import_id FROM lattice_expire_imports(:n)")
_LIVE_IMPORTS_SQL = text(
    "SELECT id FROM source_imports WHERE organization_id = :o AND status <> 'deleted'"
)
IMPORT_RETENTION_INTERVAL_SECONDS: Final = 60.0
ORPHAN_AGE_SECONDS: Final = 3600.0
_CHECK_REFERENCES: Final = 200
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
    import_id: uuid.UUID | None = None


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


def source_config(row: Any, tree: Path | None = None) -> tuple[SourceConfig, Path]:
    """Rebuild a source's configuration from its row. An operator-managed source reads the
    absolute path its last CLI sync recorded (``ingestion.store.upsert_source``; the API
    cannot change it); an organization-managed one reads ``tree``, its job's import as
    the import store resolved it by ID."""
    stored: dict[str, Any] = dict(row.config or {})
    if tree is not None:
        path: object = str(tree)
    else:
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
        # Channel settings only: account mappings are read from account_external_refs by
        # the sync (P12.3).
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
        retention: RetentionPolicy | None = None,
        store: ImportStore | None = None,
    ) -> None:
        self.db = db
        self.embedding = embedding
        self.lease_seconds = lease_seconds
        self.heartbeat = heartbeat or Heartbeat()
        self.retention = retention or RetentionPolicy()
        self.store = store

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
        """Due queued jobs; also sets the oldest one's age."""
        async with self.db.unscoped_transaction() as s:
            waiting, oldest = (await s.execute(_WAITING_SQL)).one()
        metrics.JOBS_OLDEST_WAITING.set(float(oldest))
        return int(waiting)

    async def database_stats(self) -> None:
        """Database size and WAL-archiver health gauges (alerts, runbook §10)."""
        async with self.db.unscoped_transaction() as s:
            metrics.DATABASE_SIZE.set((await s.execute(_DATABASE_SIZE_SQL)).scalar_one())
            archived, failed = (await s.execute(_ARCHIVER_SQL)).one()
        if archived is not None:
            metrics.WAL_ARCHIVE_LAST_SUCCESS.set(archived.timestamp())
        if archived is not None or failed is not None:
            failing = failed is not None and (archived is None or failed > archived)
            metrics.WAL_ARCHIVE_FAILING.set(1 if failing else 0)

    async def cleanup(self) -> dict[str, int | None]:
        """Retention (P11.4): every class's expired rows, in bounded batches, as far as
        the database's floors allow (``lattice.jobs.retention``)."""
        return await run_retention(self.db, self.retention)

    async def import_retention(self) -> int:
        """Remove the files of every import the database marked deleted, then of import
        directories with no live import that are older than an hour. Returns the number
        of import directories removed."""
        store = self.store
        if store is None:
            return 0
        removed = 0
        while True:
            async with self.db.unscoped_transaction() as s:
                expired = (await s.execute(_EXPIRE_IMPORTS_SQL, {"n": 100})).all()
            for organization_id, import_id in expired:
                await asyncio.to_thread(store.delete, organization_id, import_id)
                removed += 1
            if len(expired) < 100:
                break
        cutoff = time.time() - ORPHAN_AGE_SECONDS
        for organization_id in await asyncio.to_thread(store.organizations):
            async with self.db.tenant_transaction(organization_id) as s:
                live: set[uuid.UUID] = set(
                    (await s.execute(_LIVE_IMPORTS_SQL, {"o": organization_id})).scalars()
                )
            for import_id, modified in await asyncio.to_thread(store.stored, organization_id):
                if import_id not in live and modified < cutoff:
                    await asyncio.to_thread(store.delete, organization_id, import_id)
                    removed += 1
        if removed:
            metrics.MAINTENANCE_ROWS.labels(task="imports").inc(removed)
        metrics.MAINTENANCE_LAST_SUCCESS.labels(task="imports").set(time.time())
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
        check = job.kind == "import_check"
        try:
            result = await self._sync(job)
        except _Unrunnable as exc:
            return await self._finish(job, "failed", exc.code, None)
        except SyncError as exc:
            if exc.code == "source_busy":
                return await self._retry(job, exc.code)
            if check:
                # The check ran: the import cannot be synced as a whole (recorded on it).
                return await self._checked(job, None, exc.code)
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
        if check:
            return await self._checked(job, result, None)
        if result.status == "failed":
            return await self._finish(job, "failed", result.error_code, result)
        return await self._finish(job, "succeeded", None, result)

    async def _checked(
        self, job: ClaimedJob, result: SyncResult | None, error: str | None
    ) -> JobOutcome:
        try:
            await self._record_check(job, result, error)
        except DBAPIError:
            # The import went away meanwhile (replaced, or its source disconnected).
            return await self._finish(job, "failed", "import_unavailable", None)
        return await self._finish(job, "succeeded", None, None)

    async def _sync(self, job: ClaimedJob) -> SyncResult:
        if job.kind not in ("source_sync", "import_check"):
            raise _Unrunnable("job_kind_unsupported")
        async with self.db.tenant_transaction(job.organization_id) as s:
            organization = (
                await s.execute(_ORGANIZATION_STATUS_SQL, {"o": job.organization_id})
            ).scalar_one_or_none()
            row = (
                await s.execute(_SOURCE_SQL, {"o": job.organization_id, "s": job.source_id})
            ).one_or_none()
            imported = (
                None
                if job.import_id is None
                else (
                    await s.execute(_IMPORT_SQL, {"o": job.organization_id, "i": job.import_id})
                ).one_or_none()
            )
        if organization != "active":
            raise _Unrunnable("organization_suspended")
        if row is None:
            raise _Unrunnable("source_config:source_missing")
        check = job.kind == "import_check"
        if row.status != "active" and not (check and row.status == "paused"):
            raise _Unrunnable(f"source_config:source_{row.status}")
        tree = None
        if row.managed_by == "organization":
            tree = self._tree(job, imported)
        elif job.import_id is not None or check:
            raise _Unrunnable("import_unavailable")
        config, path = source_config(row, tree)
        if check:
            # The dry run: reads everything, writes nothing, calls no provider.
            options = SyncOptions(dry_run=True, record_config=False)
            return await sync_source(self.db, job.organization_id, config, path, None, options)
        # The recorded configuration is used as it is: the worker never writes it.
        options = SyncOptions(
            mode="acl_only" if job.mode == "acl_only" else "full",
            record_config=False,
            import_id=job.import_id,
        )
        return await sync_source(
            self.db, job.organization_id, config, path, self.embedding, options
        )

    def _tree(self, job: ClaimedJob, imported: Any) -> Path:
        """The job's import, by ID, if it may still be read: an import of this very source
        that is ready (or superseded while this job waited) -- never deleted."""
        if job.import_id is None or imported is None:
            raise _Unrunnable("import_unavailable")
        if imported.source_id != job.source_id or imported.status not in ("ready", "superseded"):
            raise _Unrunnable("import_unavailable")
        if self.store is None:
            raise _Unrunnable("storage_unavailable")
        try:
            return self.store.tree(job.organization_id, job.import_id)
        except ImportUnavailable:
            raise _Unrunnable("import_unavailable") from None

    async def _record_check(
        self, job: ClaimedJob, result: SyncResult | None, error: str | None
    ) -> None:
        """The check's counts and codes on the import (never a name or content)."""
        payload = None
        if result is not None:
            refs = sorted(result.unmapped_refs.items(), key=lambda item: (-item[1], item[0]))
            payload = json.dumps(
                {
                    "records_seen": result.records_seen,
                    "would_create": result.created,
                    "would_update": result.updated,
                    "would_update_access": result.access_updated,
                    "unchanged": result.unchanged,
                    "invalid_records": result.failed,
                    "would_quarantine": result.quarantined,
                    "would_delete": result.would_delete,
                    "deletion_guard": result.error_code if result.deletion_guard_tripped else None,
                    "failure_codes": dict(sorted(result.failure_codes.items())),
                    "unresolved_references": [
                        {"ref": ref, "records": count} for ref, count in refs[:_CHECK_REFERENCES]
                    ],
                    "unresolved_total": len(refs),
                },
                sort_keys=True,
            )
        async with self.db.tenant_transaction(job.organization_id) as s:
            await s.execute(
                _RECORD_CHECK_SQL,
                {"i": job.import_id, "r": payload, "e": None if payload else _job_code(error)},
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

    async def _import_retention_safely(self) -> None:
        try:
            await self.import_retention()
        except Exception as exc:  # the next pass retries; never stop the loop for it
            metrics.MAINTENANCE_FAILURES.labels(task="imports").inc()
            log_event(
                _log,
                logging.WARNING,
                "maintenance.import_retention_failed",
                error_type=type(exc).__name__,
            )

    # --- the loop ------------------------------------------------------------------------

    async def run_forever(
        self, stop: asyncio.Event, *, poll_seconds: float, maintenance_seconds: float = 3600.0
    ) -> None:
        """Poll until ``stop`` is set; a job in progress is finished first."""
        next_maintenance = 0.0
        next_stats = 0.0
        while not stop.is_set():
            self.heartbeat.beat()
            try:
                if time.monotonic() >= next_maintenance:
                    await self.cleanup()
                    next_maintenance = time.monotonic() + maintenance_seconds
                if time.monotonic() >= next_stats:
                    await self.database_stats()
                    await self._import_retention_safely()
                    next_stats = time.monotonic() + STATS_INTERVAL_SECONDS
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
