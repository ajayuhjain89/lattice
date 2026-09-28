"""Authorization tables (docs/data-model.md §2, §4, §5; migration 0003).

These describe shape for queries and for `alembic check`; CHECK constraints, the
department-reference trigger and row-level security live in the migration.

Only ``lattice.authz`` builds access conditions over these tables. Other modules use
its public contract (``readable_documents_predicate`` and friends); the boundary is
enforced by ``tests/test_authz_boundary.py``.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, CITEXT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base

_UUID_DEFAULT = text("gen_random_uuid()")


class AccessGroup(Base):
    """A set of members used as a grant principal: a private channel's members
    (``origin='source'``) or an admin-defined group such as "Exec staff"."""

    __tablename__ = "access_groups"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "key"),
        UniqueConstraint("organization_id", "source_id", "external_ref"),
        ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["sources.organization_id", "sources.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    # Stable, human-readable identity within the organization (decision D-9), used by
    # manifests and source synchronization to find the group again. Renaming changes
    # ``name`` only. Never an authorization input: grants name the group by ``id``.
    key: Mapped[str] = mapped_column(CITEXT)
    origin: Mapped[str] = mapped_column(Text)
    # The source a synced group mirrors (Phase 4); NULL for admin-defined groups.
    source_id: Mapped[uuid.UUID | None]
    # External identifier for source-synced groups (e.g. a Slack channel ID).
    external_ref: Mapped[str | None] = mapped_column(Text)
    # May itself be sensitive (a private channel's name): admin-only display rules apply.
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AccessGroupMember(Base):
    __tablename__ = "access_group_members"
    __table_args__ = (
        PrimaryKeyConstraint("group_id", "membership_id"),
        ForeignKeyConstraint(
            ["organization_id", "group_id"],
            ["access_groups.organization_id", "access_groups.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["memberships.organization_id", "memberships.id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_access_group_members_organization_id_membership_id",
            "organization_id",
            "membership_id",
        ),
    )

    organization_id: Mapped[uuid.UUID]
    group_id: Mapped[uuid.UUID]
    membership_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Document(Base):
    """One source record (a document file, a ticket, a Slack thread or channel-day).

    The access columns (Phase 2) are read by the policy; the source identity,
    provenance, content and version columns (Phase 4) are written by ingestion
    (docs/data-model.md §4). ``(organization_id, source_id, external_id)`` is the
    idempotency key.
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "source_id", "external_id"),
        # No ON DELETE action: deleting an account must never widen its documents'
        # visibility by clearing account_id.
        ForeignKeyConstraint(
            ["organization_id", "account_id"], ["accounts.organization_id", "accounts.id"]
        ),
        ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["sources.organization_id", "sources.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "supersedes_document_id"],
            ["documents.organization_id", "documents.id"],
        ),
        Index("ix_documents_organization_id_account_id", "organization_id", "account_id"),
        Index("ix_documents_department_ids", "department_ids", postgresql_using="gin"),
        Index(
            "ix_documents_organization_id_status_sensitivity",
            "organization_id",
            "status",
            "sensitivity",
        ),
        Index(
            "ix_documents_organization_id_source_updated_at",
            "organization_id",
            "source_updated_at",
        ),
        Index("ix_documents_organization_id_display_ref", "organization_id", "display_ref"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    account_id: Mapped[uuid.UUID | None]
    sensitivity: Mapped[str] = mapped_column(Text)
    access_mode: Mapped[str] = mapped_column(Text)
    department_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default="{}"
    )
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Source identity and provenance (Phase 4).
    source_id: Mapped[uuid.UUID]
    external_id: Mapped[str] = mapped_column(Text)
    document_kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    display_ref: Mapped[str | None] = mapped_column(Text)
    uri: Mapped[str | None] = mapped_column(Text)
    author_display: Mapped[str | None] = mapped_column(Text)
    authority: Mapped[str] = mapped_column(Text)
    source_created_at: Mapped[datetime | None]
    source_updated_at: Mapped[datetime]
    # Last time content was (re)written / access columns or source grants changed.
    ingested_at: Mapped[datetime] = mapped_column(server_default=func.now())
    acl_updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    content_hash: Mapped[bytes] = mapped_column(BYTEA)
    access_hash: Mapped[bytes] = mapped_column(BYTEA)
    version: Mapped[int] = mapped_column(server_default=text("1"))
    body: Mapped[str] = mapped_column(Text)
    supersedes_external_id: Mapped[str | None] = mapped_column(Text)
    supersedes_document_id: Mapped[uuid.UUID | None]
    # Validated per-kind extras (ticket status, channel name). Never read by authz.
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )


class DocumentGrant(Base):
    """An explicit grant of one document to one principal.

    Each principal kind has its own column with a composite foreign key, so a grant can
    only name a principal of the document's own organization; ``principal_type`` names
    the populated column (CHECK in the migration).
    """

    __tablename__ = "document_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["memberships.organization_id", "memberships.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "group_id"],
            ["access_groups.organization_id", "access_groups.id"],
            ondelete="CASCADE",
        ),
        # Source-origin and admin-origin grants are separate permission origins (D-12).
        UniqueConstraint("document_id", "origin", "membership_id"),
        UniqueConstraint("document_id", "origin", "department_id"),
        UniqueConstraint("document_id", "origin", "group_id"),
        Index(
            "ix_document_grants_organization_id_membership_id", "organization_id", "membership_id"
        ),
        Index(
            "ix_document_grants_organization_id_department_id", "organization_id", "department_id"
        ),
        Index("ix_document_grants_organization_id_group_id", "organization_id", "group_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    principal_type: Mapped[str] = mapped_column(Text)
    membership_id: Mapped[uuid.UUID | None]
    department_id: Mapped[uuid.UUID | None]
    group_id: Mapped[uuid.UUID | None]
    origin: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
