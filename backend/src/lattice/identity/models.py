"""Identity models (docs/data-model.md §2).

``users`` and ``external_identities`` are global (a person can belong to several
organizations), but they are still RLS-protected: the runtime role only sees users who
have a membership in the currently bound organization.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, ForeignKeyConstraint, LargeBinary, Text, UniqueConstraint, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base

_UUID_DEFAULT = sql_text("gen_random_uuid()")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    # Login hint and display only; never an authorization input.
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
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
