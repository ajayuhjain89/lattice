"""Evidence hydration: the selected chunks, with what Phase 6 needs to cite them.

A direct lookup by chunk ID is a read path like any other (enforcement point E5): the
statement carries the tenant, lifecycle and authorization conditions again, so it can
only return what the reader may read, even if it were handed a foreign ID.

Supersession is deterministic source metadata (``documents.supersedes_document_id``,
retrieval-design §10). It is exposed only between *readable* documents: the
predecessor this document replaces, and the documents that replace it, each through
the same authorization predicate. An unreadable successor is not mentioned at all, so
supersession cannot reveal the existence of a document the reader cannot see.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, Table, and_, any_, func, literal, select
from sqlalchemy.dialects.postgresql import ARRAY, UUID, aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz import AuthorizationContext, readable_documents_predicate
from lattice.retrieval.models import (
    KIND_SOURCE_TYPE,
    Channel,
    Evidence,
    RankedCandidate,
    VisibleAccount,
)
from lattice.retrieval.permitted import (
    CHANNEL_OPTION,
    CHUNKS,
    DOCUMENTS,
    VISIBLE_ACCOUNT,
    ResolvedScope,
    chunks_with_documents,
    permitted_chunk_conditions,
)
from lattice.sources.models import Source

_SOURCES = Source.__table__
assert isinstance(_SOURCES, Table)  # noqa: S101 - declarative models map to Tables


def evidence_statement(
    ctx: AuthorizationContext, chunk_ids: Sequence[uuid.UUID]
) -> Select[tuple[Any, ...]]:
    predecessor = DOCUMENTS.alias("predecessor")
    successor = DOCUMENTS.alias("successor")
    successors = (
        select(
            func.coalesce(
                func.array_agg(aggregate_order_by(successor.c.id, successor.c.id)),
                literal([], ARRAY(UUID(as_uuid=True))),
            )
        )
        .where(
            successor.c.supersedes_document_id == DOCUMENTS.c.id,
            successor.c.organization_id == ctx.organization_id,
            successor.c.status == "active",
            readable_documents_predicate(ctx, successor),
        )
        .scalar_subquery()
    )
    return (
        select(
            CHUNKS.c.id.label("chunk_id"),
            CHUNKS.c.ordinal,
            CHUNKS.c.text,
            CHUNKS.c.char_start,
            CHUNKS.c.char_end,
            CHUNKS.c.heading_path,
            CHUNKS.c.source_anchors,
            CHUNKS.c.occurred_at,
            CHUNKS.c.author_display.label("chunk_author"),
            DOCUMENTS.c.id.label("document_id"),
            DOCUMENTS.c.document_kind,
            DOCUMENTS.c.title,
            DOCUMENTS.c.display_ref,
            DOCUMENTS.c.uri,
            DOCUMENTS.c.author_display.label("document_author"),
            DOCUMENTS.c.source_created_at,
            DOCUMENTS.c.source_updated_at,
            DOCUMENTS.c.version,
            DOCUMENTS.c.authority,
            _SOURCES.c.name.label("source_name"),
            VISIBLE_ACCOUNT.c.id.label("account_id"),
            VISIBLE_ACCOUNT.c.key.label("account_key"),
            VISIBLE_ACCOUNT.c.name.label("account_name"),
            predecessor.c.id.label("supersedes_document_id"),
            successors.label("superseded_by"),
        )
        .select_from(
            chunks_with_documents(ctx)
            .join(
                _SOURCES,
                and_(
                    _SOURCES.c.id == DOCUMENTS.c.source_id,
                    _SOURCES.c.organization_id == ctx.organization_id,
                ),
            )
            .outerjoin(
                predecessor,
                and_(
                    predecessor.c.id == DOCUMENTS.c.supersedes_document_id,
                    predecessor.c.organization_id == ctx.organization_id,
                    predecessor.c.status == "active",
                    readable_documents_predicate(ctx, predecessor),
                ),
            )
        )
        .where(
            *permitted_chunk_conditions(ctx, ResolvedScope()),
            CHUNKS.c.id == any_(literal(sorted(chunk_ids), ARRAY(UUID(as_uuid=True)))),
        )
        .execution_options(**{CHANNEL_OPTION: "evidence"})
    )


class EvidenceUnavailable(Exception):
    """A selected chunk could not be hydrated through the predicate in the same
    snapshot. Impossible unless the candidate SQL and the hydration disagree -- a bug;
    the request is aborted rather than returning a partial list."""


async def hydrate(
    session: AsyncSession, ctx: AuthorizationContext, selected: Sequence[RankedCandidate]
) -> list[Evidence]:
    if not selected:
        return []
    ids = [r.fused.candidate.chunk_id for r in selected]
    rows = {row.chunk_id: row for row in (await session.execute(evidence_statement(ctx, ids)))}
    if set(rows) != set(ids):
        raise EvidenceUnavailable()
    return [
        _evidence(
            rows[r.fused.candidate.chunk_id],
            rank=position,
            matched_by=r.fused.matched_by,
            channel_ranks=dict(r.fused.ranks),
            rrf_score=round(r.fused.rrf_score, 8),
            features=dict(r.features),
            final_score=round(r.final_score, 8),
        )
        for position, r in enumerate(selected, start=1)
    ]


async def lookup_evidence(
    session: AsyncSession, ctx: AuthorizationContext, chunk_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Evidence]:
    """Direct lookup by chunk ID (E5: the evidence endpoint, trace re-authorization).
    The statement carries the tenant, lifecycle and authorization conditions, so an
    unreadable, foreign or missing chunk is simply absent -- indistinguishable (SEC-5).
    No ranking: ``rank`` is 0 and the scores are empty."""
    if not chunk_ids:
        return {}
    rows = await session.execute(evidence_statement(ctx, chunk_ids))
    return {
        row.chunk_id: _evidence(
            row,
            rank=0,
            matched_by=(),
            channel_ranks={},
            rrf_score=0.0,
            features={},
            final_score=0.0,
        )
        for row in rows
    }


def _evidence(
    row: Any,
    *,
    rank: int,
    matched_by: tuple[Channel, ...],
    channel_ranks: dict[Channel, int],
    rrf_score: float,
    features: dict[str, float],
    final_score: float,
) -> Evidence:
    account = (
        VisibleAccount(id=row.account_id, key=str(row.account_key), name=row.account_name)
        if row.account_id is not None
        else None
    )
    return Evidence(
        rank=rank,
        chunk_id=row.chunk_id,
        document_id=row.document_id,
        source_type=KIND_SOURCE_TYPE[row.document_kind],
        document_kind=row.document_kind,
        source_name=row.source_name,
        title=row.title,
        display_ref=row.display_ref,
        uri=row.uri,
        heading_path=tuple(row.heading_path),
        source_anchors=tuple(row.source_anchors),
        ordinal=row.ordinal,
        text=row.text,
        char_start=row.char_start,
        char_end=row.char_end,
        author=row.chunk_author or row.document_author,
        occurred_at=row.occurred_at,
        document_created_at=row.source_created_at,
        document_updated_at=row.source_updated_at,
        version=row.version,
        authority=row.authority,
        account=account,
        supersedes_document_id=row.supersedes_document_id,
        superseded_by_document_ids=tuple(row.superseded_by),
        matched_by=matched_by,
        channel_ranks=channel_ranks,
        rrf_score=rrf_score,
        features=features,
        final_score=final_score,
    )
