# Architecture Decision Records

Lightweight records for decisions that are expensive to reverse. Trivial choices are
not recorded here. Status values: Proposed · Accepted · Superseded.

| ADR | Title | Status |
| --- | ----- | ------ |
| [001](001-fastapi-modular-monolith.md) | FastAPI modular monolith with a rendering-only Next.js frontend | Proposed |
| [002](002-postgres-pgvector-single-datastore.md) | PostgreSQL + pgvector + FTS as the single datastore | Proposed |
| [003](003-authorization-inside-retrieval.md) | Authorization compiled into retrieval SQL; RLS for tenant isolation; pre-generation invariant | Proposed |
| [004](004-hybrid-retrieval-rrf-deterministic-rerank.md) | Hybrid retrieval with RRF and a deterministic first reranker | Proposed; implemented in Phase 5 (lexical query change noted) |
| [005](005-server-side-sessions.md) | Server-side sessions; dev identity for the slice; OIDC before deployment | Proposed |
| [006](006-structured-answers-verified-citations.md) | Structured answers with quote-verified citations; no streaming of unvalidated text | Proposed |
| [007](007-single-turn-ask.md) | Single-turn Ask; no conversational memory in the first release | Proposed |
| [008](008-embedding-provider.md) | Embedding provider for the first slice: OpenAI `text-embedding-3-small` at 1024 dimensions; Voyage AI kept compatible and re-evaluated after a real-provider retrieval evaluation (blocked in Phase 5) | Accepted |
