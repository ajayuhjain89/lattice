"""Rate-limit counters (migration 0013): one row per (organization, bucket, subject)
holding the current fixed window and its count. IDs and counts only."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text
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
