# Lattice — Operational Runbook

Status: **Planned.** Nothing in this runbook is runnable yet. Each section is marked
with the phase that makes it real; sections are rewritten against the actual system
when that phase lands. Do not follow planned procedures on a live system.

---

## 1. Local development — *Phase 1 (skeleton), completed in Phase 8*

See `implementation-plan.md` §5 for the target command sequence. Required env vars
will be listed in `.env.example`; the API refuses to start if any are missing.

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
