# Lattice — Operational Runbook

Status: §1 is implemented (Phases 1–2). Everything else is **planned** unless marked
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

## 3. Model-provider record — *Phase 4/6*

For each configured provider, record: provider, endpoint, model, deployment
(external API / private), approved egress level (default `internal`), training
opt-out status, data-retention mode (standard / zero-retention), region, key owner,
rotation date. Raising an approved level or the org's external egress level requires
a recorded reason and produces an audit event. Admins see this on the Sources/Settings page.
