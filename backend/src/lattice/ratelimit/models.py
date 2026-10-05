"""Rate-limit state in PostgreSQL: counters per (organization, bucket, subject)
(migration 0013), Ask slots and pre-authentication counts (0015). IDs, keyed hashes,
counts and times only."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    SmallInteger,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base


class RateLimitCounter(Base):
    __tablename__ = "rate_limit_counters"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    bucket: Mapped[str] = mapped_column(Text, primary_key=True)
    # A membership or SCIM token ID: never a name, an address or request content.
    subject: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    window_start: Mapped[datetime]
    count: Mapped[int]


class AskSlot(Base):
    """An Ask in flight (migration 0015, D-24): a leased slot per membership, shared by
    every API process; ``expires_at`` frees it if its process died."""

    __tablename__ = "ask_slots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_ask_slots_membership",
            ondelete="CASCADE",
        ),
        CheckConstraint("slot >= 0 AND slot < 100", name="slot"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    membership_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    slot: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    lease_id: Mapped[uuid.UUID]
    expires_at: Mapped[datetime]


class PreAuthRateLimit(Base):
    """Pre-authentication counts per client (migration 0015, D-24 amended): no
    organization, no address -- ``client_key`` is an HMAC of it. Reached only through the
    ``lattice_auth`` functions."""

    __tablename__ = "pre_auth_rate_limits"
    __table_args__ = (
        CheckConstraint("bucket ~ '^[a-z_]{1,32}$'", name="bucket"),
        CheckConstraint("octet_length(client_key) = 32", name="client_key"),
        CheckConstraint("count >= 0", name="count"),
        Index("ix_pre_auth_rate_limits_window_start", "window_start"),
    )

    bucket: Mapped[str] = mapped_column(Text, primary_key=True)
    client_key: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    window_start: Mapped[datetime]
    count: Mapped[int]
