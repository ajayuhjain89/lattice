"""Requesting syncs (the API side of the job queue, as the runtime role ``lattice_app``).

An administrator asks for a sync of one of their organization's sources; the request is
a row in ``jobs``, which the worker (``lattice.jobs.worker``) picks up. Requesting is
idempotent: while a sync of that source is queued or running, asking again returns that
job (one partial unique index enforces it). The organization comes from the session and
row-level security confines every statement to it; a source of another organization is
indistinguishable from one that does not exist.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.db.engine import Database

JobMode = Literal["full", "acl_only"]


class SourceNotFound(Exception):
    """No such source in the organization."""


class SourceNotActive(Exception):
    """The source is paused or being deleted; it accepts no sync."""


@dataclass(frozen=True)
class JobView:
    id: uuid.UUID
    source_id: uuid.UUID
    mode: str
    status: str
    attempts: int
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    sync_run_id: uuid.UUID | None


_COLUMNS = (
    "id, source_id, mode, status, attempts, error_code, created_at, started_at, "
    "finished_at, sync_run_id"
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
    job for this source already existed and is returned instead)."""
    async with db.tenant_transaction(organization_id) as s:
        status = (
            await s.execute(
                text("SELECT status FROM sources WHERE organization_id = :o AND id = :s"),
                {"o": organization_id, "s": source_id},
            )
        ).scalar_one_or_none()
        if status is None:
            raise SourceNotFound
        if status != "active":
            raise SourceNotActive
        created = (
            await s.execute(
                text(
                    "INSERT INTO jobs (organization_id, kind, source_id, mode, requested_by) "  # noqa: S608 - fixed columns
                    "VALUES (:o, 'source_sync', :s, :m, :r) "
                    "ON CONFLICT (organization_id, source_id) "
                    "WHERE status IN ('queued', 'running') DO NOTHING "
                    f"RETURNING {_COLUMNS}"
                ),
                {"o": organization_id, "s": source_id, "m": mode, "r": membership_id},
            )
        ).one_or_none()
        if created is not None:
            return JobView(*created), True
        existing = (
            await s.execute(
                text(
                    f"SELECT {_COLUMNS} FROM jobs WHERE organization_id = :o "  # noqa: S608 - fixed columns
                    "AND source_id = :s AND status IN ('queued', 'running')"
                ),
                {"o": organization_id, "s": source_id},
            )
        ).one()
        return JobView(*existing), False


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
