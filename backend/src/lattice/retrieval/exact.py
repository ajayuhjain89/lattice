"""Exact identifier retrieval: ``HAL-4821`` finds that record, independent of any
similarity score (retrieval-design §2).

Matches ``documents.display_ref`` -- the human reference the source shows (a ticket
ID) -- exactly, through the ``(organization_id, display_ref)`` index. All chunks of a
matched document are candidates (header first, then comments in order), at most
``k_exact``. Exact match strength is carried into rerank_v1 as ``identifier_match``.

The identifier is a lookup key, not an authority: the statement carries the tenant,
lifecycle and authorization conditions like every other retriever, so an unreadable
record with the same ID -- including another organization's, which deliberately
reuses Halden's ticket IDs -- is never selected.
"""

from collections.abc import Sequence

from sqlalchemy import Select, Text, any_, literal, literal_column, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz import AuthorizationContext
from lattice.retrieval.models import Candidate, RetrievalConfig
from lattice.retrieval.permitted import (
    CHANNEL_OPTION,
    CHUNKS,
    DOCUMENTS,
    ResolvedScope,
    candidate_columns,
    chunks_with_documents,
    permitted_chunk_conditions,
    to_candidates,
)


def exact_statement(
    ctx: AuthorizationContext,
    scope: ResolvedScope,
    identifiers: Sequence[str],
    config: RetrievalConfig,
) -> Select[tuple[object, ...]]:
    refs = literal(sorted(set(identifiers)), ARRAY(Text))
    return (
        select(*candidate_columns(), literal_column("1.0").label("score"))
        .select_from(chunks_with_documents(ctx))
        .where(
            *permitted_chunk_conditions(ctx, scope),
            DOCUMENTS.c.display_ref == any_(refs),
        )
        .order_by(DOCUMENTS.c.display_ref, CHUNKS.c.ordinal, CHUNKS.c.id)
        .limit(config.k_exact)
        .execution_options(**{CHANNEL_OPTION: "exact"})
    )


async def exact_candidates(
    session: AsyncSession,
    ctx: AuthorizationContext,
    scope: ResolvedScope,
    identifiers: Sequence[str],
    config: RetrievalConfig,
) -> list[Candidate]:
    if not identifiers:
        return []
    rows = (await session.execute(exact_statement(ctx, scope, identifiers, config))).all()
    return to_candidates(rows, "exact")
