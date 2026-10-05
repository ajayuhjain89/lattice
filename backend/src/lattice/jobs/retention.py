"""Retention (Phase 11, P11.4; plan §12, deployment.md §13): the worker deletes what the
retention classes say, in bounded batches, as ``lattice_worker``.

Safeguards, in the database rather than here (migration 0015):

* **Floors.** The worker's DELETE is permitted only by age-gated policies, restrictive
  ones included, so a bug or a wrong setting cannot delete a row younger than its floor
  -- not even by a direct statement as ``lattice_worker`` with an organization bound.
  It reads only identifying and timestamp columns (never content).
* **Deterministic.** Every cutoff is computed from the database's ``now()`` inside the
  statement, never from the worker's clock.
* **Safe under retry and concurrency.** Each batch is its own transaction and deletes by
  key, re-checking eligibility: a repeated run finds nothing left; a concurrent one (two
  workers) waits on the rows the other is deleting and finds them gone -- nothing is
  deleted twice. Such a batch may come back short and end its task early; the next run
  deletes what is left.
* **Never touched:** ``audit_events`` and the identity/configuration tombstones -- the
  worker has no privilege or policy for them.

Observable per task: ``lattice_maintenance_rows_total{task}``,
``lattice_maintenance_last_success_timestamp_seconds{task}``,
``lattice_maintenance_failures_total{task}`` and one ``maintenance.retention`` log event
per run (counts only).
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice import metrics
from lattice.db.engine import Database
from lattice.log import log_event

_log = logging.getLogger("lattice.jobs")

BATCH: Final = 500
# Finished jobs, traces and sync runs keep their default retention as the floor: the
# settings may lengthen them, never shorten them (a shorter one is a migration and a
# product decision).
TRACE_RETENTION_FLOOR_DAYS: Final = 90


def _batched(table: str, key: str, eligible: str, order: str) -> str:
    """Delete up to :n eligible rows by key; the eligibility is re-checked on delete."""
    return (
        f"DELETE FROM {table} WHERE ({key}) IN ("  # noqa: S608 - fixed identifiers
        f"SELECT {key} FROM {table} WHERE {eligible} ORDER BY {order} LIMIT :n) "
        f"AND {eligible} RETURNING 1"
    )


# task -> statement (parameters :n, and :days where the rule is a setting)
_STATEMENTS: Final = {
    "jobs": _batched(
        "jobs",
        "id",
        "status IN ('succeeded', 'failed') AND finished_at < now() - interval '30 days'",
        "finished_at",
    ),
    "query_traces": _batched(
        "query_traces", "id", "created_at < now() - make_interval(days => :days)", "created_at"
    ),
    "sessions": _batched(
        "sessions",
        "id",
        "(expires_at < now() - interval '30 days' OR revoked_at < now() - interval '30 days')",
        "expires_at",
    ),
    "oidc_login_attempts": _batched(
        "oidc_login_attempts", "id", "expires_at < now() - interval '1 day'", "expires_at"
    ),
    "rate_limit_counters": _batched(
        "rate_limit_counters",
        "organization_id, bucket, subject",
        "window_start < now() - interval '2 days'",
        "window_start",
    ),
    "ask_slots": _batched(
        "ask_slots",
        "organization_id, membership_id, slot",
        "expires_at < now() - interval '1 hour'",
        "expires_at",
    ),
}
_PRE_AUTH_SQL = text("SELECT lattice_pre_auth_retention(:n)")
# Sync runs: the organizations that have candidates (found across organizations by age),
# then, with each organization bound, every run older than 180 days that a newer run of
# its source supersedes (its record failures go with it, by cascade).
_SYNC_RUN_ORGANIZATIONS_SQL = text(
    "SELECT DISTINCT organization_id FROM source_sync_runs "
    "WHERE started_at < now() - interval '180 days'"
)
_SYNC_RUNS_SQL = text(
    _batched(
        "source_sync_runs",
        "id",
        "started_at < now() - interval '180 days' AND EXISTS (SELECT 1 FROM source_sync_runs "
        "newer WHERE newer.organization_id = source_sync_runs.organization_id "
        "AND newer.source_id = source_sync_runs.source_id "
        "AND newer.started_at > source_sync_runs.started_at)",
        "started_at",
    )
)

TASKS: Final = (*_STATEMENTS, "pre_auth_rate_limits", "source_sync_runs")


@dataclass(frozen=True)
class RetentionPolicy:
    """The settings-driven part of the rules (the rest is fixed, deployment.md §13)."""

    trace_days: int = TRACE_RETENTION_FLOOR_DAYS
    batch: int = BATCH

    def __post_init__(self) -> None:
        if self.trace_days < TRACE_RETENTION_FLOOR_DAYS:
            raise ValueError(f"trace retention is at least {TRACE_RETENTION_FLOOR_DAYS} days")
        if self.batch < 1:
            raise ValueError("a retention batch deletes at least one row")


async def _until_done(
    db: Database, batch: int, one: Callable[[AsyncSession], Awaitable[int]]
) -> int:
    removed = 0
    while True:
        async with db.unscoped_transaction() as s:
            count = await one(s)
        removed += count
        if count < batch:
            return removed


async def _sync_runs(db: Database, batch: int) -> int:
    async with db.unscoped_transaction() as s:
        organizations: list[uuid.UUID] = list(
            (await s.execute(_SYNC_RUN_ORGANIZATIONS_SQL)).scalars()
        )
    removed = 0
    for organization in organizations:
        while True:
            async with db.tenant_transaction(organization) as s:
                count = len((await s.execute(_SYNC_RUNS_SQL, {"n": batch})).all())
            removed += count
            if count < batch:
                break
    return removed


async def run_task(db: Database, task: str, policy: RetentionPolicy) -> int:
    """One retention task to completion; the number of rows deleted."""
    if task == "pre_auth_rate_limits":

        async def pre_auth(s: AsyncSession) -> int:
            deleted: int = (await s.execute(_PRE_AUTH_SQL, {"n": policy.batch})).scalar_one()
            return deleted

        return await _until_done(db, policy.batch, pre_auth)
    if task == "source_sync_runs":
        return await _sync_runs(db, policy.batch)
    statement = text(_STATEMENTS[task])
    params = {"n": policy.batch, "days": policy.trace_days}

    async def batch(s: AsyncSession) -> int:
        return len((await s.execute(statement, params)).all())

    return await _until_done(db, policy.batch, batch)


async def run_retention(db: Database, policy: RetentionPolicy) -> dict[str, int | None]:
    """Every task once; a failing task is recorded and does not stop the others.
    Returns rows deleted per task (None: the task failed)."""
    results: dict[str, int | None] = {}
    for task in TASKS:
        try:
            removed = await run_task(db, task, policy)
        except Exception as exc:  # the next run retries; the failure is counted and logged
            results[task] = None
            metrics.MAINTENANCE_FAILURES.labels(task=task).inc()
            log_event(
                _log,
                logging.WARNING,
                "maintenance.retention_failed",
                task=task,
                error_type=type(exc).__name__,
            )
            continue
        results[task] = removed
        if removed:
            metrics.MAINTENANCE_ROWS.labels(task=task).inc(removed)
        metrics.MAINTENANCE_LAST_SUCCESS.labels(task=task).set(time.time())
    log_event(
        _log,
        logging.INFO,
        "maintenance.retention",
        deleted={task: count for task, count in results.items() if count},
        failed=sorted(task for task, count in results.items() if count is None),
    )
    return results
