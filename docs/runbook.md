# Lattice — Operational Runbook

Status: §1 is implemented (Phase 1). Everything else is **planned**; each section is
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

## 2. Procedures (planned)

| Situation | Procedure (planned) | Phase |
| --------- | ------------------- | ----- |
| A sync run is `partial` or `failed` | Sources page → run → failures by code. `access_unmappable:*` → fix source config (account/department/group mapping) then `lattice sync <source>`. `source_unavailable` → check export path/credentials | 4 / 8 |
| Deletion guard tripped | Inspect export completeness; if the deletions are genuine, re-run with `--allow-mass-deletion` after confirming with the source owner | 4 |
| Urgently revoke a person's access | Suspend membership (sessions rejected on next request); remove account assignments / group membership as needed. No re-index needed | 2 / 9 |
| Remove a source's content now | `lattice source disconnect <source>` → immediate soft delete; purge runs asynchronously | 4 |
| `security_invariant_violated` alert | Treat as incident: the retrieval predicate returned an unreadable chunk. Capture trace ID; disable Ask for the org (`org_settings.ask_enabled=false`); investigate `authz` changes; add a regression test before re-enabling | 6 / 11 |
| Provider outage | Ask returns `provider_unavailable`; evidence still shown. Switch provider via config if an approved alternative exists | 6 |
| Change embedding model | `lattice reindex --reembed` per source; monitor cost estimate first | 4 |
| Organization deletion | Set status `deleting`; purge job; run verification query (0 rows per table for org) | 11 |

## 3. Model-provider record — *Phase 4/6*

For each configured provider, record: provider, endpoint, model, deployment
(external API / private), approved egress level (default `internal`), training
opt-out status, data-retention mode (standard / zero-retention), region, key owner,
rotation date. Raising an approved level or the org's external egress level requires
a recorded reason and produces an audit event. Admins see this on the Sources/Settings page.
