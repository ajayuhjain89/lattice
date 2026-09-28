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
| [008](008-embedding-provider.md) | External embedding provider: OpenAI `text-embedding-3-small` at 1024 dimensions (optional, BYOK); Voyage AI kept compatible | Accepted |
| [009](009-local-embedding-provider.md) | Local/self-hosted embedding provider: `mxbai-embed-large-v1` (1024 native) on ONNX Runtime, pinned weights fetched explicitly | Accepted |
| [010](010-gemini-embedding-provider.md) | Gemini as an optional external embedding provider: `gemini-embedding-2` at 1024; unpaid key = synthetic data only | Accepted |
| [011](011-provider-configuration-byok.md) | Provider configuration, deployment modes and organization BYOK; a credential is never an approval | Accepted (policy built; BYOK storage/UI not yet) |
