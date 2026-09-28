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
| Change embedding model | `lattice reindex --reembed` per source; monitor cost estimate first | 4 |
| Organization deletion | Set status `deleting`; purge job; run verification query (0 rows per table for org) | 11 |

## 3. Model-provider record — *Phase 4 (embedding), Phase 6 (generation)*

As built (Phase 4): the first sync with `--embedding openai|fake` records the provider
in `model_providers` (purpose `embedding`, provider, model, deployment, retention mode,
training opt-out) approved for `internal` only. There is no command to raise
`approved_for_level` or `org_settings.external_egress_level` yet (Phase 6 adds an
audited CLI, Phase 9 the UI); until then they stay at the default. The OpenAI key is
read from `LATTICE_OPENAI_API_KEY` by the provider only (ADR-008).

For each configured provider, record: provider, endpoint, model, deployment
(external API / private), approved egress level (default `internal`), training
opt-out status, data-retention mode (standard / zero-retention), region, key owner,
rotation date. Raising an approved level or the org's external egress level requires
a recorded reason and produces an audit event. Admins see this on the Sources/Settings page.

## 4. Retrieval and its evaluation — *implemented (Phase 5)*

| Situation | Action |
| --------- | ------ |
| See what a member would retrieve | From `backend/`: `uv run --env-file ../.env lattice search --org halden --as <email> "<question>"` (`--account KEY`, `--source-type doc\|ticket\|chat`, `--classification`, `--embedding none\|fake\|openai`, `--text`, `--pool`, `--json`). Development/test only; it acts as that member through RLS |
| Measure retrieval | `make sync` then `make eval` (fake embeddings; `EMBEDDING=none` for exact + lexical only). Prints overall recall and the security counts; writes `evals/reports/<timestamp>/report.{json,md}` (per category, per persona, misses and where they were lost). Exit 1 if any security count is above 0 |
| Real-provider quality evaluation (the Recall@10 gate) | Put `LATTICE_OPENAI_API_KEY` in the environment or the git-ignored `.env` (never in chat, source config or commits). Then `make sync EMBEDDING=openai` for a fresh corpus, or for an existing one `lattice reindex --org <org> --sources ../data/synthetic/<org>/sources.yaml --reembed --embedding openai` per organization; then `make eval EMBEDDING=openai`. Only `internal` chunks are sent (1,289 chunks, about 71k tokens for both organizations, about $0.0015); the query side sends each question once. Check the report's corpus-embedding line shows `embedded/text-embedding-3-small` |
| An evaluation reports a security violation | Treat as an incident in retrieval: the report names the item, stage and record. Reproduce with `lattice search`, then the SQL of that channel (`tests/retrieval/test_sql_structure.py` and `golden/test_sql_capture.py` show how to inspect it). Never "fix" by filtering results |
| `semantic: policy_excluded (provider_unapproved)` | The provider has no `model_providers` record for the organization: nothing was sent. Sync with that provider first (it records it, approved for `internal`) |
| `semantic: policy_excluded (query_classification)` | The question was declared above the effective egress level; working as designed |
| `semantic: incompatible_space` | `dimensions`: the provider's vector size is not the column's (1024). `model_ambiguous`: two providers are recorded under one model name; remove the stale record |
| `semantic: provider_failed (...)` | Provider outage, timeout (5 s), rejection or malformed vectors; exact and lexical results were still returned. Check the provider, then the key |
| Changing retrieval tunables | `RetrievalConfig` in `lattice/retrieval/models.py`; bump `version`, run `make eval` before and after, and record the change and its per-category effect in `docs/evaluation-plan.md` §9 (regression rule §7) |
