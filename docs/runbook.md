# Lattice — Operational Runbook

Status: §1 is implemented (Phases 1–2); §3 (embedding) Phase 4; §4 (retrieval) Phase 5. Everything else is **planned** unless marked
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
| `security_invariant_violated` alert | Treat as incident: the retrieval predicate returned an unreadable chunk. The `security.invariant_violated` log event (emitted by `assert_all_readable` since Phase 2) carries organization, membership, `policy_version`, counts and the offending document UUIDs. Capture trace ID; disable Ask for the org (`org_settings.ask_enabled=false`); investigate `authz` changes (compare `policy_version`); add a regression test before re-enabling | 6 / 11 |
| Provider outage | Ask returns `provider_unavailable`; evidence still shown. Switch provider via config if an approved alternative exists | 6 |
| Change embedding model or provider | `lattice reindex --reembed --embedding <provider>` per source (the new model is a new embedding space; old vectors are replaced, never mixed). For an external provider, estimate volume first (§5) | 4 |
| Organization deletion | Set status `deleting`; purge job; run verification query (0 rows per table for org) | 11 |

## 3. Model-provider record — *Phase 4 (embedding), Phase 5 closure (approval), Phase 6 (generation)*

As built: the first sync with `--embedding fake|local|gemini|openai` records the
provider in `model_providers` (purpose `embedding`, provider, model, deployment,
retention mode, training opt-out) approved for `internal` only. Raising (or lowering)
a recorded provider's approval is an explicit administrator action with a reason:

```
lattice provider approve --org halden --provider local --model mxbai-embed-large-v1@b33106f585b9 --level restricted --reason "local model; no egress"
```

It logs `provider.approval_changed`. It cannot create a record, and it does not change
`org_settings.external_egress_level`, which still caps every `external_api` provider;
no command changes that yet (the settings UI comes with BYOK, ADR-011). A `local`
provider is capped by its approval only. The next sync of each source embeds newly
eligible chunks or removes vectors that are no longer allowed.

Credentials today: `LATTICE_OPENAI_API_KEY`, `LATTICE_GEMINI_API_KEY` (or
`GEMINI_API_KEY`), read by the provider only, from the environment or the git-ignored
`.env`. They are never stored in `sources.config` or `model_providers`, and never
logged. A key never makes a provider eligible: data origin, deployment and approval
do (security-model §6): credential availability ≠ provider approval ≠ data-egress
authorization. Organization-owned credentials (BYOK) are scheduled for the Phase 6
backend, and their admin UX for Phase 7 (D-16, ADR-011). Environment variables remain
the development fallback. No evaluation or CI run needs any external key.

For each configured provider, record: provider, endpoint, model, deployment trust
class (`local` / `organization_controlled` / `external_api`; D-15, anything else is
refused), approved egress level (default `internal`; an `organization_controlled`
provider also needs an explicit `lattice provider approve` and an organization
ceiling, `org_settings.organization_controlled_level`, before anything is sent),
training opt-out status, data-retention mode, region, key owner, rotation date.
Raising an approved level or the org's external egress level requires a recorded
reason and produces an audit event. Admins will see this on the Sources/Settings page.

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
