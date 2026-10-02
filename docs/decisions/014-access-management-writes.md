# ADR-014: Access management writes by the API role, fenced in the database

Status: Accepted for Phase 9 (2026-10-02). Implemented (migration 0012, `lattice.access`,
`/v1/admin/access/*`).

## Context

Phase 9 gives administrators an Access page: change a member's clearance, account
scope, assignments, departments and administrator-defined groups, suspend or reactivate
them, and grant or revoke single documents — each previewed, with a reason, audited.
Until now nothing at runtime wrote authorization state: the seed and
`lattice apply-admin-grants` did, as `lattice_owner`, and `lattice_app` could only read
it (Phase 2). The API must now write some of it.

Options for the database identity of those writes:

1. **CLI only** (status quo): no UI changes. Rejected: the plan's deliverable is the UI,
   and a CLI that edits access by hand is less safe (no preview, no concurrency check).
2. **A fourth login role** (`lattice_access`) used by the API for these routes only.
   The API process would hold both credentials, so a compromised API holds both; the
   separation buys little and adds a second pool and a second startup check.
3. **Narrow grants to `lattice_app`, each fenced by a restrictive row-level policy**,
   with the rules repeated in code and every change audited in its own transaction.

## Decision

Option 3. `lattice_app` gains exactly (migration 0012; pinned by
`backend/tests/test_app_role_access.py`):

- `UPDATE (status, clearance, account_scope, authz_updated_at, updated_at)` on
  `memberships` — never `is_admin`, `role_label`, the user or the organization.
  Restrictive policy `app_member_status`: a `removed` membership cannot be updated, and
  only `active` or `suspended` can be written.
- `INSERT, DELETE` on `account_assignments` and `membership_departments`.
- `INSERT, DELETE` on `access_group_members`, only for groups with `origin = 'admin'`
  (`app_admin_groups_only_*`): a source-synced group mirrors its source.
- `INSERT, DELETE` on `document_grants`, only `origin = 'admin'` and
  `principal_type = 'membership'` (`app_admin_membership_grants_*`): source grants
  belong to the connector (D-12).
- `SELECT, INSERT` on `audit_events`; inserts only for `access.*` actions by a
  `member:` actor (`app_access_events`). The table stays append-only (0008 trigger;
  no UPDATE or DELETE privilege).

Still never: documents, chunks, sources, settings, provider configuration, accounts,
departments, groups themselves, `is_admin`, or deleting a membership. The API's startup
check still refuses a role that can write documents.

The code repeats each limit and adds the ones a policy can't express: the
administrator capability (E7) before anything is read; no change to your own access;
optimistic concurrency on `authz_updated_at`; a grant only of a document the
administrator can read (`authz.readable_documents_predicate`, 404 otherwise); one
`audit_events` row in the same transaction as each change.

## Consequences

- A compromised API process can change who reads what (as a compromised administrator
  session can). It cannot make anyone an administrator, touch source permissions or
  content, or rewrite the audit trail.
- **Residual risk (closed in Phase 10):** that every access write has an audit row was
  enforced by the code path only. Migration 0013 (ADR-015, decision 5) adds deferred
  constraint triggers: an access write by `lattice_app` cannot commit unless its
  transaction recorded an `access.*` event for that membership
  (`tests/test_access_audit_enforcement.py`). What remains: the event's actor and
  reason are written by the API and not authenticated by the database. Alerting on
  access events is Phase 11.
- `lattice_owner` tools stay authoritative for provisioning: re-running `make seed`
  makes members match the manifest again, recording `operator:seed` events, so a
  reverted change is visible, never silent.
