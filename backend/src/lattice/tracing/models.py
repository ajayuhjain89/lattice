"""Query traces and audit events (migration 0008; data-model §7, security-model §8)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base
from lattice.db.types import Xid8

_UUID_DEFAULT = text("gen_random_uuid()")


class QueryTrace(Base):
    """One Ask: identifiers, versions, codes, counts and timings; the validated answer
    and the asker's question (asker-only, retention-limited). Never the prompt, evidence
    text, rejected claim text or a secret."""

    __tablename__ = "query_traces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["memberships.organization_id", "memberships.id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_query_traces_organization_id_membership_id_created_at",
            "organization_id",
            "membership_id",
            text("created_at DESC"),
        ),
        # The administrator's operational activity list (Phase 8).
        Index(
            "ix_query_traces_organization_id_created_at", "organization_id", text("created_at DESC")
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    membership_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    question_text: Mapped[str] = mapped_column(Text)
    question_sensitivity: Mapped[str] = mapped_column(Text)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    authz_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    retrieval: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default=text("'{}'")
    )
    prompt_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default=text("'{}'")
    )
    cited_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default=text("'{}'")
    )
    # Accounts whose metadata the stored answer may carry; NULL = unknown (migration 0009).
    answer_account_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    egress: Mapped[dict[str, Any]] = mapped_column(JSONB)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)
    status_reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    timings_ms: Mapped[dict[str, Any]] = mapped_column(JSONB)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))


class AuditEvent(Base):
    """Append-only record of an administrative change, with a stated reason."""

    __tablename__ = "audit_events"
    # The administrators' audit view: newest first, keyset pages (Phase 9).
    __table_args__ = (
        Index(
            "ix_audit_events_organization_id_created_at",
            "organization_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        # Access writes by the API need an event of their own transaction (0013).
        Index("ix_audit_events_txid", "txid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUID_DEFAULT)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    target: Mapped[dict[str, Any]] = mapped_column(JSONB)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    # The writing transaction (its default); the access-audit triggers look it up.
    # Never loaded by the application (deferred).
    txid: Mapped[int] = mapped_column(
        Xid8, server_default=text("pg_current_xact_id()"), deferred=True
    )
