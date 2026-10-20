"""Sources and their operational history (docs/data-model.md §3; migration 0005).

CHECK constraints and row-level security live in the migration; these models describe
shape. ``sync_record_failures`` has no free-text column by design: a failure is a
stage, a fixed error code and templated parameters (SEC-14).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
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
        # The current import (P12.6, migration 0022): a ready import of this source.
        ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["source_imports.organization_id", "source_imports.id"],
            use_alter=True,
        ),
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
    # operator: the sources file (`lattice sync --sources`) owns its configuration;
    # organization: created and configured in the product, fed by imports (P12.6).
    managed_by: Mapped[str] = mapped_column(Text, server_default="operator")
    # Raised by every change of an administrator-visible setting (optimistic edits).
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    import_id: Mapped[uuid.UUID | None]
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
        ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["source_imports.organization_id", "source_imports.id"],
            ondelete="SET NULL (import_id)",
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
    # The import the run read (organization-managed sources, P12.6).
    import_id: Mapped[uuid.UUID | None]


class SourceImport(Base):
    """An uploaded export (phase-12-plan C8; migration 0022). IDs, sizes, a digest,
    counts, fixed codes and times only: never a path, a file name or content. Its files
    live where the import store derives from the organization and import IDs."""

    __tablename__ = "source_imports"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["sources.organization_id", "sources.id"],
            ondelete="SET NULL (source_id)",
        ),
        Index(
            "uq_source_imports_one_upload_per_source",
            "organization_id",
            "source_id",
            unique=True,
            postgresql_where=text("status IN ('receiving', 'validating')"),
        ),
        Index(
            "ix_source_imports_organization_id_source_id_created_at",
            "organization_id",
            "source_id",
            "created_at",
        ),
        Index(
            "ix_source_imports_due",
            "status",
            postgresql_where=text(
                "status IN ('receiving', 'validating', 'rejected', 'superseded')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    source_id: Mapped[uuid.UUID | None]
    format: Mapped[str] = mapped_column(Text)
    # receiving -> validating -> ready | rejected; ready -> superseded; -> deleted.
    status: Mapped[str] = mapped_column(Text, server_default="receiving")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    entries: Mapped[int | None]
    ignored_entries: Mapped[int | None]
    expanded_bytes: Mapped[int | None] = mapped_column(BigInteger)
    rejected_code: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    received_at: Mapped[datetime | None]
    ready_at: Mapped[datetime | None]
    superseded_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]
    checked_at: Mapped[datetime | None]
    check_result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    check_error_code: Mapped[str | None] = mapped_column(Text)


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
