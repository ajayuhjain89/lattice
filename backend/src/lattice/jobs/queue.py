"""Requesting syncs and checks (the API side of the job queue, as ``lattice_app``).

An administrator asks for a sync of one of their organization's sources; the request is
a row in ``jobs``, which the worker (``lattice.jobs.worker``) picks up. Requesting is
idempotent: while a sync of that source is queued or running, asking again returns that
job (one partial unique index enforces it); while a *check* of it is, a sync is refused
(``JobActive``) -- one job per source at a time. The organization comes from the session
and row-level security confines every statement to it; a source of another organization
is indistinguishable from one that does not exist.

Organization-managed sources (P12.6) sync their **current import**: the job records it
when requested (the database checks it is the source's current import), so a sync reads
exactly the import that was current then, and the import retention spares it while the
job is queued or running. A source without one refuses (``ImportRequired``), as does one
whose import's check could not read it as a whole (``ImportInvalid``). Each requested
sync is audited (``source.sync_requested``); a check (``import_check``, the dry run) is
not (C11).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.access.audit import record_event
from lattice.db.engine import Database

JobMode = Literal["full", "acl_only"]


class SourceNotFound(Exception):
    """No such source in the organization."""


class SourceNotActive(Exception):
    """The source is paused or being deleted; it accepts no sync."""


class ImportRequired(Exception):
    """An organization-managed source has no current import to sync."""


class ImportInvalid(Exception):
    """The current import's check could not read it as a whole (replace it)."""


class JobActive(Exception):
    """Another kind of job of this source is queued or running."""

    def __init__(self, job: "JobView") -> None:
        super().__init__(job.kind)
        self.job = job


SYNC_REQUESTED = "source.sync_requested"
_SYNC_REASON = "Sync requested in the product"


@dataclass(frozen=True)
class JobView:
    id: uuid.UUID
    source_id: uuid.UUID
    kind: str
    mode: str
    status: str
    attempts: int
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    sync_run_id: uuid.UUID | None
    import_id: uuid.UUID | None


_COLUMNS = (
    "id, source_id, kind, mode, status, attempts, error_code, created_at, started_at, "
    "finished_at, sync_run_id, import_id"
)


async def request_sync(
    db: Database,
    *,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    source_id: uuid.UUID,
    mode: JobMode = "full",
) -> tuple[JobView, bool]:
    """Queue a sync; returns the job and whether it was created now (False: an active
    sync of this source already existed and is returned instead)."""
    return await _request(db, organization_id, membership_id, source_id, "source_sync", mode)


async def request_check(
    db: Database, *, organization_id: uuid.UUID, membership_id: uuid.UUID, source_id: uuid.UUID
) -> tuple[JobView, bool]:
    """Queue the check of the source's current import (the dry run; no writes)."""
    return await _request(db, organization_id, membership_id, source_id, "import_check", "full")


async def _request(
    db: Database,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    source_id: uuid.UUID,
    kind: str,
    mode: str,
) -> tuple[JobView, bool]:
    async with db.tenant_transaction(organization_id) as s:
        source = (
            await s.execute(
                text(
                    "SELECT s.status, s.managed_by, s.import_id, i.check_error_code "
                    "FROM sources s LEFT JOIN source_imports i "
                    "ON i.organization_id = s.organization_id AND i.id = s.import_id "
                    "WHERE s.organization_id = :o AND s.id = :s"
                ),
                {"o": organization_id, "s": source_id},
            )
        ).one_or_none()
        if source is None or source.status == "deleting":
            raise SourceNotFound
        if kind == "source_sync" and source.status != "active":
            raise SourceNotActive
        if source.managed_by == "organization":
            if source.import_id is None:
                raise ImportRequired
            if kind == "source_sync" and source.check_error_code is not None:
                raise ImportInvalid
        elif kind == "import_check":
            raise ImportRequired  # an operator's source reads its path, never an import
        created = (
            await s.execute(
                text(
                    "INSERT INTO jobs (organization_id, kind, source_id, mode, requested_by, "  # noqa: S608 - fixed columns
                    "import_id) VALUES (:o, :k, :s, :m, :r, :i) "
                    "ON CONFLICT (organization_id, source_id) "
                    "WHERE status IN ('queued', 'running') DO NOTHING "
                    f"RETURNING {_COLUMNS}"
                ),
                {
                    "o": organization_id,
                    "k": kind,
                    "s": source_id,
                    "m": mode,
                    "r": membership_id,
                    "i": source.import_id,
                },
            )
        ).one_or_none()
        if created is not None:
            job = JobView(*created)
            if kind == "source_sync":
                await record_event(
                    s,
                    organization_id,
                    actor=f"member:{membership_id}",
                    action=SYNC_REQUESTED,
                    target={"source_id": str(source_id)},
                    before=None,
                    after={
                        "job_id": str(job.id),
                        "mode": mode,
                        "import_id": None if job.import_id is None else str(job.import_id),
                    },
                    reason=_SYNC_REASON,
                )
            return job, True
        existing = JobView(
            *(
                await s.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM jobs WHERE organization_id = :o "  # noqa: S608 - fixed columns
                        "AND source_id = :s AND status IN ('queued', 'running')"
                    ),
                    {"o": organization_id, "s": source_id},
                )
            ).one()
        )
        if existing.kind != kind:
            raise JobActive(existing)
        return existing, False


async def latest_jobs(
    session: AsyncSession, organization_id: uuid.UUID
) -> dict[uuid.UUID, JobView]:
    """The most recent job of each source (inside the caller's tenant transaction)."""
    rows = await session.execute(
        text(
            f"SELECT DISTINCT ON (source_id) {_COLUMNS} FROM jobs "  # noqa: S608 - fixed columns
            "WHERE organization_id = :o ORDER BY source_id, created_at DESC, id"
        ),
        {"o": organization_id},
    )
    return {row.source_id: JobView(*row) for row in rows}
