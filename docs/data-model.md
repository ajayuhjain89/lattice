# Lattice — Data Model

Status: §2 identity and tenancy tables are **implemented** (Phase 1, migration
`backend/alembic/versions/0001_baseline.py`, hardened by 0002). `access_groups`,
`access_group_members`, a Phase 2 subset of `documents` (§4) and `document_grants`
(§5) are **implemented** (Phase 2, migration `0003_authorization_core.py`);
`access_groups.key` was added in Phase 3 (migration `0004_access_group_key.py`,
decision D-9). §3, the remaining `documents` columns (§4), §6, `org_settings` and
`model_providers` (§7) are **implemented** (Phase 4, migration `0005_ingestion.py`),
each with an "as built" note where it differs from the draft. Phase 5 retrieval itself
added no tables: its query plans use the existing indexes (`retrieval-design.md` §7.6),
and its trace is an in-memory `RetrievalTrace` returned with each result. The Phase 5
closure added provider-policy columns (§7): migration `0006_provider_policy.py` (the
`local` deployment, `org_settings.default_query_sensitivity` for D-13,
`org_settings.data_origin`) and migration `0007_deployment_trust_classes.py` (D-15
trust classes, `model_providers.approved_at`,
`org_settings.organization_controlled_level`). Phase 6 added `query_traces`,
`provider_credentials` and `audit_events` (migrations 0008, 0009). **Phase 8** added
SELECT on `alembic_version` for the runtime role (0010: the schema-revision check) and
the `jobs` table, the `lattice_worker` role's grants and policies, and an
`(organization_id, created_at)` index on `query_traces` (0011; §3 and §8 below).
**Phase 9** added the API role's fenced access writes (0012). **Phase 10** (migration
0013, ADR-015) added `oidc_connections`, `oidc_login_attempts`, `directory_identities`,
`scim_tokens`, `rate_limit_counters`, `users.home_organization_id` and
`audit_events.txid` with the access-audit triggers (§2, §7). **Phase 12 P12.1**
(migration 0016) added `encrypted_secrets`, `outbound_targets`, the sign-in facts
`oidc_connections.last_success_at`/`last_failure_at`, `vault:` secret references, the
organization-name check and the first setup fence (rename, audit-coupled) (§2, §7).
Related: `authorization-model.md`, `ingestion-design.md`, `retrieval-design.md`.

Conventions:

- PostgreSQL 16 with extensions `vector` (pgvector 0.8.1) and `citext`, created by the
  superuser bootstrap (`infra/postgres/bootstrap.sql`), not by migrations. UUIDs use the
  built-in `gen_random_uuid()` (PostgreSQL 13+), so `pgcrypto` is not needed.
- Enumerated columns (`status`, `clearance`, `account_scope`, …) are `text` with named
  `CHECK` constraints rather than PostgreSQL enum types: adding a value is a one-line
  constraint change instead of an `ALTER TYPE` that cannot run in a transaction.
- `updated_at` is maintained by a `BEFORE UPDATE` trigger, so every writer (API, seed,
  manual SQL) keeps it correct.
- Every tenant-owned table has `organization_id uuid NOT NULL` and an RLS policy
  (see §8). This column is present even where it is derivable through a join, because
  RLS policies must be evaluable on the row itself.
- Composite foreign keys `(organization_id, id)` are used between tenant tables so a
  row in org A **cannot** reference a row in org B, even through an application bug.
- `created_at`/`updated_at` are `timestamptz NOT NULL DEFAULT now()` unless stated.
- "Req" column in field tables names the requirement the field exists for:
  **ISO** isolation · **AUTHZ** authorization · **ACCT** account scoping ·
  **CITE** citations/provenance · **FRESH** freshness · **AUTH-R** source authority ·
  **VER** versioning/idempotency · **DEL** deletion · **DEDUP** deduplication ·
  **DBG** debugging/operations.

---

## 1. Entity overview

```
organizations ─┬─ memberships ── users ── external_identities
               │     ├─ membership_departments ── departments
               │     ├─ account_assignments ──── accounts ── account_external_refs
               │     └─ access_group_members ─── access_groups
               ├─ sessions
               ├─ sources ── source_sync_runs ── sync_record_failures
               │     └─ documents ─┬─ chunks
               │                   └─ document_grants  (principal: user | department | group)
               ├─ query_traces
               ├─ model_providers (approved egress level per provider)
               └─ org_settings (external egress level, freshness thresholds)
```

Core typed domain objects (Pydantic, not tables) are listed in §9.

---

## 2. Identity and tenancy

### organizations

| Field        | Type      | Req  | Notes                                             |
| ------------ | --------- | ---- | ------------------------------------------------- |
| id           | uuid PK   | ISO  |                                                   |
| slug         | citext UQ | DBG  | Human-readable identifier                         |
| name         | text      |      |                                                   |
| status       | enum      | DEL  | `active`, `suspended`, `deleting`                 |

RLS-protected despite being the tenant root (Phase 1 change): the runtime role sees
only the bound organization's own row, so tenants cannot be enumerated. Operator tools
(`lattice_owner`) may provision organizations (`lattice org create`, P12.1). Since 0016
`name` is 1–200 characters without control characters (CHECK), and the runtime role may
update `name` (and `updated_at`) of its own **active** organization only — a deferred
trigger refuses the commit unless the same transaction recorded `organization.updated`
for it (ADR-017).

### users

Global identity. One person may belong to several organizations (SaaS reality:
contractors, consultants), so user ≠ membership.

| Field         | Type       | Req  | Notes                                  |
| ------------- | ---------- | ---- | -------------------------------------- |
| id            | uuid PK    |      |                                        |
| email         | citext UQ  |      | Login hint only; never an authz input  |
| display_name  | text       | CITE | Shown as author/asker                  |
| status        | enum       | DEL  | `active`, `disabled`, `deleted` (anonymized) |
| home_organization_id | uuid FK NULL | ISO | Phase 10: set for a user created by an organization's SCIM provisioning — the user is *owned* by it and can be a member of no other organization (trigger `lattice_membership_home_organization`). NULL for global users (the manifest) |

E-mail uniqueness (0013): unique among global users (`uq_users_email`, partial) and per
owning organization among owned users (`uq_users_home_organization_email`), so one
organization's provisioning never collides with — and so never reveals — another
organization's user. The API role may insert owned users of the bound organization and
update their e-mail and display name only; it sees them through `home_visible`.

### external_identities

| Field     | Type    | Notes                                              |
| --------- | ------- | -------------------------------------------------- |
| id        | uuid PK |                                                    |
| user_id   | uuid FK |                                                    |
| issuer    | text    | OIDC issuer URL, or `dev` for development login    |
| subject   | text    | Stable subject from IdP. UQ (issuer, subject)      |

Email is **not** used to link identities (email reuse / takeover risk). OIDC sign-in
(Phase 10) does not use this global table at all: identities from an organization's own
provider are `directory_identities`, scoped to that organization and connection (below).

### oidc_connections, oidc_login_attempts, directory_identities, scim_tokens (Phase 10)

Tenant tables, forced RLS (migration 0013; ADR-015).

| Table | Fields | Notes |
| ----- | ------ | ----- |
| `oidc_connections` | `id, organization_id, login_key, issuer, client_id, client_secret_ref, subject_claim, status (active/disabled)`, timestamps | One active per organization. `login_key` opaque (22–64 URL-safe characters, globally unique): the sign-in URL names it, never the organization. `client_secret_ref` is `env:LATTICE_OIDC_SECRET_<NAME>` or (0016) `vault:<uuid>` — an `encrypted_secrets` row sealed for this connection. `last_success_at` / `last_failure_at` (0016): the setup record's sign-in facts, written by the API (at most once a minute; a failure only on the provider's side or of the client secret). Operator-written otherwise (`lattice org create`, `lattice sso add/disable/enable/update`, audited); the API reads the configuration only through `lattice_oidc_connection(login_key)` (owned by `lattice_auth`), and may read `id, organization_id, status` (0014, the session-creation check, D-25) and `last_success_at, last_failure_at` (0016) of its own organization's connections |
| `oidc_login_attempts` | `id, organization_id, connection_id, state_hash, binding_hash, nonce, code_verifier, return_to, created_at, expires_at, consumed_at` | One authorization-code flow: SHA-256 of `state` and of the browser-binding cookie, ten minutes, consumed once (`consumed_at`). The API inserts, consumes and deletes finished ones (restrictive policy: only consumed or expired) |
| `directory_identities` | `id, organization_id, connection_id, membership_id, external_id, user_name, given_name, family_name, version, managed_by`, timestamps | The provider's person: UQ `(connection_id, external_id)` (the OIDC claim value), UQ `(organization_id, membership_id)`, UQ `(connection_id, lower(user_name))`. Written by SCIM as the API role (no DELETE; `external_id`, `membership_id` and `managed_by` not updatable). Its `id` is the SCIM resource ID. `managed_by` (0017, P12.2): `identity_provider` — SCIM created them and owns their lifecycle (D-20; the only value the API role may insert directly) — or `organization` — an administrator added them by hand (`lattice_create_member`) or the operator's `org create` made them the first administrator; they are suspended and reactivated in Access and never removed by the API. SCIM reads and changes only `identity_provider` identities (an `organization` identity's identifier and user name still count for SCIM's uniqueness: 409). Backfilled from the audit trail when 0017 ran |
| `scim_tokens` | `id, organization_id, connection_id, token_hash, created_at, revoked_at` | SHA-256 of `lsc_…` tokens (verification-only: never decryptable); operator-written; resolved through `lattice_resolve_scim_token(hash)` only while the token, its connection and its organization are active. Since 0016 the API may read every column but `token_hash` (the provisioning setup record) |

### encrypted_secrets, outbound_targets (Phase 12 P12.1, migration 0016)

Tenant tables, forced RLS.

| Table | Fields | Notes |
| ----- | ------ | ----- |
| `encrypted_secrets` | `id, organization_id, owner_kind (oidc_client_secret / provider_api_key), owner_id, key_id, nonce, ciphertext, hint, created_at, destroyed_at` | AES-256-GCM ciphertext of a recoverable credential (phase-12-plan C3), referenced as `vault:<id>` by its owner record (an OIDC connection, a provider credential). Associated data binds it to organization, secret ID, owner kind, owner ID and key ID; the plaintext and the master keys are never stored. 96-bit nonce per encryption; `nonce` and `ciphertext` are NULL once destroyed (rotation, revocation; CHECK). One live secret per owner (partial unique index). `hint`: the last four characters of a long provider key only. The API may read rows of its organization (it presents the credential); operators write; the worker and the web have no privilege. `lattice_secret_key_ids()` (owner `lattice_auth`) returns the distinct key IDs of live rows across organizations — IDs only — for the API's start-up check and `check-deployment` |
| `outbound_targets` | `id, organization_id, purpose (oidc / generation), scheme (https / http), host, port, created_at, revoked_at` | A private endpoint an operator registered for one organization and purpose (C4; `lattice outbound allow`, audited). Exact origin only (no range, no wildcard); revoked, never deleted; one live registration per (organization, purpose, origin). No runtime role has any privilege (administrators cannot register targets) |

### rate_limit_counters (Phase 10)

`organization_id, bucket, subject, window_start, count`; PK `(organization_id, bucket,
subject)`: one row per membership or SCIM token and bucket (closed set: `ask_minute`,
`ask_day`, `access_write`, `scim`), holding the current fixed window, updated by a
conditional upsert. Tenant RLS; IDs and counts only.

### sessions

Server-side, opaque (ADR-005). Stored so they can be revoked immediately.

| Field           | Type        | Req   | Notes                                           |
| --------------- | ----------- | ----- | ----------------------------------------------- |
| id              | uuid PK     |       |                                                 |
| token_hash      | bytea UQ    |       | SHA-256 of the random cookie token; raw token never stored |
| user_id         | uuid FK     |       |                                                 |
| organization_id | uuid FK     | ISO   | The session's active org. Switching org = new session |
| created_at, last_seen_at, expires_at, revoked_at | timestamptz | | Idle timeout 12 h, absolute 7 d (configurable) |
| oidc_connection_id | uuid FK, nullable | | The OIDC connection whose sign-in created the session (0014, D-25); NULL for development sign-in. `lattice sso disable` revokes the connection's sessions (partial index on live ones) |

Constraints (implemented): composite FK `(organization_id, user_id)` →
`memberships(organization_id, user_id)` — a session can exist only for a real
membership in its organization; `token_hash` must be exactly 32 bytes;
`expires_at > created_at`; composite FK `(organization_id, oidc_connection_id)` →
`oidc_connections(organization_id, id)` (a session cannot name another organization's
connection). The runtime role may insert sessions and update only `last_seen_at` and
`revoked_at`.

### memberships

| Field            | Type    | Req    | Notes                                                        |
| ---------------- | ------- | ------ | ------------------------------------------------------------ |
| id               | uuid PK |        |                                                              |
| organization_id  | uuid    | ISO    | UQ (organization_id, user_id)                                |
| user_id          | uuid FK |        |                                                              |
| status           | enum    | AUTHZ  | `active`, `suspended`, `removed`. Non-active ⇒ no access, sessions rejected |
| is_admin         | bool    | AUTHZ  | Administrative *capability*. Grants **no** data access       |
| clearance        | enum    | AUTHZ  | `internal` \| `confidential` (restricted is never by clearance) |
| account_scope    | enum    | ACCT   | `all` \| `assigned`                                          |
| role_label       | text    | DBG    | "Account Manager", "Support"… Display only; policy never reads it |
| authz_updated_at | timestamptz | AUTHZ | Bumped on any access-affecting change; recorded in traces   |

**Members and administrators in the product (P12.2, migration 0017; ADR-017).** The API
role still has no column privilege on `is_admin` and cannot move a membership out of
`removed`. Three fenced `SECURITY DEFINER` functions (owned by `lattice_owner`, search
path pinned, `EXECUTE` for `lattice_app` only, bound to `lattice_current_org_id()`, each
re-checking that the acting membership is an active administrator there) make the
transitions; refusals are SQLSTATE `LA001` with a fixed reason:
`lattice_create_member` (an owned user, a least-access membership written by the
function, the directory identity on the active connection, `managed_by = organization`;
identifier NFC-normalised, trimmed, case kept, unique on the connection; e-mail unique
among the organization's people), `lattice_set_admin` (another member, not removed; under
the organization's administrator-set advisory lock, never leaving zero active
administrators) and `lattice_reinstate_member` (a removed member, on their original
identity on the active connection, reset to least access, never an administrator). The
0013 audit triggers fire at commit as `lattice_app`, so each function's writes need the
`access.member_changed` event the API writes in the same transaction. A deferred
constraint trigger (`trg_memberships_administrator_set`) re-checks every API-role write
that takes an active administrator out of the set and refuses zero, unless the
transaction's event for that member is by one of the organization's SCIM tokens (the
identity provider may remove the last administrator; C18).

### departments, membership_departments

`departments(id, organization_id, key, name)`;
`membership_departments(organization_id, membership_id, department_id)`.
A member can belong to several departments (e.g. a product lead also in engineering).

### accounts, account_assignments, account_external_refs

Customer accounts are first-class because account scoping is a core authorization
dimension and a core query dimension ("What did we promise Acme?").

| accounts field  | Type     | Req   | Notes                                                |
| --------------- | -------- | ----- | ---------------------------------------------------- |
| id              | uuid PK  | ACCT  |                                                      |
| organization_id | uuid     | ISO   |                                                      |
| key             | citext   |       | `acme-corp`; UQ per org                              |
| name            | text     | ACCT  | Used for query account-mention detection             |
| aliases         | text[]   | ACCT  | "Acme", "ACME Corp"; used for mention detection      |
| status          | enum     |       | `active`, `churned`, `prospect`: a business label with no effect anywhere |
| archived_at     | timestamptz |    | P12.3 setup lifecycle: NULL = active; set = archived (kept, refuses new mappings) |

Checks for every writer (0018): key `^[a-z0-9][a-z0-9-]{0,62}$` (immutable for the API),
name 1–200 characters trimmed without control characters, at most 20 distinct aliases
(case-insensitively) of 1–100 characters. Accounts are never deleted by the product:
documents (no ON DELETE action), assignments and mappings (`ON DELETE RESTRICT`)
reference them.

**Lifecycle (P12.3).** Archiving is an administration state, not content deletion and not
authorization revocation: documents keep their account, assignments stay, traces stay
valid, existing mappings keep resolving at sync (dropping them would quarantine an
account-scoped source's records or unscope — and so widen — the others'). An archived
account refuses new mappings and is read-only to the API role until reactivated, which
keeps the same row (no duplicate identity). The Ask account picker labels it *archived*.

`account_assignments(organization_id, membership_id, account_id, assigned_at)` —
which accounts an `assigned`-scope member can see.

`account_external_refs` (P12.3, migration 0018; phase-12-plan C10):

| field           | Type        | Notes |
| --------------- | ----------- | ----- |
| id              | uuid PK     | |
| organization_id | uuid        | ISO; forced RLS `tenant_isolation` |
| source_id       | uuid        | FK `(organization_id, source_id)` → sources **ON DELETE CASCADE** |
| ref_kind        | text        | `ticketing_organization` (ticketing sources) · `chat_channel` (chat_export sources); must fit the source's connector (check trigger) |
| external_ref    | text        | exactly as the connector reports it: trimmed, NFC, case kept, 1–256, no control characters; a ticketing organization ID in canonical decimal |
| account_id      | uuid        | FK `(organization_id, account_id)` → accounts **ON DELETE RESTRICT** |
| created_by      | text        | `member:<id>`, `operator:sync`, `operator:migration` |
| created_at, updated_at | timestamptz | `updated_at` moves on a remap (the setup record's `sync_required`) |

UQ `(organization_id, source_id, ref_kind, external_ref)`: one reference → at most one
account; one account → many references, across sources. Index `(organization_id,
account_id)`. A BEFORE trigger (`lattice_check_account_ref`, every writer) refuses a kind
that does not fit the connector, a source being deleted, a change of the reference's
identity columns, and a new or changed mapping to an archived account (read under a
share lock that serialises with archiving). Ingestion reads the source's mappings at
sync (`lattice.accounts.refs.load_mappings`); unmapped references on account-scoped
sources **fail closed** (`ingestion-design.md` §6). Migration 0018 moved
`sources.config.account_refs` and `channels.*.account` here; channel *sensitivity* stays
in `sources.config.channels`.

Account visibility (A-3, revised): account existence can itself be sensitive, so a
member sees only accounts inside their account scope (`all` → every account;
`assigned` → assigned accounts) — in scope pickers, mention detection, ambiguity
choices and any future autocomplete. Admins see the account directory because
assigning accounts is their job; that confers no content access. Hiding an account's
existence even from admins (e.g. M&A targets) is a future "restricted account"
capability, not in the slice. See `security-model.md` §10.

### access_groups, access_group_members

Groups mirror access sets that exist in source systems (e.g. members of a private
Slack channel) or are defined by admins (e.g. "Exec staff").

| Field           | Type    | Notes                                                        |
| --------------- | ------- | ------------------------------------------------------------ |
| id              | uuid PK |                                                              |
| organization_id | uuid    | UQ (organization_id, id) — target of composite FKs           |
| key             | citext  | **Stable identity** (D-9): UQ (organization_id, key); CHECK `key::text ~ '^[a-z0-9][a-z0-9-]{0,62}$'` (on the text value — citext regex matching ignores case). Manifests and source sync find the group by it. Renaming changes `name` only; a different key is a different group (an identity/configuration change). **Not an authorization input**: grants and memberships use `id`. Existing rows were backfilled with `group-<id>` |
| origin          | enum    | `source` (synced from a connector) \| `admin` (managed in Lattice) |
| source_id       | uuid?   | **Phase 4:** composite FK `(organization_id, source_id)` → `sources` (CASCADE: disconnecting a source removes its groups and their members). CHECK: set if and only if origin = `source`. UQ (organization_id, source_id, external_ref) — the group's identity within its source. Source-synced groups get the key `src-<source key>-<external ref>`; a sync never takes over a key that already exists |
| external_ref    | text?   | e.g. Slack channel ID. CHECK: set if and only if origin = `source` |
| name            | text    | Shown to admins; may itself be sensitive → only admins see it |

`access_group_members(organization_id, group_id, membership_id)`: PK (group_id,
membership_id); composite FKs to `access_groups` and `memberships` (CASCADE); index
(organization_id, membership_id) for per-request context resolution.

Why groups exist: revoking a user from a private channel must be one row deletion,
not an update to thousands of documents; and permission changes must never require
re-embedding (they don't — access is joined at query time).

---

## 3. Sources and sync

### sources

**As built (Phase 4).** One row per configured source, upserted by `lattice sync` from
the organization's `sources.yaml` (Lattice-side configuration, `lattice.sources.config`).

| Field               | Type    | Req     | Notes                                                        |
| ------------------- | ------- | ------- | ------------------------------------------------------------ |
| id                  | uuid PK | CITE    | UQ (organization_id, id) — composite FK target                |
| organization_id     | uuid    | ISO     |                                                              |
| key                 | citext  | VER     | Stable operator key (`drive`, `support`, `slack`), UQ per organization, slug format |
| connector_type      | enum    |         | `document_repository`, `ticketing`, `chat_export`. A sync refuses a changed type (`source_config:connector_type_changed`) |
| name                | text    | CITE    | "Halden Drive"                                               |
| config              | jsonb   |         | The validated configuration: export path, default departments (by key), ticketing organization → account key, channel settings. Never secrets |
| default_sensitivity | enum    | AUTHZ   | **Required** (`internal` or `confidential`). Applied when a record carries no sensitivity |
| account_scoped      | bool    | ACCT    | If true, every record must map to an account or it is not indexed |
| authority           | enum    | AUTH-R  | Default authority for records: `system_of_record`, `working`, `conversational` |
| status              | enum    | DEL     | `active`, `paused`, `deleting` (a `deleting` source refuses syncs). *Current:* nothing sets `paused`. *Phase 12 target:* pause/resume from the product (P12.6) |
| opaque_ref_key      | bytea   | DBG     | 32 random bytes keying the opaque references of its failures (security-model §10). Never displayed |
| last_synced_at, last_succeeded_at, acl_synced_at | timestamptz? | DBG, FRESH | Last run; last fully successful run; last run that applied the source's access state |

Default departments are kept in `config` by key and validated against the organization
on every sync (`source_config:unknown_department`), rather than as a `uuid[]` column
that could not carry foreign keys.

### source_sync_runs

**As built.** `id, organization_id, source_id` (composite FK, CASCADE), `mode`
(`full | acl_only | reindex`), `status` (`running | succeeded | partial | failed |
aborted`), `started_at, finished_at`, counts (`records_seen, created, updated,
access_updated, unchanged, deleted, failed, chunks_embedded, embeddings_reused,
chunks_policy_excluded, embedding_failures`), `deletion_guard_tripped`, `error_code`
(CHECK: a code such as `deletion_guard:mass_deletion`, never free text). There is no
`error_summary` column. `incremental` mode is not built (no connector supports
checkpoints yet). A run is `partial` when any record failed, any embedding failed or
the deletion guard tripped; `succeeded` only when nothing did. Req: **DBG**, **DEL**.

### sync_record_failures

**As built.** `id, organization_id, sync_run_id` (composite FK, CASCADE),
`external_id?`, `location?` (e.g. `tickets.jsonl:12` — can be a filename), `opaque_ref`
(CHECK `^rec_[0-9a-f]{16}$`: HMAC-SHA-256 of the external ID, or of the location when
the record could not be identified, under the source's `opaque_ref_key`), `stage`
(`read | parse | validate | normalize | map_access | chunk | embed | persist`),
`error_code` (CHECK: lowercase code), `message_params jsonb` (short identifiers only —
a field name, a line number, an unmapped external account reference), `created_at`.
There is no free-text message column, so record content cannot leak through failures
(a test pins the column set). Admins will see *what failed, when, why* (Phase 8); the
raw `external_id` and `location` are displayed only if the admin can read the
corresponding active document, otherwise `opaque_ref` (`security-model.md` §10).

### jobs (Phase 8, migration 0011; ADR-013)

The queue of syncs requested from the web app. Holds IDs, fixed codes and timestamps
only.

| Field | Type | Notes |
| ----- | ---- | ----- |
| id, organization_id | uuid | UQ (organization_id, id) |
| kind | text | CHECK `source_sync` |
| source_id | uuid | composite FK to `sources`, CASCADE |
| mode | text | CHECK `full` \| `acl_only` (the web app requests `full`) |
| requested_by | uuid | composite FK to `memberships` (the administrator), CASCADE |
| status | text | `queued` → `running` → `succeeded` \| `failed`; a retry returns to `queued` |
| attempts, max_attempts | int | CHECK `max_attempts` 1–10 (default 3) |
| run_after | timestamptz | due time (retry back-off) |
| lease_expires_at | timestamptz | set exactly while `running` (CHECK); renewed by the worker |
| sync_run_id | uuid | composite FK to `source_sync_runs`, `ON DELETE SET NULL (sync_run_id)` |
| error_code | text | CHECK fixed-code format (`source_busy`, `job_abandoned`, `internal_error`, a sync's source-level code) |
| created_at, started_at, finished_at | timestamptz | `finished_at` set exactly when finished (CHECK) |

A partial unique index keeps **one queued or running job per source** (a repeated
request returns it). Forced RLS with the tenant policy; `lattice_worker` additionally
has `worker_queue` (SELECT) and `worker_claim` (UPDATE) policies across organizations
and `worker_retention` (DELETE only of jobs finished more than 30 days ago). The runtime
role may SELECT and INSERT the request columns only.

---

## 4. Documents

One row per source record (a doc file, a ticket, a Slack thread or channel-day).

**Implemented in Phase 2 (authorization record only):** `id`, `organization_id`,
`account_id`, `sensitivity`, `access_mode`, `department_ids`, `status`, `created_at`,
`updated_at`, with CHECKs on every enumerated column, `restricted ⇒ grants_only`, no
NULL elements in `department_ids`, the composite account FK, the GIN index on
`department_ids` and (organization_id, account_id). `account_id`'s FK has **no ON
DELETE action**: deleting an account that documents reference is refused, because
clearing `account_id` would silently make account-specific content visible to
everyone. Arrays cannot carry foreign keys, so trigger
`trg_documents_department_ids` rejects (as a `foreign_key_violation`) any
`department_ids` element that is not a department of the document's organization,
on insert and whenever `organization_id` or `department_ids` changes. A department
deleted later leaves a dangling ID, which only makes the document less visible.

**As built (Phase 4, migration 0005):** every column below exists. Additions to the
draft: `acl_updated_at` (last access change, including admin grants),
`supersedes_external_id` (what the source says; resolved within the same source into
`supersedes_document_id`), and the status `quarantined`. Migration 0005 refuses to run
if any document exists, because no earlier document could have a source.

| Field               | Type        | Req             | Notes |
| ------------------- | ----------- | --------------- | ----- |
| id                  | uuid PK     | CITE            | |
| organization_id     | uuid        | ISO             | |
| source_id           | uuid        | CITE, DEL       | Cascade: source deletion removes documents |
| external_id         | text        | VER, DEDUP      | Normalized ID in the source. UQ (organization_id, source_id, external_id) — the idempotency key |
| document_kind       | enum        | CITE            | `doc`, `ticket`, `chat_thread`, `chat_channel_day` |
| title               | text        | CITE            | Ticket subject, doc title, "#acct-acme · thread by Maya" |
| display_ref         | text?       | CITE            | Human ID shown in UI, e.g. `HAL-4821` |
| uri                 | text?       | CITE            | Link to original; https only, validated |
| author_display      | text?       | CITE            | Creator as shown in source |
| account_id          | uuid?       | ACCT, AUTHZ     | Composite FK to accounts |
| sensitivity         | enum        | AUTHZ, egress   | Classification: `internal`, `confidential`, `restricted`. Drives clearance checks; separately used by the AI egress policy (not authorization) |
| access_mode         | enum        | AUTHZ           | *Who* may read: `policy` (clearance/account/department rules) or `grants_only` (explicit grants only, e.g. private channels). CHECK: `sensitivity = 'restricted'` ⇒ `access_mode = 'grants_only'` |
| department_ids      | uuid[]      | AUTHZ           | Empty = no department restriction. GIN index. Unknown IDs make a doc *less* visible (fail closed) |
| authority           | enum        | AUTH-R          | Inherits source default; connector may raise/lower within config rules |
| source_created_at   | timestamptz | FRESH           | From source |
| source_updated_at   | timestamptz | FRESH           | From source; drives freshness |
| ingested_at         | timestamptz | FRESH, DBG      | Last time content was (re)written |
| content_hash        | bytea       | VER, DEDUP      | SHA-256 of canonical text + content-relevant metadata |
| access_hash         | bytea       | VER             | SHA-256 of normalized access metadata (sensitivity, access_mode, account, departments, source grants); lets ACL-only changes skip re-chunking |
| version             | int         | VER             | Incremented on content change |
| body                | text        | CITE            | Canonical normalized text (for inspector context). Max size enforced |
| acl_updated_at      | timestamptz | AUTHZ, DBG      | Last change of access columns, source grants or admin grants |
| status              | enum        | DEL, AUTHZ      | `active`, `deleted`, `quarantined`. Only `active` is readable. `quarantined`: the source's record exists but its access can no longer be mapped (or the record no longer parses), so the document is kept but unreadable rather than left on stale access; the next sync that maps it restores it |
| supersedes_external_id | text?    | FRESH           | The source's own statement ("v2 replaces v1") |
| supersedes_document_id | uuid?    | FRESH           | Resolved link within the same source (composite FK). Cleared before either document is deleted |
| metadata            | jsonb       | DBG             | Validated per-kind extras (ticket status/priority, channel name). **Never** read by authz |

Indexes: UQ (organization_id, source_id, external_id); (organization_id, account_id);
GIN (department_ids); (organization_id, status, sensitivity);
(organization_id, source_updated_at); (organization_id, display_ref) for exact
identifier lookup. Document IDs are deterministic (`uuid5(source_id/external_id)`);
identity is still decided by the idempotency key.

Why access fields live on `documents`, not on `chunks`: a permission change is a
single-row update that takes effect for the next query, with no fan-out and no chance
of chunks disagreeing with their document. Retrieval joins chunks→documents. We
revisit denormalization only if measurement shows the join dominates retrieval latency.

Why no `allowed_roles`/`allowed_users` arrays: roles are display labels, not policy
inputs; per-user access is expressed through `document_grants`, which are indexable
and revocable.

## 5. Document grants

Explicit grants. The **only** path to `restricted` documents; also usable to extend
access to non-restricted documents.

Implemented in Phase 2 with one deliberate change from the draft: the draft's
polymorphic `principal_id` cannot carry a foreign key, so a grant could have named a
membership, department or group of **another organization**, or kept naming one after
it was deleted. Each principal kind has its own column instead:

| Field           | Type    | Notes |
| --------------- | ------- | ----- |
| id              | uuid PK | Surrogate key (the principal columns are nullable) |
| organization_id | uuid    | |
| document_id     | uuid    | Composite FK → documents (CASCADE) |
| principal_type  | enum    | `membership`, `department`, `group` |
| membership_id   | uuid?   | Composite FK → memberships (CASCADE) |
| department_id   | uuid?   | Composite FK → departments (CASCADE) |
| group_id        | uuid?   | Composite FK → access_groups (CASCADE) |
| origin          | enum    | `source` (from connector ACL), `admin` |
| created_at      | timestamptz | |

CHECK `ck_document_grants_one_principal`: `principal_type` names exactly one populated
principal column and the others are NULL. UQ (document_id, origin, membership_id),
(document_id, origin, department_id), (document_id, origin, group_id) — **Phase 4**
added `origin` (decision D-12): a source grant and an admin grant on the same principal
are separate permission origins, so a sync replacing its own grants never collides
with, or touches, an administrator's grant. Duplicates are harmless to the policy. Indexes (organization_id,
membership_id | department_id | group_id). Deleting a principal deletes its grants.

`granted_by` (the granting admin) was deferred to Phase 9 as audit data. **Decided in
Phase 9: no column.** The grant's `access.grant_created` audit event (target
`grant_id`) records who granted it, when and why, and outlives both the grant and the
granting administrator's membership; the Access page reads it from there.

Source-origin grants are replaced wholesale on each ACL sync; admin-origin grants are
never touched by sync (so an admin grant is not silently wiped by a connector). Admin
grants come from the Access page (Phase 9; one document the administrator can read, to
one member, with a reason) or from `admin-grants.yaml` through `lattice
apply-admin-grants` (all-or-nothing, idempotent; `--prune` revokes grants no longer in
the file). Both are membership grants only, and both write audit events.

## 6. Chunks

| Field           | Type          | Req         | Notes |
| --------------- | ------------- | ----------- | ----- |
| id              | uuid PK       | CITE, VER   | Deterministic: uuid5(document_id, content_hash, occurrence) |
| organization_id | uuid          | ISO         | |
| document_id     | uuid          | CITE, DEL   | Cascade |
| ordinal         | int           | CITE        | Position in document (for neighbours in inspector) |
| text            | text          | CITE        | Display text, exactly as in `documents.body[char_start:char_end]` |
| char_start, char_end | int      | CITE        | Offsets into `documents.body` for highlighting and context |
| heading_path    | text[]        | CITE        | Doc section heading / "Comment 4 · Priya Natarajan (agent)" |
| source_anchors  | text[]        | CITE        | Record parts the chunk covers (section number, comment `c4`, message `ts`) |
| occurred_at     | timestamptz   | FRESH       | Time of *this* content (message/comment time), falls back to document time |
| author_display  | text?         | CITE        | Author of this part (comment/message author) |
| content_hash    | bytea         | VER, DEDUP  | SHA-256 of chunk text (with the occurrence, the chunk ID) |
| embedding_input_hash | bytea    | VER         | SHA-256 of `EMBEDDING_INPUT_VERSION` + the exact provider input (header + text); a vector is reused only when this matches |
| token_count     | int           | DBG         | Context budgeting |
| tsv             | tsvector      |             | Computed by ingestion: setweight(english(title + heading path),'A') ‖ setweight(simple(display_ref),'A') ‖ setweight(english(text),'D'). **No structured account name** (decision D-10). GIN index |
| embedding       | vector(1024)  |             | Fixed per deployment (ADR-008). NULL unless `embedding_status = 'embedded'` (CHECK) |
| embedding_model | text?         | VER         | Set exactly when a vector exists; a model change is a tracked re-embed. Retrieval compares a query vector only with chunks of the provider's own model (Phase 5); the provider behind a model name is resolved through `model_providers` |
| embedding_status | enum         | egress, DBG | `embedded`, `policy_excluded` (above the provider's effective egress level: lexical-only), `pending` (no provider configured yet), `failed` (provider error; retried by the next sync) |

Indexes: UQ (document_id, ordinal) DEFERRABLE (replacing a document's chunks
renumbers kept chunks in place); GIN (tsv); vector index **deferred** (exact scan is
used while per-org chunk counts are small; see `retrieval-design.md` §5).

Why `occurred_at` on chunks: a ticket opened in March may have a comment from last
week; freshness must be judged on the evidence actually cited, not the container.

## 7. Traces and settings

### query_traces

Security-sensitive. Stores **references and measurements**, not document content.

**As built (Phase 6, migrations 0008-0009):** `id` (the trace ID), `organization_id`,
`membership_id` (composite FK to `memberships`), `created_at`, `question_text`,
`question_sensitivity` (D-13), `scope`, `authz_snapshot` (counts only:
`authz.summarize_context`), `retrieval` (the Phase 5 `RetrievalTrace`),
`evidence_chunk_ids`, `prompt_chunk_ids` (what the model received), `cited_chunk_ids`,
`answer_account_ids` (0009: the accounts whose metadata the stored answer may carry --
clarification choices and the accounts of prompt evidence; NULL = unknown, which hides
the answer on read), `egress` (`{provider, model, deployment, effective_level, decision_reason,
question_sensitivity, excluded_by_egress_policy, excluded_by_prompt_budget,
below_relevance_floor, prompt_version}`), `validation` (`{generation_calls, regenerated,
schema_failures, claims_proposed, claims_valid, claims_rejected, rejection_codes,
conflicts_proposed, conflicts_discarded, model_status}`), `answer` (the validated answer
without evidence text), `status` (the table below plus `unsupported_request`),
`status_reason`, `error_code`, `timings_ms`, `usage` (`{input_tokens, output_tokens}`;
no cost estimate). Forced RLS; the runtime role may SELECT and INSERT only. No
`filter_fingerprint` (no consumer yet). Retention (P11.4): the worker deletes rows after
`LATTICE_TRACE_RETENTION_DAYS` (90), never younger than 90 days (database floor), reading
only `id, organization_id, created_at`.

Design (Phase 0):

| Field               | Notes |
| ------------------- | ----- |
| id, organization_id, membership_id, created_at | |
| question_text       | Retention-limited (default 90 days). Visible only to the asker (re-authorized); never to admins in the first release (`security-model.md` §8) |
| scope               | Requested filters (account, source types, dates) |
| authz_snapshot      | `{clearance, account_scope, assigned_account_count, department_ids, group_count, authz_updated_at, policy_version}` — enough to explain, not a full ACL dump |
| filter_fingerprint  | Hash of compiled predicate parameters (to diff behaviour across policy versions) |
| retrieval           | `{strategy, lexical: [{chunk_id, rank, score}], semantic: [...], fused: [...], rerank: [{chunk_id, features, score}]}` capped at top 50 |
| selected_chunk_ids  | Context actually sent to generation |
| egress              | `{provider, model, effective_egress_level, excluded_by_egress_policy_count, prompt_version}` |
| validation          | `{generation_calls, claims_proposed, claims_rejected, rejection_codes[]}` — counts and codes only; rejected claim text is never stored |
| answer              | Final validated answer JSON (only validated claims + citations), retention-limited; hidden on read if any of the Ask's evidence (cited or not) is no longer readable by the viewer, or any account in `answer_account_ids` or the scope is no longer visible to them (or no longer exists) |
| status              | `answered`, `partial`, `insufficient_evidence`, `evidence_only`, `ambiguous`, `failed` (+ `status_reason`) |
| error_code          | |
| timings_ms          | `{authz, lexical, embed_query, semantic, fuse_rerank, generation, validation, total}` |
| usage               | `{input_tokens, output_tokens, estimated_cost_usd}` |

### org_settings

**As built (Phase 4):** `organization_id PK, external_egress_level` (default
`internal`), timestamps. No row means the defaults.

**Added in the Phase 5 closure:** migration 0006 `default_query_sensitivity`
(`internal|confidential|restricted`, default `internal`; D-13: the trusted minimum
sensitivity of every question, which a declaration can raise and never lower) and
`data_origin` (`customer|synthetic`, default `customer`; only synthetic content may
reach a provider that may use submitted content, ADR-010). Migration 0007
`organization_controlled_level` (same levels or NULL, default NULL = the
`organization_controlled` trust class is not allowed; D-15).

The remaining fields below arrive with the phases that use them. Raising a level has
no command yet: the Phase 6 backend adds the audited organization-scoped provider
configuration (D-16), and the Phase 7 frontend the provider and egress settings UI.

Design: `organization_id PK, external_egress_level (internal|confidential|restricted,
default **internal**), freshness_thresholds jsonb (per document_kind, days),
trace_retention_days (default 90), ask_enabled bool (default true; incident kill
switch)`. Raising `external_egress_level` is an explicit, audited admin action.
There is deliberately no setting that lets admins read other members' content (D-5).

### provider_credentials (Phase 6, migration 0008)

Organization-owned provider credentials **by reference only** (D-16, ADR-011):
`id, organization_id, provider, secret_ref, data_use (unpaid|paid|NULL), status
(active|revoked), created_at, revoked_at`. `secret_ref` must match
`^(env|memory):[A-Za-z0-9_.-]{1,96}$` or (0016) `^vault:<uuid>$` (CHECK); the code
accepts only `env:LATTICE_PROVIDER_SECRET_<NAME>`, the documented legacy provider
variables and `vault:` references to a ciphertext sealed for that credential
(`lattice.providers.secrets`, `lattice provider credential set --value-stdin`); rotating or
revoking a `vault:` credential destroys its ciphertext. At most one active credential per organization and
provider (partial unique index); rotation revokes and inserts, history is kept. Forced
RLS; the runtime role may SELECT only (to resolve the reference at invocation).

### audit_events (Phase 6, migration 0008; access events Phase 9, migration 0012; enforced by the database Phase 10, migration 0013)

Append-only record of administrative changes: `id, organization_id, created_at
(clock_timestamp), actor, action, target, before, after, reason (1-500 characters)`. A
trigger rejects UPDATE; nothing may DELETE (rows go only with their organization).
Never a secret, a secret reference or document content.

| Action | Written by | Target | before / after |
| ------ | ---------- | ------ | -------------- |
| `provider.*`, `credential.*`, `egress.levels_changed` | operator CLI (Phase 6) | provider / purpose | the changed settings |
| `access.member_changed` | API (`member:<id>`), seed (`operator:seed`) | `membership_id` | access snapshots: status, administrator flag, clearance, account scope, assigned account IDs, department IDs, administrator-defined group IDs (before NULL = provisioned) |
| `access.grant_created`, `access.grant_revoked` | API, `apply-admin-grants` (`operator:admin-grants`) | `membership_id`, `document_id`, `grant_id`, `source_id`, `reference` (the document's opaque `rec_…`) | `{origin, principal_type}` |
| `access.member_changed` (Phase 10) | SCIM (`scim:<token id>`: provisioning, suspension, reactivation, removal), `lattice member admin` (`operator:<user>`: the administrator capability) | `membership_id` | access snapshots, as above |
| `access.identity_changed` | SCIM (`scim:<token id>`) | `membership_id` | `{user_name, email, name}` before and after |
| `sso.connection_created`, `sso.connection_disabled`, `scim.token_created`, `scim.token_revoked` | operator commands (`operator:<user>`) | `connection_id` / `token_id` | the connection's settings (the client secret only as `reference` or `none`) / state |

IDs, never names: the audit view resolves names at read time (deleted ones show as
gone) and lists an event that targets a document only while the viewing administrator
can read that document (D-18; the stored `reference` is for operators and is never
returned by the API). Index `(organization_id, created_at DESC, id DESC)` for the
keyset pages of `GET /v1/admin/audit-events`. The runtime role may SELECT and may
INSERT only `access.*` events by a `member:` or `scim:` actor (restrictive policy,
ADR-014, 0013).

`txid xid8` (0013; default `pg_current_xact_id()`, indexed): the writing transaction.
Deferred constraint triggers on `memberships`, `account_assignments`,
`membership_departments`, `access_group_members`, `document_grants` and
`directory_identities` refuse to commit an INSERT, UPDATE or DELETE by `lattice_app`
unless an `access.*` event of the same transaction names the same `membership_id`
(`lattice_require_access_audit`). Operator and worker writes are not affected.

### model_providers

**As built (Phase 4):** `id, organization_id, purpose, provider, model, deployment,
approved_for_level (default internal), retention_mode, training_opt_out`, timestamps;
UQ (organization_id, purpose, provider, model). Migration 0006 added `local` to the
allowed `deployment` values (inference inside the Lattice deployment, ADR-009). A sync
records the embedding provider it uses the first time (approved for `internal` only); raising the approval is an
explicit administrator action outside ingestion. `endpoint` and `updated_by` are not
built.

Migration 0007 (D-15): `deployment` ∈ {`local`, `organization_controlled`,
`external_api`} (the old `private` became `organization_controlled`), and
`approved_at timestamptz NULL`, set only by an explicit administrator approval.
`org_settings.organization_controlled_level` (NULL = the class is not allowed) is the
organization's ceiling for `organization_controlled` inference.

Migration 0008 (Phase 6): `endpoint` (a local or organization-controlled server; CHECK
`^https?://...` without credentials) and `enabled` (default true; disable without
deleting history); at most one enabled `generation` record per organization (partial
unique index). Configured and changed by the audited functions of
`lattice.providers.configuration`. `updated_by` is replaced by `audit_events`.

Design: `id, organization_id, purpose (embedding|generation), provider, endpoint, model,
deployment (local|organization_controlled|external_api), approved_for_level
(internal|confidential|restricted, default internal), approved_at, retention_mode,
training_opt_out bool, updated_by, updated_at`. Effective egress level for a call, by
trust class: `external_api` `min(external_egress_level, approved_for_level)`;
`organization_controlled` `min(organization_controlled_level, approved_for_level)` if
both are set and `approved_at` is set, otherwise nothing; `local`
`approved_for_level`. Secrets are not stored
here (environment / secret store only).

## 8. Row-Level Security (implemented in Phase 1)

### Roles

| Role | Login | Purpose | RLS |
| ---- | ----- | ------- | --- |
| `lattice_owner` | yes | Owns the schema; runs migrations and operator tools (seed) | Subject to FORCE RLS on tenant tables; has provisioning policies on the three global/root tables only |
| `lattice_app` | yes | API. Never owns tables, never BYPASSRLS, never writes documents (the API refuses such a role, Phase 8). Since Phase 9 writes access-management state only: a membership's status/clearance/scope, assignments, departments, administrator-group members, administrator membership grants, access audit events (ADR-014). Since Phase 10 also SCIM provisioning — organization-owned users (insert; e-mail and name), memberships for them through column privileges that keep the least-access defaults, `removed` only for provisioned members, directory identities — plus sign-in attempts and rate-limit counters (ADR-015); every access write must commit with its audit event | Subject to every policy; restrictive policies fence each Phase 9 and Phase 10 write; exact privileges: `backend/tests/test_app_role_access.py` |
| `lattice_worker` | yes | Background worker (Phase 8, ADR-013). Never owns tables, never BYPASSRLS | Subject to every tenant policy; role-targeted policies on `jobs` (queue across organizations); restrictive policies limit its grant/group writes to `origin = 'source'` and its `model_providers` inserts to embedding records approved for `internal`. Exact privileges: `backend/tests/test_worker_role.py` |
| `lattice_auth` | **no** | Owns the four SECURITY DEFINER pre-authentication functions (`lattice_resolve_session`, `lattice_identity_memberships`; Phase 10: `lattice_oidc_connection`, `lattice_resolve_scim_token`) — and nothing else | Role-targeted read policies on the tables those functions need |

Roles, databases and extensions are created by `infra/postgres/bootstrap.sql` (run as
superuser; idempotent). Migrations only verify they exist. `lattice_owner` is granted
membership in `lattice_auth` `WITH INHERIT FALSE, SET TRUE` — enough to hand function
ownership to it and to administer those functions' ACL (by `SET ROLE`, inside a
migration), without silently inheriting its privileges or RLS policies.

`lattice_owner` is the migration/bootstrap/operator identity. It is never the runtime
identity: the API connects as `lattice_app` only, has no fallback to another role, and
refuses to start as the owner (startup check below). Production keeps the two
credentials separate.

**Steady-state privileges of `lattice_auth`** (hardened before Phase 2; regression test
`backend/tests/test_auth_role.py`):

| Privilege | Held | Why |
| --------- | ---- | --- |
| LOGIN, SUPERUSER, BYPASSRLS, CREATEROLE, CREATEDB | no | Never a session identity |
| Membership in any role | none | Its only member is `lattice_owner` (SET only, no INHERIT) |
| `USAGE` on schema `public` | yes | The definer functions resolve `public.*` as `lattice_auth` |
| `CREATE` on any schema; `CREATE`/`TEMPORARY`/`CONNECT` on the database | **no** | Object creation is not needed at rest |
| `SELECT` on organizations, users, external_identities, memberships, sessions | yes | Exactly what the two lookups read (plus the `auth_lookup` policies) |
| Any other table privilege, on any table | no | Including every Phase 2 authorization table |
| Owns | the two lookup functions | No tables, types, schemas or other functions |

PostgreSQL requires the *new* owner of a function to hold `CREATE` on its schema only
at the moment of `ALTER FUNCTION … OWNER TO`. Migration 0001 therefore grants `CREATE`
to `lattice_auth`, transfers ownership and revokes `CREATE` in the same transaction —
no other session can observe the grant, and the privilege is not needed afterwards
(owning and executing a function does not use it). The alternative, a dedicated schema
for the two functions, would have required `lattice_auth` (or its schema owner) to keep
`CREATE` there, i.e. still a standing object-creation capability, plus a second schema
on every search path; it was not needed. A side effect worth keeping: because
`CREATE OR REPLACE FUNCTION` also requires `CREATE` on the schema, even the owner cannot
redefine a lookup outside a migration.

### Binding the organization

```sql
SELECT set_config('app.org_id', '<org uuid>', true);  -- transaction-local = SET LOCAL
```

Policies read it through a helper:

```sql
CREATE FUNCTION lattice_current_org_id() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULLIF(current_setting('app.org_id', true), '')::uuid $$;
```

`NULLIF` is required (a Phase 1 finding): once `app.org_id` has been set on a pooled
connection, `current_setting(..., true)` returns `''` — not NULL — after the
transaction ends, and `''::uuid` raises. With `NULLIF`, an unbound transaction matches
no tenant rows (fail closed) instead of erroring. A malformed value still raises
(also fail closed). Both behaviours are regression-tested on a reused connection.

### Policies

| Table(s) | Policy | Applies to |
| -------- | ------ | ---------- |
| memberships, departments, membership_departments, accounts, account_assignments, sessions, access_groups, access_group_members, documents, document_grants | `tenant_isolation`: `organization_id = lattice_current_org_id()` (USING + WITH CHECK) | everyone, incl. the owner |
| organizations | `tenant_self`: `id = lattice_current_org_id()` | everyone |
| users | `member_visible` (SELECT): a membership exists in the bound org | everyone |
| external_identities | `member_visible` (SELECT): the user is visible | everyone |
| organizations, users, external_identities | `owner_provisioning` (ALL) | `lattice_owner` |
| organizations, users, external_identities, memberships, sessions | `auth_lookup` (SELECT) | `lattice_auth` |

All thirteen application tables (nine from Phase 1, four from Phase 2; `alembic_version` holds no tenant data) have RLS **enabled and forced** (verified by tests
and by the API's startup check, which covers every table in `public`). `alembic_version` holds no tenant data and is not granted to
the runtime role.

### Runtime-role privileges (Phase 1)

`SELECT` on organizations, users, external_identities, memberships, departments,
membership_departments, accounts, account_assignments; `SELECT, INSERT` on sessions;
`UPDATE (last_seen_at, revoked_at)` on sessions; `EXECUTE` on the two lookup
functions. Phase 2: `SELECT` only on access_groups, access_group_members, documents
and document_grants. Later phases add grants as their features need them: Phase 6
traces (SELECT, INSERT), Phase 8 jobs. **Phase 9 (migration 0012, ADR-014):**
`UPDATE (status, clearance, account_scope, authz_updated_at, updated_at)` on
memberships; `INSERT, DELETE` on account_assignments, membership_departments,
access_group_members (administrator-defined groups only) and document_grants
(administrator membership grants only); `SELECT, INSERT` on audit_events (`access.*`
by a member only). Each limit is a restrictive policy targeted at `lattice_app`.

### Pre-authentication lookups

Before a request's organization is known, identity data is readable only through:

- `lattice_resolve_session(token_hash bytea)` → the one session row with that hash
  (the caller must already hold the secret token);
- `lattice_identity_memberships(issuer, subject)` → the active (user, organization)
  pairs of a verified identity, used to choose the organization at sign-in.

Both are `SECURITY DEFINER`, owned by `lattice_auth`, with a fixed `search_path`, and
executable only by `lattice_app` (their ACL is `{lattice_auth=X, lattice_app=X}`; no
`PUBLIC` execute).

Finding during the pre-Phase-2 hardening: the original 0001 issued `REVOKE … FROM
PUBLIC` / `GRANT EXECUTE … TO lattice_app` *after* handing the functions to
`lattice_auth`. `lattice_owner` was then no longer the owner (and does not inherit from
`lattice_auth`), so PostgreSQL reduced both statements to warnings and left the default
`PUBLIC` execute in place. Exposure was limited — `CONNECT` on the databases is revoked
from `PUBLIC`, so only `lattice_owner`, `lattice_app` and superusers could connect — but
it contradicted this section. 0001 now sets the ACL before the ownership transfer, and
migration 0002 repairs databases created by the original 0001 (acting as
`lattice_auth` for the ACL statements).

### Startup safety check

The API refuses to start (`lattice.db.role_check`) if its role is a superuser, has
BYPASSRLS, is a member of a superuser/BYPASSRLS role, owns or is a member of the owner
of any application table, or if any application table lacks enabled + forced RLS or
the schema is unmigrated. `lattice check-db-role` runs the same check on demand.

RLS is for **organization isolation only**. Fine-grained authorization is compiled
into retrieval SQL by `authz` (ADR-003; implemented in Phase 2 as
`readable_documents_predicate`, see `authorization-model.md` §6).

## 9. Typed domain contracts (Pydantic)

Defined once in the backend and exposed via OpenAPI where they cross HTTP.

| Type                   | Purpose |
| ---------------------- | ------- |
| `AuthorizationContext` | **Implemented (Phase 2)**, frozen, strictly validated: organization_id, membership_id, membership_status, clearance, account_scope, assigned_account_ids, department_ids, group_ids (frozensets), authz_updated_at, policy_version. No admin flag or role label, by design |
| `RetrievalRequest`     | question, analyzed query (identifiers, phrases, account mentions, temporal intent), scope filters, k values |
| `RetrievalCandidate`   | chunk_id, document_id, lexical_rank?, semantic_rank?, fused_score, rerank features |
| `EvidenceChunk`        | *As built:* `retrieval.Evidence` (Phase 5) into answering; `answering.models.EvidenceItem` out (adds `classification`, `used_for_answer`, `not_used_reason`) |
| *(design)*             | Selected context item: chunk text, handle (E1…), document kind, title, display_ref, account name (**only** when `authz.visible_document_account` returns it — decision D-8; otherwise absent), occurred_at, authority, sensitivity, uri |
| `Citation`             | handle, chunk_id, quote, quote_start/end (verified offsets), document summary fields |
| `Claim`                | text, role (`direct_answer` \| `supporting`), citations[] (all validated — SEC-13) |
| `Conflict`             | kind (`superseded` — deterministic supersession link \| `potential_conflict` — model-proposed, code-verified, unresolved), topic, positions[{claim, citations, document_id, occurred_at, authority, version}], supersession? {superseded_document_id, superseding_document_id, since} |
| `FreshnessState`       | newest_evidence_at, oldest_cited_at, is_stale, threshold_days, note |
| `Answer`               | **Implemented (Phase 6, `lattice.answering.models`)**: as below plus `message` (fixed sentence per status), `clarification {question, choices}`, `question_sensitivity`; citations carry `evidence_id` (chunk ID), `document_id`, `quote`, `quote_start/end` |
| *(design)*             | status, status_reason, claims[], conflicts[], evidence[] (every item a citation may reference; items excluded from the prompt by egress policy are flagged `used_for_answer=false`), freshness, notices[] (confidential sources used, evidence not sent to the AI model under egress policy, claims removed in validation), clarifying_question?, trace_id |
| `SourceRecord`         | Connector output (see `connector-design.md`) |
| `SyncResult`           | **Implemented (Phase 4)**, a dataclass (`lattice.ingestion.sync`): per-run counts (seen, created, updated, access_updated, unchanged, skipped, failed, quarantined, deleted, would_delete, embeddings), deletion-guard flag, error and failure codes. Per-record outcomes are the failure rows; there is no per-record result type |
| `Trace`                | API view of a trace, re-authorized for the viewer |

## 10. Lifecycle propagation

```
source disconnected (`lattice source disconnect`) ─► one transaction: sources.status=deleting,
                 documents.status=deleted (unreadable from the next request)
                 └─► purge (next transaction, resumable): DELETE source ─► CASCADE documents,
                     chunks (vectors + tsv), all grants on them (source and admin), source groups
                     and members, sync runs and failures; members who lost a group get authz_updated_at
document deleted in source ─► sync sweep (guarded) ─► DELETE document (CASCADE chunks → vectors + tsv gone)
document updated ─► same transaction: UPDATE document, DELETE removed chunks, UPSERT kept/new chunks,
                 replace source grants
access changed in source ─► UPDATE documents access columns / replace source grants (no re-chunk,
                 no re-embed); a classification above the egress level removes vectors at once
access no longer mappable ─► documents.status=quarantined (unreadable, kept) until mapped again
user removed from account ─► DELETE account_assignment ─► next query excludes (no cache)
membership suspended ─► status=suspended; sessions for that membership rejected on next request
organization deleted ─► status=deleting; sessions revoked; purge by organization_id across all tables; verification query must return 0 rows per table
```

Because vectors and lexical entries are columns of `chunks`, there is no separate
index to reconcile and no orphaned vector state is possible.

Retention (built P11.4; defaults, org-configurable in Phase 12): traces 90 days; sync runs
180 days, keeping each source's latest run, with their record failures deleted together
with the run (the schema has no notion of a failure being resolved; the earlier "30 days
after resolution" had no column to act on); finished jobs 30 days; ended sessions 30
days; login attempts 1 day after expiry; counters 2 days; Ask slots 1 hour after their
lease; audit events never. Each is a database floor on the worker's DELETE — the classes
are in [deployment.md](deployment.md) §13.

### ask_slots, pre_auth_rate_limits (P11.4)

`ask_slots (organization_id, membership_id, slot, lease_id, expires_at)`: the in-flight Ask
limit shared by API processes; tenant RLS; `lattice_app` inserts, takes over expired rows
and deletes by lease ID. `pre_auth_rate_limits (bucket, client_key, window_start, count)`:
no organization; `client_key` is a 32-byte HMAC, never an address; no login role has a
privilege on it (forced RLS, a `lattice_auth` policy only), written through
`lattice_pre_auth_hit` and cleaned through `lattice_pre_auth_retention`.
