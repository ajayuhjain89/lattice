"""Tenant models (docs/data-model.md §2).

Every tenant-owned table carries ``organization_id`` and is protected by a forced
row-level-security policy. Relationships between tenant tables use composite foreign
keys on ``(organization_id, id)``, so the database itself rejects a row in one
organization that references a row in another.

Column-level CHECK constraints live in the migration; these models describe shape.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base

_UUID_DEFAULT = text("gen_random_uuid()")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    slug: Mapped[str] = mapped_column(CITEXT, unique=True)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id"),
        UniqueConstraint("organization_id", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(Text, server_default="active")
    # Administrative capability only. Grants no access to content (SEC-15).
    is_admin: Mapped[bool] = mapped_column(server_default=text("false"))
    clearance: Mapped[str] = mapped_column(Text, server_default="internal")
    account_scope: Mapped[str] = mapped_column(Text, server_default="assigned")
    # Display label ("Account Manager"). Policy code never reads it.
    role_label: Mapped[str | None] = mapped_column(Text)
    authz_updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("organization_id", "key"),
        UniqueConstraint("organization_id", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(CITEXT)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class MembershipDepartment(Base):
    __tablename__ = "membership_departments"
    __table_args__ = (
        PrimaryKeyConstraint("membership_id", "department_id"),
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
    )

    organization_id: Mapped[uuid.UUID]
    membership_id: Mapped[uuid.UUID]
    department_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("organization_id", "key"),
        UniqueConstraint("organization_id", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(CITEXT)
    name: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AccountAssignment(Base):
    __tablename__ = "account_assignments"
    __table_args__ = (
        PrimaryKeyConstraint("membership_id", "account_id"),
        ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["memberships.organization_id", "memberships.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "account_id"],
            ["accounts.organization_id", "accounts.id"],
            ondelete="CASCADE",
        ),
    )

    organization_id: Mapped[uuid.UUID]
    membership_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]
    assigned_at: Mapped[datetime] = mapped_column(server_default=func.now())


class OrgSettings(Base):
    """Per-organization settings (docs/data-model.md §7): the AI egress policy. An
    organization without a row uses the defaults."""

    __tablename__ = "org_settings"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    # internal | confidential | restricted; raising it is an explicit admin action.
    external_egress_level: Mapped[str] = mapped_column(Text, server_default="internal")
    # D-13: the trusted minimum sensitivity of a question; a caller can only raise it.
    default_query_sensitivity: Mapped[str] = mapped_column(Text, server_default="internal")
    # customer | synthetic: only synthetic content may reach a provider that may use
    # submitted content (training_opt_out = false; ADR-010).
    data_origin: Mapped[str] = mapped_column(Text, server_default="customer")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
