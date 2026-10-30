"""rerank_v2: record-aware ranking (phase-13-followups §17, F5.2). Deterministic; no model.

rerank_v1 scores each chunk on its own. About half of the dev targets missed at 10 are
already in the reranked pool while their record (document, ticket, chat thread) is not in
the top 10: nothing asks whether a record *as a whole* answers the question. rerank_v2
does, after rerank_v1, over the pool the channels already returned -- it adds and
removes nothing:

* **Coverage.** One statement over the pooled chunk IDs returns, per chunk, the
  question's content lexemes it contains (P13.3's definition: question-framing words and
  the names of the visible accounts named do not count). It carries the tenant,
  lifecycle and authorization conditions like every retrieval statement.
* **Complementary coverage** of record *d*: ``gain(d) = cov(d) - cov_best(d)``, the
  share of the question's content lexemes *d*'s pooled chunks cover together minus the
  share its best-ranked chunk covers alone. 0 for a record with one pooled chunk, and
  for a question with fewer than two content lexemes.
* ``soft``: every chunk of *d* is multiplied by ``1 + w * gain(d)`` (``w`` at most 0.5)
  and the pool re-sorted; a record's own order is kept.
* ``interleave``: records ordered by ``best(d) * (1 + w * gain(d))``, then their chunks
  taken in turns (every record's best, then every record's second, ...).
* ``record_within``: inside a record, a chunk adding a content lexeme its higher-ranked
  siblings lack moves ahead of one adding none; the record's best chunk stays first and
  the record keeps the positions it had, so no other record moves.

Selection (``select_evidence``) is unchanged.
"""

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from sqlalchemy import Select, any_, literal, select
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz import AuthorizationContext
from lattice.retrieval.evidence import EvidenceUnavailable, QuestionTerms, coverage_columns
from lattice.retrieval.models import RankedCandidate, RetrievalConfig
from lattice.retrieval.permitted import (
    CHANNEL_OPTION,
    CHUNKS,
    ResolvedScope,
    chunks_with_documents,
    permitted_chunk_conditions,
)

RecordKey = tuple[uuid.UUID, uuid.UUID]  # (organization, document)


def coverage_statement(
    ctx: AuthorizationContext, chunk_ids: Sequence[uuid.UUID], terms: QuestionTerms
) -> Select[tuple[Any, ...]]:
    """Per pooled chunk, the question's content lexemes it contains -- lexemes of the
    question only, never chunk text."""
    return (
        select(CHUNKS.c.id.label("chunk_id"), *coverage_columns(terms))
        .select_from(chunks_with_documents(ctx))
        .where(
            *permitted_chunk_conditions(ctx, ResolvedScope()),
            CHUNKS.c.id == any_(literal(sorted(chunk_ids), ARRAY(UUID(as_uuid=True)))),
        )
        .execution_options(**{CHANNEL_OPTION: "record_coverage"})
    )


async def pool_coverage(
    session: AsyncSession,
    ctx: AuthorizationContext,
    ranked: Sequence[RankedCandidate],
    terms: QuestionTerms,
) -> tuple[dict[uuid.UUID, frozenset[str]], tuple[str, ...]]:
    """Each pooled chunk's content lexemes, and the question's. A pooled chunk that
    does not come back through the predicate aborts the request, as in hydration."""
    if not ranked:
        return {}, ()
    ids = [r.fused.candidate.chunk_id for r in ranked]
    rows = {row.chunk_id: row for row in await session.execute(coverage_statement(ctx, ids, terms))}
    if set(rows) != set(ids):
        raise EvidenceUnavailable()
    question = tuple(next(iter(rows.values())).question_terms)
    return {cid: frozenset(row.terms_matched) for cid, row in rows.items()}, question


def _record(r: RankedCandidate) -> RecordKey:
    c = r.fused.candidate
    return (c.organization_id, c.document_id)


def record_gains(
    ranked: Sequence[RankedCandidate],
    matched: Mapping[uuid.UUID, frozenset[str]],
    question: Sequence[str],
) -> dict[RecordKey, float]:
    """``gain(d)`` for every record in the pool (``ranked`` in rerank_v1 order)."""
    terms = frozenset(question)
    members: dict[RecordKey, list[RankedCandidate]] = defaultdict(list)
    for r in ranked:
        members[_record(r)].append(r)
    if len(terms) < 2:
        return dict.fromkeys(members, 0.0)
    gains: dict[RecordKey, float] = {}
    for key, chunks in members.items():
        found = [matched.get(c.fused.candidate.chunk_id, frozenset()) & terms for c in chunks]
        best, union = found[0], frozenset().union(*found)
        gains[key] = (len(union) - len(best)) / len(terms)
    return gains


def _within(
    order: list[RankedCandidate], matched: Mapping[uuid.UUID, frozenset[str]]
) -> list[RankedCandidate]:
    """Inside each record, chunks adding a new lexeme first (best chunk fixed), each
    record keeping its positions."""
    positions: dict[RecordKey, list[int]] = defaultdict(list)
    members: dict[RecordKey, list[RankedCandidate]] = defaultdict(list)
    for i, r in enumerate(order):
        positions[_record(r)].append(i)
        members[_record(r)].append(r)
    out = list(order)
    for key, chunks in members.items():
        head, *rest = chunks
        covered = set(matched.get(head.fused.candidate.chunk_id, frozenset()))
        adding, adding_none = [], []
        for r in rest:
            lexemes = matched.get(r.fused.candidate.chunk_id, frozenset())
            if lexemes - covered:
                adding.append(r)
                covered |= lexemes
            else:
                adding_none.append(r)
        for i, r in zip(positions[key], [head, *adding, *adding_none], strict=True):
            out[i] = r
    return out


def rerank_records(
    ranked: Sequence[RankedCandidate],
    matched: Mapping[uuid.UUID, frozenset[str]],
    question: Sequence[str],
    config: RetrievalConfig,
) -> list[RankedCandidate]:
    """The pool in rerank_v2 order. Each candidate's features gain ``record_gain``;
    ``final_score`` is the lifted score (soft) and ``final_rank`` the new position."""
    gains = record_gains(ranked, matched, question)
    w = config.record_weight
    lifted = {
        r.fused.candidate.chunk_id: r.final_score * (1 + w * gains[_record(r)]) for r in ranked
    }
    if config.record_mode == "soft":
        order = sorted(ranked, key=lambda r: (-lifted[r.fused.candidate.chunk_id], r.final_rank))
    elif config.record_mode == "interleave":
        members: dict[RecordKey, list[RankedCandidate]] = defaultdict(list)
        for r in ranked:
            members[_record(r)].append(r)
        records = sorted(
            members.values(),
            key=lambda m: (-lifted[m[0].fused.candidate.chunk_id], m[0].final_rank),
        )
        depth = max((len(m) for m in records), default=0)
        order = [m[i] for i in range(depth) for m in records if i < len(m)]
    else:
        order = list(ranked)
    if config.record_within:
        order = _within(order, matched)
    soft = config.record_mode == "soft"
    return [
        replace(
            r,
            features={**r.features, "record_gain": round(gains[_record(r)], 6)},
            final_score=lifted[r.fused.candidate.chunk_id] if soft else r.final_score,
            final_rank=position,
        )
        for position, r in enumerate(order, start=1)
    ]
