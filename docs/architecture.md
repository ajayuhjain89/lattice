# Lattice — Architecture

Status: Foundation draft (Phase 0). Describes the intended system; nothing is built yet.
Related: `data-model.md`, `authorization-model.md`, `retrieval-design.md`,
`trust-boundaries.md`, ADRs 001–007.

---

## 1. Shape of the system

A modular monolith. One Python codebase provides the HTTP API and the ingestion worker;
one Next.js app renders the UI; one PostgreSQL database holds everything, including
vectors and the lexical index.

```
 Browser
   │  HTTPS, same origin (session cookie)
   ▼
 Next.js web app  (apps/web)          ← rendering + client interaction only
   │  /api/* proxied to FastAPI; server components forward the session cookie
   ▼
 FastAPI application  (backend, `lattice` package)
   │   identity · orgs · authz · sources · retrieval · answering · tracing
   │
   ├──► PostgreSQL 16 + pgvector      ← relational data, chunks, embeddings,
   │       (RLS for org isolation)       tsvector index, sync runs, traces
   │
   ├──► Embedding provider  ─┐        ← narrow Protocols; external trust boundary
   └──► Generation provider ─┘
 Ingestion worker / CLI  (same `lattice` package, separate process)
   ├──► Connectors (local exports in the slice)
   ├──► Embedding provider
   └──► PostgreSQL
```

Why this shape (details in ADR-001, ADR-002):

- **One backend language.** Authorization, retrieval and ingestion share types and the
  same policy code. A second (Node) backend would duplicate security-critical logic.
- **One datastore.** Chunks, their embeddings and their lexical index live in the same
  row. Deleting a chunk deletes its vector and its index entry in the same transaction —
  no orphaned vectors in an external store, no dual-write consistency problem, and the
  authorization predicate runs in the same SQL statement as the similarity search.
- **Worker as a process, not a platform.** Ingestion runs in a separate process so it
  cannot block interactive requests. It shares code and database with the API. No
  broker: in the slice, ingestion is triggered by CLI; when the UI needs to trigger
  syncs (Phase 8), a Postgres `jobs` table with `FOR UPDATE SKIP LOCKED` is enough.

Deliberately absent: Redis, Celery, Kafka, Elasticsearch, a vector DB, LangChain /
LangGraph, Kubernetes, microservices. Each has a named reconsideration trigger in
§9.

## 2. Frontend / backend boundary

| Concern                                    | Next.js | FastAPI |
| ------------------------------------------ | :-----: | :-----: |
| Rendering, routing, client state, a11y      | ✔       |         |
| Session cookie issuance / validation        |         | ✔       |
| Authorization decisions (any)               |         | ✔       |
| Retrieval, ranking, generation, citations   |         | ✔       |
| Input validation (authoritative)            |         | ✔ (Next.js may validate for UX only) |
| Formatting dates / relative times for display | ✔     | (API returns ISO-8601 UTC) |

Rules:

- Next.js holds **no secrets** other than the internal API base URL, and **no business
  rules**. If Next.js were fully compromised, it could only do what the logged-in
  user's session can already do.
- Same-origin: `/api/*` is proxied to FastAPI (Next.js rewrites in development, the
  reverse proxy in deployment). Cookies stay first-party; no CORS configuration.
- The FastAPI OpenAPI schema is the canonical HTTP contract. TypeScript types are
  generated with `openapi-typescript`, and the client uses `openapi-fetch` (both tiny,
  no runtime framework). CI fails if generated types are out of date.

## 3. Backend modules

Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x (async, asyncpg), Alembic, pytest.
Package: `backend/src/lattice/`.

| Module        | Owns                                                                                      | Depends on                  |
| ------------- | ----------------------------------------------------------------------------------------- | --------------------------- |
| `config`      | Typed settings from env; fail fast on missing/invalid config at startup                   | —                           |
| `errors`      | Error taxonomy (`LatticeError` + codes), mapping to HTTP                                  | —                           |
| `db`          | Engine, sessions, tenant binding (`SET LOCAL app.org_id`), base model, migrations          | config                      |
| `identity`    | Users, external identities, server-side sessions, identity providers (dev, OIDC later)     | db                          |
| `orgs`        | Organizations, memberships, departments, accounts, account assignments                    | db                          |
| `authz`       | `AuthorizationContext`, reference policy, SQL predicate compiler, access explanations      | orgs, db                    |
| `sources`     | Source records, source config validation, sync runs, sync failures                        | db, authz (admin capability)|
| `connectors`  | Connector contract + three connectors (document, ticket, chat export)                     | — (pure; no DB access)      |
| `ingestion`   | Normalization, chunking, hashing, embedding orchestration, persistence, deletion sweep     | connectors, sources, providers, db |
| `providers`   | `EmbeddingProvider`, `GenerationProvider` Protocols + implementations + fakes              | config                      |
| `retrieval`   | Query analysis, lexical + semantic search (with authz predicate), fusion, rerank, context selection | authz, providers, db |
| `answering`   | Prompt construction, generation, output validation, citation verification, freshness & conflict assembly, refusal logic | retrieval, providers |
| `tracing`     | Trace records, redaction, re-authorized trace reads                                      | authz, db                   |
| `api`         | FastAPI app, routers (thin), request dependencies (session → identity → org → authz ctx)  | all service modules         |
| `evals`       | Golden-set loader, retrieval/generation/security eval runners, reports                    | retrieval, answering        |
| `cli`         | `lattice seed`, `lattice sync`, `lattice search`, `lattice ask`, `lattice eval`           | services                    |

Boundary rules (enforced by review and an import-linter contract):

- `connectors` never touch the database or `authz`. They translate external data into
  typed records. This keeps the untrusted-input surface small and testable.
- Only `authz` constructs authorization predicates. `retrieval` must call
  `authz.readable_documents_predicate(ctx)`; it never writes its own access SQL.
- `answering` receives `EvidenceChunk`s, never database sessions. It cannot fetch more
  content than retrieval gave it.
- Route handlers parse input, call one service function, and map the result. No SQL,
  no policy, no prompt text in `api`.
- `providers` know nothing about organizations, users or authorization; they receive
  text and return text/vectors.

## 4. Request lifecycle: Ask

```
POST /v1/ask {question, scope?}
 1. api: session cookie → Session (DB, not expired, not revoked)
 2. identity: Session → User; org_id comes from the session, never from the request body
 3. db: BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL app.org_id = :org_id
    (RLS confines every query to this org; one snapshot for steps 4–9 so the
    authz context, retrieval and the invariant check see the same permissions)
 4. authz: resolve AuthorizationContext fresh from DB (membership active? clearance,
    account scope, assigned accounts, departments, group memberships)
 5. retrieval: analyze query (identifiers, quoted phrases, account mentions, temporal intent)
 6. retrieval: build RetrievalRequest; authz compiles the readable-documents predicate
 7. retrieval: lexical search  ─┐  both SQL statements include the predicate and
    retrieval: semantic search ─┘  status='active'; forbidden rows are never returned
 8. retrieval: reciprocal-rank fusion → deterministic rerank → context selection
 9. authz: invariant check — every selected chunk re-verified with the Python reference
    policy. Any mismatch = hard failure (no generation), security event logged.
10. answering: AI egress policy — evidence above the effective external level
    (default: internal only) is kept out of the prompt but stays in the user's
    evidence list; if nothing remains → `evidence_only`, no model call.
    (A data-egress control, separate from authorization.)
11. answering: prompt = trusted instructions + delimited question + evidence blocks
    labelled E1..En (no internal IDs)
12. providers: generation call with a JSON schema for the answer
13. answering: claim-level fail-closed validation — a claim survives only if every
    one of its citations has a valid E-handle and a verbatim-verified quote; invalid
    claims trigger one regeneration, then are removed (never shown with the bad
    citation stripped); no valid direct-answer claim → answer withheld
    (`insufficient_evidence`, `claims_unverified`). Attach deterministic freshness,
    supersession / potential-conflict, sensitivity and egress notices; assert every
    visible citation references evidence included in the response
14. tracing: persist trace (IDs, scores, timings, statuses — not document text)
15. api: return Answer
```

The web client receives progress events (Phase 7 decision: SSE) for stages 7–13 so
evidence can appear before the answer text. Unvalidated model text is never streamed
to the user (ADR-006).

## 5. Ingestion lifecycle (summary; full design in `ingestion-design.md`)

`lattice sync <source>` → load source config → connector yields `SourceRecord`s →
validate → normalize to `CanonicalDocument` → map access metadata → compare content
and access hashes → (unchanged: skip) / (access-only change: update access columns) /
(content change: chunk, embed new chunk hashes only, replace chunks atomically) →
deletion sweep for records not seen in a full snapshot (with mass-deletion guard) →
`SourceSyncResult` recorded in `source_sync_runs`.

## 6. Organization isolation (summary; details in `security-model.md` §3)

Three independent layers:

1. **Session-derived org.** The org for a request comes only from the server-side
   session. No endpoint accepts an org ID from the client for data access.
2. **Explicit predicates.** Every service query filters by `organization_id`.
3. **Postgres Row-Level Security.** Every tenant table has an RLS policy
   `organization_id = current_setting('app.org_id')::uuid`. The application connects
   as a non-owner role without `BYPASSRLS`. A forgotten filter returns zero rows
   instead of another org's rows.

Migrations and the seed CLI run as the owner role; the API and worker never do.

## 7. Error taxonomy

All failures surfaced by the API use one envelope:

```json
{ "error": { "code": "insufficient_evidence", "message": "…safe, user-facing…", "trace_id": "…" } }
```

| Code                           | HTTP | Meaning / notes                                                       |
| ------------------------------ | ---- | --------------------------------------------------------------------- |
| `authentication_required`      | 401  | No/expired/revoked session                                            |
| `permission_denied`            | 403  | Missing *capability* (e.g. non-admin on admin route); only where existence is not secret |
| `not_found`                    | 404  | Resource absent **or not readable** — the two are indistinguishable by design |
| `validation_failed`            | 422  | Request failed schema/semantic validation                            |
| `source_unavailable`           | 503  | Connector/source could not be read (sync context)                     |
| `ingestion_failed`             | —    | Recorded per record in sync runs; not an HTTP error                   |
| `retrieval_failed`             | 500  | Search stage error                                                    |
| `provider_unavailable`         | 503  | Embedding/generation provider error or timeout                        |
| `generation_invalid`           | 502  | Model output failed schema validation after retry                     |
| `security_invariant_violated`  | 500  | Pre-generation authz invariant failed; answer withheld; alert         |
| `internal_error`               | 500  | Anything else; never leaks internals                                  |

`insufficient_evidence` (reasons `no_relevant_evidence`, `claims_unverified`),
`evidence_only` (reason `egress_policy`), `partial` and `ambiguous` are **answer
statuses** (HTTP 200 with a structured `Answer`), not errors: they are expected product
outcomes, and the UI renders them as designed states. Unverifiable citations are
never an HTTP error that leaks partial text; they resolve to one of these statuses.
`citation_validation_failed` exists only as an internal trace error code for the
response-invariant bug case (`retrieval-design.md` §9 step 10).

## 8. Cross-cutting

- **Configuration.** `pydantic-settings`; required variables validated at import;
  process exits non-zero with a precise message. `LATTICE_ENV ∈ {development, test,
  production}`; development-only features (dev login, fake providers) refuse to start
  when `LATTICE_ENV=production`.
- **Logging.** Structured JSON (`structlog` or stdlib `logging` with a JSON formatter —
  decided in Phase 1 by what is smaller). Logs carry IDs, counts, durations and codes;
  never question text, document text, embeddings, or provider payloads.
- **Time.** All timestamps `timestamptz`, stored UTC; the API emits ISO-8601 UTC.
- **IDs.** UUIDs (`gen_random_uuid()`), except chunk IDs which are deterministic
  (`uuid5(document_id, chunk_content_hash, occurrence)`) so unchanged chunks keep their
  IDs — and their embeddings — across re-ingestion.
- **Async.** API and worker are async. The concrete benefit: an Ask spends most of its
  time waiting on the provider; lexical search and the query-embedding call run
  concurrently.

## 9. Things we are deliberately not adding — and when we would

| Technology              | Reconsider when                                                                     |
| ----------------------- | ----------------------------------------------------------------------------------- |
| Dedicated vector DB     | Per-org corpus > ~5M chunks or filtered ANN p95 > 300 ms after HNSW + iterative scan tuning |
| Elasticsearch/OpenSearch| Postgres FTS fails measurable search-quality needs (e.g. multilingual, fuzzy IDs) that `pg_trgm` or a second tsvector config can't fix |
| Redis                   | A measured hot path needs a shared cache, or rate limiting across multiple API instances |
| Celery / broker queue   | Job throughput or scheduling needs exceed a Postgres jobs table (thousands of jobs/min) |
| LangChain / LangGraph   | Never for the core Ask path (fixed pipeline). Possibly for future multi-step agent features |
| Separate services       | Independent scaling or ownership boundaries become real (e.g. ingestion needs 10× the API's compute) |
| Kubernetes              | Multiple services with independent scaling; not before                               |

## 10. Repository layout (target)

```
lattice/
  README.md
  compose.yaml                  # postgres + pgvector for local dev (only infra)
  Makefile                      # thin wrappers: setup, db-up, migrate, seed, test, eval, gen-api
  .env.example
  backend/
    pyproject.toml  uv.lock
    alembic/  alembic.ini
    src/lattice/…               # modules in §3
    tests/  unit/ integration/ security/
  apps/web/
    package.json  pnpm-lock.yaml  tsconfig.json (strict)
    src/app/…  src/components/…  src/styles/tokens.css  src/api/schema.d.ts (generated)
  data/synthetic/
    halden/  veridian/          # manifests + document/ticket/chat exports
  evals/
    golden/*.yaml               # golden questions
  docs/ …
```

Tooling choices: `uv` for Python (lockfile + interpreter pinning, one tool), `pnpm` for
Node (strict, fast, lockfile). Both are reversible.
