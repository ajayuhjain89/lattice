"""The background job queue (migration 0011; architecture §1, ADR-013).

CHECK constraints, partial indexes and row-level security live in the migration; this
model describes shape. A job holds IDs, codes and timestamps only -- never content.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base

_ACTIVE = text("status IN ('queued', 'running')")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["sources.organization_id", "sources.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "requested_by"],
            ["memberships.organization_id", "memberships.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sync_run_id"],
            ["source_sync_runs.organization_id", "source_sync_runs.id"],
            ondelete="SET NULL (sync_run_id)",
        ),
        Index(
            "uq_jobs_one_active_per_source",
            "organization_id",
            "source_id",
            unique=True,
            postgresql_where=_ACTIVE,
        ),
        Index("ix_jobs_due", "run_after", postgresql_where=text("status = 'queued'")),
        Index("ix_jobs_lease", "lease_expires_at", postgresql_where=text("status = 'running'")),
        Index(
            "ix_jobs_finished",
            "finished_at",
            postgresql_where=text("status IN ('succeeded', 'failed')"),
        ),
        Index("ix_jobs_organization_id_source_id", "organization_id", "source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text)
    source_id: Mapped[uuid.UUID]
    mode: Mapped[str] = mapped_column(Text, server_default="full")
    requested_by: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(server_default=text("3"))
    run_after: Mapped[datetime] = mapped_column(server_default=text("now()"))
    lease_expires_at: Mapped[datetime | None]
    sync_run_id: Mapped[uuid.UUID | None]
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
