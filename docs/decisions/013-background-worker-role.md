# ADR-013: A Postgres job queue and a dedicated runtime role for the background worker

Status: Accepted for Phase 8 (2026-09-29). Implemented (migration 0011, `lattice.jobs`,
`lattice worker`).

## Context

Phase 8 lets administrators request a sync from the Sources page. Until now a sync
was an operator command (`lattice sync`) run as `lattice_owner`, the schema owner.
The architecture already chose a Postgres `jobs` table with `FOR UPDATE SKIP LOCKED`
over a broker (architecture §1, §9), and planned for the worker to use the runtime
role. That plan did not hold: `lattice_app` can only *read* the ingestion tables, by
design, so that a compromised API cannot write documents or grants.

Three options for the worker's database identity:

1. **`lattice_owner`**, like the CLI. The owner can alter tables and disable
   row-level security. Keeping that credential in a long-running process that reads
   files from disk widens the blast radius of any worker bug to the whole schema. The
   runbook already says the owner's credential belongs to the migration job only.
2. **Grant write privileges to `lattice_app`.** One runtime identity, but the API
   (the internet-facing process) could then write documents, chunks and grants.
3. **A third login role, `lattice_worker`,** with exactly what a sync writes, through
   the same forced row-level security, one organization per job.

## Decision

Option 3.

- `lattice_worker` is created by the bootstrap (it needs a password; migrations hold
  no `CREATEROLE`). It is not an owner and has no `BYPASSRLS`. The worker refuses to
  start otherwise, and against a schema at another revision — the same checks as the
  API.
- **The queue is the only thing it sees across organizations.** Role-targeted
  policies let it read and claim every organization's `jobs` rows (IDs, codes and
  timestamps only) and delete finished jobs older than 30 days. Everything else is
  read and written in a transaction bound to the job's organization, under the tenant
  policies.
- **Least privilege on the ingestion tables.** It gets SELECT on what a sync reads and
  INSERT/UPDATE/DELETE only where a sync writes (documents, chunks, sources, runs,
  failures, source grants and groups), UPDATE on `memberships.authz_updated_at` only,
  and INSERT on `model_providers`. Restrictive policies narrow the rest: only
  `origin = 'source'` grants and groups (an administrator's grant or group is never
  touched, D-12), and only embedding provider records approved for `internal` with no
  explicit approval (the same record a first CLI sync writes). It has no privilege on
  sessions, identities, traces, audit events, provider credentials or organization
  settings. A test pins the whole matrix and fails when a new table appears.
- **The API refuses a role that can write content** (`documents`, `document_grants`),
  so the worker's credential cannot be configured as the API's by mistake.
- **The API only enqueues.** `lattice_app` gets SELECT and column-limited INSERT on
  `jobs`; it cannot change a job's status. A partial unique index keeps one queued or
  running job per source, so a repeated request returns the same job.
- **The worker runs the same sync** as the CLI (same pipeline, advisory lock and
  deletion guard; never `--allow-mass-deletion`), from the configuration the last CLI
  sync recorded on the source row, including its absolute export path. The API cannot
  change that configuration.

## Consequences

- One more credential to provision (`LATTICE_WORKER_PASSWORD`,
  `LATTICE_WORKER_DATABASE_URL`). Existing databases need `make db-bootstrap` once.
- Sources are still *created* by the operator command; the web app can only re-sync
  them. Connecting a new source from the UI is not in scope.
- Leases (renewed while a sync runs) and bounded attempts make a crashed worker's job
  retry safely; a sync is idempotent, and an interrupted run is recorded `aborted` by
  the next sync of that source.
- Revisit when a connector needs credentials of its own (the worker would resolve them
  through the secret store, as generation does) or when job volume outgrows one table
  (architecture §9: thousands of jobs per minute).
