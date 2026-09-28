# ADR-004: Hybrid retrieval with reciprocal-rank fusion and a deterministic first reranker

Status: Proposed (Phase 0); implemented in Phase 5 as decided, with the lexical query
change recorded below.

## Context
Questions mix exact tokens (customer names, ticket IDs, quoted phrases, dates) and
paraphrase. Ranking must be explainable in traces and tunable against a golden set.

## Decision
Run PostgreSQL FTS and pgvector similarity in parallel (plus an exact identifier
path), fuse with Reciprocal Rank Fusion (k = 60), then apply a deterministic,
versioned reranker with a few bounded features (account match, authority, recency
under temporal intent, identifier match). The reranker sits behind a Protocol.

## Alternatives considered
- **Semantic only:** fails on IDs, names, quoted phrases.
- **Weighted score blending:** requires normalizing incomparable scores; brittle.
- **Cross-encoder / LLM reranker from day one:** extra latency, cost and egress before
  evidence shows ordering (not recall) is the problem.

## Why this option
Robust to score-scale differences; every stage is inspectable; cheap; clear upgrade
path.

## Consequences
Weights are heuristics until tuned; the eval harness must report per-category effects
of any weight change.

## Reconsider when
Evals show candidate recall is high but ordering (MRR, context recall) is poor — the
signature of a reranker problem.

## As built (Phase 5)

Three channels (exact identifier, FTS, pgvector exact scan), RRF with k = 60 over all
three, fused pool 40, `rerank_v1` with the four bounded features and the design's
weights (untuned), behind plain functions rather than a `Reranker` Protocol (one
implementation; the Protocol arrives with a second reranker). One change from the
design, made on evidence: the lexical query matches any of the question's lexemes and
ranks by coverage instead of `websearch_to_tsquery`'s all-terms match, which returned
no lexical candidate for 63 of 89 golden questions (`evaluation-plan.md` §9). The
"reconsider when" condition (high candidate recall, poor ordering) cannot be judged
until the real-provider evaluation runs.
