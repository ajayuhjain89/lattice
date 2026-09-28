"""Chunks: the retrievable units of a document (docs/data-model.md §6; migration 0005).

A chunk's vector and lexical entry are columns of its row, so deleting a chunk (or its
document) removes both in the same transaction.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base
from lattice.db.types import Vector

EMBEDDING_DIMENSIONS = 1024
"""Fixed per deployment (ADR-008); migration 0005 creates ``vector(1024)``."""


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("document_id", "ordinal", deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="CASCADE",
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    # Deterministic: uuid5(document_id, content_hash, occurrence) (ingestion-design §2).
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    document_id: Mapped[uuid.UUID]
    ordinal: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int]
    char_end: Mapped[int]
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    source_anchors: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    occurred_at: Mapped[datetime | None]
    author_display: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[bytes] = mapped_column(BYTEA)
    embedding_input_hash: Mapped[bytes] = mapped_column(BYTEA)
    token_count: Mapped[int]
    tsv: Mapped[str] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedding_status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
