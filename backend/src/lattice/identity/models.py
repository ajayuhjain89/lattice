"""Identity models (docs/data-model.md §2).

``users`` and ``external_identities`` are global (a person can belong to several
organizations), but they are still RLS-protected: the runtime role only sees users who
have a membership in the currently bound organization. A user created by an
organization's provisioning (Phase 10) is *owned* by it (``home_organization_id``) and
can belong to no other organization.

Phase 10 (migration 0013, ADR-015): ``oidc_connections``, ``oidc_login_attempts``,
``directory_identities`` and ``scim_tokens`` are tenant tables. CHECK constraints and
policies live in the migration.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base

_UUID_DEFAULT = sql_text("gen_random_uuid()")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Unique among global users, and per owning organization for owned users: an
        # organization's provisioning never collides with another organization's user.
        Index(
            "uq_users_email",
            "email",
            unique=True,
            postgresql_where=sql_text("home_organization_id IS NULL"),
        ),
        Index(
            "uq_users_home_organization_email",
            "home_organization_id",
            "email",
            unique=True,
            postgresql_where=sql_text("home_organization_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    # Login hint and display only; never an authorization input.
    email: Mapped[str] = mapped_column(CITEXT)
    display_name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    # Set for users created by an organization's provisioning (SCIM); NULL for global users.
    home_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # OIDC issuer URL, or "dev" for the development persona login.
    issuer: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Session(Base):
    """Server-side session. Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "sessions"
    __table_args__ = (
        # A session can only exist for an actual membership in its organization.
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["memberships.organization_id", "memberships.user_id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    user_id: Mapped[uuid.UUID]
    organization_id: Mapped[uuid.UUID] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]


class OidcConnection(Base):
    """An organization's OIDC identity provider (ADR-015). Operator-managed."""

    __tablename__ = "oidc_connections"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("login_key"),
        Index(
            "uq_oidc_connections_one_active",
            "organization_id",
            unique=True,
            postgresql_where=sql_text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    # Opaque and unguessable: the sign-in URL names the connection, not the organization.
    login_key: Mapped[str] = mapped_column(Text)
    issuer: Mapped[str] = mapped_column(Text)
    client_id: Mapped[str] = mapped_column(Text)
    # A reference (env:LATTICE_OIDC_SECRET_<NAME>), never the secret.
    client_secret_ref: Mapped[str | None] = mapped_column(Text)
    subject_claim: Mapped[str] = mapped_column(Text, server_default="sub")
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class OidcLoginAttempt(Base):
    """Server-side state of one authorization-code flow: single use, short-lived."""

    __tablename__ = "oidc_login_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "connection_id"],
            ["oidc_connections.organization_id", "oidc_connections.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("state_hash"),
        Index("ix_oidc_login_attempts_expiry", "organization_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID]
    connection_id: Mapped[uuid.UUID]
    state_hash: Mapped[bytes] = mapped_column(LargeBinary)
    binding_hash: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[str] = mapped_column(Text)
    code_verifier: Mapped[str] = mapped_column(Text)
    return_to: Mapped[str] = mapped_column(Text, server_default="/")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None]


class DirectoryIdentity(Base):
    """The identity provider's person in one organization: (connection, external ID) ->
    membership. Written by SCIM provisioning; read by OIDC sign-in."""

    __tablename__ = "directory_identities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "connection_id"],
            ["oidc_connections.organization_id", "oidc_connections.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["memberships.organization_id", "memberships.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("connection_id", "external_id"),
        UniqueConstraint("organization_id", "membership_id"),
        Index(
            "uq_directory_identities_user_name",
            "connection_id",
            sql_text("lower(user_name)"),
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID]
    connection_id: Mapped[uuid.UUID]
    membership_id: Mapped[uuid.UUID]
    external_id: Mapped[str] = mapped_column(Text)
    user_name: Mapped[str] = mapped_column(Text)
    given_name: Mapped[str | None] = mapped_column(Text)
    family_name: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(server_default=sql_text("1"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ScimToken(Base):
    """A SCIM bearer token. Only its SHA-256 is stored."""

    __tablename__ = "scim_tokens"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "connection_id"],
            ["oidc_connections.organization_id", "oidc_connections.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("token_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID]
    connection_id: Mapped[uuid.UUID]
    token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None]
