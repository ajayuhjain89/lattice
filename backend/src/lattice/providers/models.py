"""Configured model providers and their approved AI egress level
(docs/security-model.md §6, docs/data-model.md §7; migration 0005).

Secrets are never stored here: API keys come from the environment and are read by the
provider implementation only.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base


class ModelProvider(Base):
    __tablename__ = "model_providers"
    __table_args__ = (UniqueConstraint("organization_id", "purpose", "provider", "model"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    purpose: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    deployment: Mapped[str] = mapped_column(Text)
    approved_for_level: Mapped[str] = mapped_column(Text, server_default="internal")
    # Set by an explicit administrator approval; required for organization_controlled.
    approved_at: Mapped[datetime | None]
    retention_mode: Mapped[str] = mapped_column(Text)
    training_opt_out: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
