# ADR-017: Organization setup mutations by the API role, fenced per operation

Status: **Accepted as the P12.0 contract (2026-10-06).** Built checkpoint by checkpoint
from P12.1 (phase-12-plan Part II); **P12.1 (2026-10-08) built the first fence and the
foundations; P12.2 (2026-10-09) the member and administrator fences; P12.3 (2026-10-12) the
account and mapping fences; P12.4 (2026-10-13) the identity fences** — see "Implemented in
P12.1", "Implemented in P12.2", "Implemented in P12.3" and "Implemented in P12.4" at the end.

## Context

Phase 12 lets organization administrators configure what operators configured until
now: organization settings, members and the administrator capability, accounts and
source-reference mappings, the OIDC connection, SCIM tokens, providers and their
credentials, egress ceilings, sources and their imports.

Today `lattice_app` can only **read** `organizations`, `org_settings`, `accounts`,
`sources`, `model_providers`, `provider_credentials` and `oidc_connections`, cannot see
`scim_tokens`, cannot set `memberships.is_admin`, and may insert only `access.*` audit
events as a `member:` actor (privilege snapshot, migrations 0012/0013). That narrowness
is a security property: a compromised API process today cannot make an administrator,
reconfigure identity, redirect a sync or widen egress.

The options for giving the product these writes:

1. **A privileged setup role** used by the API for setup routes. Rejected: the API would
   hold both credentials, so a compromise holds both; and one broad role makes every
   setup table writable in every column.
2. **Broad grants to `lattice_app`** (table `INSERT/UPDATE`). Rejected: arbitrary SQL from
   a compromised API could set any column — a ceiling, a secret reference, a source path.
3. **Per-operation fences**, the ADR-014 discipline extended: column-level grants plus
   restrictive policies where a direct write is safe; `SECURITY DEFINER` functions where a
   transition has invariants the database must check itself (concurrency, cross-row
   rules); audit coupling enforced by deferred constraint triggers (the 0013 pattern).

## Decision

Option 3. Every Phase 12 write is one of the operations in the table below; nothing
else becomes writable. The privilege snapshot records each one, and
`tests/test_app_role_access.py` pins it.

### Mechanisms

- **Restricted direct write** — `INSERT`/`UPDATE` on named columns only, under the
  tenant RLS policy, with a restrictive policy for value rules (e.g. a status
  transition). Used where the rule is per row.
- **Fenced function** — a `SECURITY DEFINER` function owned by `lattice_owner` (search
  path pinned, input validated, `EXECUTE` for `lattice_app` only) for transitions whose
  invariant spans rows or needs a lock: the last-administrator rule, member creation
  with its directory identity, mapping replacement, egress escalation with a step-up
  grant. The table itself stays unwritable for those columns.
- **Audit coupling** — a deferred constraint trigger on each fenced table refuses to
  commit a `lattice_app` write unless the same transaction (`audit_events.txid =
  txid_current()`) inserted the matching event for the same subject (the 0013
  mechanism). The audit insert policy is widened from `access.%` to the families of
  phase-12-plan C10 and stays pinned to `actor LIKE 'member:%'`; `audit_events` stays
  append-only (UPDATE trigger, no DELETE privilege).
- Every operation also passes the code checks: the administrator capability before any
  read (E7), optimistic `version`/`updated_at` concurrency (409), and a reason.

### Operations

| Table / object | Operation (API role) | Mechanism | Never |
| -------------- | -------------------- | --------- | ----- |
| `organizations` | rename | `UPDATE (name, updated_at)` | slug, status, create, delete |
| `org_settings` | default question sensitivity raise; ceilings lower | restricted direct write (restrictive policy: the new value is not less restrictive) | `data_origin` |
| `org_settings` | ceiling raise, default sensitivity lower (escalation) | fenced function `lattice_apply_egress_change(change, …)` which consumes a single-use step-up grant from a fresh identity-provider re-authentication bound to the exact change (D-34, phase-12-plan C6.4) | direct escalation |
| `memberships` | create a member by hand (with `users`, `directory_identities`) | fenced function `lattice_create_member` (least-access defaults written by the function, identifier unique per connection) | choosing access at creation |
| `memberships.is_admin` | grant / revoke for another member | fenced function `lattice_set_admin` (§"Last administrator") | self, last active admin |
| `memberships.status` | suspend / reactivate (non-provisioned), reinstate removed (D-36) | existing restrictive policy extended; reinstatement via fenced function `lattice_reinstate_member` (resets access, D-36) | `removed` by the API; suspending/demoting the last active admin |
| `accounts` | create, rename, aliases, business status, archive, reactivate | `INSERT (organization_id, key, name, aliases, status)`, `UPDATE (name, aliases, status)` under a restrictive policy (never an archived row); archive/reactivate through fenced function `lattice_set_account_archived` (the `archived_at` lifecycle, P12.3 — not a status value) | delete, key or lifecycle by direct write |
| `account_external_refs` | map, remap, unmap | fenced function `lattice_set_account_ref` (one reference → one account, archived target refused) | direct writes |
| `oidc_connections` | add (none active), update client ID/secret, issuer/claim change, disable, enable | `INSERT`/`UPDATE` of named columns; disable/issuer change through fenced function `lattice_change_connection` which revokes the connection's sessions in the same transaction (P11.3 semantics, the advisory lock) | `login_key`, deleting, a second active connection, `--new-identity-namespace` (operator) |
| `scim_tokens` | create (hash only), revoke | `INSERT (organization_id, connection_id, token_hash)`, `UPDATE (revoked_at)` | reading hashes back, un-revoking |
| `model_providers` | configure, enable, disable, approve level | `INSERT`/`UPDATE` of named columns; an approval *raise* only through the egress fenced function (it can widen egress) | `deployment` change of an existing record (refused today too) |
| `provider_credentials` | set, rotate, revoke (metadata + reference) | `INSERT`, `UPDATE (status, revoked_at, last_test_at, last_test_code)` | the secret value (it is never in this table) |
| `encrypted_secrets` | store ciphertext, destroy | `INSERT`, `UPDATE (ciphertext → NULL, destroyed_at)` (ADR-011 amendment; phase-12-plan C3) | reading plaintext (there is none in the database) |
| `sources` | create, edit safe columns, pause/resume, disconnect request | `INSERT`/`UPDATE (name, default_sensitivity, default_departments, authority, account_scoped, status, config.channel_settings, import_id)`; restrictive policy: `connector_type` and `key` immutable, status transitions `active ⇄ paused`, `→ deleting` | arbitrary `config`, a host path |
| `source_imports` | register an upload, mark validated/rejected/superseded | `INSERT`/`UPDATE (status, counts, digest)`; storage location derived from IDs, never a column the API writes as a path | paths |
| `outbound_targets` | — (operator only) | — | API writes |
| `step_up_grants` | create on step-up callback, consume | fenced functions in the OIDC callback and the egress function | — |

`lattice_worker` gains only `SELECT` on `account_external_refs` and `source_imports` (to
resolve mappings and an import's tree by ID). `lattice_auth` is unchanged except the
step-up callback lookup. No RLS policy is weakened or removed.

### Last administrator under concurrency

Every transition that can reduce an organization's active administrators — revoking
`is_admin`, suspending, removing, organization-admin demotion — runs in a fenced
function (product) or an operator command that first takes
`pg_advisory_xact_lock(<org admin-set key>)`, then counts active administrators **after**
taking the lock (a new statement under READ COMMITTED sees every transaction that
committed before the lock was granted), and refuses if the result would be zero. A
deferred constraint trigger on `memberships` takes the same lock and re-counts for any
`lattice_app` write that changes `is_admin` or `status`, so even a direct statement that
slipped past the functions cannot commit a zero-admin organization from the product.

The invariant binds **product actions only**. The identity provider (SCIM deactivation or
removal of the last administrator) and operator commands may reduce the count to zero:
a person who left must lose access even if they were the last administrator. The
organization's setup record then shows *No active administrator* and only an operator can
appoint one (runbook §9, "all administrators lost").

## Consequences

- A compromised API process can do what a compromised administrator session can do
  through the product — and no more: it cannot set arbitrary columns, write a host path,
  read a secret's plaintext from the database, bypass the last-admin rule, escalate egress
  without a fresh identity-provider re-authentication (D-34), or rewrite the audit trail.
- Each operation is listed in the privilege snapshot; adding one is a migration plus a
  snapshot change reviewed like any other privilege change.
- The event's actor and reason are still written by the API and not authenticated by the
  database (unchanged residual risk from ADR-014).

## Implemented in P12.1 (2026-10-08, migration 0016)

Only what P12.1 needs; every other row of the table above is still a contract.

- **`organizations` rename** — `GRANT UPDATE (name, updated_at)` to `lattice_app`, a
  restrictive `FOR UPDATE` policy (`status = 'active'`), a name CHECK for every writer,
  and the deferred constraint trigger `trg_organizations_setup_audit` running
  `lattice_require_setup_audit(action, target key, subject column, organization
  column)`: a generalisation of 0013's access trigger (same `txid` coupling, same
  SECURITY INVOKER, fires only for `lattice_app`) that later fences reuse. The API path
  (`PATCH /v1/admin/organization`) checks the capability first, the version (409) and a
  reason, and writes the event in the same transaction.
- **Audit insert policy** widened by exactly one action: `organization.updated` from
  `member:` actors. Everything else in C11 waits for its checkpoint.
- **`encrypted_secrets`** — `lattice_app` has `SELECT` only (it opens what it presents);
  the `INSERT`/destroy rows of the table above come with the administrator credential UX
  (P12.4/P12.5); operators write in P12.1. `lattice_auth` gained `SELECT (key_id,
  destroyed_at)` and owns `lattice_secret_key_ids()` (IDs of live ciphertext across
  organizations; EXECUTE for `lattice_app` and `lattice_owner`) — a small addition to
  "`lattice_auth` is unchanged" above, needed for the start-up key check (C3.3).
- **`outbound_targets`** — operator only, as in the table; no runtime role has any
  privilege yet (the API will read it when it makes administrator-entered outbound calls).
- **Reads for the setup record** — `lattice_app` may read `oidc_connections.last_success_at`
  / `last_failure_at` (and set them at sign-in: sign-in facts, not setup writes, so not
  audited) and `scim_tokens` without `token_hash`.

The privilege snapshot records each change; `tests/test_app_role_access.py`,
`tests/test_rls.py`, `tests/test_auth_role.py`, `tests/test_worker_role.py` and
`tests/test_setup_status.py` pin them.

## Implemented in P12.2 (2026-10-09, migration 0017)

The `memberships` rows of the operation table, and nothing else.

- **`lattice_create_member(actor, email, display_name, given_name, family_name,
  identifier)`**, **`lattice_set_admin(actor, membership, administrator)`**,
  **`lattice_reinstate_member(actor, membership)`** — `SECURITY DEFINER`, owned by
  `lattice_owner`, search path pinned, `EXECUTE` for `lattice_app` only (and the owner).
  Each runs bound to `lattice_current_org_id()` (forced RLS applies to the owner too),
  re-checks that the acting membership is an active administrator of that organization
  (an actor from another organization is invisible, so refused identically) and refuses
  with SQLSTATE `LA001` and a fixed reason as the message, which the API maps to its
  error codes. Member creation is serialised per organization by an advisory lock so its
  uniqueness checks are exact; `lattice_set_admin` takes the administrator-set lock.
- **Audit coupling, refined.** The functions do not write events: the 0013 access-audit
  triggers are deferred and fire at commit as the session's role (`lattice_app`),
  including for rows a definer function wrote (verified on PostgreSQL 16), so the API's
  `access.member_changed` event in the same transaction is required exactly as for direct
  writes. No new audit action was needed: C11 records these as `access.member_changed`.
- **Last administrator.** As specified: lock (`pg_advisory_xact_lock(b"LADM",
  hashtext(org))`), then count; the deferred constraint trigger
  `trg_memberships_administrator_set` (`AFTER UPDATE OF is_admin, status`) re-checks any
  API-role write that takes an *active* administrator out of the set. The identity
  provider is recognised by the transaction's event for that member being by one of the
  organization's SCIM tokens (`scim:<token id>`); operators are not the API role. Because
  an acting administrator is itself counted, a single product request cannot reach zero:
  the rule holds against concurrent requests (tested: each pair of revoke/suspend) and
  against statements that bypass the code (tested: two direct API-role suspensions).
- **`directory_identities.managed_by`** (an addition the contract did not name): "a
  member with a directory identity is provisioned" stopped being true once people can be
  added by hand. `identity_provider` (SCIM; the default and the only value the API role
  may insert directly — restrictive policy) or `organization` (hand-added, or the
  operator's first administrator). `removed` stays reserved to identity-provider
  members; SCIM reads only its own identities.
- **Reads.** `lattice_app` may read `oidc_connections.issuer` and `subject_claim` (the
  add-member form names the claim). No other privilege changed; `is_admin` still has no
  column grant and `removed` cannot be left by a direct write.

`tests/test_app_role_access.py` (the functions' ownership, settings and executors; the
`managed_by` insert policy; removal only for identity-provider members; no direct
reinstatement), `tests/access/test_lifecycle.py` (the API and the functions: refusals,
audit coupling, concurrency, SCIM interplay) and the privilege snapshot pin them.

## Implemented in P12.3 (2026-10-12, migration 0018)

The `accounts` and `account_external_refs` rows of the operation table, as amended there.

- **Lifecycle, not a status.** The product owner's P12.3 instruction replaced C10's
  `archived` status value: `accounts.archived_at` is a separate setup lifecycle (business
  statuses stay labels with no effect), and administrators may **reactivate** (C10 made
  un-archiving operator-only). `lattice_set_account_archived(actor, account, archived)` —
  `SECURITY DEFINER`, owned by `lattice_owner`, search path pinned, `EXECUTE` for
  `lattice_app` only — re-checks the active administrator, locks the row and sets or clears
  `archived_at`; the API role has no grant on the column.
- **Direct writes.** `INSERT (organization_id, key, name, aliases, status)` and `UPDATE
  (name, aliases, status)` on `accounts`; restrictive policies refuse inserting an archived
  row and touching an archived one. Checks for every writer: key shape, name, aliases.
- **`lattice_set_account_ref(actor, source, kind, value, expected, account)`** — the only
  API write path to `account_external_refs`: validates the reference, takes a per-reference
  advisory lock (`b"LREF"`), compares the current mapping with `expected` (`mapping_changed`
  otherwise), then inserts, updates or deletes. A BEFORE trigger
  (`lattice_check_account_ref`) binds every writer, operators included: the kind fits the
  source's connector, the source is not being deleted, the reference's identity never
  changes, and a new or changed mapping never targets an archived account (read `FOR
  SHARE`, serialising with archiving's row lock).
- **Audit coupling.** `lattice_require_account_audit` (accounts: `account.created`,
  `account.updated`, `account.archived`, `account.reactivated`, chosen from the row
  change) and `lattice_require_mapping_audit` (references: `account.mapping_changed` for
  the same source, kind and value) — deferred, SECURITY INVOKER, `lattice_app` only, as
  0016's. The audit insert policy admits these five actions from `member:` actors.
  `account.reactivated` is new (C11 had no reactivation).
- **Reads.** `lattice_app` and `lattice_worker` `SELECT` on `account_external_refs` (as the
  contract said); the worker reads mappings at sync instead of `sources.config`.
- **Operators.** `lattice sync --sources` writes the file's mappings directly as the owner
  (the check trigger applies), with `account.mapping_changed` events by `operator:sync`.

`tests/test_app_role_access.py` (grants, the functions' ownership and executors, the
audit actions), `tests/test_rls.py`, `tests/test_worker_role.py`,
`tests/accounts/*` (the API, the functions, the triggers, direct-SQL bypass attempts,
concurrency, ingestion) and the privilege snapshot pin them.

## Implemented in P12.4 (2026-10-13, migration 0020)

The `oidc_connections`, `scim_tokens` and (for OIDC client secrets) `encrypted_secrets`
rows of the operation table — narrower than the table: **every** write is a fenced
function, none a column grant (the product owner's instruction for P12.4: no broad writes
on connections, directory identities or tokens).

- **`lattice_oidc_create`, `lattice_oidc_set_client`, `lattice_oidc_set_secret`,
  `lattice_oidc_clear_secret`, `lattice_oidc_set_status`, `lattice_oidc_change_identity`**
  and **`lattice_create_scim_token`, `lattice_rotate_scim_token`,
  `lattice_revoke_scim_token`** — `SECURITY DEFINER`, owned by `lattice_owner`, search
  path pinned, `EXECUTE` for `lattice_app` only; each re-checks an active administrator of
  the bound organization (`LA001 not_administrator`), finds the connection or token under
  RLS (another organization's is `connection_not_found` / `token_not_live`), compares the
  version the administrator reviewed (`connection_changed`), and holds the invariants:
  one active connection (and none added while a disabled one still signs members in),
  ciphertext bound to this connection with the previous one destroyed, disable and
  identity changes revoking the connection's sessions under its advisory lock (taken
  before the row lock, as sign-in takes it shared), an identity change only with an
  unused, unexpired, matched test sign-in of the same administrator under exactly the new
  values whose identifier equals their directory identity on this connection.
  `lattice_scim_token_used` sets `last_used_at` (a fact, no actor).
- **Audit coupling.** `lattice_require_connection_audit` (connection inserts and changes
  of issuer, client ID, secret reference, claim or status need an `sso.connection_*`
  event for that connection) and `lattice_require_scim_token_audit` (a token's creation
  needs `scim.token_created` or `scim.token_rotated` naming it; its revocation
  `scim.token_revoked` naming it or `scim.token_rotated` naming it as replaced) —
  deferred, SECURITY INVOKER, `lattice_app` only. The audit insert policy admits the
  `sso.connection_*` and `scim.token_*` families from `member:` actors and
  `scim.request_refused` from `scim:` actors.
- **Facts and reads.** `lattice_app` may update `oidc_connections.last_failure_code,
  last_test_at, last_test_kind, last_test_result` (sign-in and test facts, like
  `last_success_at`) and a test attempt's `outcome, observed_identifier, completed_at`;
  it may read every connection column (the secret's *reference*, shown as a kind),
  `scim_tokens.last_used_at, hint` (never `token_hash`) and `outbound_targets` (to apply
  the operator's registrations). `outbound_targets` stays operator-written.
- **Residual risk.** The proof of an identity change is written by the API (the ID token
  is verified in the API, not the database); a compromised API process could forge one.
  The fence still binds it to an administrator, the exact values, a single use and ten
  minutes, and the event records it.

`tests/test_app_role_access.py` (the functions' ownership, settings and executors; the
column grants; the trigger functions not callable), `tests/test_rls.py` (direct writes
refused), `tests/identity/test_identity_settings.py` (refusals, coupling, bypass attempts,
proofs) and the privilege snapshot pin them.
