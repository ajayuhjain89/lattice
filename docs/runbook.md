# Lattice — Operational Runbook

Status: §1 is implemented (Phases 1–2, worker role Phase 8); §2 procedures are marked
per row; §3 (embedding) Phase 4, provider configuration Phase 6; §4 (retrieval) Phase 5;
§5 Phase 5 closure; §6 (answering) Phase 6; §7 (operations: health, metrics, logs, kill
switches, limits, the worker, deployment, backups, CI) and §8 (failure drills) Phase 8.
A row marked *planned* names the phase that makes it real; do not follow planned
procedures on a live system.

---

## 1. Local development — *implemented (Phase 1)*

Follow the README "Local setup" section; it is verified from a clean clone. Useful
operations:

| Situation | Action |
| --------- | ------ |
| Changed a password in `.env` after the database volume was created | `make db-bootstrap` (idempotent; re-asserts roles, passwords, databases, extensions) |
| API exits at startup with `Refusing to start: database role ... is unsafe` | `LATTICE_DATABASE_URL` must use `lattice_app`. Run `make check-db-role` for the list of problems. Never "fix" this by granting privileges to the runtime role |
| API exits with `Invalid configuration:` | The message names each missing/invalid `LATTICE_*` variable (values are never printed). Compare with `.env.example` |
| `database schema is not migrated` | `make migrate` |
| Start over with an empty local database | `make db-destroy` (**deletes all local data**), then `make db-up migrate seed` |
| Port 5433 already in use | Set `LATTICE_DB_HOST_PORT` in `.env` and update the ports in the `LATTICE_*_URL` values |
| Edited the synthetic dataset or a golden item | `make dataset-lint` (must print `OK`); after changing authored tickets, `make dataset-generate` and commit the regenerated `tickets-bulk.jsonl`. See `data/synthetic/README.md` §13 |
| `dataset lint` reports `tickets-bulk.jsonl: out of date` | The bulk distractors were edited by hand or authored tickets changed: `make dataset-generate` |
| Database created before the `lattice_auth` hardening (`\dn+ public` shows `lattice_auth=UC`, or the lookup functions show `=X/lattice_auth`) | `make db-bootstrap` and `make migrate` (migration 0002). Both are idempotent. Verify with `make test` (`tests/test_auth_role.py`) |
| `make migrate` fails with `Role lattice_worker is required (Phase 8)`, or `make db-up` with `set LATTICE_WORKER_PASSWORD` | The database predates the worker role. Copy `LATTICE_WORKER_PASSWORD`, `LATTICE_WORKER_DATABASE_URL` and `LATTICE_TEST_WORKER_DATABASE_URL` from `.env.example` into `.env`, then `make db-up` (recreates the container with the new variable; the data volume is kept), `make db-bootstrap`, `make migrate` |
| API or worker exits with `database schema is at revision X, this build expects Y` | The code and the database disagree. `make migrate` (development); in a deployment, run the migration job of the build being deployed, or deploy the build that matches the database. Never edit `alembic_version` by hand |

### Database roles and credentials

| Role | Used by | Rule |
| ---- | ------- | ---- |
| `lattice_app` | API via `LATTICE_DATABASE_URL` | The API's runtime identity: reads, plus Ask traces and sync requests. The API refuses to start with a role that is a superuser, has `BYPASSRLS`, can act as a table owner, or can write documents or grants; it never falls back to another credential |
| `lattice_worker` | The background worker via `LATTICE_WORKER_DATABASE_URL` (Phase 8, ADR-013) | Writes what a sync writes, through forced row-level security, one organization per job; sees every organization's *jobs* only. Refuses to start under the same checks as the API. Never configured as the API's role (the API refuses it) |
| `lattice_owner` | `make migrate`, `make seed`, operator tools via `LATTICE_MIGRATION_DATABASE_URL` | Migration/bootstrap/administrative role. Never configured as the API's or the worker's database URL. In production its credential lives in a different secret from the runtime ones and is available only to the migration job and operators |
| `lattice_auth` | Nobody (NOLOGIN) | Owns the two pre-authentication lookup functions only. Holds no `CREATE` anywhere at rest |

### Changing a pre-authentication lookup function

`lattice_resolve_session` and `lattice_identity_memberships` are owned by `lattice_auth`,
which cannot create objects, so they cannot be changed in place (`CREATE OR REPLACE`
fails even for the owner). In a new migration, as `lattice_owner`:

1. `DROP FUNCTION …` (the schema owner may drop it) and `CREATE FUNCTION …` again with
   `SECURITY DEFINER` and `SET search_path = pg_catalog, public, pg_temp`.
2. While `lattice_owner` still owns it: `REVOKE ALL … FROM PUBLIC` and
   `GRANT EXECUTE … TO lattice_app`. (After the ownership transfer these statements
   would silently do nothing.)
3. `GRANT CREATE ON SCHEMA public TO lattice_auth`, `ALTER FUNCTION … OWNER TO
   lattice_auth`, `REVOKE CREATE ON SCHEMA public FROM lattice_auth` — all in the same
   migration transaction.
4. Run `make test`: `tests/test_auth_role.py` fails if any privilege is left behind.

## 2. Procedures

| Situation | Procedure | Phase |
| --------- | --------- | ----- |
| A sync run is `partial` or `failed` | **As built (Phase 8):** Sources page (administrators) → the source's last sync and failure codes → *Show records*: stage, fixed code, safe parameters, and the record's identifier only if you can read its document (otherwise `rec_…`). `access_unmappable:account` shows the unmapped external account reference: map it in the source configuration, then *Sync now* (or `lattice sync <source>`). `source_unavailable:*` → check the export path and its mount. The raw identifier behind a `rec_…` is available to infrastructure operators only, with database access: `sync_record_failures.external_id` / `location` | 4 / 8 |
| Re-sync a source now | Sources page → *Sync now* (administrators; queued for the worker, one per source at a time), or `lattice sync --org … --sources … --source <key>` as an operator. Both take the same per-source lock; a request that meets a running CLI sync is retried after a backoff | 8 |
| Deletion guard tripped | Inspect export completeness; if the deletions are genuine, re-run with `--allow-mass-deletion` after confirming with the source owner. A sync requested from the web app never overrides the guard | 4 |
| Urgently revoke a person's access | **Works today (Phase 2), without a UI:** set the member's `status: suspended` in the org manifest and `make seed`, or as `lattice_owner` with the organization bound: `UPDATE memberships SET status = 'suspended', authz_updated_at = now() WHERE id = …`. Sessions are rejected and no authorization context can be resolved on the very next request. Narrower changes (account assignment, department, group membership, grant) are single-row writes with the same immediacy. No re-index, re-embed or cache flush exists or is needed. **Admin UI (Phase 9):** Access → the member → *Suspend*, with a reason; the preview states what they lose among documents you can read; audited. Another administrator must do it for your own membership. Note that `make seed` makes members match the manifest again (recording `operator:seed` events), so a suspension done in the UI must also be made in the manifest (or the identity provider, Phase 10) to last | 2 / 9 |
| "Why can't Maya see the Kestrel tickets?" | Access → Maya: the sentence states her scope, clearance, departments, groups and grants. *Check a document* explains one document you can read (the reasons that decide). If you can't read the document yourself, you can't inspect it: the rules (her accounts, clearance, departments) answer the question; ask an administrator who can read it, or Leadership for restricted content | 9 |
| Change a member's access, or grant one document | Access → the member → *Change access* (clearance, account scope, accounts, departments, administrator-defined groups) or *Grant a document* (one document you can read). Each shows a preview (counts only among documents you can read), needs a reason, and fails with 409 if someone changed the member meanwhile: reload and review again. Private channels and source grants follow their source and can't be changed here | 9 |
| Review who changed access, and why | Access → *Audit log*: every access and AI-provider change, newest first, with actor, reason and effects. Events about documents you can't read aren't listed (D-18): ask an administrator who can read the document, or, as an operator: `SELECT created_at, actor, action, target, before, after, reason FROM audit_events ORDER BY created_at DESC` (organization bound). Events can't be edited or deleted | 9 |
| Remove a source's content now | `lattice source disconnect <source>` → immediate soft delete; purge runs asynchronously | 4 |
| `security_invariant_violated` (HTTP 500 with that code; metric `lattice_security_invariant_violations_total` > 0; log event `security.invariant_violated` or `answer.citation_invariant_violated`) | Treat as incident: the retrieval predicate returned an unreadable chunk, or a response cited evidence it did not carry. The `security.invariant_violated` log event carries organization, membership, `policy_version`, counts and the offending document UUIDs; capture the request ID and trace ID. Stop generation: for one organization `lattice provider disable --purpose generation …` (live, audited); for everyone `LATTICE_GENERATION_ENABLED=false` and restart the API (§7.4). Investigate `authz` changes (compare `policy_version`); add a regression test before re-enabling. Alerting on the metric is Phase 11 | 6 / 8 |
| Provider outage | **As built (Phase 6, bounded in Phase 8):** Ask returns HTTP 200 with status `failed` / `provider_unavailable` and the evidence list, within `LATTICE_GENERATION_DEADLINE_SECONDS` (default 120 s for both attempts together); the trace's `error_code` says why (`generation_failed:timeout`, `rate_limited`, `deadline_exceeded`, …) and `lattice_generation_calls_total{outcome}` counts it. Query embedding degrades to exact + lexical after 5 s (`lattice_retrieval_semantic_total{outcome="provider_failed"}`). Switch provider with `lattice provider configure` if an approved alternative exists; or stop generation (§7.4) and keep serving evidence | 6 / 8 |
| Database outage | The API answers 503 `service_unavailable` with `Retry-After: 5` within `LATTICE_DB_CONNECT_TIMEOUT_SECONDS` (default 5 s) instead of hanging; `/v1/health/ready` fails (take the instance out of rotation), `/v1/health/live` stays up (do not restart for it). The worker keeps polling with back-off and resumes by itself. Nothing is retried on the user's behalf: Ask is safe to retry (a new trace each time) | 8 |
| Change embedding model or provider | `lattice reindex --reembed --embedding <provider>` per source (the new model is a new embedding space; old vectors are replaced, never mixed). For an external provider, estimate volume first (§5). Set `LATTICE_SYNC_EMBEDDING` for the worker to the same provider | 4 / 8 |
| Organization deletion | *Planned:* set status `deleting`; purge job; run verification query (0 rows per table for org) | 11 |

## 3. Model-provider configuration — *Phase 4 (embedding), Phase 5 closure (approval), Phase 6 (generation, credentials, audit)*

Each organization's providers are rows of `model_providers` (purpose `embedding` or
`generation`, provider, model, trust class, endpoint, approval, data terms, enabled).
A first sync records the embedding provider it uses, approved for `internal` only.
Generation providers are configured explicitly. Every change below is an
administrator action: it needs `--reason`, runs as `lattice_owner` through row-level
security, and appends an `audit_events` row with the before/after state (never a
secret or a secret reference). From `backend/`, `uv run --env-file ../.env lattice ...`:

| Action | Command |
| ------ | ------- |
| Configure generation (becomes the one enabled generation configuration; approved for `internal` only; the trust class is fixed from then on) | `lattice provider configure --org halden --purpose generation --provider gemini --model gemini-3.5-flash-lite --deployment external_api --reason "..."` · local: `--provider ollama --model qwen3.5:4b --deployment local --endpoint http://127.0.0.1:11434` · organization-controlled: `--deployment organization_controlled --endpoint https://...` |
| Approve a level (sets `approved_at`) | `lattice provider approve --org halden --purpose generation --provider gemini --model gemini-3.5-flash-lite --level confidential --reason "..."` |
| Raise or lower the organization's ceilings | `lattice org egress --org halden --external-level confidential --reason "..."`; `--organization-controlled-level internal\|confidential\|restricted\|none` (`none`: the class is not allowed) |
| Record or rotate a credential (BYOK, by reference) | Put the value in the process environment as `LATTICE_PROVIDER_SECRET_<NAME>` (for example in the git-ignored `.env` in development, the platform's secret store in deployment), then `lattice provider credential set --org halden --provider gemini --secret-ref env:LATTICE_PROVIDER_SECRET_HALDEN_GEMINI --data-use paid --reason "..."`. Setting again rotates (the old reference is revoked, history kept). The legacy names `LATTICE_GEMINI_API_KEY`, `GEMINI_API_KEY`, `LATTICE_OPENAI_API_KEY` are also accepted; a reference to any other variable is refused |
| Revoke a credential | `lattice provider credential revoke --org halden --provider gemini --reason "..."` (next request: `credential_unavailable`, nothing sent) |
| Disable / enable a provider | `lattice provider disable --org halden --purpose generation --provider gemini --model gemini-3.5-flash-lite --reason "..."` (next request: nothing sent; the record and history stay) |
| See the configuration (masked) | `lattice provider list --org halden`, or `GET /v1/admin/providers` as an administrator: approval, class, enabled, and whether a credential is configured and resolvable; never the value or its reference |

A credential never makes a provider eligible: data origin, trust class, the
organization's ceiling and the approval do (security-model §6). **Credential
availability ≠ provider approval ≠ data-egress authorization.** No evaluation or CI
run needs any external key. The embedding provider of `lattice sync`/`search` and of the
API (`LATTICE_QUERY_EMBEDDING`) still reads `LATTICE_OPENAI_API_KEY` /
`LATTICE_GEMINI_API_KEY` from the process environment (the Phase 5 stop-gap, ADR-011).

For each configured provider, keep outside Lattice: region, key owner, rotation date,
the provider's retention terms (security-model §6).

## 4. Retrieval and its evaluation — *implemented (Phase 5)*

| Situation | Action |
| --------- | ------ |
| See what a member would retrieve | From `backend/`: `uv run --env-file ../.env lattice search --org halden --as <email> "<question>"` (`--account KEY`, `--source-type doc\|ticket\|chat`, `--classification` (can only raise the org default, D-13), `--embedding none\|fake\|local\|gemini\|openai`, `--text`, `--pool`, `--json`). Development/test only; it acts as that member through RLS |
| Measure retrieval | `make sync` then `make eval` (fake embeddings; `EMBEDDING=none` for exact + lexical only). Prints overall recall and the security counts; writes `evals/reports/<timestamp>/report.{json,md}` (per category, per persona, misses and where they were lost). Exit 1 if any security count is above 0 |
| Real-provider quality evaluation (the Recall@10 gate) | With the local model (no key): `make models-fetch`, `make sync EMBEDDING=local`, `make eval EMBEDDING=local`; for the local-model-approved-restricted configuration, approve (§3), sync again, evaluate again. With an external provider: put its key in the git-ignored `.env` (never in chat, source config or commits), estimate volume (§5), then `make sync EMBEDDING=<provider>` for a fresh corpus, or `lattice reindex --org <org> --sources ../data/synthetic/<org>/sources.yaml --reembed --embedding <provider>` per organization, then `make eval EMBEDDING=<provider>`. Check that the report's corpus-embedding line shows only that model (a partially embedded corpus is not a benchmark). Prefer an isolated database (`COMPOSE_PROJECT_NAME`, own port) so the development database keeps its vectors |
| An evaluation reports a security violation | Treat as an incident in retrieval: the report names the item, stage and record. Reproduce with `lattice search`, then the SQL of that channel (`tests/retrieval/test_sql_structure.py` and `golden/test_sql_capture.py` show how to inspect it). Never "fix" by filtering results |
| `semantic: policy_excluded (provider_unapproved)` | The provider has no `model_providers` record for the organization, or it is `organization_controlled` without an explicit approval (`approved_at`): nothing was sent. Sync with that provider first (it records it, approved for `internal`); an `organization_controlled` record also needs `lattice provider approve` |
| `semantic: policy_excluded (query_classification)` | The question's effective sensitivity (org default, raised by any declaration; D-13) is above the provider's effective level; working as designed. Exact + lexical still ran |
| `semantic: policy_excluded (provider_data_use)` | The provider may use submitted content (for example an unpaid Gemini key) and the organization is not `synthetic`: nothing was sent, by design (ADR-010) |
| `semantic: policy_excluded (provider_deployment_mismatch)` | The provider's declared deployment differs from its `model_providers` record; nothing was sent. Fix the configuration; never edit the record to make them match without review |
| `semantic: policy_excluded (deployment_unknown)` | The provider's recorded or declared deployment is not a known trust class (`local`, `organization_controlled`, `external_api`; D-15). Nothing was sent. Fix the provider configuration; an unknown class is never recorded or allowed |
| `semantic: policy_excluded (deployment_not_allowed)` | The provider is `organization_controlled` and the organization has not allowed that class (`org_settings.organization_controlled_level` is unset). Nothing was sent. Allowing it is an explicit administrator decision with a ceiling, and the record also needs `lattice provider approve` (D-15) |
| `semantic: incompatible_space` | `dimensions`: the provider's vector size is not the column's (1024). `model_ambiguous`: two providers are recorded under one model name; remove the stale record |
| `semantic: provider_failed (...)` | Provider outage, timeout (5 s), rejection or malformed vectors; exact and lexical results were still returned. Check the provider, then the key |
| Changing retrieval tunables | `RetrievalConfig` in `lattice/retrieval/models.py`; bump `version`, run `make eval` before and after, and record the change and its per-category effect in `docs/evaluation-plan.md` §9 (regression rule §7) |

## 5. Model providers: local model and Gemini — *Phase 5 closure*

| Situation | Action |
| --------- | ------ |
| Use the local model (no key, no egress) | `make models-fetch` (once: 1.34 GB, pinned revision, SHA-256 verified, into `LATTICE_MODEL_DIR`, default `~/.cache/lattice/models`), then `make sync EMBEDDING=local` and `make eval EMBEDDING=local`. `lattice models list` shows the cache state; `lattice models verify mxbai-embed-large-v1` re-hashes it. Nothing ever downloads implicitly |
| `local_model_missing` / `local_model_corrupt` / `local_model_invalid` | Run `lattice models fetch mxbai-embed-large-v1` (again). An interrupted download resumes from its `.partial` file (HTTP Range, re-hashed), retrying up to 5 times per file. Only files that match their pinned size and SHA-256 enter the cache. Lattice never falls back to another model |
| `local_runtime_missing` | `cd backend && uv sync --extra local` (`make setup` does this) |
| Embed confidential/restricted content locally | After a local sync: `lattice provider approve --org <org> --provider local --model mxbai-embed-large-v1@b33106f585b9 --level restricted --reason "..."`, then sync again. External providers stay capped by the org's external level |
| Use Gemini (synthetic data only with an unpaid key) | Key in `.env` as `GEMINI_API_KEY` or `LATTICE_GEMINI_API_KEY`; `LATTICE_GEMINI_DATA_USE=unpaid` (default). Only organizations seeded with `data_origin: synthetic` can send anything to it; for any other it is `provider_data_use`, and it is refused in production. Before a full run, estimate: the synthetic corpus is 1,289 internal chunks, ~306k characters (~76k tokens), ~20 batched requests; the evaluation prefetches its questions in 1–2 batched requests |
| Gemini `embedding_failed:rate_limited` | The quota (per project; see AI Studio) is exhausted. The provider waited the server's delay twice, then stopped sending for 60 s; affected chunks are `failed`. Run the same `lattice sync` again later: only failed chunks are embedded (no quota spent on stored vectors). Daily quotas reset at midnight Pacific |
| Switch an organization between providers | `lattice reindex --org <org> --sources … --reembed --embedding <provider>` per organization; queries must then use the same provider (vectors of another model are never compared) |

## 6. Answering — *implemented (Phase 6)*

| Situation | Action |
| --------- | ------ |
| See what a member would get | `uv run --env-file ../.env lattice ask --org halden --as <email> "<question>"` (`--account KEY`, `--classification`, `--embedding`, `--json`). Development/test only; it acts as that member and uses the organization's own generation configuration |
| Measure answering (security gate) | `make sync` then `make eval-answer` (fake generation and embeddings: plumbing and security, **not answer quality**). Writes `evals/reports/answer-<timestamp>/report.{json,md}`; exit 1 on any security count above 0. It reconfigures each organization's generation provider (audited, reason "answer evaluation"): run it on an evaluation database only |
| Real-model answer quality | **Not measured** (ADR-012). Local: the reference M1 8 GB could not host Ollama + `qwen3.5:4b` beside PostgreSQL and the tests (the machine stopped responding), so the run was not done. On adequate hardware: `ollama pull qwen3.5:4b`, `ollama serve` (loopback), then `lattice eval answer --generation ollama`, with nothing else heavy running. Gemini (synthetic organizations only, spends quota): `lattice eval answer --generation gemini --allow-external`. Never in CI |
| Status `evidence_only` | The trace's `egress.decision_reason` says why no model was called: `not_configured`, `provider_disabled`, `provider_unsupported`, `credential_unavailable` → reason `generation_unavailable`; `provider_unapproved`, `deployment_not_allowed`, `provider_deployment_mismatch`, `deployment_unknown`, `provider_data_use`, `question_classification`, or all relevant evidence above the level → reason `egress_policy`. Working as designed unless the configuration is wrong |
| Status `insufficient_evidence` / `claims_unverified` | The model's claims all failed validation; `validation.rejection_codes` in the asker's trace counts why (`quote_not_found`, `unknown_handle`, ...). Never "fixed" by relaxing validation (evaluation-plan §5) |
| Status `failed` | `provider_unavailable` (see the trace's `error_code`), `generation_invalid` (schema failure twice) or `citation_validation_failed` (a bug: the response invariant withheld the answer; logged as `answer.citation_invariant_violated`) |
| A trace | `GET /v1/traces/{id}`: the asker sees their own (answer hidden if any of its evidence, cited or not, is no longer readable, or any account it depends on is no longer visible); an administrator sees other members' operational fields only; anyone else 404 |

## 7. Operations — *implemented (Phase 8)*

### 7.1 Processes

| Process | Command | Role | Notes |
| ------- | ------- | ---- | ----- |
| API | `uvicorn lattice.api.app:create_app --factory --no-access-log` (`make api` in development) | `lattice_app` | One process per container (metrics are per process). Refuses to start on invalid configuration, an unsafe role, or a schema at another revision |
| Worker | `lattice worker` (`make worker`) | `lattice_worker` | Runs requested syncs one at a time; SIGTERM/SIGINT finish the current job, then exit. `lattice worker --once` runs every due job and exits. Several workers may run: `SKIP LOCKED` hands each job to one of them |
| Web | `next start` behind the reverse proxy (`make web` in development) | — | Proxies `/api/*` to the API only |
| Migrations | `alembic upgrade head` (`make migrate`) | `lattice_owner` | Run **before** starting a new build: the API and worker refuse an older schema, and an older build refuses a newer one |

### 7.2 Health and readiness

| Endpoint | Checks | Probe | Failing means |
| -------- | ------ | ----- | ------------- |
| `GET /v1/health/live` | the process serves requests; touches nothing | liveness | restart the process |
| `GET /v1/health/ready` | the database answers and its schema is at the revision this build expects | readiness / load balancer | send no traffic here; a restart will not help (database down, or migrations not applied) |
| `GET /v1/health` | database connectivity (Phase 1, kept for compatibility) | — | — |

All three are unauthenticated and return states only (`ok`, `ready`, 503 `service_unavailable`
with `Retry-After`): never versions, revisions, hosts, counts or error text.
The worker has no HTTP endpoint; watch `lattice_worker_polls_total` and `lattice_jobs_waiting`.

### 7.3 Metrics and logs

`LATTICE_METRICS_ADDRESS=host:port` starts a **separate** Prometheus listener in the API
(and, set for the worker, in the worker). It has no authentication: bind it to an internal
interface and never route it publicly. The web app proxies only `/api/*` to the API's
own port, so metrics are never reachable through it. Every label comes from a closed set
(route *template*, method, status, answer status/reason, provider implementation, fixed
error and rejection codes, sync mode/status/stage, job kind/outcome); anything else is
`other`. No organization, member, trace, document or source identifier and no text is
ever a label (`lattice.metrics`, tested).

| Metric | What to watch |
| ------ | ------------- |
| `lattice_http_requests_total{method,route,status}`, `lattice_http_request_duration_seconds` | error rate by route; latency |
| `lattice_security_invariant_violations_total{check}` | **must stay 0**; page on any increase (§2) |
| `lattice_asks_total{status,reason}`, `lattice_ask_duration_seconds{status}` | share of `failed` / `evidence_only`; Ask latency |
| `lattice_generation_calls_total{provider,outcome}`, `lattice_generation_call_duration_seconds` | provider errors (`timeout`, `rate_limited`, `unauthorized`, …) |
| `lattice_generation_decisions_total{decision}` | why generation was not invoked (`generation_disabled`, `credential_unavailable`, `provider_unapproved`, …) |
| `lattice_retrieval_semantic_total{outcome}` | query-embedding degradation (`provider_failed`, `policy_excluded`) |
| `lattice_claims_rejected_total{code}` | citation validation rejections by code |
| `lattice_dependency_failures_total{dependency,check}` | database unavailable (requests, readiness, worker) |
| `lattice_auth_rejections_total{reason}`, `lattice_http_requests_rejected_total{reason}` | session rejections; CSRF and oversized requests |
| `lattice_sync_runs_total{mode,status}`, `lattice_sync_record_failures_total{stage}`, `lattice_sync_duration_seconds` | ingestion health |
| `lattice_jobs_total{kind,outcome}`, `lattice_jobs_waiting`, `lattice_worker_polls_total{result}`, `lattice_job_duration_seconds` | worker throughput, backlog, errors; `abandoned` jobs |
| `lattice_maintenance_rows_total{task}` | job retention |
| `process_*`, `python_gc_*` | memory and CPU per process |

**Logs** are JSON lines on stdout (architecture §8): one `http.request` per request
(method, path, route template, status, duration, request ID, organization and user IDs),
`answer.completed` per Ask (status, reason, provider, model, counts, duration, trace ID),
`ingestion.sync` per run, `jobs.started` / `jobs.finished` / `jobs.abandoned` /
`jobs.retention` per job, `db.unavailable`, `health.*`, `http.request_too_large`,
`http.csrf_rejected`, `auth.session.rejected`, `security.invariant_violated`. Never
question, answer, document or prompt text, titles, file names or secrets. Correlate a
user report with its `X-Request-ID` header, then the trace ID in `answer.completed`.

### 7.4 Kill switches and limits

| Control | Scope | Effect | Takes effect |
| ------- | ----- | ------ | ------------ |
| `lattice provider disable --org … --purpose generation …` | one organization | no generation provider is built or called; answers are `evidence_only` / `generation_unavailable` | next request (audited) |
| `LATTICE_GENERATION_ENABLED=false` | the deployment | the same, for every organization; no credential is resolved; trace reason `generation_disabled` | API restart |
| `LATTICE_QUERY_EMBEDDING=none` | the deployment | no question leaves Lattice for embedding; exact + lexical retrieval only | API restart |
| `LATTICE_GENERATION_DEADLINE_SECONDS` (120) | per Ask | both generation attempts together; on expiry `failed` / `provider_unavailable` with the evidence | API restart |
| `LATTICE_MAX_REQUEST_BYTES` (65,536) | per request | larger bodies get 413 `request_too_large` before parsing | API restart |
| `LATTICE_DB_STATEMENT_TIMEOUT_MS` (30,000), `LATTICE_DB_CONNECT_TIMEOUT_SECONDS` (5), `LATTICE_DB_POOL_SIZE` (5 + 5 overflow) | API connections | a long statement is cancelled (503); an unreachable database fails fast | API restart |
| Question length 2,000 characters; one active sync job per source; generation output ≤ 2,048 tokens | per request | 422 / idempotent request / bounded model output | always |

Per-member rate limiting (T19) is Phase 10; it is **not** built. Until then, bound cost at
the provider (quota) and with the kill switches above.

### 7.5 The worker

| Situation | Action |
| --------- | ------ |
| Requests stay *Queued* | Is a worker running (`lattice_worker_polls_total` increasing)? At startup it exits 1 with a one-line reason (configuration, role, schema revision, database unreachable): run it under a supervisor that restarts it. Once running, it survives database outages by itself |
| A job failed | The Sources row shows the code; the job's run (if one opened) has the details. `source_config:*` codes need the operator's `sources.yaml` and a CLI sync; `source_unavailable:*` the export path |
| `source_busy` | Another sync of that source (CLI or worker) holds its lock; the request is retried after 60 s × attempt, up to 3 attempts |
| A worker died mid-job | Its lease expires (`LATTICE_WORKER_LEASE_SECONDS`, 300; renewed while the sync runs) and another poll retries it; after 3 attempts the job is `failed` / `job_abandoned`. The interrupted run is recorded `aborted` / `internal_error:interrupted` by the next sync of that source |
| Job history | Finished jobs are deleted after 30 days, in batches of 500, once an hour; the database policy forbids deleting younger ones. Sync runs and failures are kept (retention: Phase 11) |
| Embeddings of requested syncs | `LATTICE_SYNC_EMBEDDING` (default `none`: new chunks stay `pending` until a sync with a provider). Use the provider the corpus was synced with |

### 7.6 Deployment checklist

Production refuses to start on any of: `LATTICE_ENV` unset; development login enabled;
insecure cookies; `LATTICE_QUERY_EMBEDDING=fake` or `LATTICE_SYNC_EMBEDDING=fake`; an
unsafe runtime role; a schema at another revision; an invalid operational setting. In
addition, before a first deployment:

1. Three database credentials in the platform's secret store: runtime (`lattice_app`),
   worker (`lattice_worker`), owner (`lattice_owner`, migration job only). Bootstrap once
   as a superuser (`infra/postgres/bootstrap.sql`), then never use the superuser again.
2. Order: migrate → start API and worker → readiness passes → route traffic.
3. Metrics listener on an internal interface; alert on
   `lattice_security_invariant_violations_total > 0`, readiness failures,
   `lattice_dependency_failures_total`, and a growing `lattice_jobs_waiting`.
4. Real identity (OIDC, Phase 10) and container images / infrastructure (Phase 11) are
   **not built**: the slice runs as documented in the README only.

### 7.7 Backups, recovery and migrations

Everything Lattice holds is in PostgreSQL (ADR-002): no second store to back up.
Managed PostgreSQL with point-in-time recovery is the target (Phase 11, not built). For
a self-managed database:

| Task | Procedure |
| ---- | --------- |
| Back up | `pg_dump --format=custom --no-owner lattice > lattice-<date>.dump` as a superuser or a role that bypasses RLS (a dump as `lattice_owner` or a runtime role sees no tenant rows) |
| Restore | Bootstrap an empty server (`bootstrap.sql`), `pg_restore --no-owner --role=lattice_owner -d lattice lattice-<date>.dump`, then `make check-db-role` and `lattice worker --once` (both verify roles and the schema revision) |
| Migrate | Take a backup, run the new build's migrations as `lattice_owner`, then start the new build. Migrations are tested to round-trip (`alembic downgrade base` / `upgrade head` in CI); a downgrade is a last resort, after restoring is ruled out |
| Rebuild derived data | Chunks, vectors and the lexical index are derived from the sources: `lattice reindex` rebuilds them without deleting documents |

### 7.8 CI and releases

CI (`.github/workflows/ci.yml`) pins its runner image (`ubuntu-24.04`, not `ubuntu-latest`),
the uv version and each action's major version (all on Node.js 24; `setup-uv` by exact
version, since it publishes no major tags). Update them deliberately, one commit at a
time, and check the run. Each job has a 30-minute timeout. The backend job lints, type
checks, bootstraps a clean database with all four roles, round-trips the migrations,
seeds, verifies the API's and the worker's startup checks, syncs twice, runs both
evaluation security gates and the full test suite; the web job checks the generated API
contract, lints, type checks, runs unit tests, builds and runs the browser tests.

## 8. Failure drills — *run for Phase 8 (2026-09-29)*

Run against the real API and worker on the development database (fake generation
provider recorded for Halden, fake embeddings), and as automated tests
(`tests/test_operations.py`, `tests/answering/test_operations.py`,
`tests/ingestion/test_worker.py`). Repeat them after changing any of the controls.

| Drill | How | Observed |
| ----- | --- | -------- |
| Database unavailable | `docker stop lattice-postgres-1` with the API running | `/v1/health/ready` 503 `service_unavailable` + `Retry-After: 5`; Ask 503 in 0.09 s, Sources 503 in 0.02 s; `/v1/health/live` 200; no stack trace or host in any body. `docker start` → readiness recovers with no restart. `lattice worker` started while the database is down exits 1 with `ConnectionRefusedError: … Connect call failed`; a running worker keeps polling (test) |
| Schema/config mismatch | `alembic downgrade 0010` under a running API | readiness 503, liveness 200; `lattice worker --once` and `lattice check-db-role` refuse with the revision problem; `alembic upgrade head` → ready again |
| Provider timeout / hang | tests: a provider that never answers, a spent budget | `failed` / `provider_unavailable`, evidence returned, `generation_failed:timeout` or `:deadline_exceeded`, no second call past the deadline, `lattice_generation_calls_total{outcome="timeout"}` |
| Malformed provider response | tests (Phase 6): malformed JSON twice | `failed` / `generation_invalid`; no claim shown |
| Credential missing / revoked | tests (Phase 6) | `evidence_only` / `generation_unavailable` (`credential_unavailable`), nothing sent |
| Deployment disallowed | tests (Phase 5-6) | `evidence_only` / `egress_policy` (`deployment_not_allowed`), nothing sent |
| Kill switch | API restarted with `LATTICE_GENERATION_ENABLED=false`; Ask as Maya | `evidence_only` / `generation_unavailable`, 12 evidence items, 0 generation calls, trace `decision_reason: generation_disabled` |
| Stale / revoked trace dependencies | tests (Phase 6) and Activity | the answer stays hidden in the trace; Activity lists the question only |
| Ingestion failure | requested sync of `support` through the UI and `lattice worker --once` | run `partial` (147 unchanged, 1 failed), job `succeeded`; the failure shown as `access_unmappable:account`, `rec_…`, external account ref only. Missing export: job `failed` / `source_unavailable:missing_export` (test) |
| Worker crash / busy source | tests | lease expiry → retried, then `job_abandoned` after 3 attempts; the interrupted run recorded `aborted`; `source_busy` → retried after 60 s |
| Client retry | Sync now twice; Ask repeated | the second sync request returns the same job (200); each Ask is independent (a new trace) and read-only apart from it |
| Oversized / pathological requests | 2,001-character question, 200-character account key, `limit=100000`, `before=not-a-uuid`, 70 KB body, 20 concurrent Asks | 422 naming the field (never its value) ×4, 413 `request_too_large`, 20 × 200 (max 1.6 s) |

**Performance sanity (M1 8 GB, heavily swapped, dev data, fake providers).** Ask p50
0.26 s over 8 requests (the first, cold, 11 s); `GET /v1/sources` p50 43 ms,
`/v1/activity` 31 ms, `/v1/admin/sources` 30 ms, `/v1/admin/activity` 21 ms; a requested
sync of 148 unchanged tickets 1.7 s, of Drive 0.3 s; API resident memory about 77 MB.
No model benchmark was run (ADR-012).
