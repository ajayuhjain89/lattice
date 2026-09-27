# ADR-002: PostgreSQL + pgvector + full-text search as the single datastore

Status: Proposed (Phase 0)

## Context
Retrieval must apply authorization inside the query, support exact identifiers and
semantic search, and delete content completely when sources change. Corpus sizes
per organization in the first release are thousands to low hundreds of thousands of
chunks.

## Decision
Store documents, chunks, embeddings (`vector` column) and the lexical index
(`tsvector` generated column) in PostgreSQL 16 with pgvector ≥ 0.8. No separate vector
database or search engine.

## Alternatives considered
- **Pinecone/Weaviate/Qdrant + Postgres:** dual writes; permission metadata duplicated
  into the vector store; deletion and permission changes must propagate to two
  systems; metadata filters in a second system become a second authorization
  implementation.
- **Elasticsearch/OpenSearch (BM25 + kNN):** strong search features, but another
  cluster to run, and again a second copy of access metadata.

## Why this option
One transaction updates content, vectors, lexical index and access together. The
authorization predicate is a SQL expression in the same statement as the similarity
search. Deleting a chunk row deletes its vector and index entry — no orphans. RLS
provides structural tenant isolation for all of it.

## Consequences
- Start with exact vector scans (perfect recall under filters); add HNSW with
  iterative scans when latency requires.
- FTS is less featureful than dedicated engines (no BM25 by default, basic fuzzy);
  measured via the golden set.
- Embedding dimension is fixed per deployment; changing models is a tracked re-embed.

## Reconsider when
Per-org corpora reach millions of chunks with filtered ANN p95 > 300 ms after tuning,
or search-quality needs (multilingual, fuzzy IDs) exceed what Postgres + `pg_trgm` can
provide, as shown by eval failures.
