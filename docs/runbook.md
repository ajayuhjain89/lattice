# Lattice — Operational Runbook

Status: §1 is implemented (Phases 1–2, worker role Phase 8); §2 procedures are marked
per row; §3 (embedding) Phase 4, provider configuration Phase 6; §4 (retrieval) Phase 5;
§5 Phase 5 closure; §6 (answering) Phase 6; §7 (operations: health, metrics, logs, kill
switches, limits, the worker, deployment, backups, CI) and §8 (failure drills) Phase 8;
§9 (identity: SSO, SCIM provisioning, first administrator) and the rate limits of §7.4
Phase 10; the images, worker liveness, trusted ingress hop, §7.9 (local reference stack)
and the P11.1 drills of §8 Phase 11 (P11.1); the production bootstrap, `lattice
migrate`, `check-deployment`, releases and rollbacks (§7.6, §7.7, §7.10) and the P11.2
drills of §8 Phase 11 (P11.2).
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
| `database schema is not migrated` | `make migrate` (development); in a deployment the migration job runs `lattice migrate` (§7.7) |
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
| `lattice_app` | API via `LATTICE_DATABASE_URL` | The API's runtime identity: reads, plus Ask traces, sync requests, access changes (ADR-014), SCIM provisioning of organization-owned users with least-access memberships, sign-in attempts and rate-limit counters (ADR-015) — each fenced by restrictive policies, and every access write must commit with its audit event. The API refuses to start with a role that is a superuser, has `BYPASSRLS`, can act as a table owner, or can write documents or grants; it never falls back to another credential |
| `lattice_worker` | The background worker via `LATTICE_WORKER_DATABASE_URL` (Phase 8, ADR-013) | Writes what a sync writes, through forced row-level security, one organization per job; sees every organization's *jobs* only. Refuses to start under the same checks as the API. Never configured as the API's role (the API refuses it) |
| `lattice_owner` | `make migrate`, `make seed`, operator tools via `LATTICE_MIGRATION_DATABASE_URL` | Migration/bootstrap/administrative role. Never configured as the API's or the worker's database URL. In production its credential lives in a different secret from the runtime ones and is available only to the migration job and operators |
| `lattice_auth` | Nobody (NOLOGIN) | Owns the four pre-authentication lookup functions only (session, development identity, OIDC connection by login key, SCIM token by hash). Holds no `CREATE` anywhere at rest |

### Changing a pre-authentication lookup function

`lattice_resolve_session`, `lattice_identity_memberships`, `lattice_oidc_connection` and
`lattice_resolve_scim_token` are owned by `lattice_auth`,
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
| Urgently revoke a person's access | **Operator command (P11.3, D-26):** `lattice member suspend --org <slug> --email <e-mail> --reason "…"` — the membership becomes `suspended` and every session of the person is revoked in the same transaction, with an `access.member_changed` audit event (`operator:<user>`); the next request on any API instance is 401 and no authorization context can be resolved. `lattice member reinstate` undoes it (old sessions stay revoked). Narrower changes (account assignment, department, group membership, grant) are single-row writes with the same immediacy; no re-index, re-embed or cache flush exists or is needed. **Admin UI (Phase 9):** Access → the member → *Suspend*, with a reason; the preview states what they lose among documents you can read; audited. Another administrator must do it for your own membership. `make seed` makes manifest members match the manifest again (recording `operator:seed` events), so a suspension of a manifest member must also be made in the manifest to last. **Provisioned members (Phase 10):** suspend or remove them in the identity provider (SCIM pushes it; sessions are revoked in the same transaction); the Access page refuses lifecycle changes for them. If the provider itself is the problem: `lattice scim token revoke` and `lattice sso disable` (§9; it revokes every session the connection issued), then `lattice member suspend`, which works for provisioned members too — suspend them in the provider as well, or its next update may reactivate them (D-19). No incident procedure needs owner-role SQL | 2 / 9 / 10 / 11 |
| End sessions without changing access | `lattice session revoke --org <slug> (--email <e-mail> \| --all) --reason "…"`: revokes the person's (or the whole organization's) live sessions now, with a `session.revoked` audit event and the count; people sign in again normally. Use after a stolen device or cookie, or a session-key leak | 11 |
| Stop an organization (emergency) | `lattice org suspend --org <slug> --reason "…"`: the organization becomes `suspended` — every session is revoked, sign-in (OIDC and development) and its SCIM tokens fail, and the worker fails its queued syncs with `organization_suspended`; audited (`organization.suspended`). `lattice org reactivate` restores sign-in and SCIM (no session comes back) | 11 |
| "Why can't Maya see the Kestrel tickets?" | Access → Maya: the sentence states her scope, clearance, departments, groups and grants. *Check a document* explains one document you can read (the reasons that decide). If you can't read the document yourself, you can't inspect it: the rules (her accounts, clearance, departments) answer the question; ask an administrator who can read it, or Leadership for restricted content | 9 |
| Change a member's access, or grant one document | Access → the member → *Change access* (clearance, account scope, accounts, departments, administrator-defined groups) or *Grant a document* (one document you can read). Each shows a preview (counts only among documents you can read), needs a reason, and fails with 409 if someone changed the member meanwhile: reload and review again. Private channels and source grants follow their source and can't be changed here | 9 |
| Review who changed access, and why | Access → *Audit log*: every access and AI-provider change, newest first, with actor, reason and effects. Events about documents you can't read aren't listed (D-18): ask an administrator who can read the document, or, as an operator: `SELECT created_at, actor, action, target, before, after, reason FROM audit_events ORDER BY created_at DESC` (organization bound). Events can't be edited or deleted | 9 |
| Connect SSO, provision with SCIM, give the first administrator | §9 | 10 |
| A member gets 429 `rate_limited` | They reached a per-member limit (§7.4); `Retry-After` says when to try again, and the web app shows it. Raise the limit for the deployment only if the use is legitimate; there is no per-person exemption. `lattice_rate_limited_total{bucket}` shows which limit | 10 |
| "Sign-in didn't complete" (`/sign-in?error=sso_failed`) | `auth.oidc.failed` log events carry a fixed `outcome`: `not_provisioned` (the ID token's identifying claim matches no member this connection provisioned: check the claim and the SCIM `externalId` mapping, §9), `inactive` (suspended or removed), `token_invalid` (issuer, audience, signature, expiry or nonce: compare the connection with the provider's app settings), `invalid_state` (an old, replayed or foreign callback: start again). `sso_unavailable` = `provider_error` (discovery, keys or token endpoint unreachable, or the client secret reference unset) | 10 |
| Remove a source's content now | `lattice source disconnect <source>` → immediate soft delete; purge runs asynchronously | 4 |
| `security_invariant_violated` (HTTP 500 with that code; metric `lattice_security_invariant_violations_total` > 0; log event `security.invariant_violated` or `answer.citation_invariant_violated`) | Treat as incident: the retrieval predicate returned an unreadable chunk, or a response cited evidence it did not carry. The `security.invariant_violated` log event carries organization, membership, `policy_version`, counts and the offending document UUIDs; capture the request ID and trace ID. Stop generation: for one organization `lattice provider disable --purpose generation …` (live, audited); for everyone `LATTICE_GENERATION_ENABLED=false` and restart the API (§7.4). Investigate `authz` changes (compare `policy_version`); add a regression test before re-enabling. Alerting on the metric is Phase 11 | 6 / 8 |
| Provider outage | **As built (Phase 6, bounded in Phase 8):** Ask returns HTTP 200 with status `failed` / `provider_unavailable` and the evidence list, within `LATTICE_GENERATION_DEADLINE_SECONDS` (default 120 s for both attempts together); the trace's `error_code` says why (`generation_failed:timeout`, `rate_limited`, `deadline_exceeded`, …) and `lattice_generation_calls_total{outcome}` counts it. Query embedding degrades to exact + lexical after 5 s (`lattice_retrieval_semantic_total{outcome="provider_failed"}`). Switch provider with `lattice provider configure` if an approved alternative exists; or stop generation (§7.4) and keep serving evidence | 6 / 8 |
| Database outage | The API answers 503 `service_unavailable` with `Retry-After: 5` within `LATTICE_DB_CONNECT_TIMEOUT_SECONDS` (default 5 s) instead of hanging; `/v1/health/ready` fails (take the instance out of rotation), `/v1/health/live` stays up (do not restart for it). The worker keeps polling with back-off and resumes by itself. Nothing is retried on the user's behalf: Ask is safe to retry (a new trace each time) | 8 |
| Change embedding model or provider | `lattice reindex --reembed --embedding <provider>` per source (the new model is a new embedding space; old vectors are replaced, never mixed). For an external provider, estimate volume first (§5). Set `LATTICE_SYNC_EMBEDDING` for the worker to the same provider | 4 / 8 |
| Organization deletion | *Not available.* Executable purge is **Phase 15** (D-37A, 2026-10-06; semantics in phase-12-plan C14: suspend, waiting period, platform deletion record, deletion, verification query, backup expiry). Until then stop the organization with the audited `lattice org suspend` (above) | 15 |

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
| API | the `lattice-backend` image's default command: `uvicorn lattice.api.app:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log --no-proxy-headers --timeout-graceful-shutdown 150` (`make api` in development) | `lattice_app` | One process per container (metrics are per process). Refuses to start on invalid configuration, an unsafe role, or a schema at another revision. SIGTERM: in-flight requests (an Ask: up to the 120 s generation deadline) get up to 150 s; give the container a stop grace of at least 160 s |
| Worker | `lattice worker` in the same image (`make worker`) | `lattice_worker` | Runs requested syncs one at a time; SIGTERM/SIGINT finish the current job, then exit. `lattice worker --once` runs every due job and exits. Several workers may run: `SKIP LOCKED` hands each job to one of them. Liveness: the heartbeat (§7.2) |
| Web | the `lattice-web` image: `node server.js` on `0.0.0.0:3000` (`make web` = `next dev` in development) | — | Holds no secret. The production build has no `/api` route: the ingress routes `/api/*` straight to the API ([deployment.md](deployment.md) §4). Only `next dev` proxies `/api/*` (to `LATTICE_API_URL`, 150 s proxy timeout) |
| Migrations | `alembic upgrade head` (`make migrate`) | `lattice_owner` | Run **before** starting a new build: the API and worker refuse an older schema, and an older build refuses a newer one |

### 7.2 Health and readiness

| Endpoint | Checks | Probe | Failing means |
| -------- | ------ | ----- | ------------- |
| `GET /v1/health/live` | the process serves requests; touches nothing | liveness | restart the process |
| `GET /v1/health/ready` | the database answers and its schema is at the revision this build expects | readiness / load balancer | send no traffic here; a restart will not help (database down, or migrations not applied) |
| `GET /v1/health` | database connectivity (Phase 1, kept for compatibility) | — | — |

All three are unauthenticated and return states only (`ok`, `ready`, 503 `service_unavailable`
with `Retry-After`): never versions, revisions, hosts, counts or error text.
The worker has no HTTP endpoint. **Liveness (P11.1):** its heartbeat — the gauge
`lattice_worker_heartbeat_timestamp_seconds` and, with `LATTICE_WORKER_HEARTBEAT_FILE`, that
file's modification time — is renewed at every loop iteration (also while the database is
down) and at every lease renewal during a job; a container health check fails when the file
is older than 300 s (dead, frozen or blocked worker). `lattice smoke --worker-metrics`
checks the gauge. For throughput watch `lattice_worker_polls_total` and
`lattice_jobs_waiting`.

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
| `lattice_maintenance_rows_total{task}`, `lattice_maintenance_last_success_timestamp_seconds{task}`, `lattice_maintenance_failures_total{task}` | retention per task (`jobs`, `query_traces`, `source_sync_runs`, `sessions`, `oidc_login_attempts`, `rate_limit_counters`, `pre_auth_rate_limits`, `ask_slots`; P11.4) |
| `lattice_rate_limited_total{bucket}` | requests refused by a limit (`ask_minute`, `ask_day`, `ask_concurrent`, `access_write`, `scim`, `sign_in`) |
| `lattice_sso_sign_ins_total{outcome}` | OIDC sign-ins: `started`, `succeeded` and fixed failure outcomes (§2) |
| `lattice_scim_operations_total{operation,status}` | SCIM requests by operation and status class; `unauthenticated` for bad tokens |
| `lattice_clock_skew_seconds` (P11.3) | this process's clock minus the database's, refreshed by each API readiness check and each worker poll; OIDC token times have 60 s of leeway and sessions expire on the API's clock, so keep hosts on NTP and investigate anything beyond a few seconds |
| `lattice_member_lifecycle_total{change,actor_kind}` (P11.6) | membership created / suspended / reactivated / removed by `scim`, `administrator` or `operator`: provisioning bursts (§10.5) |
| `lattice_sessions_revoked_total{reason}` (P11.6) | revocations by cause: `logout`, `sign_in_rotation`, `member_suspended`, `member_removed`, `sso_disabled`, `sso_identity_changed`, `operator_revoke`, `organization_suspended` |
| `lattice_jobs_oldest_waiting_seconds`, `lattice_database_size_bytes`, `lattice_wal_archive_failing`, `lattice_wal_archive_last_success_timestamp_seconds` (P11.6, worker) | backlog age; database size (once a minute); WAL archiver health from `pg_stat_archiver` |
| `process_*`, `python_gc_*` | memory and CPU per process |

**Logs** are JSON lines on stdout (architecture §8): one `http.request` per request
(method, path, route template, status, duration, request ID, organization and user IDs),
`answer.completed` per Ask (status, reason, provider, model, counts, duration, trace ID),
`ingestion.sync` per run, `jobs.started` / `jobs.finished` / `jobs.abandoned` /
`jobs.retention` per job, `db.unavailable`, `health.*`, `http.request_too_large`,
`http.csrf_rejected`, `auth.session.rejected`, `auth.oidc.started` / `succeeded` /
`failed` (fixed outcome only), `security.invariant_violated`. Never
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

**Rate limits (Phase 10, `lattice.ratelimit`).** Server-side, for every role
(administrators included); none can be set to 0. A refusal is 429 `rate_limited` with
`Retry-After` (seconds); browser sign-in redirects to `/sign-in?error=rate_limited`.

| Limit | Subject | Default | Where counted |
| ----- | ------- | ------- | ------------- |
| `LATTICE_RATE_LIMIT_ASK_PER_MINUTE` / `_PER_DAY` | each membership | 10 / 500 | PostgreSQL (shared by all API processes) |
| `LATTICE_RATE_LIMIT_ASK_CONCURRENT` | each membership | 2 in flight | PostgreSQL lease rows `ask_slots` (P11.4); a crashed process's slot frees itself after the generation deadline + 60 s |
| `LATTICE_RATE_LIMIT_ACCESS_WRITES_PER_MINUTE` | each administrator (previews, changes, grants, revocations) | 60 | PostgreSQL |
| `LATTICE_RATE_LIMIT_SCIM_PER_MINUTE` | each SCIM token | 600 | PostgreSQL |
| `LATTICE_RATE_LIMIT_SIGN_IN_PER_MINUTE` | each client (IPv4 address or IPv6 /64): development sign-in, OIDC start and callback, failed SCIM authentication | 30 | PostgreSQL `pre_auth_rate_limits` through `lattice_pre_auth_hit` (P11.4), keyed by HMAC under `LATTICE_CLIENT_KEY_SECRET` — no address stored |

Fixed windows: a burst of up to twice a limit can straddle a window boundary. **Every
limit is shared by all API processes** (P11.4, D-24; [deployment.md](deployment.md) §12):
adding instances never raises a limit. The client address is the
client the API derives (P11.1): the TCP peer, unless that peer is listed in
`LATTICE_TRUSTED_PROXIES` (the ingress), in which case the right-most untrusted address of
`X-Forwarded-For`. Set it to the ingress addresses only (never the web server's: under
`next dev` the web server is the peer and every browser is one client), and keep uvicorn's
`--no-proxy-headers`. Provider quotas and the kill switches above still bound cost. A
member stuck at 429 `ask_concurrent` after an API crash waits for the lease to expire
(deadline + 60 s); there is nothing to clear by hand.

### 7.5 The worker

| Situation | Action |
| --------- | ------ |
| Requests stay *Queued* | Is a worker running (`lattice_worker_polls_total` increasing)? At startup it exits 1 with a one-line reason (configuration, role, schema revision, database unreachable): run it under a supervisor that restarts it. Once running, it survives database outages by itself |
| A job failed | The Sources row shows the code; the job's run (if one opened) has the details. `source_config:*` codes need the operator's `sources.yaml` and a CLI sync; `source_unavailable:*` the export path |
| `source_busy` | Another sync of that source (CLI or worker) holds its lock; the request is retried after 60 s × attempt, up to 3 attempts |
| A worker died mid-job | Its lease expires (`LATTICE_WORKER_LEASE_SECONDS`, 300; renewed while the sync runs) and another poll retries it; after 3 attempts the job is `failed` / `job_abandoned`. The interrupted run is recorded `aborted` / `internal_error:interrupted` by the next sync of that source |
| Retention (P11.4) | Once an hour (and at start) the worker deletes, in batches of 500: finished jobs after 30 days, traces after `LATTICE_TRACE_RETENTION_DAYS` (90), superseded sync runs after 180 days (each source's latest run is kept), ended sessions after 30 days, login attempts a day after expiry, counters after 2 days, Ask slots an hour after their lease ([deployment.md](deployment.md) §13). The database refuses deleting anything younger than each floor. Audit events and identity tombstones are never deleted |
| Retention failing | `lattice_maintenance_failures_total{task}` rising or `lattice_maintenance_last_success_timestamp_seconds{task}` older than a few hours; the `maintenance.retention_failed` log event names the task and the error type. Other tasks keep running; the failed one is retried at the next run. Typical causes: database unreachable or a statement timeout on a very large backlog (it resumes where it stopped, batch by batch) |
| Embeddings of requested syncs | `LATTICE_SYNC_EMBEDDING` (default `none`: new chunks stay `pending` until a sync with a provider). Use the provider the corpus was synced with |

### 7.6 Deployment checklist

Production refuses to start on any of: `LATTICE_ENV` unset; development login enabled;
insecure cookies; `LATTICE_PUBLIC_URL` unset or not `https://`; `LATTICE_QUERY_EMBEDDING=fake` or `LATTICE_SYNC_EMBEDDING=fake`; an
unsafe runtime role; a schema at another revision; an invalid operational setting. In
addition, before a first deployment:

1. Three database credentials in the platform's secret store: runtime (`lattice_app`),
   worker (`lattice_worker`), owner (`lattice_owner`, migration job and operators only — a
   production api or worker refuses to start if the owner credential is in its
   environment). Bootstrap with the platform's administrative role (CREATEROLE + CREATEDB
   suffice): `psql "<admin connection>" -v owner_password=… -v app_password=… -v
   worker_password=… -f infra/postgres/bootstrap_production.sql` (in the backend image
   under `/opt/lattice/app/infra/postgres/`); it fails loudly if a role is unsafe. Enable
   `vector` through the platform first if its admin role cannot create it. Never use a
   superuser for anything else.
2. Order: `lattice migrate` → `lattice check-deployment` → start API and worker →
   readiness passes → route traffic (deployment.md §9).
3. Metrics listener on an internal interface; alert on
   `lattice_security_invariant_violations_total > 0`, readiness failures,
   `lattice_dependency_failures_total`, and a growing `lattice_jobs_waiting`.
4. Identity: connect each organization's OIDC provider and SCIM (§9); set
   `LATTICE_PUBLIC_URL`; keep `LATTICE_OIDC_SECRET_*` in the secret store; set
   `LATTICE_TRUSTED_PROXIES` to the ingress addresses (§7.4).
5. Validate each process's configuration in its image without starting it:
   `lattice check-config api|worker|migrate` (names problems, never values).
6. Images, the reference stack, publishing and the release procedure exist (P11.1, P11.2;
   §7.9, §7.10); retention (P11.4), backups (P11.5) and alerting (P11.6) are **not
   built**. No cloud staging exists (D-22 amended: deferred, no budget). The production
   contract is [deployment.md](deployment.md), which marks each item built or target.

### 7.7 Backups, recovery and migrations

Everything Lattice holds is in PostgreSQL (ADR-002): no second store to back up. A
production platform must provide continuous WAL archiving / PITR and daily base backups
([deployment.md](deployment.md) §11); the platform restores, Lattice verifies. On the
local reference stack PostgreSQL's own tools do both (`deploy/backup.sh`, P11.5).
Managed-platform backups and cloud RPO/RTO are **not verified** (deferred, no budget;
phase-11-plan §21.1).

| Task | Procedure |
| ---- | --------- |
| Back up (reference stack) | `deploy/backup.sh backup`: `pg_basebackup` into the `pgbackups` volume + a row-count manifest in `deploy/state/backups/`; WAL is archived continuously (`archive_timeout` 60 s). `deploy/backup.sh manifest <name>` records a manifest of the live database now (a recovery point to verify against) |
| Back up (managed platform) | The platform's automated backups and PITR. Before relying on a recovery point, take a manifest: `lattice verify-restore --write-manifest > manifest-<time>.json` as `lattice_owner` |
| Restore | Restore into a **new** instance on an isolated network (platform: "restore to a new cluster" at a time; reference stack: `deploy/backup.sh restore <id> [--target-time '<ISO time>'] [--manifest <file>]`). Never overwrite the primary. Then, before any traffic: `lattice verify-restore --manifest <file>` (owner), `lattice check-db-role` (app), `lattice worker --once` (worker), an api instance to readiness, and a scan for configured secret values — the script runs all of them and prints the recovery time. Then point the deployment at the restored instance and deploy as for a migrating release (§7.10) |
| `verify-restore` fails `row counts` | The copy does not hold what the manifest counted: a wrong recovery target (earlier or later than the manifest), or missing WAL. The difference names the organization and table; pick the right manifest or target. Never serve a copy that fails |
| `verify-restore` fails `audit integrity` / `privilege snapshot` | A trigger, policy or grant differs from the build's: the backup is from another schema state, or someone changed privileges by hand. Do not serve it; investigate |
| WAL archiving failing | `SELECT archived_count, failed_count, last_failed_wal, last_failed_time FROM pg_stat_archiver` (reference stack); platform: its backup status. PostgreSQL keeps unarchived WAL, so the disk fills: fix the archive destination first |
| Restore drill | Monthly at least (alert "restore drill overdue", §10): restore the latest backup to a point in time, verify, discard (`deploy/backup.sh discard-restore`), record the date and durations in §8 |
| Migrate | Take a backup; stop every api instance and the worker (stop-the-world, D-23); run **`lattice migrate`** with the new image as `lattice_owner` (lock, fence, pre-flight, one transaction, exact verification — deployment.md §9.2); start the new build. `deploy/deploy.sh release --allow-migration` does all of it (§7.10). Never `alembic upgrade` by hand in a deployment, never edit `alembic_version` |
| `lattice migrate` refused: `application sessions are still connected (lattice_app/lattice-api xN, …)` | An api instance or the worker is still running against the database. Stop it (every replica) and run again; nothing was changed |
| `lattice migrate` refused: `another migration holds the migration lock` | Another `lattice migrate` is running (`lattice migrate --status` shows `migration_running: True`). Wait for it; never run two |
| api/worker fail with `permission denied for database "lattice"`; `check-deployment` says `a migration fence was left in place` | A migration was killed after fencing (its transaction rolled back). Run `lattice migrate --status`, then `lattice migrate` (finishes and lifts the fence) or `lattice migrate --release-fence` |
| `lattice migrate` failed (`FAILED: …`) | Its transaction rolled back: the schema is the old revision. Restart the previous build (the script does); fix forward |
| Downgrade | Only `deploy/deploy.sh rollback --allow-downgrade`, and only when every revision being undone declares `Reversible: yes` (`lattice migrate --downgrade-to <rev> --check` decides; of 0001–0015 only 0002, 0010, 0012 do). Otherwise roll forward or restore |
| Check a deployment's database | `lattice check-deployment [--api-instances N --api-pool-size N]` as `lattice_owner`: exact schema and no model drift, safe roles, forced RLS, no PUBLIC access or fence, privilege snapshot, connection budget. A privilege-snapshot difference after a migration that changed privileges: `make privilege-snapshot` and review the diff; anywhere else it is drift — find who granted what |
| Rebuild derived data | Chunks, vectors and the lexical index are derived from the sources: `lattice reindex` rebuilds them without deleting documents |

### 7.8 CI and releases

CI (`.github/workflows/ci.yml`) pins its runner image (`ubuntu-24.04`, not `ubuntu-latest`),
the uv version and each action's major version (all on Node.js 24; `setup-uv` by exact
version, since it publishes no major tags). Update them deliberately, one commit at a
time, and check the run. Each job has a 30-minute timeout. The backend job lints, type
checks, bootstraps a clean database with all four roles, round-trips the migrations,
seeds, verifies the API's and the worker's startup checks, syncs twice, runs both
evaluation security gates and the full test suite; the web job checks the generated API
contract, lints, type checks, runs unit tests, builds and runs the browser tests; the
dependency-audit job (Phase 10) runs `make audit` (pip-audit over the locked backend set
with hashes, `pnpm audit` over the web lockfile). A new advisory fails only that job:
upgrade the package (or document why it does not apply) in a reviewed commit.

**Images (P11.2, D-27).** The `images` job builds both images, starts the reference
stack from them, smokes and checks it, and publishes `sha-<commit>` (and `vX.Y.Z` on a
tag) to GHCR with the run's token — only on a push to `main`, a `v*` tag (after the three
jobs of the same run) or a manual dispatch. To publish an unmerged revision for future
staging: push it, wait for its CI run to pass, then *Actions → CI → Run workflow* on that
branch with a reason; the test jobs are skipped and the job refuses unless a successful
push run of that exact commit exists. Deploy by the digest in the job summary.

**Audit exceptions.** When no fix exists, an advisory may be accepted, one at a time and
by its GHSA identifier only, in `apps/web/pnpm-workspace.yaml` (`auditConfig.ignoreGhsas`)
with a row here; `src/test/audit-exceptions.test.ts` fails if the two disagree or the
audit command is weakened (`--ignore-unfixable`, a higher `--audit-level`, `ignoreCves`).
Remove a row as soon as its condition no longer holds.

| Advisory | Package and path | Why accepted | Remove when |
| --- | --- | --- | --- |
| GHSA-vfj7-8cjw-p6xm (CVE-2026-93687, high), accepted 2026-10-03 | `braces` ≤3.0.3, via `micromatch` on two dev-only paths: `eslint-config-next` › `@next/eslint-plugin-next` › `fast-glob` › `micromatch`, and `stylelint` (directly and through `globby` › `fast-glob`) › `micromatch` (re-checked 2026-10-03) | No patched release exists. Dev lint tooling only, never in the production bundle; already on `main` before the design overhaul (the advisory record was updated 2026-10-02) | A patched `braces` reaches that chain, or `braces` leaves the lockfile |

### 7.9 Local reference stack — *implemented (P11.1; verification drivers P11.3–P11.7)*

The production topology on one machine ([deployment.md](deployment.md) §15):
`compose.production.yaml`, Compose project `lattice-reference` (never the development
database). Stop the development database first on the reference machine (`make db-down`;
Docker has 3 GB).

| Situation | Action |
| --------- | ------ |
| Build the images | `make prod-build` (labels them with the current git revision) |
| Start / check / stop | `make prod-up` (waits for every health check), `make prod-smoke`, `make prod-ps`, `make prod-down`; `make prod-destroy` deletes its volumes |
| Open it | `https://localhost:8443` (Caddy's internal CA: the browser warns). `http://localhost:8080` redirects |
| Sign in as a persona / run an Ask | `make prod-up VERIFY=1 [FAKE_DELAY=40]`: test env, development sign-in, fake providers (deploy/compose.verify.yaml); then `make prod-seed VERIFY=1` (synthetic-only: seed with `--require-synthetic`, sync with fake embeddings, admin grants) |
| Database checks | `make prod-check` (`lattice check-deployment`), `make prod-migrate` (`lattice migrate --status`) |
| Re-run the verification | `make prod-up VERIFY=1 IDP=1 FAKE_DELAY=20`, `make prod-seed VERIFY=1 IDP=1`, then `make prod-limits`, `prod-authz`, `prod-drills` (each with `VERIFY=1 IDP=1`); `prod-perf` needs the API instances at `FAKE_DELAY=0`. Each prints PASS/FAIL per case and exits 1 on any failure; results are recorded in §8 |
| `bootstrap` fails with `password authentication failed for user "platform_admin"` | The volume was initialised before P11.2 (superuser bootstrap): `make prod-destroy`, then `make prod-up` |
| A container is `unhealthy` | `make prod-ps`; `docker logs lattice-reference-<service>-1`. api: readiness (database or schema). worker: heartbeat older than 300 s (frozen or blocked). web: `GET /` failing |
| Changed `deploy/caddy/Caddyfile` | Recreate Caddy (`… up -d --force-recreate caddy`): it is a single-file bind mount, so an editor that replaces the file leaves the container on the old one |
| `lattice smoke` cannot read the CA (`--ca-file is not a readable file`) | It runs as uid 10001; `make prod-smoke` copies only Caddy's public root certificate to `deploy/.reference-ca.crt` (git-ignored), makes it 0644 (Caddy stores it 0600, unreadable to another uid on a Linux host) and mounts it |
| Passwords | `deploy/.env.reference` (git-ignored, generated once by `make prod-env`); each service receives only its own credential |

### 7.10 Releases and rollbacks — *implemented (P11.2)*

`deploy/deploy.sh` (reference stack; the steps are the contract of deployment.md §9 for
any platform). One deployment at a time; every image is resolved to its immutable ID
first; the log is `deploy/state/deploy.log`.

| Situation | Action |
| --------- | ------ |
| What is running | `deploy/deploy.sh status` (image ID and revision per service, database revision, open application sessions, migration lock) |
| Release without a schema change | `deploy/deploy.sh release --backend <ref> --web <ref> --expect-revision <sha>`: rolling, readiness-gated, no downtime, smoke with every process's revision |
| Release with a migration | the same plus `--allow-migration` (refused without it): stop-the-world, `lattice migrate`, unavailable interval printed (19 s measured) |
| `FAILED: revision mismatch` | Backend and web images are from different commits, the image's label and baked revision differ, or `--expect-revision` differs. Build or pull the pair from one commit |
| `FAILED: configuration of <process> is invalid` / `the stack configuration is incomplete` | Nothing was changed. Fix the named variable (or the image's baked setting) |
| `FAILED: <service> did not become ready … previous image restored` | That instance runs the previous image again; the others never changed. Read `docker logs lattice-reference-<service>-1` (startup refusals name the problem) |
| `FAILED: smoke failed after deploying …` | The new release is running: `deploy/deploy.sh rollback` (no-schema releases), then investigate |
| `FAILED: schema change did not complete; previous build … restarted` | The migration failed or was refused (its transaction rolled back); the previous build serves again, maintenance lifted |
| `FAILED: the new build did not become ready after the schema change; maintenance stays on` | Roll **forward**: the previous build cannot serve the new schema. Fix the configuration or deploy a fixed build of the same schema; `/api` answers 503 until then |
| Roll back | `deploy/deploy.sh rollback` — the previous release by ID, rolling. Across a migration: refused (`the database … is newer than this build: roll forward`) unless `--allow-downgrade` and every undone revision is reversible |
| `FAILED: another deployment is running` | Another `deploy.sh` holds `deploy/state/lock`; remove the directory only if none runs |

## 8. Failure drills — *run for Phase 8 (2026-09-29) and Phase 11 (P11.1–P11.7, local reference stack)*

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

**P11.1 drills (2026-10-03), local reference stack** (two API instances behind Caddy,
fake providers where an Ask was needed):

| Drill | How | Observed |
| ----- | --- | -------- |
| Clean start | first `make prod-up` on empty volumes | PostgreSQL, migrations 0001–0013, api1, api2, worker, web, Caddy healthy in 35 s; `make prod-smoke` 10/10 |
| Both instances serve | 20 × `GET /api/v1/health/live` through Caddy | 10 to api1, 10 to api2 (their own request logs) |
| No `/api` on the web server | `GET http://web:3000/api/v1/health/live` from the private network | the web server's 404 (no proxy) |
| Spoofed `X-Forwarded-For` through the ingress | 70 OIDC starts from the host, each with a different `X-Forwarded-For` | one client: 60 allowed (30 per API process), 10 `rate_limited` |
| A real second client | 10 OIDC starts from another container through Caddy | all allowed: counted as a different client |
| Spoofing directly at an API instance | 31 OIDC starts to `api1:8000` from inside the network, each spoofing a different `X-Forwarded-For` | one client: 30 allowed, the 31st `rate_limited` |
| Long Ask | Maya, `FAKE_DELAY=40`, through `https://localhost:8443/api/v1/ask` | HTTP 200 `answered` after 40.4 s (2 claims, 12 evidence) — past Next.js's old 30 s cut |
| API SIGTERM during an Ask | api2 stopped; a 40 s Ask started on api1; `docker compose stop api1` 5 s later | the Ask completed (200 `answered`, 40.3 s); the stop took 36 s; clean shutdown (pool disposed) |
| API restart under traffic | 60 readiness requests over 30 s through Caddy while api1 restarts | 60 × 200; both instances healthy again |
| Worker SIGTERM during a job | a requested sync held inside its job by a table lock; `docker compose stop worker` | the worker finished the job (`succeeded`, 1 attempt) and exited after 17 s |
| Worker SIGKILL during a job | the same, `docker kill -s KILL` | job stayed `running`; the restarted worker (healthy at once) re-ran it after the 30 s lease: `succeeded`, 2 attempts; no job left running |
| Frozen worker | SIGSTOP the worker process | `lattice smoke` worker check fails at once (its metrics listener stops answering); Docker health `unhealthy` after 5 min 47 s (300 s + 3 checks); SIGCONT → healthy in 15 s |
| Request bodies | 70 KB and 2 MB `POST /api/v1/ask` through Caddy | both the API's typed 413 `request_too_large` (it refuses on `Content-Length`) |
| Exposure | host ports 8000, 9464, 9465, 3000, 5432, 2019; `/metrics`, `/api/metrics` through Caddy | all refused / 404; only 8443 and 8080 published, on 127.0.0.1 |

**Performance sanity (M1 8 GB, heavily swapped, dev data, fake providers).** Ask p50
0.26 s over 8 requests (the first, cold, 11 s); `GET /v1/sources` p50 43 ms,
`/v1/activity` 31 ms, `/v1/admin/sources` 30 ms, `/v1/admin/activity` 21 ms; a requested
sync of 148 unchanged tickets 1.7 s, of Drive 0.3 s; API resident memory about 77 MB.
No model benchmark was run (ADR-012).

**P11.2 drills (2026-10-05), local reference stack in production mode** (two API and two
web instances behind Caddy, the managed-PostgreSQL emulation, release images built from
the working tree under drill revision labels, `deploy/drills/` variants; traffic: one
request each to `/api/v1/health/ready` and `/` through the ingress every ~0.2 s):

| Drill | How | Observed |
| ----- | --- | -------- |
| Production bootstrap without a superuser | fresh volumes, `make prod-up` | `bootstrap_production.sql` as `platform_admin` (CREATEROLE, CREATEDB only) created roles, database, extensions and grants, idempotent on re-run (password rotation works); `lattice migrate` empty → 0013 in 0.5 s; smoke 13/13, `check-deployment` 7/7 (privilege snapshot identical to the superuser-bootstrapped test database) |
| Migration with API and worker running | `ops migrate` | `REFUSED: application sessions are still connected (lattice_app/lattice-api x2, lattice_worker/lattice-worker x1)`; nothing changed, fence lifted |
| Migration with only the worker running | api ×2 stopped | refused, naming `lattice_worker/lattice-worker x1` |
| Migration with both stopped | api ×2 and worker stopped | `OK: 0013 -> 0013 (applied: none)` |
| Concurrent migration | another `lattice_owner` session holds the migration lock 15 s | `--status` showed `migration_running: True`; `REFUSED: another migration holds the migration lock (waited 4 s)`; after release the migration ran |
| Revision mismatch | `--expect-revision` differing; backend and web from different commits | both refused before any change |
| Missing secret / invalid setting | `LATTICE_APP_PASSWORD` empty; an image with `LATTICE_RATE_LIMIT_ASK_PER_MINUTE=0` | `the stack configuration is incomplete`; `LATTICE_RATE_LIMIT_ASK_PER_MINUTE: Input should be greater than or equal to 1` from `check-config`; nothing changed |
| Rolling release and rollback (no schema change) | A → B → A → B, each ~70 s, under traffic | 765 requests, **0** failed (api and web); smoke 13/13 with the expected revision on api1, api2 and the worker |
| First attempt with one web instance | the same, before `web2` existed | API 0 failed, web 35 × 502/503 (a single instance cannot be replaced without a gap, plus the ingress's re-check): the reason the stack runs `web1`/`web2` and waits for the health check after each replacement |
| Readiness never passes | an API listening on the wrong port | api1 unhealthy, restored to its previous image, deployment failed; 324 requests, 0 failed |
| Ready but smoke fails | a production API serving `/docs` | rolled out, smoke `FAIL production API surface`, deployment failed; `rollback` → 13/13 |
| Failing migration | revision 0014 fails (division by zero) inside its transaction | maintenance on, api/worker stopped, `lattice migrate` failed, previous build restarted, maintenance off; database still 0013; `/api` 503 for 11 s, web 0 failed |
| Migrating release | 0013 → 0014 (a 2 s drill revision), `--allow-migration`, under traffic | lock, fence, pre-flight, upgrade, verify; **API unavailable 19 s** (target < 2 min) answering the maintenance 503 (`service_unavailable`, `Retry-After: 30`, the API's CSP); web 0 failed of 350; smoke 13/13 |
| Schema mismatch | the 0013 build started against 0014 | `Refusing to start: … database schema is at revision 0014, this build expects 0013`; `check-deployment` `FAIL schema revision`; recovered by rolling forward |
| Rollback across a reversible migration | `rollback` (refused: `roll forward`), then `rollback --allow-downgrade` | `allowed: would undo 0014`; stop-the-world downgrade by the running build; API unavailable 16 s; back on the 0013 release, smoke 13/13 |
| Rollback across an irreversible migration | `rollback --allow-downgrade` | `REFUSED: revision(s) 0014 declare no data-safe downgrade` before anything stopped |
| Migration killed mid-transaction | `docker kill` during the drill revision's upgrade | transaction rolled back (still 0013); new api connections `permission denied for database "lattice"` (fail closed); `check-deployment` reported the fence and the lock still held by the dying session; `lattice migrate --release-fence` → `check-deployment` 7/7 |

(At P11.2 the schema head was 0013, so the drill revision was 0014; `deploy/drills/` now
numbers it one above the base image's head.)

**P11.3 identity verification (2026-10-05), local reference stack in production mode**
(`make prod-up IDP=1` + `make prod-identity`: two API instances behind Caddy,
`LATTICE_ENV=production`, the verification OIDC provider over HTTPS, personas provisioned
over SCIM through the ingress; which instance served each step read from its own
metrics). **42/42 passed in 123 s**, then `check-deployment` 7/7 (privilege snapshot
including 0014) and smoke 13/13:

| Case | Observed |
| ---- | -------- |
| Sign-in across instances | start on api2, callback on api1 → `/ask`; the session answered 200 on both instances |
| Replay and binding | the used callback refused (`sso_failed`); a callback from another browser (with its own binding cookie) and from a browser with none refused |
| ID-token time boundaries | `exp` −59 s accepted, −61 s refused; `iat` +59 s accepted, +61 s refused (60 s leeway) |
| Logout | 204; the kept token 401 on both instances |
| `member suspend` / `reinstate` | the live session 401 on both, sign-in refused; after reinstatement the old session stays 401 and a new sign-in works |
| SCIM deactivate / remove | 200 / 204; the live session 401 on both; sign-in refused |
| SCIM token rotation | both tokens 200 on both instances during the overlap; after `scim token revoke` the old one 401 on both at once, the new one 200 |
| Identity-provider outage | provider stopped between authorization and callback → `sso_unavailable`; an existing session 200 on both throughout; after restart (new signing key, new key ID) sign-in works once the JWKS is refetched |
| `sso disable` / `enable` | 6 sessions revoked; the session 401 on both; sign-in and the SCIM token refused; after `enable` the same provisioned member signs in, the revoked session stays 401, the SCIM token works again |
| `sso update --client-id` | sign-in with the new client ID works; existing sessions kept |
| Secret rotation, same reference | provider holds both secrets, rolling restart of api1 then api2 with the new value (13 s), provider drops the old: sign-in works, sessions survive the restarts; rotated back the same way |
| Secret rotation, new reference | provider rotated first → `sso_unavailable` (no session affected); `sso update --client-secret-ref env:LATTICE_OIDC_SECRET_VERIFY_NEXT` → sign-in works, sessions kept |
| Clock skew | `lattice_clock_skew_seconds` reported by both instances, ≈0 s |

In-process (`tests/identity/test_two_instances.py`, two applications on one database,
one cookie jar) the same matrix plus: the race of a sign-in with `sso disable` (the
callback waits on the connection lock, then refuses; no session), the idle and absolute
expiry edges (±30 s), the issuer rename with `--subjects-unchanged` (sessions revoked,
same members sign in), and a development sign-in session untouched by `sso disable`.
Removing the shared lock or the revocation fails those tests (mutation check by hand, not
committed).

**P11.4 shared limits, retention and worker drills (2026-10-05)**, local reference stack
(`make prod-up VERIFY=1 IDP=1 FAKE_DELAY=20`, `make prod-seed VERIFY=1 IDP=1`,
`make prod-limits VERIFY=1 IDP=1`: the verification overlay's persona sign-in and fake
model waiting 20 s, Halden's generation provider set to the fake model, each API
instance's own metrics read for every step). **10/10 passed**, then `check-deployment` 7/7
(0015, privilege snapshot) and smoke 12/12:

| Drill | Observed |
| ----- | -------- |
| Retention in the worker | every task (`jobs`, `query_traces`, `source_sync_runs`, `sessions`, `oidc_login_attempts`, `rate_limit_counters`, `pre_auth_rate_limits`, `ask_slots`) reported a last success at worker start; 7 restrictive floor policies installed |
| In-flight Asks across instances (limit 2) | three concurrent Asks of one member through Caddy: `[200, 200, 429]` in 20 s; the 429 came from an instance that held only one of the member's Asks itself — refused because the other instance held the second (process-local, it would have been accepted); no slot left afterwards |
| Pre-authentication across instances (30/min) | 40 sign-ins in a fresh window: exactly **30 accepted (15 per instance)**, then 10 × 429 split 5/5 — one shared count (process-local it would have been 60) |
| Worker killed mid-job | the source's row held so the sync blocked at its last statement; `docker kill` with the job `running:1`; worker restarted; the job was reclaimed when its 30 s lease expired and finished `succeeded:2`; the interrupted run was recorded `aborted` by the retry |
| Worker stopped (SIGTERM) mid-job | `running:1` when stopped; the worker finished the job (`succeeded:1`) before exiting |


**P11.5 backup and restore drill (2026-10-05)**, local reference stack in production mode
(Halden and Veridian synthetic data, 60 MB; `deploy/backup.sh`):

| Step | Observed |
| ---- | -------- |
| WAL archiving | `archive_mode=on`, segments in `pgarchive`, `pg_stat_archiver` 0 failures |
| Base backup | `pg_basebackup` 0.9 s (60 MB) + manifest (2 organizations, 1,966 tenant rows) |
| Changes after the recovery point | manifest `before-activity` (10:43:51); `lattice session revoke --all` (17 sessions) and `lattice member suspend` (2 audit events); manifest `after-activity` (10:44:00) |
| Restore to the latest WAL | recovered in 2.4 s; `verify-restore` 9/9 (row counts equal `after-activity`: 1,968 rows); `check-db-role`, `worker --once`, api readiness PASS; secret scan: none of 9 configured values in the backup, the archive or the copy |
| Restore to a point in time (10:43:52.5) | recovered in 2.4 s; 9/9 with row counts equal `before-activity` (the two later audit events absent); the same startup checks and scan PASS |
| Sensitivity | the point-in-time copy checked against `after-activity` FAILS `row counts` (`halden.audit_events: 28 rows, expected 30`); the scanner finds a planted known string in 4 files of the copy (so "none found" is meaningful); in tests, a disabled audit trigger fails `audit integrity` |
| After | `discard-restore`, `make prod-up`, smoke 13/13 |

These are local durations on one laptop with the backups on the same disk. They are
**not** an RPO/RTO for any deployment; managed backup/PITR and cloud RPO/RTO remain
deferred (phase-11-plan §21.1).

**P11.7 deployed authorization suite, failure drills and performance (2026-10-06)**, local
reference stack (`make prod-up VERIFY=1 IDP=1 FAKE_DELAY=20`; the verification overlay's
persona sign-in, fake embeddings, the fake model waiting 20 s, each API instance's own
metrics read to see which instance served a step; synthetic data only).

`make prod-authz VERIFY=1 IDP=1` — **18/18 passed in 51 s**:

| Case | Observed |
| ---- | -------- |
| Route sweep (read from the API's routers) | anonymous: 22/22 member and administrator routes 401 `authentication_required`; SCIM without a token 7/7 401 |
| A member on administrator routes | 15/15 one identical 403 for Halden's identifiers and random ones; nothing of Halden's changed |
| Another organization's administrator | Ines (Veridian; capability granted for the sweep, audited, then revoked): 10/10 identified routes answer Halden's real source, member, document, grant, trace and evidence identifiers exactly like random ones (404, identical body); Halden's grants, Maya's access, jobs and audit trail unchanged; Veridian's member directory names no Halden member |
| Administrator ≠ content (SEC-15) | Lena reads Maya's trace as `operational` (no question, no answer); Maya reads her own as `asker`, re-authorized |
| Revocation | Lena suspends Maya on the Access page (PATCH 200): Maya's live session 401 on api1 and api2 at the next request; reactivation revives no session; a new sign-in 200 on both |
| Roles | `check-deployment` 7/7; a requested sync per organization ran in the worker (`succeeded`) |
| Eval security gates inside the stack | an operator job on the private network against `lattice_app`, golden set mounted read-only: retrieval 89 items, answer 96 items, **every security count 0** |
| Not vacuous (hand mutation, not committed) | given Veridian's own identifiers as the "foreign" set, the sweep FAILS on the routes that answer 200 |

`make prod-drills VERIFY=1 IDP=1` — **18/18 passed in 205 s**:

| Drill | Observed |
| ----- | -------- |
| API instance killed (SIGKILL) during an Ask | one Ask in flight on each instance; api1 killed 3 s in: the Ask on api2 completed (200 `answered`, 20.1 s), the one on api1 failed at the ingress in 4.1 s with 503 `service_unavailable` (no hang); 10/10 readiness through Caddy while api1 was down; api1 restarted |
| ... its Ask slot | still held after the kill (the member: two concurrent Asks → `[200, 429]`); freed when the lease expired (≈ 3 min after it was taken): `[200, 200]` |
| API instance stopped (SIGTERM) during an Ask | both instances serving an Ask; `docker compose stop api1` 3 s in: both Asks completed (200 `answered`, 20.1 s); the stop took 18 s; 32/32 readiness through Caddy while api1 drained; no slot left behind |
| Database stopped 18 s | each instance: `/v1/me` 503 `service_unavailable` + `Retry-After: 5`, readiness 503, liveness 200; through Caddy 503 + `Retry-After: 5` with the API's envelope at once and after its health checks marked both instances down; after `start` the same session served again at once (0.0 s after PostgreSQL was healthy), no API or worker restart; `lattice_dependency_failures_total{dependency="database"}` +16; a requested sync then succeeded |
| Generation provider unavailable | Halden's generation set to an `ollama` `local` record on the instance's loopback port 11434, where nothing listens (no model server runs): the Ask answered 200 `failed` / `provider_unavailable` with 12 evidence items in 0.1 s; generation-call counter +1 (provider-surge alert input); the fake model reconfigured → `answered` |
| Found by the first run (fixed, then re-run) | the database case answered **500** `internal_error` on `/v1/me` (the stopped service's name no longer resolved: `socket.gaierror`, not mapped) and Caddy answered its own empty 502/503 when an instance died or none was ready. Fixed in the API's handler and with Caddy `handle_errors` (deployment.md §4, §10) |

**Earlier evidence for the remaining §18 rows** (phase-11-plan §18): source sync failure —
the Phase 8 drill above (`partial`, `access_unmappable:account`; missing export →
`source_unavailable:missing_export`) and the repeated-sync-failure alert's rule tests
(P11.6); revoked secret — P11.3 (new secret reference before the provider → `sso_unavailable`;
revoked SCIM token 401 on both instances) and the Phase 6 credential tests
(`credential_unavailable`); clock skew — the gauge on both instances (P11.3) and the
clock-skew alert's rule tests (P11.6); worker kill/restart — P11.1 and P11.4; migration
failure and rollback — P11.2; backup restore and PITR — P11.5 and the restore drill below.

**Performance sanity** (`make prod-perf VERIFY=1 IDP=1` at `FAKE_DELAY=0`; through Caddy,
sequential, both instances, fake embeddings and the fake model, so an Ask is retrieval,
authorization, trace and pipeline without a model; M1 8 GB, Docker at 3 GB):

| Request (through Caddy) | n | p50 | p95 | max |
| ----------------------- | - | --- | --- | --- |
| `GET /api/v1/me` | 200 | 4.2 ms | 5.9 ms | 17.9 ms |
| `GET /api/v1/sources` | 200 | 8.5 ms | 27.4 ms | 147.3 ms |
| `POST /api/v1/ask` (4 members × 10) | 40 | 52.0 ms | 90.5 ms | 216.6 ms |

A local regression signal, not a capacity figure.

**Final gate** — backend 2,367 passed; lint and mypy clean; web 211 unit, production build, Playwright 290 passed (desktop, mobile, production; no retries locally — the first branch CI run then caught `primitives.spec.ts:85` failing twice: its hydration race and the same one in `shell.spec.ts:309` were fixed, phase-11-plan §25.8); `make audit` green after GHSA-68fv-2mgg-jv7q (`source-map-js`) was fixed by the lockfile (allowlist unchanged); `make alerts-test` SUCCESS; production-mode stack from `make prod-build`: smoke 13/13, `check-deployment` 7/7; restore drill: base backup 1.0 s (65 MB), recovered 2.9 s, `verify-restore` 9/9 (2 organizations, 2,534 tenant rows), `check-db-role`, `worker --once`, api readiness, secret scan (none of 9 values) PASS; then discarded and smoke 13/13. Local durations; not an RPO/RTO.

## 9. Identity: SSO, SCIM provisioning and administrators — *implemented (Phase 10; lifecycle and incident commands P11.3)*

ADR-015, decisions D-1, D-19, D-20. Every command runs as `lattice_owner`
(`LATTICE_MIGRATION_DATABASE_URL`), needs `--reason` where it changes something, and
records an audit event (no secret or secret reference in it). URLs are printed from
`LATTICE_PUBLIC_URL`.

| Task | Procedure |
| ---- | --------- |
| Connect an organization's identity provider | In the provider, create an OIDC web application: redirect URI `<public URL>/api/v1/auth/oidc/<login key>/callback` (printed by the next command; register it after), grant type authorization code, PKCE S256, scopes `openid email profile`. Then `lattice sso add --org <slug> --issuer <issuer exactly as in its discovery document> --client-id <id> [--client-secret-ref env:LATTICE_OIDC_SECRET_<NAME>] [--subject-claim <claim>] --reason "…"`. It prints the sign-in URL (`/sso/<login key>`, also the provider's app-tile / initiate-login URL), the redirect URI and the SCIM base URL. One active connection per organization; the login key is opaque (it names no organization) |
| Choose the identifying claim | The claim's value must be stable and equal the SCIM `externalId` the provider sends: `sub` where the provider's `sub` is its user ID (Okta, Keycloak); `oid` for Microsoft Entra ID (its `sub` is per application) with `externalId` mapped to `objectId`. E-mail is never used to match a person |
| Turn on SCIM provisioning | `lattice scim token create --org <slug> --reason "…"` prints a token **once**: paste it into the provider's provisioning settings with the SCIM base URL `<public URL>/api/scim/v2`, auth "HTTP header / Bearer token". Map `userName`, `externalId` (as above), name, e-mail, `active`. The provider can then create, update, deactivate, reactivate and delete members. Groups are not provisioned (not supported); access is granted on the Access page |
| Create an organization in production | *Current:* there is no production path (`lattice seed` refuses `LATTICE_ENV=production`). *Phase 12 target (P12.1, not built):* `lattice org create` creates the organization, its OIDC connection and the first administrator bound to their exact IdP identifier in one audited transaction (phase-12-plan C1) |
| Give the organization its first administrator | After the person is provisioned: `lattice member admin --org <slug> --email <e-mail> --grant --reason "…"`. `--revoke` takes it away. Administration grants no access to content (SEC-15). This is the only way to change the capability |
| What a new member can read | Least access: clearance Internal, account scope "assigned" with no accounts, no departments, groups or grants — so documents that are Internal, not account-specific and not department-restricted. Everything else is granted on the Access page (previewed, audited) |
| Rotate or revoke a SCIM token | `lattice scim token create` (new), update the provider, then `lattice scim token revoke --org <slug> --id <token id> --reason "…"` (`lattice scim token list` shows IDs and states, never values). A leaked token: revoke first. A token is per organization and connection; it cannot reach another organization |
| Stop SSO for an organization | `lattice sso disable --org <slug> --reason "…"`: sign-in through it fails at once, its SCIM tokens stop working, and **every unrevoked session it issued is revoked in the same transaction** (D-25; the count is printed and recorded; development sign-in sessions are untouched). A sign-in completing at that moment cannot leave a session behind: it holds the connection's advisory lock shared while it checks the connection and creates the session, and the disable holds it exclusively. Disabling is reversible: the connection keeps its ID, login key and directory identities |
| Re-enable SSO | `lattice sso enable --org <slug> [--connection-id <id>] --reason "…"` (`lattice sso list` shows every connection): the **same** connection becomes active again — the members it provisioned sign in as before. Refused while another connection of the organization is active. Sessions revoked by the disable stay revoked; SCIM tokens not revoked meanwhile work again |
| Change the client ID | `lattice sso update --org <slug> --client-id <id> --reason "…"`: in place, no session impact. Register the new client at the provider first (or keep both during the switch) |
| Rotate the client secret | **Same reference, new value:** add the new secret at the provider (most providers keep two client secrets during a rotation), change `LATTICE_OIDC_SECRET_<NAME>` in the secret store, restart the API instances one at a time (readiness-gated), then remove the old secret at the provider. **New reference:** put `LATTICE_OIDC_SECRET_<NEW>` into every API instance first (rolling restart), then `lattice sso update --org <slug> --client-secret-ref env:LATTICE_OIDC_SECRET_<NEW> --reason "…"`, then remove the old variable. `--no-client-secret` turns the client into a public PKCE client. No session is affected; the audit event says "reference (rotated)", never the reference or the value. A wrong or missing secret fails sign-in as `sso_unavailable` (`provider_error`) and touches no session |
| Change the issuer or the identifying claim | `lattice sso update --org <slug> --issuer <url> \| --subject-claim <claim> --subjects-unchanged --reason "…"`: allowed in place only with `--subjects-unchanged`, the operator's assertion that every person keeps the same subject value (for example an issuer URL rename at the same provider); the connection's sessions are revoked. Moving to a provider whose subjects differ needs an identity mapping (Phase 12), not this |
| Add a new connection after a disabled one | `lattice sso add` is refused while a disabled connection still has provisioned (not removed) members: a new connection's directory identities start empty, so they would not map. Use `sso enable` / `sso update`; only if the organization deliberately starts a new identity namespace (its people will be re-provisioned under new identities), add `--new-identity-namespace` (recorded in the event) |
| A person was re-hired after removal | Removal is terminal for the identity provider: SCIM cannot re-provision over it (409, D-20). Either provision them under a new `externalId`, or restore the old membership deliberately with `lattice member reinstate --org <slug> --email <e-mail> --reason "…"` (audited; it keeps the access they had and the same directory identity, so the provider's existing `externalId` signs in again) |
| All administrators lost (left, removed or locked out) | `lattice member admin --org <slug> --email <e-mail> --grant --reason "…"` for a person who still has an active membership (reinstate first if needed). Administration grants no access to content |
| Identity provider lost or compromised | Compromised: `lattice scim token revoke` (every token), `lattice sso disable` (revokes its sessions), review the audit log for provisioning changes. Unavailable: existing sessions keep working (server-side sessions do not depend on the provider); new sign-ins fail as `sso_unavailable` until it returns. There is no break-glass sign-in (Phase 11 non-goal); manifest members of a seeded organization keep the development sign-in only outside production |
| Members created by the manifest | `make seed` organizations keep the development persona sign-in; SCIM never touches members it did not provision, and the manifest never touches provisioned ones |

## 10. Alert responses — *implemented (P11.6)*

The rules are `deploy/observability/alerts.yml` (Prometheus; tested by `make
alerts-test`, also in CI); what must be scraped is in [deployment.md](deployment.md) §16.
`page` = act now; `ticket` = act within a working day. Every alert names its section
below. Thresholds marked *baseline* come from the Phase 11 drills (§8) and are expected to
be retuned after real traffic.

### 10.1 Service unavailable

`LatticeServiceUnavailable` (page): the external probe of `/api/v1/health/ready` failed for
2 minutes. Silence it for the duration of a declared migrating release (§7.10: the ingress
answers 503 by design). Otherwise: `lattice smoke` from the private network says which
layer fails — ingress, both API instances, or only the public path. All instances not
ready + `database` failures → §10.10; `schema_revision` → §10.3; instances ready but the
probe fails → ingress or TLS (§10.9). Restarting an instance whose readiness fails on the
database or the schema does not help.

### 10.2 High 5xx ratio

`LatticeHigh5xxRatio` (page): more than 5 % of API responses are 5xx for 10 minutes.
`lattice_http_requests_total{status=~"5.."}` by `route` shows where; the `http.request`
log events carry the request IDs, `http.unhandled_exception` the exception type. 503
`service_unavailable` with `Retry-After` is the database (§10.10); 500
`security_invariant_violated` is §10.13. Roll back a release that introduced it
(§7.10, no migration: zero downtime).

### 10.3 Schema mismatch

`LatticeSchemaMismatch` (page): an API instance's readiness reports a schema revision its
build does not expect, for 10 minutes — a release stopped half-way, or a build deployed
against the wrong database. `lattice migrate --status` and `deploy/deploy.sh status`:
finish the release (roll forward) or roll back per §7.10 / deployment.md §9. Never edit
`alembic_version`.

### 10.4 Backups and WAL archiving

`LatticeWalArchiveFailing` (page): PostgreSQL's last WAL archive attempt failed and none
has succeeded since (`lattice_wal_archive_failing`, read by the worker from
`pg_stat_archiver`). Unarchived WAL accumulates on the database's disk and the recovery
point stops advancing: `SELECT * FROM pg_stat_archiver` for `last_failed_wal`, then fix the
archive destination (space, permissions, the platform's backup service) — §7.7.
**Not alert rules** (no honest metric from inside Lattice): the platform's backup and PITR
window (watch the platform's own backup status) and the **restore drill**, which is a
calendar control: run §7.7's restore drill at least every 35 days and record it in §8;
an overdue drill is a ticket for the operator on duty.

### 10.5 Provisioning burst

`LatticeProvisioningBurst` (page, security): the identity provider suspended or removed
more than 20 members, or created more than 100, within 10 minutes (*baseline*: ≤ 4 in the
drills). It may be a legitimate re-organisation — or a compromised provider or SCIM token
(threat T23). Check with the organization's identity administrator first. If it is not
expected: `lattice scim token revoke` (every token, `lattice scim token list`), then
`lattice sso disable` (revokes the sessions it issued), review the audit log
(`actor scim:<token>`), and restore access with the audited commands (§9).

### 10.6 Retention failing

`LatticeRetentionFailing` (ticket): a retention task has not succeeded for 3 hours (it runs
hourly). `lattice_maintenance_failures_total{task}` and the `maintenance.retention_failed`
log event name the task and the error type; §7.5 "Retention failing".

### 10.7 Storage high

`LatticeStorageHigh` (ticket, 80 %) and `LatticeStorageCritical` (page, 90 %): the database
is approaching its provisioned storage (thresholds are absolute in the rule file — set them
for the deployment). Check retention is running (§10.6), the WAL archive (§10.4: a failing
archive keeps WAL on disk), and growth by table; then grow the volume through the platform.
PostgreSQL stops accepting writes when its disk is full.

### 10.8 Clock skew

`LatticeClockSkew` (ticket): a process's clock differs from the database's by more than 5 s
for 10 minutes (`lattice_clock_skew_seconds`). OIDC accepts ID tokens with 60 s of leeway,
and sessions expire on the API's clock: fix the host's time synchronisation (NTP) before
the skew approaches a minute.

### 10.9 TLS certificate expiring

`LatticeTlsCertificateExpiring` (ticket): the public certificate expires within 14 days.
Renew it through the platform's ingress or certificate manager; a lapsed certificate
breaks the web app, SCIM and every OIDC callback at once.

### 10.10 Database unavailable

`LatticeDatabaseUnavailable` (page): API or worker calls to PostgreSQL keep failing for 2
minutes. The API answers 503 with `Retry-After` and readiness fails; the worker backs off
and resumes by itself (§2 "Database outage"). Check the platform's database status,
connections against the budget (`lattice check-deployment`), and credentials (a rotated
password not yet in the secret store).

### 10.11 Worker stopped or backlog

`LatticeWorkerStopped` (page): the worker is not scraped or its heartbeat is older than 5
minutes; `LatticeJobBacklog` (ticket): a sync has waited more than 15 minutes;
`LatticeJobsAbandoned` (ticket): a job ran out of attempts. §7.5: restart a hung worker
(a killed job is retried after its lease), look at `jobs.error` / `jobs.abandoned`, and run
the source with `lattice sync` to see the failure directly.

### 10.12 Repeated sync failures

`LatticeRepeatedSyncFailures` (ticket): three or more sync runs failed in 6 hours. §2 "A
sync run is partial or failed": the Sources page or `sync_record_failures` give the codes;
`source_unavailable:*` is the export mount, `source_config:*` the operator's `sources.yaml`.

### 10.13 Security invariant violation

`LatticeSecurityInvariantViolation` (page, immediately): retrieval returned evidence the
asker may not read, or an answer cited evidence it did not carry. Treat as an incident:
§2 `security_invariant_violated` (stop generation, capture the request and trace IDs,
compare `policy_version`, add a regression test before re-enabling).

### 10.14 Authentication failures

`LatticeAuthenticationFailures` (ticket): more than 50 refused sign-ins (`token_invalid`,
`invalid_state`), SCIM authentication failures and pre-authentication refusals in 10
minutes (*baseline*: about 6 of each in the identity drill). A broken integration (a
rotated client secret or SCIM token not updated — §9) or guessing; the shared
pre-authentication limit already slows the latter.

### 10.15 Rate-limit pressure

`LatticeRateLimitPressure` (ticket): a limit has refused more than 30 requests a minute for
30 minutes (`bucket` label; *baseline*: a 10-request burst in the drills). A stuck client
or script retrying, or a limit too low for real use (§2 "A member gets 429").

### 10.16 Provider failure surge

`LatticeProviderFailureSurge` (ticket): more than half of the model-provider calls fail for
10 minutes. §2 "Provider outage": answers degrade to evidence; switch to an approved
alternative or stop generation (§7.4).
