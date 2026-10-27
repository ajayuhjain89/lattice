"""The bounded second hop, ``expansion_v1`` (P13.6, D-40; phase-13-plan §24).

After the first selection, the best ``k_seed`` selected chunks are **seeds**. One statement
adds at most ``k_expand`` candidates from three sources, each a branch of a ``UNION ALL``:

Only chunks no first-hop channel returned are added (``found``).

* **neighbours** -- chunks of a seed's document next to a seed chunk (ordinal ± 1), or
  containing one of the question's content lexemes: the rest of a record the question
  already found (HAL-4821's comment 3 when its opening matched);
* **references** -- chunks of records whose display reference (``HAL-1234``) is written in
  a seed chunk's text and is not in the question, at most ``max_references`` records;
* **links** -- chunks of the readable documents a seed's document supersedes or is
  superseded by, containing a question content lexeme.

Every branch carries the tenant, lifecycle and ``readable_documents_predicate`` conditions
for the chunk it returns **and** for the seed it reads (S1): a seed is a chunk the reader
may read, and only readable text can name a reference. Nothing here leaves PostgreSQL as
text or reaches a provider -- the hop is SQL over IDs, ordinals, display references and the
seeds' own text (S6). An identifier planted in a readable chunk can reach only a record the
reader may already read, at most ``max_references`` of them, and only at the expansion
channel's lower fusion weight (S9). The candidates go through E4 and fusion with the other
channels; the trace holds their IDs and counts only (S10).
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import replace
from typing import Any

from sqlalchemy import (
    ColumnElement,
    CompoundSelect,
    Integer,
    Select,
    Text,
    all_,
    any_,
    bindparam,
    cast,
    func,
    literal,
    literal_column,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSQUERY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz import AuthorizationContext, readable_documents_predicate
from lattice.retrieval.lexical import content_lexeme_condition
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

SEED_CHUNK = CHUNKS.alias("seed_chunk")
SEED_DOCUMENT = DOCUMENTS.alias("seed_document")

# The union is cut in Python (each chunk once, ``max_references`` records, ``k_expand``);
# this bounds what it may return before that.
MAX_ROWS = 200

# Branch order: neighbours of a record first, then the records it names, then its links.
_NEIGHBOUR, _REFERENCE, _LINK = 1, 2, 3


def _seed_conditions(ctx: AuthorizationContext, seeds: Sequence[uuid.UUID]) -> list[Any]:
    """The seed rows: the given chunks, re-checked as readable in this snapshot."""
    return [
        SEED_CHUNK.c.id == any_(literal(sorted(seeds), ARRAY(UUID(as_uuid=True)))),
        SEED_CHUNK.c.organization_id == ctx.organization_id,
        SEED_DOCUMENT.c.id == SEED_CHUNK.c.document_id,
        SEED_DOCUMENT.c.organization_id == ctx.organization_id,
        SEED_DOCUMENT.c.status == "active",
        readable_documents_predicate(ctx, SEED_DOCUMENT),
    ]


def _content_query(regconfig: ColumnElement[str], text: Any, excluded: Any) -> Any:
    """The question's content lexemes OR-ed into one tsquery (NULL when it has none)."""
    term = func.unnest(func.tsvector_to_array(func.to_tsvector(regconfig, text))).column_valued(
        "term"
    )
    joined = (
        select(
            func.array_to_string(func.array_agg(func.quote_literal(term)), literal_column("' | '"))
        )
        .where(content_lexeme_condition(regconfig, term, excluded))
        .scalar_subquery()
    )
    return cast(joined, TSQUERY)


def _escaped(value: Any) -> Any:
    """``value`` as a literal inside a POSIX regular expression."""
    return func.regexp_replace(value, r"([.^$*+?()\[\]{}|\\-])", r"\\\1", "g")


def expansion_branches(
    ctx: AuthorizationContext,
    scope: ResolvedScope,
    seeds: Sequence[uuid.UUID],
    text: str,
    excluded: str,
    identifiers: Sequence[str],
    config: RetrievalConfig,
) -> list[Select[tuple[object, ...]]]:
    """The three branches, each with every security condition in its own WHERE.
    ``seeds`` are chunk IDs the reader may read (selected evidence); ``excluded`` is the
    words that are not content terms (``QuestionTerms.excluded``)."""
    # The configuration name is validated by RetrievalConfig (never client input).
    regconfig: ColumnElement[str] = literal_column(f"'{config.text_search_config}'::regconfig")
    content = _content_query(
        regconfig,
        bindparam("expansion_question", text, type_=Text),
        bindparam("expansion_excluded", excluded, type_=Text),
    )
    has_content = CHUNKS.c.tsv.bool_op("@@")(content)
    seed_rank = func.array_position(
        literal(list(seeds), ARRAY(UUID(as_uuid=True))), SEED_CHUNK.c.id
    )
    permitted = permitted_chunk_conditions(ctx, scope)
    seed = _seed_conditions(ctx, seeds)
    not_a_seed = CHUNKS.c.id != all_(literal(list(seeds), ARRAY(UUID(as_uuid=True))))

    def branch(source: int, order: Any, *conditions: Any) -> Select[tuple[object, ...]]:
        return (
            select(
                *candidate_columns(),
                literal_column("1.0").label("score"),
                literal(source, Integer).label("source"),
                seed_rank.label("seed_rank"),
                order.label("detail"),
            )
            .select_from(chunks_with_documents(ctx))
            .join(SEED_CHUNK, true())
            .join(SEED_DOCUMENT, true())
            .where(*permitted, *seed, not_a_seed, *conditions)
        )

    neighbours = branch(
        _NEIGHBOUR,
        func.abs(CHUNKS.c.ordinal - SEED_CHUNK.c.ordinal),
        DOCUMENTS.c.id == SEED_DOCUMENT.c.id,
        or_(func.abs(CHUNKS.c.ordinal - SEED_CHUNK.c.ordinal) == 1, has_content),
    )
    asked = literal(sorted({i.upper() for i in identifiers}), ARRAY(Text))
    named = SEED_CHUNK.c.text.op("~*", is_comparison=True)(
        func.concat(r"(^|[^A-Za-z0-9-])", _escaped(DOCUMENTS.c.display_ref), "([^0-9]|$)")
    )
    references = branch(
        _REFERENCE,
        CHUNKS.c.ordinal,
        DOCUMENTS.c.id != SEED_DOCUMENT.c.id,
        DOCUMENTS.c.display_ref.is_not(None),
        func.upper(DOCUMENTS.c.display_ref) != all_(asked),
        named,
    )
    links = branch(
        _LINK,
        CHUNKS.c.ordinal,
        or_(
            DOCUMENTS.c.id == SEED_DOCUMENT.c.supersedes_document_id,
            DOCUMENTS.c.supersedes_document_id == SEED_DOCUMENT.c.id,
        ),
        has_content,
    )
    return [neighbours, references, links]


def expansion_statement(
    ctx: AuthorizationContext,
    scope: ResolvedScope,
    seeds: Sequence[uuid.UUID],
    text: str,
    excluded: str,
    identifiers: Sequence[str],
    config: RetrievalConfig,
) -> CompoundSelect[tuple[object, ...]]:
    """Every candidate row of the three branches, best first: by seed, then branch, then
    distance or ordinal. Python keeps each chunk once, ``max_references`` referenced
    records and ``k_expand`` candidates (the union is bounded by the seeds' records)."""
    branches = expansion_branches(ctx, scope, seeds, text, excluded, identifiers, config)
    return (
        union_all(*branches)
        .order_by(
            literal_column("seed_rank"),
            literal_column("source"),
            literal_column("detail"),
            literal_column("external_id"),
            literal_column("ordinal"),
        )
        .limit(MAX_ROWS)
        .execution_options(**{CHANNEL_OPTION: "expansion"})
    )


async def expansion_candidates(
    session: AsyncSession,
    ctx: AuthorizationContext,
    scope: ResolvedScope,
    seeds: Sequence[uuid.UUID],
    text: str,
    excluded: str,
    identifiers: Sequence[str],
    config: RetrievalConfig,
    *,
    found: Collection[uuid.UUID] = (),
) -> list[Candidate]:
    """``found``: chunks the first hop already returned. The hop only adds evidence; a
    chunk both found it would otherwise gain a second fusion contribution and outrank the
    seed it was reached from."""
    if not seeds:
        return []
    statement = expansion_statement(ctx, scope, seeds, text, excluded, identifiers, config)
    rows = (await session.execute(statement)).all()
    kept: list[Any] = []
    seen: set[uuid.UUID] = set()
    referenced: set[uuid.UUID] = set()
    for row in rows:
        if row.chunk_id in seen or row.chunk_id in found:
            continue
        if row.source == _REFERENCE:
            if row.document_id not in referenced and len(referenced) >= config.max_references:
                continue
            referenced.add(row.document_id)
        seen.add(row.chunk_id)
        kept.append(row)
        if len(kept) == config.k_expand:
            break
    # Each candidate's rank in the expansion list is its seed's rank (ties allowed): fused
    # at the channel's lower weight, it lands below the seed it was reached from.
    return [
        replace(c, rank=int(row.seed_rank))
        for c, row in zip(to_candidates(kept, "expansion"), kept, strict=True)
    ]
