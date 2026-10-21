"""Lexical retrieval with PostgreSQL full-text search (retrieval-design §4).

``chunks.tsv`` was built by ingestion: title and heading path (weight A), the display
reference in the ``simple`` configuration (weight A), the chunk text (weight D) -- and,
by decision D-10, never a structured account name. The query is parsed in the same
text-search configuration and served by the GIN index on ``tsv``; how depends on
``RetrievalConfig.lexical_mode``:

* ``coverage`` (the default since retrieval-2): any of the question's lexemes may match
  (each quoted, OR-ed); ranked by how many distinct question lexemes the chunk contains,
  plus ``ts_rank_cd(tsv, query, 32)``;
* ``any_term``: the same any-lexeme match, ranked by ``ts_rank_cd`` alone;
* ``all_terms``: ``websearch_to_tsquery`` (every term must match; the Phase 0 design).

With ``lexical_identifier_phrases`` and ``lexical_account_terms`` (both on by default),
an identifier from the question also scores as a phrase (+1), and a *visible* account
named in the question counts as one matched term for documents of that account (D-10,
query time) -- so that term alone also matches every chunk of the account
(phase-13-plan G1). Ties are broken deterministically.

One statement: tenant, lifecycle and authorization conditions are in its WHERE clause,
so an unreadable chunk that matches the words better is never selected.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    Text,
    any_,
    bindparam,
    case,
    cast,
    func,
    literal,
    literal_column,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSQUERY, UUID
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
    stable_order,
    to_candidates,
)


def _terms(regconfig: ColumnElement[str], text: Any) -> Any:
    """The question's own lexemes, parsed and stemmed by PostgreSQL in the index's
    configuration (as ingestion parsed the chunks), one row each."""
    return func.unnest(func.tsvector_to_array(func.to_tsvector(regconfig, text))).column_valued(
        "term"
    )


def _any_term_query(regconfig: ColumnElement[str], text: Any) -> Any:
    """The question's lexemes OR-ed into one tsquery. Each lexeme is quoted, so no query
    syntax can be injected through the text."""
    term = _terms(regconfig, text)
    joined = select(
        func.array_to_string(func.array_agg(func.quote_literal(term)), literal_column("' | '"))
    ).scalar_subquery()
    return cast(joined, TSQUERY)


def _terms_matched(regconfig: ColumnElement[str], text: Any) -> Any:
    """How many distinct question lexemes the chunk contains (coordination)."""
    term = _terms(regconfig, text)
    return (
        select(func.count())
        .where(CHUNKS.c.tsv.bool_op("@@")(cast(func.quote_literal(term), TSQUERY)))
        .scalar_subquery()
    )


def lexical_statement(
    ctx: AuthorizationContext,
    scope: ResolvedScope,
    text: str,
    config: RetrievalConfig,
    *,
    identifiers: Sequence[str] = (),
    mentioned_account_ids: Sequence[uuid.UUID] = (),
) -> Select[tuple[object, ...]]:
    """``identifiers`` are the record references found in the question;
    ``mentioned_account_ids`` must be accounts the reader may see (resolved through
    ``authz.visible_accounts_predicate``) -- never any other account."""
    # The configuration name is validated by RetrievalConfig (never client input).
    regconfig: ColumnElement[str] = literal_column(f"'{config.text_search_config}'::regconfig")
    param = bindparam("lexical_query", text, type_=Text)
    if config.lexical_mode == "all_terms":
        query = func.websearch_to_tsquery(regconfig, param)
    else:
        query = _any_term_query(regconfig, param)
    matches: ColumnElement[bool] = CHUNKS.c.tsv.bool_op("@@")(query)
    # ts_rank_cd with normalization 32 is in [0, 1): with coordination it only breaks
    # ties between chunks that contain the same number of the question's terms.
    score: Any = func.ts_rank_cd(CHUNKS.c.tsv, query, literal_column("32"))
    if config.lexical_mode == "coverage":
        score = score + _terms_matched(regconfig, param)
    if config.lexical_identifier_phrases:
        # "HAL-4821" parses into the lexemes 'hal' and '-4821'; 'hal' is in every
        # ticket. The identifier as a phrase is what distinguishes a mention of it.
        for i, identifier in enumerate(identifiers):
            phrase = func.phraseto_tsquery(
                regconfig, bindparam(f"identifier_{i}", identifier, type_=Text)
            )
            score = score + case((CHUNKS.c.tsv.bool_op("@@")(phrase), 1), else_=0)
    if config.lexical_account_terms and mentioned_account_ids:
        # D-10: account names are not in the shared index. A *visible* account named in
        # the question counts as one matched term for documents of that account, per
        # reader, at query time. Hidden accounts are never in this list.
        of_account = DOCUMENTS.c.account_id == any_(
            literal(sorted(mentioned_account_ids), ARRAY(UUID(as_uuid=True)))
        )
        matches = or_(matches, of_account)
        score = score + case((of_account, 1), else_=0)
    return (
        select(*candidate_columns(), score.label("score"))
        .select_from(chunks_with_documents(ctx))
        .where(*permitted_chunk_conditions(ctx, scope), matches)
        .order_by(score.desc(), *stable_order())
        .limit(config.k_lexical)
        .execution_options(**{CHANNEL_OPTION: "lexical"})
    )


async def lexical_candidates(
    session: AsyncSession,
    ctx: AuthorizationContext,
    scope: ResolvedScope,
    text: str,
    config: RetrievalConfig,
    *,
    identifiers: Sequence[str] = (),
    mentioned_account_ids: Sequence[uuid.UUID] = (),
) -> list[Candidate]:
    statement = lexical_statement(
        ctx,
        scope,
        text,
        config,
        identifiers=identifiers,
        mentioned_account_ids=mentioned_account_ids,
    )
    rows = (await session.execute(statement)).all()
    return to_candidates(rows, "lexical")
