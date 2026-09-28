# Lattice — Architecture

Status: Phase 1 implemented (config, db, errors, logging, identity/sessions, orgs, API
skeleton, seed CLI); Phase 2 implemented `authz` (no HTTP surface yet). Later-phase
modules below are still design; the ✔ marks in §3 say which.
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
| `config` ✔    | Typed settings from env; fail fast on missing/invalid config at startup                   | —                           |
| `errors` ✔    | Error taxonomy (`LatticeError` + codes), mapping to HTTP                                  | —                           |
| `log` ✔       | JSON log formatter with redaction; `log_event` helper                                     | —                           |
| `db` ✔        | Engine, tenant-bound transactions (`set_config('app.org_id', …, true)`), base model, runtime-role safety check | config |
| `identity` ✔  | Users, external identities, server-side sessions, identity providers (dev now, OIDC later) | db                          |
| `orgs` ✔      | Organizations, memberships, departments, accounts, account assignments (models)           | db                          |
| `seed` ✔      | Manifest model + idempotent upsert of orgs, users, memberships and admin-defined access groups (by stable key, D-9) | db, orgs, identity, authz (group models only) |
| `dataset` ✔   | Synthetic dataset tooling (Phase 3): loaders for the three raw export formats, the expected-access oracle (reference policy over fixture metadata), `lattice dataset lint`, the deterministic distractor generator. Never touches the database | authz (public API), seed (manifest model) |
| `authz` ✔     | `AuthorizationContext` + resolver, reference policy, SQL predicate, account visibility, explanations, E4 tripwire, `POLICY_VERSION`; owns the access tables (`access_groups`, `access_group_members`, `documents` access record, `document_grants`) | orgs, db, errors, log |
| `sources` ✔   | Lattice-side source configuration (`sources.yaml`) models and loader (Phase 4). Source rows, runs and failures are written by `ingestion.store` | — |
| `connectors` ✔ | Connector contract, registry + three connectors (Markdown documents, ticket JSONL, Slack export) (Phase 4) | — (pure: stdlib, Pydantic, PyYAML; a test enforces it) |
| `ingestion` ✔ | Canonical text and hashing, chunking, fail-closed access mapping, embedding input, persistence (`store`: the one writer of document access columns, source grants and source groups), sync pipeline, deletion sweep, source disconnect (Phase 4) | connectors, sources, providers, db |
| `providers` ✔ | `EmbeddingProvider` Protocol, fake + recording + OpenAI (Phase 4), AI egress policy; `GenerationProvider` in Phase 6 | config |
| `admin_grants` ✔ | Applies `admin-grants.yaml` as admin-origin grants after ingestion (D-12) | db |
| `retrieval`   | Query analysis, lexical + semantic search (with authz predicate), fusion, rerank, context selection | authz, providers, db |
| `answering`   | Prompt construction, generation, output validation, citation verification, freshness & conflict assembly, refusal logic | retrieval, providers |
| `tracing`     | Trace records, redaction, re-authorized trace reads                                      | authz, db                   |
| `api`         | FastAPI app, routers (thin), request dependencies (session → identity → org → authz ctx)  | all service modules         |
| `evals`       | Golden-set loader, retrieval/generation/security eval runners, reports                    | retrieval, answering        |
| `cli` ✔ (partial) | `lattice seed`, `lattice check-db-role` now; later `sync`, `search`, `ask`, `eval`   | services                    |

✔ = implemented (Phases 1–2). Alembic migrations live in `backend/alembic/`, outside the
package. Boundary rules (enforced by review; the `authz` rule is also enforced
mechanically since Phase 2):

- `connectors` never touch the database or `authz`. They translate external data into
  typed records. This keeps the untrusted-input surface small and testable.
- Only `authz` constructs authorization predicates. `retrieval` must call
  `authz.readable_documents_predicate(ctx)`; it never writes its own access SQL.
  Enforced by `backend/tests/test_authz_boundary.py`, an AST check over every module
  outside `lattice/authz/`: imports only from `lattice.authz`'s public `__all__`, and
  no attribute, keyword argument or SQL string naming `sensitivity`, `access_mode`,
  `department_ids`, `principal_type` or the grant/group tables. This replaces the
  planned import-linter contract, which could only see imports — not
  `Document.sensitivity == "internal"` written in retrieval. Writers of access state
  (ingestion) get an explicit, reviewed allowance in that test.
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

`lattice sync --org … --sources sources.yaml` (implemented, Phase 4) → validate the
source configuration → connector yields `SourceRecord`s → normalize to a
`CanonicalDocument` → map access metadata (fail closed; quarantine on failure) →
compare content and access hashes → (unchanged: nothing written) / (access-only change:
access columns and source grants; vectors removed if the new classification is above
the egress level) / (content change: chunk, embed only changed inputs under the egress
policy, replace chunks in one transaction) → guarded deletion sweep for records not
seen in a full snapshot → counts and failures in `source_sync_runs` /
`sync_record_failures`. Admin grants are applied separately (`lattice
apply-admin-grants`).

## 6. Organization isolation (summary; details in `security-model.md` §3)

Three independent layers:

1. **Session-derived org.** The org for a request comes only from the server-side
   session. No endpoint accepts an org ID from the client for data access.
2. **Explicit predicates.** Every service query filters by `organization_id`.
3. **Postgres Row-Level Security.** Every application table has RLS enabled and
   forced; tenant tables use `organization_id = lattice_current_org_id()`. The
   application connects as `lattice_app` — not an owner, no `BYPASSRLS` — and refuses
   to start otherwise. A forgotten filter returns zero rows instead of another org's
   rows. Exact policies, roles and grants: `data-model.md` §8.

Implementation (Phase 1): `lattice.db.engine.Database.tenant_transaction(org_id)` opens
a transaction and binds the organization with `set_config('app.org_id', …, true)`
(transaction-local, so it cannot leak through the connection pool); the org ID must be
a `uuid.UUID` from the server-side session. `unscoped_transaction()` exists only for
health checks and the SECURITY DEFINER pre-authentication lookups, and sees no tenant
rows. Migrations and the seed CLI run as `lattice_owner`; the API never does.
`tenant_snapshot(org_id)` (Phase 2) is the same binding in a REPEATABLE READ, READ
ONLY transaction: the one snapshot that Ask steps 3–9 share.

## 7. Error taxonomy

All failures surfaced by the API use one envelope:

```json
{ "error": { "code": "validation_failed", "message": "…safe, user-facing…",
             "request_id": "…", "details": [{ "field": "subject", "issue": "string_too_long" }] } }
```

`request_id` (Phase 1 rename from the draft's `trace_id`) is a server-generated
correlation ID, also returned as the `X-Request-ID` header and present in every log
line for the request; query traces (Phase 6) have their own `trace_id`. `details` is
present only for validation failures and names the field and the pydantic error type —
never the submitted value. `message` is a fixed sentence per code; exception text,
SQL and paths never reach a response.

| Code                           | HTTP | Meaning / notes                                                       |
| ------------------------------ | ---- | --------------------------------------------------------------------- |
| `authentication_required`      | 401  | No/expired/idle/revoked session, or inactive user/membership/organization — all identical |
| `authentication_failed`        | 401  | Sign-in attempt failed (generic: never says why)                      |
| `csrf_rejected`                | 403  | State-changing request without CSRF proof                             |
| `method_not_allowed`           | 405  | Route exists, method does not                                         |
| `service_unavailable`          | 503  | Dependency (database) unavailable                                     |
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
- **Logging.** Structured JSON via stdlib `logging` and a small custom formatter
  (`lattice.log`; decided in Phase 1 — no extra dependency). One `http.request` line
  per request (method, path, status, duration, request ID; never query strings,
  headers, cookies or bodies); uvicorn's access log is disabled. Sensitive field names
  are redacted and credential-shaped values (URL passwords, bearer tokens, session
  cookies) scrubbed as a safety net. Outside development, exceptions are logged as
  type + stack frames only, because driver exceptions embed SQL parameters. Logs carry
  IDs, counts, durations and codes; never question text, document text, embeddings, or
  provider payloads.
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

## 10. Repository layout

Implemented in Phase 1 (✔) and planned:

```
lattice/
  README.md                     ✔ verified local setup
  compose.yaml                  ✔ Postgres 16 + pgvector 0.8.1 only (bound to 127.0.0.1)
  Makefile                      ✔ setup, db-up/down/bootstrap/destroy, migrate, seed, api,
                                  test, lint, format, typecheck, check-db-role,
                                  dataset-lint, dataset-generate, sync, sync-dry-run,
                                  admin-grants
  .env.example                  ✔ documented local-only placeholders
  .github/workflows/ci.yml      ✔ lint, mypy, dataset lint, bootstrap, migration round-trip,
                                  seed, dataset sync (twice), tests
  infra/postgres/               ✔ superuser bootstrap: roles, databases, extensions
  backend/
    pyproject.toml  uv.lock     ✔ (Python 3.12 pinned in .python-version)
    alembic.ini  alembic/       ✔ 0001–0005
    src/lattice/                ✔ config, errors, log, db/, identity/, orgs/, authz/, dataset/,
                                  sources/, connectors/, ingestion/, providers/,
                                  admin_grants, api/, seed, cli
    tests/                      ✔ one module per concern, all against real PostgreSQL;
                                  tests/security/ holds the authorization suites (Phase 2);
                                  tests/dataset/ validates the synthetic dataset (Phase 3);
                                  tests/ingestion/ covers connectors and ingestion (Phase 4)
  data/synthetic/               ✔ README (ground truth for maintainers), canaries.yaml
    halden/                     ✔ org.yaml, sources.yaml, admin-grants.yaml, ground-truth.yaml,
                                  documents/ (Markdown), tickets/ (JSONL), slack/ (export)
    veridian/                   ✔ isolation twin, same shape
  apps/web/                     Phase 7
  evals/golden/*.yaml           ✔ golden set v1 (89 items)
  docs/ …
```

Tooling: `uv` for Python (lockfile + interpreter pinning), `pnpm` for Node (Phase 7).
