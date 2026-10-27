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

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import (
    ColumnElement,
    Select,
    Text,
    and_,
    any_,
    bindparam,
    cast,
    func,
    literal,
    literal_column,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSQUERY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz import AuthorizationContext
from lattice.retrieval.lexical import LexicalWeights, content_lexeme_condition
from lattice.retrieval.models import RetrievalConfig
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
# Lexical ranking (P13.4) weighs at most this many of the question's lexemes, content
# lexemes first; the rest still match, unweighed.
MAX_LEXEMES: Final = 48


def idf_weight(chunks: int, containing: int) -> float:
    """``ln((N + 1) / (n + 1)) + 1``: smoothed inverse document frequency -- a word every
    chunk has weighs least but not nothing, a word no chunk has weighs most."""
    return math.log((chunks + 1) / (min(containing, chunks) + 1)) + 1.0


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


@dataclass(frozen=True, slots=True)
class QuestionStatistics:
    """The question's lexemes and how many permitted chunks contain each (P13.4)."""

    chunks: int  # permitted active chunks
    account_chunks: int  # permitted chunks of the visible accounts the question names
    document_frequency: dict[str, int]  # per lexeme (at most MAX_LEXEMES)
    content: frozenset[str]  # the content lexemes among them

    def weight(self, lexeme: str) -> float:
        return idf_weight(self.chunks, self.document_frequency.get(lexeme, 0))

    @property
    def account_weight(self) -> float:
        return idf_weight(self.chunks, self.account_chunks)


def question_statistics_statement(
    ctx: AuthorizationContext,
    text: str,
    excluded: str,
    text_search_config: str,
    account_ids: Sequence[uuid.UUID],
) -> Select[tuple[Any, ...]]:
    """The question's lexemes -- parsed by PostgreSQL in the index's configuration, at
    most ``MAX_LEXEMES``, content lexemes first -- each with the permitted chunks
    containing it, and the permitted chunks in all and of the named visible accounts.
    ``excluded`` holds the words that are not content terms
    (``lattice.retrieval.lexical.content_lexeme_condition``); ``account_ids`` must be
    accounts the reader may see.

    One pass over the reader's permitted chunks (the statement's WHERE carries the
    tenant, lifecycle and authorization conditions), each joined to every lexeme and
    grouped by lexeme; a question without lexemes is one group whose lexeme is NULL."""
    # The configuration name is validated by RetrievalConfig (never client input).
    regconfig: ColumnElement[str] = literal_column(f"'{text_search_config}'::regconfig")
    question = bindparam("statistics_question", text, type_=Text)
    parsed = (
        func.unnest(func.tsvector_to_array(func.to_tsvector(regconfig, question)))
        .table_valued("term")
        .render_derived(name="parsed")
    )
    content = content_lexeme_condition(
        regconfig, parsed.c.term, bindparam("statistics_excluded", excluded, type_=Text)
    )
    lexeme = (
        select(parsed.c.term, and_(content).label("content"))
        .order_by(and_(content).desc(), parsed.c.term)
        .limit(MAX_LEXEMES)
        .subquery("lexeme")
    )
    of_account = DOCUMENTS.c.account_id == any_(
        literal(sorted(account_ids), ARRAY(UUID(as_uuid=True)))
    )
    containing = CHUNKS.c.tsv.bool_op("@@")(cast(func.quote_literal(lexeme.c.term), TSQUERY))
    return (
        select(
            lexeme.c.term,
            lexeme.c.content,
            func.count().filter(containing).label("containing"),
            func.count().label("chunks"),
            func.count().filter(of_account).label("account_chunks"),
        )
        .select_from(chunks_with_documents(ctx).outerjoin(lexeme, true()))
        .where(*permitted_chunk_conditions(ctx, ResolvedScope()))
        .group_by(lexeme.c.term, lexeme.c.content)
        .execution_options(**{CHANNEL_OPTION: "question_statistics"})
    )


async def question_statistics(
    session: AsyncSession,
    ctx: AuthorizationContext,
    text: str,
    excluded: str,
    text_search_config: str,
    account_ids: Sequence[uuid.UUID],
) -> QuestionStatistics:
    rows = (
        await session.execute(
            question_statistics_statement(ctx, text, excluded, text_search_config, account_ids)
        )
    ).all()
    lexemes = [r for r in rows if r.term is not None]
    # No permitted chunk: no row at all.
    return QuestionStatistics(
        chunks=int(rows[0].chunks) if rows else 0,
        account_chunks=int(rows[0].account_chunks) if rows else 0,
        document_frequency={str(r.term): int(r.containing) for r in lexemes},
        content=frozenset(str(r.term) for r in lexemes if r.content),
    )


def lexical_weights(stats: QuestionStatistics, config: RetrievalConfig) -> LexicalWeights:
    """What each question lexeme adds to lexical ranking under ``config`` (P13.4)."""
    counted = (
        stats.document_frequency
        if config.lexical_counted_terms == "all"
        else {t: n for t, n in stats.document_frequency.items() if t in stats.content}
    )
    idf = config.lexical_term_weights == "idf"
    return LexicalWeights(
        lexemes={t: stats.weight(t) if idf else 1.0 for t in counted},
        account=config.lexical_account_factor * (stats.account_weight if idf else 1.0),
    )
