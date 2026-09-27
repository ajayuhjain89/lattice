# Lattice — Data Model

Status: §2 identity and tenancy tables are **implemented** (Phase 1, migration
`backend/alembic/versions/0001_baseline.py`); §3 onward are still design.
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
(`lattice_owner`) may provision organizations.

### users

Global identity. One person may belong to several organizations (SaaS reality:
contractors, consultants), so user ≠ membership.

| Field         | Type       | Req  | Notes                                  |
| ------------- | ---------- | ---- | -------------------------------------- |
| id            | uuid PK    |      |                                        |
| email         | citext UQ  |      | Login hint only; never an authz input  |
| display_name  | text       | CITE | Shown as author/asker                  |
| status        | enum       | DEL  | `active`, `disabled`, `deleted` (anonymized) |

### external_identities

| Field     | Type    | Notes                                              |
| --------- | ------- | -------------------------------------------------- |
| id        | uuid PK |                                                    |
| user_id   | uuid FK |                                                    |
| issuer    | text    | OIDC issuer URL, or `dev` for development login    |
| subject   | text    | Stable subject from IdP. UQ (issuer, subject)      |

Email is **not** used to link identities (email reuse / takeover risk).

### sessions

Server-side, opaque (ADR-005). Stored so they can be revoked immediately.

| Field           | Type        | Req   | Notes                                           |
| --------------- | ----------- | ----- | ----------------------------------------------- |
| id              | uuid PK     |       |                                                 |
| token_hash      | bytea UQ    |       | SHA-256 of the random cookie token; raw token never stored |
| user_id         | uuid FK     |       |                                                 |
| organization_id | uuid FK     | ISO   | The session's active org. Switching org = new session |
| created_at, last_seen_at, expires_at, revoked_at | timestamptz | | Idle timeout 12 h, absolute 7 d (configurable) |

Constraints (implemented): composite FK `(organization_id, user_id)` →
`memberships(organization_id, user_id)` — a session can exist only for a real
membership in its organization; `token_hash` must be exactly 32 bytes;
`expires_at > created_at`. The runtime role may insert sessions and update only
`last_seen_at` and `revoked_at`.

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
| status          | enum     |       | `active`, `churned`, `prospect`                      |

`account_assignments(organization_id, membership_id, account_id, assigned_at)` —
which accounts an `assigned`-scope member can see.

`account_external_refs(organization_id, account_id, source_id, external_ref)` — maps
e.g. a ticketing "organization id" or a Slack channel to an account. UQ
(organization_id, source_id, external_ref). Unmapped references on account-scoped
sources **fail closed** (see `ingestion-design.md` §6).

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
| organization_id | uuid    |                                                              |
| origin          | enum    | `source` (synced from a connector) \| `admin` (managed in Lattice) |
| source_id       | uuid?   | Set when origin = `source`                                   |
| external_ref    | text?   | e.g. Slack channel ID                                        |
| name            | text    | Shown to admins; may itself be sensitive → only admins see it |

`access_group_members(organization_id, group_id, membership_id)`.

Why groups exist: revoking a user from a private channel must be one row deletion,
not an update to thousands of documents; and permission changes must never require
re-embedding (they don't — access is joined at query time).

---

## 3. Sources and sync

### sources

| Field               | Type    | Req     | Notes                                                        |
| ------------------- | ------- | ------- | ------------------------------------------------------------ |
| id                  | uuid PK | CITE    |                                                              |
| organization_id     | uuid    | ISO     |                                                              |
| connector_type      | enum    |         | `document_repository`, `ticketing`, `chat_export`            |
| name                | text    | CITE    | "Halden Drive — Customer Projects"                           |
| config              | jsonb   |         | Validated by the connector's Pydantic config model on write and on load |
| default_sensitivity | enum    | AUTHZ   | **Required.** Applied when a record carries no sensitivity   |
| default_department_ids | uuid[] | AUTHZ | Optional default department restriction                       |
| account_scoped      | bool    | ACCT    | If true, every record must map to an account or it is not indexed |
| authority           | enum    | AUTH-R  | Default authority for records: `system_of_record`, `working`, `conversational` |
| status              | enum    | DEL     | `active`, `paused`, `deleting`                               |
| last_sync_run_id    | uuid?   | DBG     |                                                              |

### source_sync_runs

`id, organization_id, source_id, mode (full|incremental|acl_only), started_at,
finished_at, status (running|succeeded|partial|failed|aborted), records_seen,
created, updated, access_updated, unchanged, deleted, failed, deletion_guard_tripped
bool, error_code, error_summary (redacted)`. Req: **DBG**, **DEL**.

### sync_record_failures

`id, organization_id, sync_run_id, external_id, opaque_ref, stage (validate|
normalize|map_access|chunk|embed|persist), error_code, message_params jsonb,
created_at`. Messages are rendered from templates keyed by `error_code`; there is no
free-text message field, so record content cannot leak through failures.
`opaque_ref` is a keyed hash of the external ID. Admins see *what failed, when, why*;
the raw `external_id` (a filename or title in some sources) is displayed only if the
admin can read the corresponding active document, otherwise `opaque_ref`
(`security-model.md` §10).

---

## 4. Documents

One row per source record (a doc file, a ticket, a Slack thread or channel-day).

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
| status              | enum        | DEL             | `active`, `deleted`. Only `active` is retrievable |
| supersedes_document_id | uuid?    | FRESH           | Explicit supersession when source provides it (e.g. "Policy v2 replaces v1") |
| metadata            | jsonb       | DBG             | Validated per-kind extras (ticket status/priority, channel name). **Never** read by authz |

Indexes: UQ (organization_id, source_id, external_id); (organization_id, account_id);
GIN (department_ids); (organization_id, status, sensitivity);
(organization_id, source_updated_at).

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

| Field           | Type    | Notes |
| --------------- | ------- | ----- |
| organization_id | uuid    | |
| document_id     | uuid    | Cascade delete with document |
| principal_type  | enum    | `membership`, `department`, `group` |
| principal_id    | uuid    | |
| origin          | enum    | `source` (from connector ACL), `admin` |
| granted_by      | uuid?   | Admin membership, when origin = admin |
| created_at      | timestamptz | |

PK (document_id, principal_type, principal_id). Index (organization_id,
principal_type, principal_id).

Source-origin grants are replaced wholesale on each ACL sync; admin-origin grants are
never touched by sync (so an admin grant is not silently wiped by a connector).

## 6. Chunks

| Field           | Type          | Req         | Notes |
| --------------- | ------------- | ----------- | ----- |
| id              | uuid PK       | CITE, VER   | Deterministic: uuid5(document_id, content_hash, occurrence) |
| organization_id | uuid          | ISO         | |
| document_id     | uuid          | CITE, DEL   | Cascade |
| ordinal         | int           | CITE        | Position in document (for neighbours in inspector) |
| text            | text          | CITE        | Display text, exactly as in `documents.body[char_start:char_end]` |
| char_start, char_end | int      | CITE        | Offsets into `documents.body` for highlighting and context |
| heading_path    | text[]        | CITE        | Doc headings / "Ticket HAL-4821 › Comment 4" |
| occurred_at     | timestamptz   | FRESH       | Time of *this* content (message/comment time), falls back to document time |
| author_display  | text?         | CITE        | Author of this part (comment/message author) |
| content_hash    | bytea         | VER, DEDUP  | SHA-256 of chunk text; embedding reuse key |
| token_count     | int           | DBG         | Context budgeting |
| tsv             | tsvector      |             | Computed by ingestion (not a generated column, because it includes document-level header terms: title, account name, heading path): setweight(header,'A') ‖ setweight(text,'D'), `english` config. GIN index |
| embedding       | vector(D)     |             | D fixed per deployment by the chosen embedding model |
| embedding_model | text          | VER         | Model identifier; a model change is a tracked re-embed |

Indexes: (document_id, ordinal); GIN (tsv); vector index **deferred** (exact scan is
used while per-org chunk counts are small; see `retrieval-design.md` §5).

Why `occurred_at` on chunks: a ticket opened in March may have a comment from last
week; freshness must be judged on the evidence actually cited, not the container.

## 7. Traces and settings

### query_traces

Security-sensitive. Stores **references and measurements**, not document content.

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
| answer              | Final validated answer JSON (only validated claims + citations), retention-limited; hidden on read if any cited evidence is no longer readable by the viewer |
| status              | `answered`, `partial`, `insufficient_evidence`, `evidence_only`, `ambiguous`, `failed` (+ `status_reason`) |
| error_code          | |
| timings_ms          | `{authz, lexical, embed_query, semantic, fuse_rerank, generation, validation, total}` |
| usage               | `{input_tokens, output_tokens, estimated_cost_usd}` |

### org_settings

`organization_id PK, external_egress_level (internal|confidential|restricted,
default **internal**), freshness_thresholds jsonb (per document_kind, days),
trace_retention_days (default 90), ask_enabled bool (default true; incident kill
switch)`. Raising `external_egress_level` is an explicit, audited admin action.
There is deliberately no setting that lets admins read other members' content (D-5).

### model_providers

`id, organization_id, purpose (embedding|generation), provider, endpoint, model,
deployment (external_api|private), approved_for_level (internal|confidential|
restricted, default internal), retention_mode, training_opt_out bool, updated_by,
updated_at`. Effective egress level for a call =
`min(org_settings.external_egress_level, approved_for_level)`. Secrets are not stored
here (environment / secret store only).

## 8. Row-Level Security (implemented in Phase 1)

### Roles

| Role | Login | Purpose | RLS |
| ---- | ----- | ------- | --- |
| `lattice_owner` | yes | Owns the schema; runs migrations and operator tools (seed) | Subject to FORCE RLS on tenant tables; has provisioning policies on the three global/root tables only |
| `lattice_app` | yes | API (and later the worker). Never owns tables, never BYPASSRLS | Subject to every policy; least-privilege grants (below) |
| `lattice_auth` | **no** | Owns the two SECURITY DEFINER pre-authentication functions | Role-targeted read policies on the tables those functions need |

Roles, databases and extensions are created by `infra/postgres/bootstrap.sql` (run as
superuser; idempotent). Migrations only verify they exist. `lattice_owner` is granted
membership in `lattice_auth` `WITH INHERIT FALSE, SET TRUE` — enough to hand function
ownership to it, without silently inheriting its RLS policies.

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
| memberships, departments, membership_departments, accounts, account_assignments, sessions | `tenant_isolation`: `organization_id = lattice_current_org_id()` (USING + WITH CHECK) | everyone, incl. the owner |
| organizations | `tenant_self`: `id = lattice_current_org_id()` | everyone |
| users | `member_visible` (SELECT): a membership exists in the bound org | everyone |
| external_identities | `member_visible` (SELECT): the user is visible | everyone |
| organizations, users, external_identities | `owner_provisioning` (ALL) | `lattice_owner` |
| organizations, users, external_identities, memberships, sessions | `auth_lookup` (SELECT) | `lattice_auth` |

All ten application tables have RLS **enabled and forced** (verified by tests and by
the API's startup check). `alembic_version` holds no tenant data and is not granted to
the runtime role.

### Runtime-role privileges (Phase 1)

`SELECT` on organizations, users, external_identities, memberships, departments,
membership_departments, accounts, account_assignments; `SELECT, INSERT` on sessions;
`UPDATE (last_seen_at, revoked_at)` on sessions; `EXECUTE` on the two lookup
functions. No `DELETE` anywhere. Later phases add grants as their features need them.

### Pre-authentication lookups

Before a request's organization is known, identity data is readable only through:

- `lattice_resolve_session(token_hash bytea)` → the one session row with that hash
  (the caller must already hold the secret token);
- `lattice_identity_memberships(issuer, subject)` → the active (user, organization)
  pairs of a verified identity, used to choose the organization at sign-in.

Both are `SECURITY DEFINER`, owned by `lattice_auth`, with a fixed `search_path`, and
executable only by `lattice_app`.

### Startup safety check

The API refuses to start (`lattice.db.role_check`) if its role is a superuser, has
BYPASSRLS, is a member of a superuser/BYPASSRLS role, owns or is a member of the owner
of any application table, or if any application table lacks enabled + forced RLS or
the schema is unmigrated. `lattice check-db-role` runs the same check on demand.

RLS is for **organization isolation only**. Fine-grained authorization will be compiled
into retrieval SQL by `authz` (ADR-003, Phase 2).

## 9. Typed domain contracts (Pydantic)

Defined once in the backend and exposed via OpenAPI where they cross HTTP.

| Type                   | Purpose |
| ---------------------- | ------- |
| `AuthorizationContext` | Resolved per request: org_id, membership_id, clearance, account_scope, assigned_account_ids, department_ids, group_ids, authz_updated_at, policy_version |
| `RetrievalRequest`     | question, analyzed query (identifiers, phrases, account mentions, temporal intent), scope filters, k values |
| `RetrievalCandidate`   | chunk_id, document_id, lexical_rank?, semantic_rank?, fused_score, rerank features |
| `EvidenceChunk`        | Selected context item: chunk text, handle (E1…), document kind, title, display_ref, account name, occurred_at, authority, sensitivity, uri |
| `Citation`             | handle, chunk_id, quote, quote_start/end (verified offsets), document summary fields |
| `Claim`                | text, role (`direct_answer` \| `supporting`), citations[] (all validated — SEC-13) |
| `Conflict`             | kind (`superseded` — deterministic supersession link \| `potential_conflict` — model-proposed, code-verified, unresolved), topic, positions[{claim, citations, document_id, occurred_at, authority, version}], supersession? {superseded_document_id, superseding_document_id, since} |
| `FreshnessState`       | newest_evidence_at, oldest_cited_at, is_stale, threshold_days, note |
| `Answer`               | status, status_reason, claims[], conflicts[], evidence[] (every item a citation may reference; items excluded from the prompt by egress policy are flagged `used_for_answer=false`), freshness, notices[] (confidential sources used, evidence not sent to the AI model under egress policy, claims removed in validation), clarifying_question?, trace_id |
| `SourceRecord`         | Connector output (see `connector-design.md`) |
| `IngestionResult`      | Per-record outcome: created/updated/access_updated/unchanged/failed + reason |
| `SourceSyncResult`     | Aggregate of a sync run |
| `Trace`                | API view of a trace, re-authorized for the viewer |

## 10. Lifecycle propagation

```
source deleted ─► sources.status=deleting ─► documents.status=deleted (one UPDATE, immediate)
                 └─► purge job: DELETE documents (CASCADE chunks, grants) ─► DELETE source
document deleted in source ─► sync sweep ─► DELETE document (CASCADE chunks → vectors + tsv gone)
document updated ─► same transaction: UPDATE document, DELETE removed chunks, INSERT new chunks
access changed in source ─► UPDATE documents access columns / replace source grants (no re-embed)
user removed from account ─► DELETE account_assignment ─► next query excludes (no cache)
membership suspended ─► status=suspended; sessions for that membership rejected on next request
organization deleted ─► status=deleting; sessions revoked; purge by organization_id across all tables; verification query must return 0 rows per table
```

Because vectors and lexical entries are columns of `chunks`, there is no separate
index to reconcile and no orphaned vector state is possible.

Retention (defaults, org-configurable later): traces 90 days; sync runs 180 days;
sync failures 30 days after resolution. Retention jobs are Phase 11.
