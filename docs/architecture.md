# Lattice — Architecture

Status: Phase 1 implemented (config, db, errors, logging, identity/sessions, orgs, API
skeleton, seed CLI); Phase 2 implemented `authz` (no HTTP surface yet); Phase 3 the
dataset tooling; Phase 4 sources, connectors, ingestion and providers; Phase 5
`retrieval` and the retrieval half of `evals` (CLI only); Phase 6 `answering`, `tracing`,
generation providers, provider configuration and credentials by reference, the Ask,
evidence, trace and admin-provider endpoints, and `lattice eval answer`; Phase 7 the web
app (`apps/web`); Phase 8 the job queue and background worker (`jobs`, `lattice worker`,
ADR-013), Sources and Activity endpoints and pages, health/readiness, metrics and the
operational limits of §8; Phase 9 `access`; Phase 10 OIDC sign-in (`identity.oidc`), SCIM
provisioning (`provisioning`), rate limits (`ratelimit`) and operator identity commands
(ADR-015).
Later-phase modules below are still design; the ✔ marks in §3 say which.
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
  broker: operators sync with the CLI; syncs requested from the web app (Phase 8) are
  rows of a Postgres `jobs` table that `lattice worker` claims with
  `FOR UPDATE SKIP LOCKED`, leases and bounded retries. The worker has its own runtime
  role, `lattice_worker`, with exactly the privileges a sync needs (ADR-013).

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
| `identity` ✔  | Users, external identities, server-side sessions, identity providers: development persona; OIDC with PKCE and organization-scoped mapping (`oidc`, Phase 10); operator actions on connections, SCIM tokens and the administrator capability (`admin`) | db, access (audit)          |
| `provisioning` ✔ | SCIM 2.0 `Users` (Phase 10, D-20): representation, filters and PATCH (`scim`, pure), the database side (`service`: owned users, least-access memberships, directory identities, lifecycle with session revocation, audit), bearer tokens (`tokens`) | access (audit, snapshots), db |
| `ratelimit` ✔ | Fixed-window limits in PostgreSQL per membership/token, in-process concurrency and pre-authentication client limits; `RateLimited` → 429 (Phase 10) | db, metrics |
| `orgs` ✔      | Organizations, memberships, departments, accounts, account assignments (models)           | db                          |
| `seed` ✔      | Manifest model + idempotent upsert of orgs, users, memberships and admin-defined access groups (by stable key, D-9) | db, orgs, identity, authz (group models only) |
| `dataset` ✔   | Synthetic dataset tooling (Phase 3): loaders for the three raw export formats, the expected-access oracle (reference policy over fixture metadata), `lattice dataset lint`, the deterministic distractor generator. Never touches the database | authz (public API), seed (manifest model) |
| `authz` ✔     | `AuthorizationContext` + resolver, reference policy, SQL predicate, account visibility, explanations, E4 tripwire, `POLICY_VERSION`; owns the access tables (`access_groups`, `access_group_members`, `documents` access record, `document_grants`) | orgs, db, errors, log |
| `sources` ✔   | Lattice-side source configuration (`sources.yaml`) models and loader (Phase 4). Source rows, runs and failures are written by `ingestion.store`. `status`: what members and administrators may know about sources and failures (security-model §10; Phase 8) | authz (public API), jobs |
| `connectors` ✔ | Connector contract, registry + three connectors (Markdown documents, ticket JSONL, Slack export) (Phase 4) | — (pure: stdlib, Pydantic, PyYAML; a test enforces it) |
| `ingestion` ✔ | Canonical text and hashing, chunking, fail-closed access mapping, embedding input, persistence (`store`: the one writer of document access columns, source grants and source groups), sync pipeline, deletion sweep, source disconnect (Phase 4) | connectors, sources, providers, db |
| `providers` ✔ | `EmbeddingProvider` Protocol; adapters: fake + recording (Phase 4), OpenAI (Phase 4), local ONNX model + pinned weights cache, Gemini (Phase 5 closure); the AI egress policy / invocation rule (`egress`, pure) and the reader of each organization's provider configuration (`records`); Phase 6: `GenerationProvider` (fake, scripted, recording, Ollama, Gemini), the factory (`declare` without secrets, `build` after the rule), secret references (`secrets`), audited configuration (`configuration`) | config, db (records, configuration) |
| `admin_grants` ✔ | Applies `admin-grants.yaml` as admin-origin grants after ingestion (D-12); audits each grant added or pruned (Phase 9) | db, access (audit) |
| `access` ✔ | Access management (Phase 9, ADR-014): read models for administrators (`directory`: members, a member's access, the assignable directory, documents the viewer can read, group display rules), previews (`preview`: effects as rules, counts only over the viewer's readable documents), audited changes and grants (`changes`), the audit view (`audit`), explanations (`explain`) | authz (public API), orgs, identity, sources, tracing, db |
| `retrieval` ✔ | Query model and analysis; exact, lexical and semantic channels (each one statement with the authz predicate, tenant and lifecycle conditions); query-side AI egress and embedding-space checks; RRF, `rerank_v1`, selection; evidence hydration; E4 tripwire; trace (Phase 5, `retrieval-design.md` Part I) | authz, providers, ingestion (chunk model), orgs, sources, db |
| `answering` ✔ | Prompt construction (`prompt`), the invocation rule for generation (`policy`), claim-level validation (`validate`), deterministic assembly of statuses, freshness, supersession, potential conflicts and notices (`assemble`), the Ask service (`service`), evidence lookup (`evidence`) (Phase 6) | retrieval, providers, authz (public API), tracing |
| `tracing` ✔   | `query_traces` and `audit_events` models; one INSERT per Ask; re-authorized trace reads (asker / administrator operational / 404) (Phase 6); Activity lists (own questions; administrators' operational rows) (Phase 8) | authz, retrieval, db        |
| `jobs` ✔      | The job queue (`jobs` table): sync requests from the API (`queue`, as `lattice_app`) and the worker loop that claims, leases, runs and retires them (`worker`, as `lattice_worker`) (Phase 8, ADR-013) | ingestion, sources, db |
| `metrics` ✔   | Prometheus metrics with closed label sets, served on a separate listener (Phase 8) | config, log |
| `api` ✔       | FastAPI app, routers (thin), request dependencies (session → identity → org → authz ctx); Phase 6: `POST /v1/ask`, `GET /v1/evidence/{id}`, `GET /v1/traces/{id}`, `GET /v1/admin/providers`; Phase 8: `GET /v1/health/live`, `GET /v1/health/ready`, `GET /v1/sources`, `GET /v1/admin/sources`, `GET /v1/admin/sources/{id}/failures`, `POST /v1/admin/sources/{id}/sync`, `GET /v1/activity`, `GET /v1/admin/activity`; Phase 9: `GET /v1/accounts`, `/v1/admin/access/*` (members, directory, member, preview, change, documents, explanation, grants, revoke), `GET /v1/admin/audit-events`; Phase 10: `GET /v1/auth/oidc/{login_key}/start`, `…/callback`, `/scim/v2/*` (bearer token; not in OpenAPI) | all service modules         |
| `evals` ✔     | Retrieval + security runner (Phase 5); answer + security runner with an optional uncalibrated local judge (Phase 6) | retrieval, answering, dataset |
| `cli` ✔ | `seed`, `check-db-role`, `dataset`, `sync`, `reindex`, `source disconnect`, `apply-admin-grants`, `search` and `eval retrieval` (Phase 5, development/test only: they act as a member), `models list/fetch/verify` and `provider approve` (Phase 5 closure); `ask`, `eval answer`, `provider …`, `org egress` (Phase 6); `worker` (Phase 8); `sso add/show/disable`, `scim token create/list/revoke`, `member admin` (Phase 10) | services                    |

✔ = implemented (Phases 1–5). Alembic migrations live in `backend/alembic/`, outside the
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
  (ingestion; access management since Phase 9) get an explicit, reviewed allowance in
  that test.
- `answering` receives `EvidenceChunk`s, never database sessions. It cannot fetch more
  content than retrieval gave it.
- Route handlers parse input, call one service function, and map the result. No SQL,
  no policy, no prompt text in `api`.
- Provider *adapters* (`fake`, `local`, `gemini`, `openai`, later generation) know
  nothing about organizations, users or authorization: they receive text and return
  text/vectors, and each maps `purpose` to its own query/document semantics. Whether an
  adapter may be called is decided outside it, by `providers.egress` over the
  organization's recorded configuration (`providers.records`), never by the adapter
  or its credential. Retrieval and ingestion contain no provider-specific code.

**Model providers (Phase 5 closure; ADR-008, ADR-009, ADR-010, ADR-011)**

| Provider | Deployment | External egress | Current purpose |
| -------- | ---------- | --------------- | --------------- |
| Fake | test | no (declared `external_api` for policy tests) | CI and tests |
| Local (`mxbai-embed-large-v1`, ONNX Runtime) | `local` | no | real zero-cost / self-hosted / offline deployments |
| Gemini (`gemini-embedding-2`, 1024 dims) | `external_api` | yes | optional external provider / BYOK; unpaid key = synthetic evaluation only |
| OpenAI (`text-embedding-3-small`, 1024 dims) | `external_api` | yes | optional managed / BYOK provider; real evaluation not run |
| Organization-controlled endpoint (future) | `organization_controlled` | to the organization's endpoint | customer-owned model infrastructure; explicit organization policy required |
| Fake generation (Phase 6) | declared `external_api` | no | CI and tests; never an answer-quality figure |
| Ollama generation (`qwen3.5:4b`, Phase 6, ADR-012) | `local` on loopback, otherwise `organization_controlled` | no (loopback) | zero-cost / self-hosted generation; real-model evaluation not run (M1 8 GB) |
| Gemini generation (`gemini-3.5-flash-lite`, Phase 6, ADR-012) | `external_api` | yes | organization BYOK; unpaid key = synthetic data only |

The trust classes are those of D-15 (`local`, `organization_controlled`,
`external_api`; anything else is `unknown` and receives nothing). The sensitivity each
may receive comes from the approval and egress policy (security-model §6), never from
this table. Credentials are process-wide environment
variables for the operator CLI's embedding providers; generation resolves each
organization's configuration and its credential *reference* per request (Phase 6,
ADR-011). Lattice runs with none of them.

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
 5. retrieval: analyze query (identifiers, visible-account mentions, temporal intent);
    scope account must be visible; query egress decision (embed concurrently or not)
 6. retrieval: authz compiles the readable-documents predicate into every statement
 7. retrieval: exact identifier ─┐  each one SQL statement with the predicate, the
    retrieval: lexical search    ├  tenant and status='active' conditions; forbidden
    retrieval: semantic search  ─┘  rows are never returned; semantic may degrade
 8. authz: invariant check over every candidate's document (abort, never filter)
    retrieval: reciprocal-rank fusion → deterministic rerank → selection → evidence
    lookup (predicate inside)
 9. authz: invariant check — every evidence chunk re-verified with the Python reference
    policy. Any mismatch = hard failure (no generation), security event logged.
    (Steps 5–9 are built in Phase 5: `lattice.retrieval.retrieve`.)
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

As built (Phase 6): steps 3–9 plus the classification of the evidence and the
generation access decision (configuration → `provider_decision` → question sensitivity
→ credential reference) run in one snapshot, which is closed before the model call.
A relevance floor (retrieval-design §13) and the ambiguity rule precede the call. The
provider is built only when a call will happen.
```

Progress events (D-6: SSE, for stages 7–13 so evidence could appear before the answer
text) are not built: Phase 7 shows elapsed-time progress over the single response.
Unvalidated model text is never streamed to the user (ADR-006).

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
| `service_unavailable`          | 503  | Dependency (database) unavailable, unreachable or too slow (connection refused/lost, pool wait, `statement_timeout`), or readiness failed; carries `Retry-After: 5` (Phase 8) |
| `request_too_large`            | 413  | Request body over `LATTICE_MAX_REQUEST_BYTES` (Phase 8); refused before parsing |
| `permission_denied`            | 403  | Missing *capability* (e.g. non-admin on admin route); only where existence is not secret |
| `not_found`                    | 404  | Resource absent **or not readable** — the two are indistinguishable by design |
| `validation_failed`            | 422  | Request failed schema/semantic validation                            |
| `source_unavailable`           | 503  | Connector/source could not be read (sync context)                     |
| `ingestion_failed`             | —    | Recorded per record in sync runs; not an HTTP error                   |
| `retrieval_failed`             | 500  | Search stage error                                                    |
| `provider_unavailable`         | 503  | Embedding/generation provider error or timeout                        |
| `generation_invalid`           | 502  | Model output failed schema validation after retry                     |
| `security_invariant_violated`  | 500  | Pre-generation authz invariant failed; answer withheld; alert (mapped from `AuthorizationInvariantViolation` since Phase 8; metric `lattice_security_invariant_violations_total`) |
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
- **Operational signals (Phase 8).** Health: `/v1/health/live` (process) and
  `/v1/health/ready` (database reachable and schema at the revision this build
  expects, `lattice.db.schema`); both report states only. Metrics: Prometheus on a
  separate listener (`LATTICE_METRICS_ADDRESS`), closed label sets, never an identifier
  or text (`lattice.metrics`). The per-query trace (Postgres) and the structured logs
  remain the detailed record; OpenTelemetry stays deferred (no consumer).
- **Fail closed at startup (Phase 8).** The API and the worker refuse to start on a
  schema at another revision than their code; the API also refuses a role that can
  write documents or grants (so the worker's credential cannot be used by mistake).
- **Bounds (Phase 8).** Per-statement timeout and fast connection failure for runtime
  connections; a total generation deadline per Ask; a request-body limit; one queued or
  running sync per source; bounded job attempts and leases. Details and defaults:
  runbook §7.4.
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
    alembic.ini  alembic/       ✔ 0001–0011
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
  apps/web/                     ✔ Next.js web app (Phase 7): src/app routes, src/components,
                                  src/api (generated types, client, queries), e2e/ (Playwright)
  evals/golden/*.yaml           ✔ golden set v1 (89 items)
  docs/ …
```

Tooling: `uv` for Python (lockfile + interpreter pinning), `pnpm` for Node (Phase 7;
`apps/web/pnpm-lock.yaml`).
