"""Term statistics over the reader's permitted chunks (P13.3, the relevance gate).

How specific a question's word is depends on how many chunks contain it. The counts are
taken over the chunks **this reader may read** -- the same tenant, lifecycle and
authorization conditions as every retrieval statement, in its WHERE clause -- so a
document the reader cannot see never changes a weight, and so never changes whether a
question is answered (SEC-5, phase-13-plan S4).

One statement, one pass over the permitted chunks: ``count(*)``, one filtered count per
content lexeme (at most ``MAX_TERMS``), and the permitted chunks of the visible accounts
the question names. Cost grows with the reader's corpus (a Phase 14 scale item); at the
reference corpus (~1,300 chunks) it is a few milliseconds.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import Select, Text, any_, bindparam, cast, func, literal, select
from sqlalchemy.dialects.postgresql import ARRAY, TSQUERY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz import AuthorizationContext
from lattice.retrieval.permitted import (
    CHANNEL_OPTION,
    CHUNKS,
    DOCUMENTS,
    ResolvedScope,
    chunks_with_documents,
    permitted_chunk_conditions,
)

# A question has a handful of content lexemes; 2,000 characters could have hundreds.
# Only the first MAX_TERMS (sorted) are weighed; the gate ignores the rest.
MAX_TERMS: Final = 32


@dataclass(frozen=True, slots=True)
class TermStatistics:
    chunks: int  # permitted active chunks
    document_frequency: dict[str, int]  # per content lexeme: permitted chunks containing it
    account_chunks: int  # permitted chunks of the visible accounts the question names


def term_statistics_statement(
    ctx: AuthorizationContext, terms: Sequence[str], account_ids: Sequence[uuid.UUID]
) -> Select[tuple[Any, ...]]:
    """``account_ids`` must be accounts the reader may see (the question's mentions)."""
    columns: list[Any] = [func.count().label("chunks")]
    for i, term in enumerate(terms[:MAX_TERMS]):
        lexeme = cast(func.quote_literal(bindparam(f"term_{i}", term, type_=Text)), TSQUERY)
        columns.append(func.count().filter(CHUNKS.c.tsv.bool_op("@@")(lexeme)).label(f"df_{i}"))
    of_account = DOCUMENTS.c.account_id == any_(
        literal(sorted(account_ids), ARRAY(UUID(as_uuid=True)))
    )
    columns.append(func.count().filter(of_account).label("account_chunks"))
    return (
        select(*columns)
        .select_from(chunks_with_documents(ctx))
        .where(*permitted_chunk_conditions(ctx, ResolvedScope()))
        .execution_options(**{CHANNEL_OPTION: "term_statistics"})
    )


async def term_statistics(
    session: AsyncSession,
    ctx: AuthorizationContext,
    terms: Sequence[str],
    account_ids: Sequence[uuid.UUID],
) -> TermStatistics:
    row = (await session.execute(term_statistics_statement(ctx, terms, account_ids))).one()
    return TermStatistics(
        chunks=int(row.chunks),
        document_frequency={
            t: int(row._mapping[f"df_{i}"]) for i, t in enumerate(terms[:MAX_TERMS])
        },
        account_chunks=int(row.account_chunks),
    )
