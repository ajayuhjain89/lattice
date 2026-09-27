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
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base

_UUID_DEFAULT = text("gen_random_uuid()")


class AccessGroup(Base):
    """A set of members used as a grant principal: a private channel's members
    (``origin='source'``) or an admin-defined group such as "Exec staff"."""

    __tablename__ = "access_groups"
    __table_args__ = (UniqueConstraint("organization_id", "id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    origin: Mapped[str] = mapped_column(Text)
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
    """The Phase 2 document record: identity, organization, lifecycle and access columns
    only. Content, source and provenance columns arrive with ingestion (Phase 4)."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        # No ON DELETE action: deleting an account must never widen its documents'
        # visibility by clearing account_id.
        ForeignKeyConstraint(
            ["organization_id", "account_id"], ["accounts.organization_id", "accounts.id"]
        ),
        Index("ix_documents_organization_id_account_id", "organization_id", "account_id"),
        Index("ix_documents_department_ids", "department_ids", postgresql_using="gin"),
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
        UniqueConstraint("document_id", "membership_id"),
        UniqueConstraint("document_id", "department_id"),
        UniqueConstraint("document_id", "group_id"),
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
