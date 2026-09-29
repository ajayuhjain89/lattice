# Lattice — Operational Runbook

Status: §1 is implemented (Phases 1–2); §3 (embedding) Phase 4, provider configuration Phase 6; §4 (retrieval) Phase 5; §6 (answering) Phase 6. Everything else is **planned** unless marked
otherwise; each section is
marked with the phase that makes it real; sections are rewritten against the actual system
when that phase lands. Do not follow planned procedures on a live system.

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

### Database roles and credentials

| Role | Used by | Rule |
| ---- | ------- | ---- |
| `lattice_app` | API (and later the worker) via `LATTICE_DATABASE_URL` | The only runtime identity. The API refuses to start with any other kind of role; it never falls back to another credential |
| `lattice_owner` | `make migrate`, `make seed`, operator tools via `LATTICE_MIGRATION_DATABASE_URL` | Migration/bootstrap/administrative role. Never configured as the API's `LATTICE_DATABASE_URL`. In production its credential lives in a different secret from the runtime one and is available only to the migration job |
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

## 2. Procedures (planned)

| Situation | Procedure (planned) | Phase |
| --------- | ------------------- | ----- |
| A sync run is `partial` or `failed` | Sources page → run → failures by code. `access_unmappable:*` → fix source config (account/department/group mapping) then `lattice sync <source>`. `source_unavailable` → check export path/credentials | 4 / 8 |
| Deletion guard tripped | Inspect export completeness; if the deletions are genuine, re-run with `--allow-mass-deletion` after confirming with the source owner | 4 |
| Urgently revoke a person's access | **Works today (Phase 2), without a UI:** set the member's `status: suspended` in the org manifest and `make seed`, or as `lattice_owner` with the organization bound: `UPDATE memberships SET status = 'suspended', authz_updated_at = now() WHERE id = …`. Sessions are rejected and no authorization context can be resolved on the very next request. Narrower changes (account assignment, department, group membership, grant) are single-row writes with the same immediacy. No re-index, re-embed or cache flush exists or is needed. Admin UI: Phase 9 | 2 / 9 |
| Remove a source's content now | `lattice source disconnect <source>` → immediate soft delete; purge runs asynchronously | 4 |
| `security_invariant_violated` alert | Treat as incident: the retrieval predicate returned an unreadable chunk. The `security.invariant_violated` log event (emitted by `assert_all_readable` and, since Phase 6, `readable_document_classifications`) carries organization, membership, `policy_version`, counts and the offending document UUIDs. Capture trace ID; stop generation for the org (`lattice provider disable --purpose generation ...`; an `ask_enabled` kill switch is not built); investigate `authz` changes (compare `policy_version`); add a regression test before re-enabling | 6 / 11 |
| Provider outage | **As built (Phase 6):** Ask returns HTTP 200 with status `failed` / `provider_unavailable` and the evidence list; the trace's `error_code` says why (`generation_failed:timeout`, `rate_limited`, ...). Switch provider with `lattice provider configure` (a new record starts approved for `internal` only) if an approved alternative exists | 6 |
| Change embedding model or provider | `lattice reindex --reembed --embedding <provider>` per source (the new model is a new embedding space; old vectors are replaced, never mixed). For an external provider, estimate volume first (§5) | 4 |
| Organization deletion | Set status `deleting`; purge job; run verification query (0 rows per table for org) | 11 |

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
