"""Sources and their operational history (docs/data-model.md §3; migration 0005).

CHECK constraints and row-level security live in the migration; these models describe
shape. ``sync_record_failures`` has no free-text column by design: a failure is a
stage, a fixed error code and templated parameters (SEC-14).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base

_UUID_DEFAULT = text("gen_random_uuid()")


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(CITEXT)
    connector_type: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    # Validated by the connector's and ingestion's config models; never secrets.
    config: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    default_sensitivity: Mapped[str] = mapped_column(Text)
    account_scoped: Mapped[bool] = mapped_column(server_default=text("false"))
    authority: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    opaque_ref_key: Mapped[bytes] = mapped_column(
        BYTEA, server_default=text("uuid_send(gen_random_uuid()) || uuid_send(gen_random_uuid())")
    )
    last_synced_at: Mapped[datetime | None]
    last_succeeded_at: Mapped[datetime | None]
    acl_synced_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SourceSyncRun(Base):
    __tablename__ = "source_sync_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["sources.organization_id", "sources.id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_source_sync_runs_organization_id_source_id_started_at",
            "organization_id",
            "source_id",
            "started_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    source_id: Mapped[uuid.UUID]
    mode: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="running")
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    records_seen: Mapped[int] = mapped_column(server_default=text("0"))
    created: Mapped[int] = mapped_column(server_default=text("0"))
    updated: Mapped[int] = mapped_column(server_default=text("0"))
    access_updated: Mapped[int] = mapped_column(server_default=text("0"))
    unchanged: Mapped[int] = mapped_column(server_default=text("0"))
    deleted: Mapped[int] = mapped_column(server_default=text("0"))
    failed: Mapped[int] = mapped_column(server_default=text("0"))
    chunks_embedded: Mapped[int] = mapped_column(server_default=text("0"))
    embeddings_reused: Mapped[int] = mapped_column(server_default=text("0"))
    chunks_policy_excluded: Mapped[int] = mapped_column(server_default=text("0"))
    embedding_failures: Mapped[int] = mapped_column(server_default=text("0"))
    deletion_guard_tripped: Mapped[bool] = mapped_column(server_default=text("false"))
    error_code: Mapped[str | None] = mapped_column(Text)


class SyncRecordFailure(Base):
    __tablename__ = "sync_record_failures"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "sync_run_id"],
            ["source_sync_runs.organization_id", "source_sync_runs.id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_sync_record_failures_organization_id_sync_run_id",
            "organization_id",
            "sync_run_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    sync_run_id: Mapped[uuid.UUID]
    # Raw identifiers: infrastructure operators only (security-model §10).
    external_id: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    opaque_ref: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str] = mapped_column(Text)
    message_params: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
