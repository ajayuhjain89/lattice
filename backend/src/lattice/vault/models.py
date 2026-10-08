"""``encrypted_secrets`` and ``outbound_targets`` (migration 0016; phase-12-plan C3, C4).
CHECK constraints and policies live in the migration; these models describe shape."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, LargeBinary, Text, UniqueConstraint, func
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base


class EncryptedSecret(Base):
    """A recoverable credential's ciphertext (``vault:<id>``). Never plaintext, never
    a key; NULL material once destroyed."""

    __tablename__ = "encrypted_secrets"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        Index(
            "uq_encrypted_secrets_live_owner",
            "organization_id",
            "owner_kind",
            "owner_id",
            unique=True,
            postgresql_where=sql_text("destroyed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    owner_kind: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID]
    key_id: Mapped[str] = mapped_column(Text)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    hint: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    destroyed_at: Mapped[datetime | None]


class OutboundTarget(Base):
    """A private endpoint an operator registered for one organization and purpose."""

    __tablename__ = "outbound_targets"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        Index(
            "uq_outbound_targets_live",
            "organization_id",
            "purpose",
            "scheme",
            "host",
            "port",
            unique=True,
            postgresql_where=sql_text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    purpose: Mapped[str] = mapped_column(Text)
    scheme: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(Text)
    port: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None]
