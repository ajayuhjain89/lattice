# Lattice — Implementation Plan

Status: Phase 0 approved. Phase 1 approved and closed (after the `lattice_auth`
hardening below). Phase 2 approved, with decisions D-8 and D-9 (implemented at the start
of Phase 3). Phase 3 approved and merged to `main`. Phase 4 approved and merged to
`main` (5e12616), with decisions D-10 and D-12 applied and D-11 recorded for Phase 6.
**Phase 5 approved and merged to `main`** (78e5640, 2026-09-29; branch
`phase/5-retrieval` kept for reference). **Phase 6** (dc0973c) and **Phase 7** (5fe873f)
are merged, and **Phase 8** (9fed350) is merged. **Phase 9 (access management and audit
events)** was built on `phase/9-access-management`, reviewed and merged (8d3af3b); its plan
and as-built notes follow Phase 8 below. The Phase 5 closure added a local model, Gemini and the provider invocation
rule, and decided and implemented D-13 and D-14. The final closure (2026-09-29)
removed the rejected cross-encoder experiment, resolved D-15 (deployment trust
classes, migration 0007) and decided D-16's timing, and it froze retrieval.
Phase 5 engineering and security are **complete** (every security metric 0, final
verification passed). The product objective Recall@10 ≥ 0.90 is **not yet achieved**:
best real result is Gemini 0.726, local 0.695 (evaluation-plan §12). **Phase 6** was
built on `phase/6-generation`, reviewed and merged (dc0973c): engineering and security
verification complete; the real-model answer-quality evaluation was **not run**
(reference hardware constraint, M1 8 GB; ADR-012).

**Current state (2026-10-06).** Phases 1–11 are merged (Phase 9: 8d3af3b; Phase 10:
8e26522; Phase 11: 143392f), and the frontend product-design overhaul (D0–D7) is merged
(27da421); `design-system.md` v2 is canonical. Phase 11 closed on local production-shaped
verification only: real external staging validation is deferred for lack of a deployment
budget and remains a release gate before Phase 15 may declare v1 GA
([phase-11-plan](phase-11-plan.md) §21.1, §28). The paragraph above is the historical
status log. **Next: Phase 12 — Organization Setup, Identity Configuration &
Integrations** (§4 "Later phases") on `phase/12-org-setup-integrations`
([phase-12-plan.md](phase-12-plan.md)): plan approved, P12.0 contracts written,
P12.1 foundations delivered (local commit, 2026-10-08); P12.2 members and administrators
delivered (local commit, 2026-10-09); P12.3 waits for approval.

---

## 1. Requirements register

Every phase task must trace to at least one of these IDs.

| ID | Requirement | Source doc |
| -- | ----------- | ---------- |
| PR-1 | Employees ask one question and get a cited answer across docs, tickets, Slack | product-brief §5 |
| PR-2 | Answers show dates, freshness, potential conflicts (and deterministic supersession), uncertainty | product-brief §5, retrieval-design §10 |
| PR-3 | Evidence is inspectable with the exact supporting passage | product-brief §5, design-system §B12 |
| PR-4 | Honest insufficient-evidence and ambiguity handling | retrieval-design §11 |
| PR-5 | Operators can see what synced, what failed, when, why — without seeing content or metadata they can't read | ingestion-design §8, security-model §10 |
| PR-6 | Admins understand and change who has access and why | authorization-model §3–4 |
| ARCH-1 | FastAPI modular monolith; thin routes; typed contracts; error taxonomy | architecture §3, §7 |
| ARCH-2 | Postgres + pgvector + FTS as single datastore; migrations | ADR-002 |
| ARCH-3 | Provider-agnostic model boundaries | architecture §3, security-model §6 |
| ARCH-4 | OpenAPI is the contract; generated TS types | architecture §2 |
| ARCH-5 | Idempotent, deterministic ingestion with lifecycle handling | ingestion-design |
| SEC-1…15 | Security requirements, including AI egress policy (SEC-10), claim-level fail-closed citations (SEC-13), operational-metadata side channels (SEC-14), admin ≠ content access (SEC-15) | security-model §1 |
| OPS-1 | Another engineer can run Lattice from README without undocumented steps | this doc §5 |
| OPS-2 | Missing/invalid configuration fails clearly at startup | architecture §8 |
| OPS-3 | Structured logs; per-query traces | architecture §8, data-model §7 |
| EVAL-1 | Retrieval, generation and security evaluated separately; security metrics = 0 | evaluation-plan |
| DS-1 | UI implements tokens/components/states from the design system | design-system |

## 2. Principles for sequencing

1. Security primitives (tenancy, authz) are built and proven **before** content
   exists, so every later layer is born constrained.
2. Evaluation arrives with the thing it evaluates (retrieval evals in Phase 5, not
   Phase 9).
3. The first UI is built only when the API can produce real, validated answers, so
   the UI never mocks the trust model.
4. Each phase ends with passing tests and docs updated to match reality.

## 3. The first vertical slice

Complete when Phases 1–8 are done. It proves the 32 points in the brief; the mapping:

| Slice capability | Phase |
| ---------------- | ----- |
| Org, users, memberships, sessions | 1 |
| Permissions, predicate, equivalence proof | 2 |
| Realistic synthetic data for 3 source types, two orgs, golden set | 3 |
| Repeatable, idempotent ingestion; normalization; chunking; access metadata; embeddings; FTS | 4 |
| Org isolation + authz inside lexical and semantic retrieval; fusion; rerank; retrieval evals | 5 |
| Only authorized evidence enters generation; grounded answer; claim-level verified citations; potential conflicts & supersession; AI egress policy; staleness; insufficient/ambiguous; traces; injection tests; critical regression | 6 |
| Ask UI, evidence, inspector, all states | 7 |
| Sources status, trace view, README/runbook complete, design QA | 8 |

## 4. Phases

Each phase is intended to be reviewable in one sitting (roughly 1–2 weeks of focused
work for one engineer; estimates are rough).

---

### Phase 1 — Repository, infrastructure, tenancy, identity

**Purpose.** A runnable backend skeleton whose first real feature is structural org
isolation and revocable sessions.

**Scope / deliverables**
1. `git init`, `.gitignore`, `.editorconfig`, README skeleton (status + setup).
2. `compose.yaml` with `pgvector/pgvector:pg16`; `Makefile` targets `db-up`,
   `migrate`, `test`.
3. `backend/` with `uv`, Python 3.12, ruff, mypy (strict), pytest, pytest-asyncio.
4. `config` (pydantic-settings, fail-fast, env-mode guards), `.env.example`.
5. `errors` taxonomy + FastAPI exception handlers + error envelope.
6. Structured JSON logging with a redaction-safe formatter.
7. `db`: async engine, session dependency, `SET LOCAL app.org_id` binding;
   Alembic baseline migration creating extensions + roles (`lattice_owner`,
   `lattice_app`).
8. Tables: organizations, users, external_identities, sessions, memberships,
   departments, membership_departments, accounts, account_assignments — all with RLS.
9. `identity`: `DevIdentityProvider`, session create/validate/revoke, cookie handling,
   CSRF header check.
10. Endpoints: `GET /v1/health`, `POST /v1/auth/dev-login`, `POST /v1/auth/logout`,
    `GET /v1/me` (user, org, role label, capabilities).
11. Minimal seed CLI: `lattice seed --manifest data/synthetic/halden/org.yaml` for
    orgs/users/memberships (content comes in Phase 3).
12. CI workflow: lint, type-check, tests against a Postgres service container.

**Tests**
- Config: missing required var → startup error naming the var; production + dev
  auth → refuses to start; DB role with `BYPASSRLS` → refuses to start.
- RLS catalog test: every table with `organization_id` has RLS enabled + forced.
- Isolation: with `app.org_id` = A, selecting B's rows returns nothing; with no
  `app.org_id`, tenant tables return nothing.
- Composite FK: inserting a membership_department linking org A membership to org B
  department fails.
- Sessions: expired/revoked/suspended-membership sessions → 401; logout revokes;
  token stored only as hash.
- CSRF: state-changing request without header → rejected.

**Dependencies.** Approval of this foundation; decisions D-1 (auth target) only
affects later phases.

**Definition of done.** `make db-up migrate test` passes from a clean clone
following README; CI green; docs updated where reality differs.

**Non-goals.** OIDC, web app, documents, authz policy beyond org isolation, admin UI.

**Phase 1 as built — deviations from the plan above (all reflected in the docs):**

| Planned | As built | Why |
| ------- | -------- | --- |
| Baseline migration creates extensions + roles | `infra/postgres/bootstrap.sql` (superuser, idempotent) creates roles, databases and extensions; the migration verifies them | Role creation needs passwords (secrets) and CREATEROLE; `vector` needs superuser. Migrations run as `lattice_owner` and hold neither |
| Two roles (`lattice_owner`, `lattice_app`) | Plus NOLOGIN `lattice_auth` owning the SECURITY DEFINER lookups | With FORCE RLS a definer function owned by the table owner still sees nothing; a dedicated role with role-targeted read policies makes the documented pre-auth lookups work without BYPASSRLS anywhere |
| Policy `organization_id = current_setting('app.org_id', true)::uuid` | `lattice_current_org_id()` with `NULLIF(..., '')` | On a reused pooled connection the setting reads `''`, which would make the documented cast raise. Regression-tested |
| `organizations`, `users`, `external_identities` not RLS-scoped | RLS enabled + forced on all three | Otherwise the runtime role could enumerate other tenants and their people |
| Seed: orgs/users/memberships | Also departments, accounts, account assignments, dev identities; `lattice check-db-role` added | Needed to exercise memberships realistically; the check makes the startup guard runnable on demand |
| Error envelope field `trace_id` | `request_id` (+ `details` for validation) | Avoids confusion with query-trace IDs (Phase 6) |
| Makefile: db-up, migrate, test | Also setup, seed, api, lint, format, typecheck, db-bootstrap, db-down, db-destroy, check-db-role | Every documented command is real |

**Phase 1 closing hardening (before Phase 2).** `lattice_auth` held `CREATE` on
`public` permanently (granted by the bootstrap so migrations could transfer function
ownership to it). It now receives `CREATE` only inside migration 0001's transaction,
for the ownership transfer, and loses it before commit; the bootstrap revokes any
leftover grant and migration 0002 repairs existing databases. The review also found
that the lookup functions were executable by `PUBLIC` (0001's ACL statements ran after
the ownership transfer and were silently ignored); fixed in 0001 and repaired by 0002.
Final state and rationale: `data-model.md` §8; regression test
`backend/tests/test_auth_role.py` (26 tests, including behaviour probes run as
`lattice_auth` and a repair test from the original Phase 1 state); mutation check: the
original 0001/bootstrap behaviour fails 8 of them.

---

### Phase 2 — Authorization core

**Purpose.** The policy, its SQL form, and the proof they agree — before any
content exists.

**Deliverables.** access_groups, members, documents (access columns only +
minimal fields), document_grants tables; `AuthorizationContext` resolver; reference
`readable()`; `readable_documents_predicate()`; `explain()`; `assert_all_readable()`;
`policy_version`; import-linter contract (only `authz` builds access predicates).

**Tests.** Property-based SQL ⇔ reference equivalence (hypothesis, ≥ 500 examples in
CI); persona matrix (brief roles); revocation per change type; admin-capability ≠
data access; restricted reachable only by grant; fail-closed on unknown department.

**Non-goals.** Access management UI or endpoints (Phase 9), deny rules.

**Phase 2 as built — deviations and additions (all reflected in the docs):**

| Planned | As built | Why |
| ------- | -------- | --- |
| `document_grants(principal_type, principal_id)`, PK (document_id, principal_type, principal_id) | Typed nullable `membership_id` / `department_id` / `group_id`, each with a composite FK; CHECK ties `principal_type` to exactly one; surrogate `id` PK; three per-document unique constraints | A polymorphic ID cannot carry a foreign key, so a grant could name another organization's principal or a deleted one. Now structurally impossible (tested as superuser) |
| `document_grants.granted_by` | Deferred to Phase 9 | Audit data, not a policy input; its lifecycle belongs with `audit_events` |
| `access_groups.source_id` | Deferred to Phase 4 | No `sources` table yet; an unconstrained UUID would be an unchecked reference. `external_ref` present, CHECK: set iff `origin = 'source'` |
| `documents` (access columns + minimal fields) | Exactly: id, organization_id, account_id, sensitivity, access_mode, department_ids, status, timestamps. Account FK with no ON DELETE action; trigger validates `department_ids` against the document's organization | SET NULL on account deletion would widen access; arrays cannot have FKs |
| import-linter contract | AST test `tests/test_authz_boundary.py`: public-API-only imports **and** no access columns/tables named outside `authz` (attributes, keywords, SQL strings) | Import rules cannot see `Document.sensitivity == …` written in retrieval, the failure the rule exists to prevent. No new dependency |
| `AuthorizationContext` fields per data-model §9 | Plus `membership_status`; explicitly no admin flag or role label | The policy's `m.status = 'active'` clause needs an input; capability must be unreachable from the policy |
| `policy_version` constant | Plus a fingerprint test pinning each version to the AST of `context.py`, `policy.py`, `sql.py`, `resolve.py` | Makes "bump on any policy change" mechanical |
| `explain()` | `explain` (internal), `explain_own_access`, `explain_for_reviewer` | Explanations reveal document facts; the two public forms return `None` identically for missing and unreadable documents |
| — | `account_visible` / `visible_accounts_predicate` | Account existence is authorized too (security-model §10); needed by lists, filters, mention detection and autocomplete from Phase 5 on |
| — | `Database.tenant_snapshot` (REPEATABLE READ, READ ONLY) | The one-snapshot rule for context → retrieval → E4 (authorization-model §5) |
| Seed | Unchanged: groups and documents exist only as test fixtures | Groups and content are the Phase 3 dataset (Phase 3: the seed now upserts admin-defined groups by key; documents still come only from ingestion) |

Evidence: `tests/security/` (equivalence, personas, admin ≠ content, fail-closed,
revocation, explanations, tripwire, context), `tests/test_authz_schema.py`,
`tests/test_authz_boundary.py`, and the Phase 1 isolation suite extended to the new
tables.

**Phase 2 mutation check** (performed by hand, never committed; each defect applied
alone to a pristine `lattice/authz`, then the authorization, security, isolation and
role suites run). "Failed" counts include the policy-version fingerprint test, which
fails for every mutation of the policy modules; every mutation is also caught by at
least one behavioural test:

| # | Defect introduced | Tests failed | Caught by (besides the fingerprint) |
| - | ----------------- | -----------: | ----------------------------------- |
| M1 | Account check removed from the reference only | 21 | equivalence, personas, revocation, explain, admin, fail-closed |
| M2 | Account check removed from reference **and** SQL (equivalence cannot see it) | 12 | personas, revocation, explain, admin, fail-closed |
| M3 | `grants_only` treated as `policy` (both) | 20 | personas, fail-closed, revocation, admin |
| M4 | Department restriction ignored (both) | 16 | personas, revocation, fail-closed, admin |
| M5 | Admins implicitly widened by the resolver (scope all, confidential) | 8 | admin ≠ content, equivalence, personas, explain |
| M6 | SQL predicate replaced by `TRUE` | 42 | equivalence, personas, revocation, fail-closed, admin |
| M7 | Membership-status check removed from SQL only | 2 | equivalence |
| M8 | Tripwire returns (filters) instead of aborting | 6 | tripwire suite |
| M9 | `restricted` reachable through confidential clearance (both) | 3 | fail-closed (reference, and SQL with the CHECK removed); the CHECK itself prevents the state |
| M10 | Grant-organization check removed from SQL only | 2 | fail-closed (cross-tenant grant row with the FKs removed) |
| M11 | `explain_own_access` answers for unreadable documents | 6 | explanation oracle tests |
| M12 | Resolver accepts suspended/removed memberships as active | 4 | equivalence, revocation (deactivation) |

M9 was initially caught only by the reference-level test; its SQL-level twin used an
internal-clearance reader, which masked the defect. It now uses a confidential reader
and fails on the SQL side too.

---

### Phase 3 — Synthetic enterprise dataset + golden set v1

**Purpose.** Realistic data designed to exercise retrieval, permissions, freshness,
conflicts, refusal, injection and isolation (evaluation-plan §2).

**Deliverables.** `data/synthetic/halden/` and `data/synthetic/veridian/`: org
manifests (people, departments, accounts, assignments, groups, source configs);
Markdown documents, tickets JSONL, Slack export; canary strings; ≥ 50 golden items
covering every category; a dataset README describing storylines.

**Tests.** Dataset lint: every golden `relevant`/`must_not_retrieve` key exists;
every canary appears exactly once; manifests validate.

**Non-goals.** Generating filler with a model without review — every storyline
document is authored/reviewed deliberately (bulk distractor tickets may be
template-generated, then spot-checked).

**Phase 3 as built — decisions applied first, then the dataset:**

- **D-8** (structured account metadata): `authz.visible_document_account` —
  a readable document shows its account only when `account_visible`; `POLICY_VERSION`
  `authz-2`; `tests/security/test_account_metadata.py` (reference and SQL `LEFT JOIN`
  form, every persona × document). Documented in authorization-model §6/§9,
  security-model §10, data-model §9, retrieval-design §8, design-system §B12.
- **D-9** (stable access-group key): migration `0004_access_group_key.py`
  (`key citext NOT NULL`, UNIQUE per organization, slug-format CHECK on the text value,
  backfill `group-<id>` binding each organization under forced RLS); the seed upserts
  admin groups by key, renames in place, treats a new key as a new group and never takes
  over a source group. Tests: `tests/test_access_group_key.py`,
  `tests/security/test_access_group_key.py` (renaming or re-keying changes no access),
  `tests/test_seed.py`.
- **Dataset** (`data/synthetic/`, README there): Halden — 48 documents, 148 tickets
  (28 authored, 120 generated), a 237-message Slack export in 15 channels; Veridian — 7
  documents, 37 tickets, 19 messages; eleven storylines; 19 canaries; 89 golden items
  in 14 categories.
- **Tooling** (`lattice.dataset`, `lattice dataset lint|generate`, `make dataset-lint`,
  CI step): loaders for the three formats, the expected-access oracle (the reference
  policy over fixture metadata), the lint, the distractor generator.

| Planned | As built | Why |
| ------- | -------- | --- |
| Org manifests incl. groups and source configs | `org.yaml` gains `groups` (seeded); source configs in `sources.yaml` and person-level shares in `admin-grants.yaml`, validated but not seeded | `sources` and documents arrive in Phase 4; seeding them now would blur fixtures with ingested content |
| Dataset lint: keys exist, canaries once, manifests validate | Plus an expected-access oracle that asks the **reference policy** who can read each record; golden expectations checked against it (relevant readable, prohibited not, egress levels, hidden accounts, D-8, ambiguity choices, trap phrases); ground-truth checks (dates, order, supersession metadata, staleness, authority, quiet periods, collisions); 44 mutation tests prove each check fires | "Do not create records that contradict the real policy implementation" — enforced, not reviewed |
| Canaries appear exactly once | Plus a manifest locating each canary in a record part, detection of undeclared canaries, and `access` vs `isolation` canaries that must protect something | Tripwires only work where they are expected |
| Golden schema per evaluation-plan §3 | Extended: `organization`, category slugs incl. new `account_visibility`, `hidden_accounts`, `account_metadata_hidden`, `forbidden_claims`, `must_not_contain`, `trap_phrases`, `same_question_group`, provisional status `unsupported_request` (D-11) | Needed to express account side channels, security traps and refusals; documented in evaluation-plan §3–4 |
| ~120 documents, ~600 tickets, ~1,200 messages | 48 / 148 / 237 for Halden | Quality over volume (Phase 3 brief); grow where retrieval evals show a need |
| Sync-failure fixtures with sensitive filenames | Deferred to Phase 4 (connector fixtures); one unmapped ticket (HAL-4951) is in the corpus | They test connector/ingestion failure handling |
| Persona table (evaluation-plan §2) | Unchanged, plus Grace in #acct-acme (as in the Phase 2 fixture); Veridian gains Ines (COO, confidential) | Critical scenario realism; cross-org probes by the most privileged persona |
| Phase 2 persona fixture | Uses the seeded `exec-staff` group instead of creating a duplicate; the factory generates unique keys | The manifest now defines that group |

Findings while building it, fixed in the documents: the critical scenario
(authorization-model §10) was ambiguous for Grace — with scope `all`, "Acme" matches
Acme Corp and Acme Health — so her run selects the Acme Corp scope; and the
account-name terms in the shared index header conflict with D-8 (open decision D-10).

---

### Phase 4 — Connectors and ingestion

**Deliverables.** Connector Protocol + 3 connectors + shared contract test kit;
sources, sync runs, record failures tables; full pipeline (validate → normalize → map
access → hash → decide → chunk → embed → persist); deletion sweep with guard;
`acl_only` mode; `EmbeddingProvider` Protocol + fake + one real provider (per D-2);
`model_providers` records; AI egress policy on embedding (default: only `internal`
content is embedded by an external provider; confidential/restricted lexical-only);
templated sync failures with opaque references; CLI `lattice sync`, `--dry-run`,
`reindex`, `source disconnect`.

**Tests.** Idempotency (second sync = zero writes); update replaces chunks atomically
and reuses embeddings for unchanged chunks; ACL-only change → no re-embed; deletion
sweep + guard trip; fail-closed mapping cases; malformed input fixtures; chunker
golden outputs; recording embedding provider never receives confidential/restricted
chunk text under the default policy; failure rows never contain free text and show
opaque references for records the viewer can't read.

**Non-goals.** Scheduled syncs, API-backed connectors, PDF/DOCX parsing.

**Phase 4 as built.** Decisions applied first:

- **D-2 / ADR-008** (embedding provider): OpenAI `text-embedding-3-small` at 1024
  dimensions over plain HTTP (`httpx`, now a runtime dependency), approved for
  `internal` only. Voyage AI stays compatible (1024-dimension column, query/document
  purpose in the Protocol) and is re-evaluated with the Phase 5 retrieval evals.
- **D-10** (account names in shared index text): no structured account name or alias
  in the embedded header or the shared `tsv`; account matching happens at query time
  over visible accounts only (Phase 5). Payloads are asserted exactly in tests.
- **D-12** (admin grants): `lattice apply-admin-grants` after ingestion, all-or-nothing
  and idempotent, `--prune` to revoke; grant uniqueness now includes `origin`, so a
  sync never collides with or touches an admin grant.
- **D-11** (`unsupported_request`): recorded as decided; implemented with answering
  (Phase 6).

Deliverables: migration `0005_ingestion.py` (`sources`, `source_sync_runs`,
`sync_record_failures`, `chunks`, `org_settings`, `model_providers`; `documents`
source/provenance/content/version columns and the `quarantined` status;
`access_groups.source_id`); `lattice.connectors` (contract, registry, Markdown, ticket
JSONL and Slack connectors); `lattice.ingestion` (canonical text and hashing, chunking,
fail-closed access mapping, embedding input, persistence, the sync pipeline, source
disconnect); `lattice.providers` (Protocol, fake, recording, OpenAI adapter, egress
policy); `lattice.sources.config`; `lattice.admin_grants`; CLI `lattice sync`,
`reindex`, `source disconnect`, `apply-admin-grants`; Makefile `sync`, `sync-dry-run`,
`admin-grants`; a CI step syncing the dataset twice (the second writing nothing).

| Planned | As built | Why |
| ------- | -------- | --- |
| Pipeline order validate → … → embed → persist | Same; embeddings computed per batch outside transactions, then one transaction per document | No network call inside a transaction; a document's rows change together |
| Embedding failure: nothing written, old version stays searchable | New content written; chunks whose embedding failed are `failed` (lexical-only) and retried by the next sync; run `partial` | Keeping the old version would keep stale content — and its vectors — retrievable while waiting for a provider |
| Access-mapping failure: record fails | Also: an *existing* document whose record now fails mapping, normalization or validation is **quarantined** (new status, unreadable, kept) until the source states mappable access again | Leaving it active would keep it readable on access the source no longer states |
| Deletion guard: > 20% unseen | Also an empty snapshot; and any record that failed before it could be identified blocks the sweep (not overridable) and quarantines the unseen documents | A corrupt line could be any document, including one whose access changed |
| Sync modes full, incremental, acl_only | full, acl_only, reindex (`--rechunk`, `--reembed`); no incremental | No connector supports checkpoints; reindex is the planned re-chunk/re-embed operation |
| Connector contract: async iterators, `message` on errors, config models per connector | Synchronous iterators; `RecordError` has code + validated identifier params + an operator-only location, **no message**; connector config is just the export path | Local exports; a free-text field is where content would leak |
| `account_external_refs` table for ticketing organization IDs | `account_refs` in `sources.yaml` | Lattice-side configuration of one source; a table is needed once accounts are managed in Lattice — **built in P12.3** (migration 0018; `sources.yaml` mappings are written into it) |
| `sources.default_department_ids uuid[]` | Default departments by key in `config`, validated each sync | Arrays cannot carry foreign keys |
| Contextual header with account name and date | Kind, title, heading path (D-10); per-part time is rendered in chat chunk text | D-10; dates are per chunk (`occurred_at`) |
| Grants: one per (document, principal) | One per (document, origin, principal) | D-12: separate permission origins |
| `model_providers` configured by admins | Registered by the first sync that uses a provider, approved for `internal`; raising it has no command until Phase 6 | Records exactly which provider receives content; the default is the safe one |
| Egress: lowering the level deletes vectors in the same operation | Classification raised on a document: same transaction; org level or provider approval lowered: the next sync of each source | The settings change has no command yet (Phase 6/9); the sync reconciles |
| `IngestionResult` per record | Failure rows per failed record; `SyncResult` per run | Outcomes other than failures are counts |
| Performance target: full sync < 5 min (remote provider), no-op re-sync < 30 s | Both organizations with the fake provider in seconds; no-op re-sync writes nothing | Remote timings recorded when a real key is used |

Findings fixed while building it: the Slack connector would have put a thread root
without `reply_count` in both its thread and its day record (any message a reply points
to is now a root); the authorization boundary check stopped at the first access name in
a string, so an allowed name could hide a disallowed one later in the same SQL (it now
checks every name, with a regression test).

Evidence: `backend/tests/ingestion/` — the connector contract kit (both organizations'
real exports and malformed fixtures for every connector), access mapping, chunker golden
outputs and invariants over every record, sync (idempotency by row versions, dry run,
updates with chunk and vector reuse, atomic replacement, ACL-only, reclassification,
provider failures, quarantine, groups, supersession, reindex, locking, configuration
errors), deletion and disconnect, admin grants, **ingested access equals the dataset
oracle for every record and persona** (through the SQL predicate as `lattice_app`,
plus D-8 account display), egress payloads and canaries, failure rows and logs, the
OpenAI adapter over a mock transport, and the CLI. The Phase 1–2 suites cover the new
tables (RLS, runtime-role privileges, composite keys) and the boundary test has
reviewed allowances for the modules that write access state.

**Phase 4 mutation check** (by hand, never committed; each defect applied alone to
pristine sources, then `tests/ingestion` and the boundary test run; sources verified
byte-identical afterwards). Every defect is caught:

| # | Defect introduced | Tests failed | Caught by (modules) |
| - | ----------------- | -----------: | ------------------- |
| M1 | Unknown department keys dropped instead of failing the record | 3 | access_mapping, sync |
| M2 | Unmapped account on an account-scoped source indexed without an account | 14 | access_mapping, cli_ingestion, deletion, egress, ingested_access, sync |
| M3 | Restricted no longer forces grants_only | 26 | access_mapping, admin_grants, cli_ingestion, deletion, egress, ingested_access, sync |
| M4 | A sync replaces admin-origin grants too | 1 | admin_grants |
| M5 | Egress policy ignored when embedding new chunks | 2 | egress, sync |
| M6 | Deletion guard removed | 3 | deletion |
| M7 | Account reference added to the embedding header (D-10) | 1 | egress |
| M8 | Record failures never quarantine the existing document | 2 | sync |
| M9 | An access-only change re-writes content and chunks | 3 | admin_grants, sync |
| M10 | Change detection broken (every record rewritten) | 15 | admin_grants, cli_ingestion, deletion, egress, sync |
| M11 | Lowering the egress level keeps existing vectors | 1 | egress |
| M12 | Admin grants applied partially when an entry is invalid | 9 | admin_grants |
| M13 | Members removed from a private channel keep their group membership | 1 | sync |
| M14 | Markdown connector drops front-matter departments | 4 | ingested_access, sync |
| M15 | Private Slack channels not reported as grants_only | 3 | connector_contract, ingested_access |
| M16 | Failure parameters carry record content | 2 | access_mapping, egress |
| M17 | Unidentified failures no longer quarantine unseen documents | 1 | deletion |
| M18 | An unchanged chunk input is re-embedded instead of reused | 2 | sync |

Final Phase 4 check (before merge): a failed re-embed after a content change leaves no
stale vector. `tests/ingestion/test_embedding_consistency.py` covers old content + old
vector → content change → failed embedding (new body and lexical entries stored; the
changed passage is a new, `failed` chunk without a vector; the old row and vector gone;
the database refuses a vector on it) → next sync embeds exactly that input, and the same
text under a new title (same chunk IDs, vectors cleared, all re-embedded). Targeted
mutations: N1 chunk upsert keeps the old vector on conflict — caught; N2 vector reuse
keyed by chunk ID instead of embedding-input hash — caught; N3 failed chunks not retried
by the next sync — caught (both tests). No implementation change was needed.

M4 (a sync deleting admin grants) is observable only when a document is rewritten, so
the admin-grants suite catches it (a source-side change on a document with an admin
grant); the dataset-oracle test's re-sync after applying grants writes nothing.

---

### Phase 5 — Permission-aware hybrid retrieval + retrieval evals

**Deliverables.** Query analysis; lexical, semantic (exact scan), identifier paths;
RRF; `rerank_v1`; context selection; E4 tripwire; `lattice search` CLI (prints
candidates with per-stage ranks); eval harness `lattice eval retrieval` + report.

**Tests.** Critical Acme scenario at retrieval level; isolation suite over search;
account-mention detection ignores out-of-scope accounts (no interpretation, no boost,
no ambiguity choice);
canary never in candidate lists for unauthorized personas; mutation check (predicate
removed ⇒ suite fails); retrieval metrics baseline recorded (Recall@10 target ≥ 0.9).

**Non-goals.** HNSW, model reranker, LLM query rewriting.

**Phase 5 as built.** Deliverables: `lattice.retrieval` -- typed query and limits,
deterministic analysis (normalization, identifiers, temporal intent, visible-account
mentions), the shared conditions (tenant, lifecycle, `authz.readable_documents_predicate`,
narrowing scope; account joined only through `visible_accounts_predicate`), the exact,
lexical and semantic channels (each one statement), query-side AI egress and
embedding-space checks with explicit degradation outcomes, RRF (k = 60) with org-safe
deduplication and `matched_by`, `rerank_v1`, selection, evidence hydration through the
predicate, the E4 tripwire over every candidate and over the evidence, the trace and its
log line; `lattice.evals.retrieval` and `lattice eval retrieval` (JSON + Markdown
reports); `lattice search`; `make eval`; a CI step running the evaluation as every
persona (security gate). No migration: no new index was justified
(`retrieval-design.md` §7.6).

| Planned | As built | Why |
| ------- | -------- | --- |
| Lexical: `websearch_to_tsquery` (all terms), `ts_rank_cd` | Any of the question's lexemes; ranked by coverage of distinct lexemes, identifier phrases and visible-account terms, `ts_rank_cd` as tie-break | Measured: all-terms matching returned no lexical candidate for 63/89 golden questions (R@10 0.418 → 0.512 fake / 0.668 lexical over four measured changes, `evaluation-plan.md` §9) |
| Identifier path "injected at the top of the lexical list" | Its own channel and RRF list, plus the `identifier_match` rerank feature | ADR-004 and retrieval-design §6 already treated it as a list; three independent channels are testable separately |
| Identifier regex per source configuration | One pattern (`PREFIX-123`, `PREFIX-2026-17`) against `display_ref` | No source configures one; the only references in the corpus are ticket IDs |
| Questions always sent to the embedding provider (security-model §6 draft) | Query egress policy: declared question classification (default `internal`) ≤ effective level, provider recorded in `model_providers` | Phase 5 requirement: the question can carry confidential information. Default behaviour unchanged for ordinary questions; D-13 decides the declaration's UX and Phase 6 use |
| Vectors compared by model name | Plus the provider's dimensions and an unambiguous (provider, model) registration | A failed `--reembed` keeps the previous model's vectors, so spaces coexist; the model name alone does not name the provider |
| Context selection: dedupe, ≤ 3 per document, ≤ 12 chunks / 6k tokens, egress split, relevance floor, conflict coverage | Retrieval selects: dedupe by content hash, ≤ 3 per document first (the rest deferred, not dropped), ≤ 20 evidence chunks. Budget, egress split, relevance floor and conflict coverage move to Phase 6 | They decide what reaches the model, not what is permitted evidence |
| Supersession for Phase 6 | Evidence carries supersession links between **readable** documents only | An unreadable successor must not be revealed (SEC-5); D-14 |
| `query_traces` recorder | In-memory `RetrievalTrace` returned with the result; persisted in Phase 6 | The table and its viewer rules belong with Ask (data-model §7) |
| Evidence endpoint / HTTP retrieval API | None; `lattice search` for developers | Ask and `GET /v1/evidence` are Phase 6; no endpoint invented for testing |
| Real-provider Recall@10 ≥ 0.9 | Phase 5: blocked (no key). **Phase 5 closure: measured -- Gemini 0.726, local 0.695 (not met)**; OpenAI not run | Fake embeddings are a test double, not a quality proxy; see evaluation-plan §10 |

Evidence: `backend/tests/retrieval/` -- query model and limits (no authorization claims
accepted, abusive input rejected), fusion and rerank units, retrievers against
hand-built worlds (adversarial strongest-match per channel, cross-org identifier
collisions, lifecycle, D-8/D-10 with an account-less control twin), structural SQL
proofs for all four statements, query egress and degradation with a recording
provider (incl. OpenAI outage without leaking the key), E4 abort-not-filter, trace
content, snapshot consistency under a mid-request revocation, no existence oracle, a
200-world property test (also with RLS bypassed), and `golden/` over the real corpus:
all 89 items with zero violations (semantic on and off), recall regression floors,
same-question groups, adversarial withheld-evidence checks, the critical Acme
scenario, D-8/D-10 items, injection fixtures as data, raw SQL capture and
re-execution. The authz boundary test names the retrieval modules explicitly.

**Phase 5 mutation check** (by hand, never committed; each defect applied alone to
pristine sources, then `tests/retrieval` and the authz boundary test run; every file
restored and verified byte-identical by SHA-256). Every defect is caught:

| # | Defect introduced | Tests failed | Caught by (modules) |
| - | ----------------- | -----------: | ------------------- |
| M1 | Authorization predicate removed from lexical retrieval | 142 | structure, retrievers, invariants (tripwire), property, golden, SQL capture, critical Acme |
| M2 | Authorization predicate removed from semantic retrieval | 148 | same seven modules |
| M3 | Tenant conditions removed from the exact lookup | 2 | structure (the predicate's own organization clause and RLS still isolate: defence in depth) |
| M4 | Hidden structured account names usable as search input (all accounts "mentionable") | 29 | retrievers, invariants, property, golden |
| M5 | `failed` embeddings admitted to vector retrieval | 1 | structure (a failed chunk has no vector or model -- DB CHECK -- so the model condition also excludes it) |
| M6 | Query vector compared with another embedding model | 3 | retrievers, degradation, structure |
| M7 | Both E4 `assert_all_readable` calls skipped | 3 | invariants |
| M8 | Deduplication keyed by chunk without the organization | 1 | fusion (cross-tenant chunk IDs cannot collide in the database; the unit test pins identity) |
| M9 | Count of matching documents the reader cannot see added to the trace | 2 | invariants (existence oracle, trace content) |
| M10 | Recency drops evidence older than the newest by 60+ days | 6 | fusion/rerank, golden, critical Acme |
| M11 | Authority drops conversational evidence when a system of record exists | 2 | fusion/rerank |
| M12 | A semantic failure aborts the request | 8 | degradation |
| M13 | Scope account resolved among all accounts (trusting the client's value) | 2 | retrievers, property |
| M14 | Exact lookup without tenant or authorization conditions | 10 | retrievers, property (incl. RLS bypassed), structure, golden |
| M15 | Evidence lookup by ID without the authorization predicate | 2 | structure |
| M16 | Structured account joined without account visibility (D-8) | 16 | retrievers, invariants, property, structure, golden |
| M17 | Supersession names successors the reader cannot read | 1 → 2 | structure; after adding the unreadable-successor test, also retrievers |
| M18 | Query egress ignores the question's classification | 4 | degradation, invariants |

---

**Phase 5 closure (2026-09-28): local embeddings, Gemini, BYOK architecture.**
Deliverables: `LocalEmbeddingProvider` (`mxbai-embed-large-v1`, 1024 native, ONNX
Runtime, pinned weights fetched only by `lattice models fetch`; ADR-009),
`GeminiEmbeddingProvider` (`gemini-embedding-2` at 1024, text-prefix task semantics,
unpaid key synthetic-only; ADR-010), the provider invocation rule for documents and
questions (`providers.egress.provider_decision`: recorded, deployment-true, data-use
acceptable, approved for the sensitivity; ADR-011), migration 0006 (`local`
deployment, `default_query_sensitivity`, `data_origin`), `lattice provider approve`,
`lattice models list/fetch/verify`, D-13 and D-14 implemented, evaluation query
prefetch and usage accounting. No retrieval tunable changed (`retrieval-6`). No
Phase 6 work.

| Planned (closure brief) | As built | Why |
| ----------------------- | -------- | --- |
| Fake provider has deployment `test`, no egress | No network, but *declared* `external_api` | So every egress test exercises the external rule; production refuses it anyway |
| Local provider: `deployment = local` or `self_hosted` | `local` (migration 0006); `private` unchanged and still capped by the external level, until the final closure replaced it (D-15, migration 0007) | Conservative until D-15 was decided |
| Gemini credential "existing convention" | `LATTICE_GEMINI_API_KEY` or Google's `GEMINI_API_KEY` | The user's `.env` uses Google's name |
| Unpaid-tier key synthetic-only | Enforced by data: `org_settings.data_origin = synthetic` (seeded for Halden and Veridian) plus the recorded `training_opt_out = false`; refused in production | A declaration alone would be documentation, not a control |
| Evaluation over the whole corpus with Gemini | Done in two runs: 985/1,289 chunks before the project's observed daily quota (about 1,000 embedded texts for that project and tier), then only the 304 failed chunks with a second valid key (stored vectors kept) | The quota came from the 429's quota detail, not from Google's docs; quotas vary by project, model and tier |
| One local model | Two: `mxbai-embed-large-v1` (default) and `snowflake-arctic-embed-l-v2.0` (comparison, pinned; R@10 0.657 < 0.695, so not adopted) | The first miss analysis flagged embedding quality as a plausible factor (brief §21). The comparison ruled it out as the lever |
| Harness: `must_not_contain` anywhere in evidence is a security hit | Only for canaries or unreadable records; readable injection payloads are informational | The harness's documented definition; the item targets that record (evaluation-plan §10) |

**Phase 5 closure mutation check** (applied one at a time to the working tree, the
named test modules run, every file restored and verified byte-identical by SHA-256;
never committed). All 15 caught:

| # | Defect introduced | Tests failed | Caught by |
| - | ----------------- | -----------: | --------- |
| C1 | Local provider opens a network connection while embedding | 1 | `test_inference_makes_no_network_call` |
| C2 | Local provider stops checking vector dimensions | 1 | `test_malformed_inference_output_is_a_typed_failure` |
| C3a | Gemini questions bypass the query egress check | 1 | `test_a_provider_credential_cannot_lower_it` |
| C3b | Gemini document chunks bypass the egress eligibility | 1 | `test_an_unpaid_key_receives_only_synthetic_content` |
| C4a | Unpaid Gemini key declared as not using content | 3 | data-use declaration, factory, unpaid-key eligibility |
| C4b | Unpaid Gemini key allowed in production | 1 | `test_factory_gemini` |
| C4c | Data-use policy ignored (a data-using provider treated as approved) | 2 | unpaid-key eligibility; customer question never sent |
| C5 | The user's declaration can lower the organization default | 6 | question-sensitivity property and client-lowering tests |
| C6 | A provider credential lowers the question sensitivity | 1 | `test_a_provider_credential_cannot_lower_it` |
| C7 | External semantic runs although policy excluded it | 13 | question-sensitivity and semantic-degradation suites |
| C8 | Supersession names an unreadable successor | 2 | D-14 tests |
| C9a | Query vector compared with another model's vectors | 2 | `test_semantic_compares_only_the_providers_embedding_space`, degradation |
| C9b | A provider's deployment claim trusted over its record | 2 | deployment-mismatch (ingestion and query) |
| C9c | The local rule (no external cap) applied to external providers | 1 | `test_provider_selection_cannot_lower_it` |
| C10 | Provider-specific code drops the authorization predicate for local models | 1 | `test_every_provider_returns_only_readable_same_tenant_evidence` |

**Forward requirements for Phase 6** (recorded now, not implemented): generation
resolves its provider per organization through the same configuration and invocation
rule (ADR-011; no call site depends on one global key); the question's effective
sensitivity (`RetrievalResult.question_sensitivity`, D-13) governs whether the
question may be placed in an external prompt (otherwise `evidence_only`, no model
call); evidence above the provider's effective level is excluded from the prompt as
security-model §6 describes; the Anthropic generation adapter is BYOK like the
embedding providers.

**Final Phase 5 closure (2026-09-29): freeze retrieval, D-15, D-16.**

- **Rejected experiment removed.** A local cross-encoder (`gte-reranker-modernbert-base`)
  was measured as a bounded Phase 5.1 experiment and rejected for the default path.
  It improved MRR and R@1, but it lowered Gemini R@10 (0.726 → 0.711), added only
  +0.018 locally, regressed supersession, and took 5–13 s per query on the reference
  M1. Its uncommitted integration (a `Reranker` protocol, a cross-encoder provider, a
  `crossrank` stage, service, trace and evaluation hooks, a CLI flag, a factory, a model
  registry entry and a setting) was removed. It never reached a commit and it added no
  dependency. The findings are in evaluation-plan §11.
- **Retrieval frozen.** The retained path is exact, lexical and semantic retrieval with
  authorization in SQL, RRF, rerank_v1, selection and E4. No retrieval tunable or
  algorithm changed in the final closure, so the Gemini benchmark (R@10 0.726) stands
  as recorded and was not re-run (no quota spent; evaluation-plan §12).
- **D-15 as built.** Trust classes `local`, `organization_controlled`, `external_api`,
  plus `unknown` (fail closed) in `providers.egress` (`trust_class`, `class_ceiling`,
  `provider_decision`). Migration 0007 relabels `private` records as
  `organization_controlled` and restricts the column to the three known classes. It
  adds `model_providers.approved_at` (set by `lattice provider approve`) and
  `org_settings.organization_controlled_level` (NULL = class not allowed). An
  `organization_controlled` provider needs both before anything is sent. `local` and
  `external_api` decisions are unchanged. Ingestion never records an unknown declared
  class. New reason codes: `deployment_unknown`, `deployment_not_allowed`. Tests:
  `tests/providers/test_trust_classes.py` (the decision for every class and class
  pair), `tests/retrieval/test_trust_classes.py` (questions, observed),
  `tests/ingestion/test_provider_eligibility.py` (document chunks, observed) and
  `tests/test_migrations.py` (0006 → 0007 → 0006 relabel round trip).
- **D-16 timing.** The Phase 6 backend builds the minimum organization-scoped provider
  configuration and secret abstraction. The Phase 7 frontend builds the administrative
  UX (ADR-011). Nothing is built in Phase 5.

**Final closure mutation check (D-15)** (each applied alone to the working tree; the
trust-class, eligibility, migration, question-sensitivity and provider-authorization
modules run, 98 tests; every file restored and verified byte-identical by SHA-256;
never committed). 8 of 8 relevant mutations caught:

| # | Defect introduced | Tests failed | Caught by (examples) |
| - | ----------------- | -----------: | -------------------- |
| D15-1 | An unknown class is treated as `external_api` | 15 | `test_an_unknown_deployment_sends_no_query[*]`, `…_no_document_text` |
| D15-2 | `organization_controlled` needs no explicit approval | 7 | `test_organization_controlled_needs_an_explicit_approval[*]`, other-organization test, 0007 migration test |
| D15-3 | `organization_controlled` allowed up to `restricted` by default | 4 | class-ceiling tests (query and documents), relabelling test |
| D15-4 | The external ceiling is ignored (an approval or credential raises it) | 8 | `test_an_external_credential_does_not_raise_the_ceiling`, external minimum tests |
| D15-5 | The declared class is trusted over the record (downgrade/upgrade between classes) | 10 | `test_a_declared_class_that_differs_from_the_record_sends_nothing[*]` (all 6 class pairs) |
| D15-6 | An unknown declared class is recorded and used | 15 | unknown-class query and document tests |
| D15-7 | The schema accepts the old label `private` | 1 | `test_the_schema_stores_only_known_classes[private]` |
| D15-8b | Another organization's provider record is used (organization filter dropped **and** RLS disabled on `model_providers`) | 1 | `test_another_organizations_configuration_is_never_used` |

Dropping only the organization filter in `providers.records` (D15-8) is **not** a
defect: all 98 tests pass because `model_providers` has forced row-level security, and
the runtime role cannot bypass it. The filter and RLS are two independent layers, and
D15-8b shows the test fails as soon as both are gone.

**Forward requirements for Phase 6, added by the final closure** (not implemented):

- **Retrieved content is untrusted data, never model instructions (hard security
  requirement).** Retrieving a readable document that contains prompt-injection text
  is not an authorization leak (evaluation-plan §10). The Phase 6 adversarial tests
  must prove that retrieved passages cannot override system/developer instructions,
  request hidden documents, reveal unauthorized content, credentials or secrets,
  change the organization context, alter authorization or provider/egress policy,
  cause unauthorized actions, or exfiltrate information.
- **Multi-hop evidence** remains an open product requirement (evaluation-plan §12). No
  approach is approved.

### Phase 6 — Answering: generation, claim-level citations, freshness, potential conflicts, traces

**Deliverables.** `GenerationProvider` Protocol + fake (deterministic, cites first
sentences; plus adversarial fakes that emit invalid handles and fabricated quotes) +
real provider; prompt v1; `AnswerDraft` schema with claim roles; **claim-level,
fail-closed validation** (any failed citation invalidates its claim; one
regeneration; then removal; no percentage threshold; answer withheld when no valid
direct-answer claim remains); response invariant (every citation references
returned, readable evidence); freshness; deterministic supersession +
model-proposed, code-verified **potential conflicts**; statuses incl. `evidence_only`
and `insufficient_evidence/claims_unverified`; **AI egress policy for generation**
(default `internal` only; org opt-in + approved provider for higher levels; minimal
append-only `audit_events` for egress-level changes via CLI); `query_traces` +
recorder (asker view; admin operational view without content); `POST /v1/ask`,
`GET /v1/evidence/{chunk_id}`, `GET /v1/traces/{id}`; OpenAPI → TS generation script;
`lattice eval answer` with judges.

**Deliverables carried from the Phase 5 closure (required):**

- **Organization-scoped provider configuration and secret abstraction (D-16,
  ADR-011).** The minimum needed for generation and BYOK: organization → provider
  configuration → deployment trust class → provider/model → secret reference →
  invocation. Credentials belong to the organization; secret values are encrypted or
  held through a secret-manager abstraction; they never appear in API responses, logs,
  traces, prompts or errors; rotation, replacement, revocation and provider
  disablement are supported; environment variables stay as the development fallback.
  No settings UI (Phase 7).
- **Generation goes through the same invocation rule** (`providers.egress`): user
  authorization, the organization's allowance and ceiling for the deployment trust
  class (D-15; `unknown` fails closed), provider approval, data-use terms, then
  configuration and credentials. A credential never authorizes egress. The question's
  effective sensitivity (D-13) decides whether it may enter an external prompt.
- **Retrieved content is untrusted data, never model instructions** (hard security
  requirement, trust-boundaries §4).

**Tests.**
- Citations (claim-level fail-closed):
  1. Invalid evidence handle → that claim is absent from the response.
  2. Fabricated quote (not in the cited evidence) → that claim is absent.
  3. Mixed valid and invalid claims → only validated claims survive; a claim with
     one valid and one invalid citation is absent (never shown with the bad citation
     stripped); rejected claim text appears nowhere in response, trace or logs.
  4. All material (direct-answer) claims invalid → answer withheld,
     `insufficient_evidence` / `claims_unverified`, evidence list returned.
  5. Every citation in the response references an evidence item present in the same
     response and readable by the user; a forced invariant violation withholds the
     answer. (Phase 7 adds the UI counterpart: a chip can never render for missing
     evidence.)
- Conflicts: supersession only when metadata links the documents; model-proposed
  disagreements without a link are `potential_conflict` with both positions kept;
  summary line contains no "supersedes/correct/resolved" wording; a proposed conflict
  whose positions fail validation is discarded.
- Egress: under the default policy no confidential/restricted text or canary reaches
  any provider for any persona (incl. Grace/Arjun); excluded evidence is still
  returned to authorized users with the egress notice; `evidence_only` when all
  relevant evidence is excluded; opt-in + approved provider admits `confidential`
  but not `restricted`; unapproved provider caps the level at `internal`.
- Recording provider: payload contains no UUIDs, no asker identity; refusal paths
  identical for restricted vs nonexistent (response equality test); trace contains no
  document text and no rejected claim text; admin trace view contains no question,
  answer, evidence references, counts or scope.
- Injection fixtures (deterministic parts) and **adversarial prompt-injection tests**:
  retrieved passages cannot override system/developer instructions, request hidden
  documents, reveal unauthorized content, credentials or secrets, change the
  organization context, alter authorization or provider/egress policy, cause
  unauthorized actions, or exfiltrate information.
- Provider configuration: secrets never in responses, logs, traces, prompts or errors;
  rotation and revocation take effect on the next call; a disabled provider receives
  nothing; another organization's configuration is never used; generation obeys each
  trust class exactly as retrieval does.

**Non-goals.** Streaming of answer text; multi-turn; any admin capability to read
other members' content.

**Phase 6 as built.** Deliverables: `lattice.answering` (prompt `answer-prompt-1`,
claim-level validation, the generation invocation rule, deterministic assembly, the Ask
service, evidence lookup), `lattice.tracing` (query traces, re-authorized views, audit
event model), generation providers (`fake`, scripted, recording, `ollama`, `gemini`;
ADR-012), the provider factory, secret references (`lattice.providers.secrets`), audited
provider configuration (`lattice.providers.configuration`), migration 0008
(`model_providers.endpoint/enabled`, `provider_credentials`, `query_traces`,
`audit_events`), `authz.readable_document_classifications` and
`authz.summarize_context`, `retrieval.lookup_evidence`, the routes `POST /v1/ask`,
`GET /v1/evidence/{id}`, `GET /v1/traces/{id}`, `GET /v1/admin/providers`, the CLI
`lattice ask`, `lattice eval answer`, `lattice provider configure|approve|enable|disable|
credential set|credential revoke|list`, `lattice org egress`, `make eval-answer`, and a CI
step running the answer evaluation with fake providers (security gate). No retrieval
tunable or algorithm changed (`retrieval-6`).

| Planned | As built | Why |
| ------- | -------- | --- |
| Real generation provider; D-4 recommends Anthropic | Ollama (`qwen3.5:4b`, local) and Gemini (`gemini-3.5-flash-lite`, BYOK) adapters, mock-tested; no Anthropic/OpenAI adapter; D-4 open | No Anthropic/OpenAI credential exists or may be requested; the user's constraint is no paid API. The factory's `declare`/`build` pair is where they go |
| Real-provider `lattice eval answer` with judges | Harness and optional local judge built; **real-model run NOT RUN -- reference hardware constraint (M1 8 GB)** (ADR-012); Gemini quota exhausted | A 940-token smoke prompt ran at ~10 tokens/s and the machine stopped responding; the product owner chose not to run it or substitute a smaller model |
| Secret values "encrypted at rest or held through a secret-manager abstraction" | References only (`env:LATTICE_PROVIDER_SECRET_<NAME>`, legacy provider names); a `SecretStore` Protocol with environment and in-memory stores; references to other process variables refused in code and schema | The database never holds a value; a production secret manager is one more scheme behind the Protocol (decision before Phase 7: which one, for the UI's "paste a key" flow) |
| Provider-configuration API for Phase 7 | Read-only masked `GET /v1/admin/providers`; writes by audited CLI | Writes need the Phase 7 UX decisions (who, reasons, confirmation); the audited functions are what the UI will call |
| `audit_events` for egress-level changes | Also provider configuration, approval, enablement and credential set/rotate/revoke; append-only by trigger; runtime role has no privilege | Every D-16 administrative action needs a record |
| Relevance floor | Per item: exact/lexical match, or semantic similarity above a per-model calibrated threshold (none calibrated) | Semantic nearest neighbours always exist (retrieval-design §13) |
| `ambiguous` when ≥ 2 in-scope accounts match | Only when no matched account is named in full | "Acme Corp" contains Acme Health's alias |
| Validation: handles + verbatim quotes | Plus quote length floor, unsupported numbers, shared content terms, unsafe content (links, addresses, markup) | One valid quote must not legitimize an unrelated statement; no rendered exfiltration |
| Prompt delimiters | Per-request nonce-suffixed delimiters | Retrieved text cannot close its own block |
| `evidence_only (egress_policy)` | Plus `evidence_only (generation_unavailable)` for missing/disabled/unsupported configuration or credentials | Configuration states are not policy outcomes |
| `org_settings.ask_enabled`, `freshness_thresholds`, `trace_retention_days`, `filter_fingerprint`, cost estimates | Not built; `lattice provider disable --purpose generation` is the per-organization stop | No consumer yet; retention jobs are Phase 11 |
| OpenAPI → TS generation script | Not built (Phase 7) | No web app exists yet to consume it |
| Streaming progress | Not built (D-6, Phase 7) | — |

Evidence: `backend/tests/answering/` (validation and prompt units, the Ask pipeline over
hand-built worlds, the generation invocation rule, the HTTP API, the golden corpus with
the harness's security gate, the critical Acme scenario's payload/response/lookup
halves, an obedient model against the injection fixtures),
`tests/providers/test_generation_adapters.py`, `tests/test_provider_configuration.py`,
and the Phase 1 isolation suites extended to the three new tables.

**Final Phase 6 verification (2026-09-29).** Clean clone of `phase/6-generation` with no
external API key in the environment, in an isolated Compose project (dev database
stopped; one PostgreSQL at a time): setup, migrate (0001-0009), seed, runtime-role
check, dataset lint, sync twice, `make eval` (fake; R@10 0.512, unchanged; security 0),
`make eval-answer` (fake; every security metric 0), lint, format, mypy strict (203
files), and 1,638 tests: all passed. Secret scan of tracked files and of every commit on
the branch: no key patterns, and the real `.env` key value appears nowhere. The real
answer-quality evaluation is **NOT RUN — reference hardware constraint (M1 8 GB)**
(evaluation-plan §13, ADR-012).

**Phase 6 mutation check** (by hand, never committed; each defect applied alone to the
working tree, the Phase 6 suites run -- `tests/answering/` including the golden corpus,
`tests/providers/test_generation_adapters.py`, `tests/test_provider_configuration.py` --
every file restored and verified byte-identical by SHA-256). The first run caught 25 of
28. The three survivors were test gaps, not code defects; the tests named below were
added and the three mutations re-run. **All 28 are caught.** The G27 test also found a
real defect: the service derived freshness from citations *before* the response
invariant, so a dangling citation would have raised instead of withholding the answer;
the invariant now runs first. Counts exclude one API assertion that was flaky (a short
rejected value could occur by chance inside the random request ID), since fixed.

| # | Defect introduced | Tests failed | Caught by |
| - | ----------------- | -----------: | --------- |
| G1 | Egress level ignored: every evidence item may enter the prompt | 10 | default-policy, opt-in, evidence-only, credential-cannot-widen tests; critical Acme payload half |
| G2 | Question sensitivity (D-13) not applied to generation | 3 | `test_a_question_that_may_not_be_sent_is_never_sent[*]` |
| G3 | Credential resolved before the invocation rule | 2 | `test_no_secret_is_resolved_when_the_policy_refuses[*]` (added after the first run) |
| G4 | Declared trust class ignored (record trusted) | 4 | class-mismatch tests, unknown declared class |
| G5 | Disabled configuration still used | 1 | `test_a_disabled_provider_receives_nothing` |
| G6 | Provider built although no call will be made | 1 | `test_no_evidence_is_insufficient_without_a_model_call` |
| G7 | Revoked credentials still resolve | 1 | `test_revocation_and_rotation_take_effect_on_the_next_request` |
| G8 | A claim keeps its valid citations when one fails (bad one stripped) | 5 | invalid handle, one-valid-one-invalid citation (unit and end to end) |
| G9 | Quotes not verified against the evidence | 2 | quote-not-found unit cases |
| G10 | Unsafe content (links, addresses) allowed in claims | 4 | unsafe-content unit cases, non-claim text |
| G11 | Unsupported numbers allowed | 1 | unsupported-number unit case |
| G12 | Conflicts kept with positions from one document | 2 | conflict unit and end-to-end tests |
| G13 | Fixed delimiters (no nonce) | 1 | `test_prompt_blocks_handles_and_order` |
| G14 | Document IDs sent in the prompt | 1 | `test_prompt_blocks_handles_and_order` (no UUIDs in prompts) |
| G15 | Supersession inferred from dates for model conflicts | 1 | `test_a_potential_conflict_keeps_both_positions_and_declares_no_winner` |
| G16 | unsupported_request shows the model's claims | 1 | `test_unsupported_request_shows_no_claims` |
| G17 | Relevance floor removed (semantic neighbours reach the model) | 2 | no-evidence and evidence-only tests |
| G18 | No regeneration after rejected claims | 2 | regeneration tests |
| G19 | The trace stores the prompt | 1 | `test_the_trace_row_holds_references_not_content` |
| G20 | Trace answer not hidden after revocation | 1 | `test_trace_views` (revocation) |
| G21 | Administrators see other members' questions | 1 | `test_trace_views` (administrator view) |
| G22 | Any member reads another member's trace in full | 1 | `test_trace_views` (other member 404) |
| G23 | Secret references to any variable accepted | 7 | secret-reference tests (code and configuration) |
| G24 | Ollama remote endpoint declared local | 5 | endpoint trust-class and factory tests |
| G25 | Gemini key sent in the URL | 2 | Gemini request shape, key never logged |
| G26 | Credential secret logged | 1 | `test_a_credential_is_required_and_resolved_last` |
| G27 | Response invariant disabled and dangling citations kept | 1 | `test_a_forced_invariant_violation_withholds_the_answer` (added after the first run) |
| G28 | Classification of an unreadable document answered (egress input not re-authorized) | 1 | `test_classifications_are_answered_for_readable_documents_only` (added after the first run) |

---

### Phase 7 — Web app: Ask, evidence, inspector

> Historical plan. The interface described here (sidebar shell, citation chips, the v1
> palette and Geist) was replaced by the design overhaul before Phase 11; the current
> reference is [design-system.md](design-system.md) v2.

**Deliverables.** Next.js (App Router, TS strict) app; tokens.css; primitives;
Radix-based overlays; dev login screen (persona picker, dev only); shell + sidebar;
Ask page with composer, scope chips, progress stages (SSE — decision D-6), answer
block, potential-conflict/supersession/stale/egress/removed-claims notice components,
evidence list (incl. "Not used to draft this answer" group), inspector; all §B13
states with fixtures; generated API client. **Administrative provider settings (D-16,
ADR-011):** provider and model selection where appropriate, masked credential state,
connection test, enable/disable, the organization's egress levels and per-provider
approvals (each change with a stated reason, audited), and clear security/egress
communication, over the Phase 6 provider-configuration API.

**Tests.** Component tests for states; a citation chip cannot render for evidence
absent from the response (and its claim is not rendered); Playwright flows for Maya vs
Grace on the Acme question (different evidence; Grace sees the egress notice under the
default policy), inspector keyboard flow; axe accessibility checks; visual QA at 5
widths.

**Non-goals.** Accounts workspace, Find mode, dark theme.

**Phase 7 as built.** `apps/web`: Next.js 16 (App Router, TypeScript strict), React 19,
CSS Modules over `src/styles/tokens.css` (design-system Part B; Stylelint forbids raw
colours elsewhere), Radix primitives (Dialog, Popover, Tooltip), Lucide icons, Geist
self-hosted through `next/font`, TanStack Query for server state, `openapi-fetch` over
types generated by `openapi-typescript` from the API's OpenAPI schema
(`backend/scripts/export_openapi.py`, no database; `make api-contract`; CI fails on
drift). Routes: `/sign-in` (development persona picker; personas read on the server from
the seed manifests named by `LATTICE_DEV_PERSONA_MANIFESTS`), `/ask`, `/evidence/{id}`,
`/traces/{id}`, `/settings/providers`. `make web-setup | web | web-build | web-check |
web-e2e | api-contract`; a `web` CI job.

| Planned | As built | Why |
| ------- | -------- | --- |
| Progress stages over SSE (D-6) | Not built. One honest progress line (what Lattice is doing, elapsed seconds) over a skeleton with the answer's geometry | The API answers in one response; inventing stages would misreport the pipeline. D-6 stays open |
| Scope chips `Account`, `Sources`, `Date` | `Sources`, `Dates`, and `Sensitivity` (the D-13 per-question raise); an account scope is set only from a clarification choice the API returned | No endpoint lists the accounts or source types a member may see; the UI must not guess them |
| Provider settings with model selection, connection test, enable/disable, egress levels and approvals with reasons | Read-only view of `GET /v1/admin/providers` (policy, trust class, approval, enablement, masked credential state, data terms) and a statement that changes go through the audited CLI | No provider write API exists; adding one, or client-only controls, was out of scope for Phase 7 |
| Inspector context: previous and next chunk | The cited chunk only, with every verified quote of the answer marked | `GET /v1/evidence/{id}` returns one chunk |
| Activity (own questions) | In-tab session history (question, status, link to the trace), memory only | No trace list endpoint; Activity is Phase 8 |
| Playwright Maya vs Grace on the real Acme question | Browser tests against the production build with the API mocked from fixtures typed by the generated contract; the real API checked by hand (below) | CI has no generation model; fixtures make every state deterministic |
| Visual QA at five widths | A screenshot harness (`playwright --project=screens`) renders every screen and state at 1440/1280/1024/768/390 for review; CI asserts the layout invariants (no horizontal overflow, shared header rule, shared reading-column edges, touch targets, drawer and sheet geometry) | Pixel baselines differ across platforms' font rasterisers; geometry assertions do not |
| — | `/evidence/{id}` page (the inspector's "Copy link" target) | A shared link needs a page; its 404 is the inspector's |

**API contract observations** (documented; no backend change made):

1. `AskerTrace.answer`, `timings_ms`, `validation`, `usage` and `scope` are untyped
   objects in the OpenAPI schema. The web app reads them in one module
   (`src/lib/trace-model.ts`) and treats anything unexpected as absent. Typing them as
   response models would let the contract carry them.
2. When no model call is possible (no generation provider, an action request, no
   relevant evidence), evidence items still carry `not_used_reason: "egress_policy"`,
   although the model's own docstring says the reason is `None` "when no model was called
   for another reason (the status says why)". Seen against the running API
   (`evidence_only / generation_unavailable`). The UI shows per-item reasons only when a
   model call was in play; aligning the backend is a small follow-up.
3. No endpoint lists a member's visible accounts or readable source types (needed for
   an account picker and a first-run scope line, design-system §B13) or their past
   traces (Activity, Phase 8).

**Verification (2026-09-29).** `pnpm lint` (ESLint incl. jsx-a11y strict, Stylelint),
`pnpm typecheck`, 41 Vitest unit and component tests, `next build`, and 52 Playwright
tests (desktop 1440 plus a Pixel 7 phone project: every answer status, citation to
evidence, keyboard-only flows, focus trapping and return, not-found safety for evidence
and traces, a hidden trace answer never resurrected from memory, axe WCAG 2.2 AA audits
of every screen and state, responsive geometry at five widths) — all passed, twice in a
row. The screenshot harness rendered 23 screens × 5 widths (115 images) with no
horizontal overflow; each width was reviewed and the findings fixed. Against the real
API (dev database, fake embeddings, the development-only fake generation provider
recorded for Halden): development sign-in, the 401 redirect, `/v1/me`, Ask
(`answered`; Veridian's `evidence_only / generation_unavailable`), the evidence
endpoint, the asker and administrator trace views, provider settings and sign-out all
rendered from real responses. The backend was not modified apart from the OpenAPI
export script.

---

### Phase 8 — Operability: Sources, traces, docs (slice complete)

**Deliverables.** `jobs` table + worker loop for UI-triggered syncs; Sources page
(admins: aggregate status, counts, templated failures with opaque references,
permission mode, last ACL sync; members: only sources they can read, counts inside
their predicate); Activity (own questions; admin operational view without content);
trace views per `security-model.md` §8; operational-metadata side-channel suite
(`security-model.md` §10) green; README and runbook complete and verified by a
clean-clone run; design QA pass.

**Definition of done = slice DoD** (product brief §11 and the prompt's definition of
done): all security suites green; retrieval metrics at target; generation metrics
recorded; every §B13 state reachable; README verified by someone other than the
author (or a fresh machine).

**Phase 8 as built.** Scope: the Phase 8 deliverables above, plus the operational
readiness the user asked for within the existing architecture (no new infrastructure).

| Planned | As built | Why |
| ------- | -------- | --- |
| `jobs` table + worker loop for UI-triggered syncs | Migration 0011 `jobs` (one queued/running job per source, leases, bounded attempts, fixed error codes); `lattice worker` (`make worker`) claims with `FOR UPDATE SKIP LOCKED`, renews its lease, retries `source_busy` and unexpected errors with back-off, abandons after 3 attempts, deletes finished jobs after 30 days in batches. A new runtime role **`lattice_worker`** (ADR-013); a worker sync uses the source's recorded configuration and can update only its sync timestamps | `lattice_app` must not write content; the owner role must not run in a long-lived process. The next sync of a source records an interrupted run `aborted` |
| Sources page | `GET /v1/sources` (members: sources with ≥ 1 readable document, counts inside the predicate), `GET /v1/admin/sources` (aggregates, last run, failure codes, latest job), `GET /v1/admin/sources/{id}/failures` (identifier only if the admin can read the document, else `rec_…`; parameters allow-listed), `POST /v1/admin/sources/{id}/sync` (202 / 200 idempotent); `/sources` page | security-model §10 |
| Activity (own questions; admin operational view) | `GET /v1/activity`, `GET /v1/admin/activity` (keyset pages; operational fields only); `/activity` page; index `query_traces (organization_id, created_at DESC)` | D-5, security-model §8 |
| Trace views per security-model §8 | Unchanged from Phase 6 (already per §8); Activity links to them | — |
| Operational-metadata side-channel suite green | `tests/security/test_operational_metadata.py` (hand-built worlds) and `tests/security/operational/` (every persona of both organizations over the real corpus, expected values from the reference policy) | T20 |
| README and runbook complete, clean-clone verified | README status, setup, operations section; runbook §2 procedures made real, §7 operations, §8 failure drills | OPS-1 |
| Design QA pass | Screenshot harness extended with four screens, reviewed at five widths; two findings fixed (codes wrapping inside a word at 1024 px, opaque references at 390 px), and one from the real API (a configured but uncalled provider shown as "No model call") | DS-1 |
| — (operational readiness) | `/v1/health/live`, `/v1/health/ready` (database + schema revision); schema-revision guard at startup (API, worker; migration 0010 lets the runtime role read `alembic_version`); API refuses a role that can write content; 503 + `Retry-After` for database outages and cancelled statements instead of 500s; `security_invariant_violated` mapped (it was a generic 500); statement/connect timeouts and pool size settings; generation deadline per Ask (was 2 × 360 s); request-body cap (413); deployment-wide generation kill switch; Prometheus metrics on a separate listener with closed label sets; sync, job, Ask, generation, auth and dependency metrics | Requested for production readiness; each fits the existing modules |
| — (CI) | Node 24 action majors, `ubuntu-24.04` pinned, uv pinned, 30-minute job timeouts, separate uv cache per job, worker startup check in CI | GitHub deprecation warnings (both gone) |
| `org_settings.ask_enabled`, `trace_retention_days`, rate limits, alerting, container images, backups | **Not built**: the env kill switch covers the incident stop deployment-wide, `lattice provider disable` per organization; trace retention, rate limits (T19), alerting, images and PITR stay in Phases 10–11 | Phase boundaries (§4 "Later phases") |

**Final Phase 8 verification (2026-09-29).** Clean clone of `phase/8-operational-polish`
(69c7bf0) with no external API key in the environment, in an isolated Compose project on
its own port (development database stopped; one PostgreSQL at a time): `make setup`,
`db-up` (bootstrap with all four roles), `migrate` (0001-0011), `seed`, `check-db-role`
(including the new content-write check), `lattice worker --once` (worker startup checks),
`dataset-lint`, `sync` twice (the second wrote nothing), `eval` (fake; R@10 0.512,
unchanged; every security count 0), `eval-answer` (fake; every security count 0),
`lint`, `typecheck` (mypy strict, 228 files), **1,788 backend tests**; `web-setup`,
`web-check` (ESLint, Stylelint, TypeScript, **48 unit and component tests**),
`web-build`, `web-e2e` (**68 Playwright tests**, desktop and phone, no retries needed).
GitHub CI green on the branch (backend and web) with no annotations. Secret scan of
tracked files and of every commit on the branch: no key patterns, and the real `.env`
key value appears nowhere. Live drills against the running API and worker: runbook §8.
Mutation check: not repeated for Phase 8 (no retrieval, authorization or answering logic
changed); the new controls are pinned by the privilege-matrix and side-channel tests.

**Slice definition of done, as it stands.** Security suites green (including the new
side-channel suite); every §B13 state reachable. **Not met:** retrieval metrics at
target (R@10 ≥ 0.90; best real 0.726, evaluation-plan §12) and generation metrics
recorded with a real model (not run, ADR-012). The slice is operable; its quality
objectives remain open.


---

### Phase 9 — Access management and audit events

The first phase after the slice (§4 "Later phases" row 9), plus the gap Phase 7 left
for it: the Ask account picker and first-run scope line (Phase 7 contract observation
3, design-system Part C).

**Deliverables.**
1. *Member scope (Ask).* `GET /v1/accounts`: the accounts the member may know exist
   (`authz.visible_accounts_predicate`, the same set a scope may name) and their scope
   kind. Ask gets the `Account` chip (only those accounts), the `Sources` chip narrowed
   to source types with readable documents (`GET /v1/sources`), and the first-run scope
   line ("You can search 3 sources · Acme Corp, Northpeak Logistics"); with no readable
   source the composer is disabled (B13 "Empty knowledge base").
2. *Access page (administrators).* Member directory (keyset pages, name/e-mail
   filter); a member's access as plain sentences built from typed data (clearance,
   account scope and assignments, departments, groups, explicit grants); a
   source-synced (private) group exists for the administrator only if they are a member
   of it, otherwise the member's access only acknowledges "private channels you aren't
   in" (closure, below); grants on documents the administrator cannot read
   acknowledged only as a yes/no; "check a document" (`authz.explain_for_reviewer`) over a search of
   documents the administrator can read.
3. *Changes with previews.* Clearance, account scope, account assignments,
   departments, membership of administrator-defined groups, suspension/reactivation,
   and administrator grants of one readable document to one member (and their
   revocation). Every change: a preview first (effects as rules; document counts only
   over documents the administrator can read), a stated reason, optimistic concurrency
   (409 on a stale view), one transaction with its audit event. An administrator cannot
   change their own access through the API. Source-synced groups and source grants stay
   owned by the source (read-only).
4. *Full `audit_events`.* `access.member_changed`, `access.grant_created`,
   `access.grant_revoked`, written by the API, the seed (`operator:seed`) and
   `lattice apply-admin-grants` (`operator:admin-grants`), next to the Phase 6 provider
   events. `GET /v1/admin/audit-events` (keyset pages, newest first, no totals); targets
   re-authorized for the viewing administrator at read time.
5. *Database.* Migration 0012: the API role gets exactly the writes above (memberships'
   status/clearance/scope columns, assignments, departments, admin-group members,
   admin-origin membership grants, audit inserts), each limited by restrictive RLS
   policies; `is_admin` stays unwritable by the API; an audit index.

**Tests.** Persona sweep: `/v1/accounts` equals the reference `account_visible` for every
persona of both organizations; scope with a hidden account refused like an unknown one.
Access API: 403 for non-administrators whether or not the target exists; another
organization's members, grants and events are 404 and never listed; previews count only
administrator-readable documents (checked against the reference policy); unreadable
granted documents and unnamed private channels never leak a title, name or count;
every change writes exactly one audit event in the same transaction (and none when it
fails); self-changes refused; stale versions refused; the database refuses source
grants, source-group edits, `is_admin` and `removed` from the API role. Web: typed
client, every state (loading, empty, error, 403, 404, 409, revoked capability),
Playwright desktop and phone, axe.

**Non-goals (later phases).** Creating, renaming or deleting groups, departments or
accounts; changing `is_admin`; removing members or provisioning (SCIM, Phase 10 with
OIDC); grants to departments or groups from the UI; time-bound grants; an end-user
"why can I see this?" UI; the Accounts workspace (Phase 12); rate limits (Phase 10);
audit retention and alerting (Phase 11).

**Phase 9 as built.** Scope exactly as planned above; nothing from Phases 10–11 pulled
forward.

| Planned | As built | Why |
| ------- | -------- | --- |
| Member scope for Ask | `GET /v1/accounts` (`{account_scope, accounts: [{key, name}]}`, exactly `visible_accounts_predicate`); Ask's `Account` chip lists it, `Sources` offers only source types with a readable source, the intro shows "You can search N sources · …", the composer is disabled when nothing readable is indexed | Phase 7 contract observation 3, B13 |
| Access page | `GET /v1/admin/access/members` (keyset by name, `q` filter, LIKE wildcards literal), `/directory` (accounts, departments, administrator-defined groups), `/members/{id}` (summary, version, groups with the §10 naming rule, grants on readable documents, `has_other_grants`), `/documents?q=` (documents the caller can read), `/members/{id}/documents/{doc}` (`explain_for_reviewer`); `/access`, `/access/members/{id}`, `/access/audit` pages | security-model §10 |
| Changes with previews | `POST …/members/{id}/preview` (effects as rules; `documents_gained/lost` in one statement with the caller's predicate), `PATCH …/members/{id}` (version, reason; 409 stale, 403 own access, 422 `no_change` / `unknown` / `source_group` / `member_removed`), `POST …/members/{id}/grants` (201, 200 if it exists), `POST …/grants/{id}/revoke` (204; 422 `source_grant`). New error code `conflict` (409) | D-17, D-18 |
| Full `audit_events` | `access.member_changed`, `access.grant_created`, `access.grant_revoked` from the API, the seed (`operator:seed`, provisioning and changes) and `apply-admin-grants` (`operator:admin-grants`); snapshots include the administrator flag; empty state is SQL NULL; `GET /v1/admin/audit-events` (keyset, no totals, documents named only while readable) | T3; D-19 |
| Database | Migration 0012 (ADR-014): the API role's fenced writes; audit index. `authz` gains `resolve_member_context` and `proposed_context` in a new, unfingerprinted module: `POLICY_VERSION` stays `authz-2` | Least privilege in the database, not only in code |
| `document_grants.granted_by` | Not built: the grant's audit event is the record (data-model §5) | It must outlive the grant and the granting membership |
| — | Found and fixed during verification: duplicate fields and Python booleans in settings audit details; Radix returned focus only to its own trigger (dialogs now restore focus to their opener); the reason field's accessible name included its help text; a "can't read" explanation listed passing clauses too | Live API check, Playwright, component tests |
| — (review closure, 2026-10-02) | Private groups an administrator isn't in are no longer returned anywhere (filtered in SQL: no name, member list, count or reference; `has_hidden_groups`; explanations say "a private channel you aren't in"); any non-administrator-defined group ID is refused as `unknown` (was `source_group`, which told a private group's ID from a missing one); the audit view omits events about documents the viewer can't read (`document_reference` removed from the contract). `tests/security/test_access_private_objects.py` | D-18 revised: existence, timing and count side channels |
| Not built (later phases) | Group/department/account creation, `is_admin` changes, member removal and provisioning (Phase 10 with OIDC/SCIM), department or group grants from the UI, time-bound grants, trigger-enforced audit (Phase 10 review candidate, ADR-014), audit retention and alerting (Phase 11) | Phase boundaries |

**Final Phase 9 verification (2026-10-02).** Clean clone of
`phase/9-access-management` (d5275e8) with no external API key in the environment, in an
isolated Compose project (`lattice-clean9`) on its own port with the development
database stopped: `make setup`, `db-up`, `migrate` (0001–0012), `seed`,
`check-db-role`, `lattice worker --once`, `dataset-lint`, `sync` twice (the second wrote
nothing), `eval` (fake; R@10 0.512, unchanged; every security count 0), `eval-answer`
(fake; every security count 0), `lint`, `typecheck` (mypy strict, 246 files),
**1,884 backend tests**; `web-setup`, `web-check` (ESLint, Stylelint, TypeScript,
**69 unit and component tests**), `web-build`, `web-e2e` (**84 Playwright tests**,
desktop and phone, axe audits of every Access screen, five-width overflow checks).
Secret scan of every commit on the branch: no key patterns; the real `.env` key appears
nowhere. Against the real API (development database migrated to 0012, as Lena): member
list, Maya's page (grants she holds on documents Lena can't read acknowledged only;
the private channel then shown by `rec_…`, since withdrawn by the review closure), change preview (0 / 0 counted: Lena reads no
Kestrel content) → apply → revert, grant → explanation → revoke, audit log; as Maya:
the scope line, the account picker, a scoped Ask, and the administrator-only page with
no admin request made. Those verification changes remain in the development database's
audit log; Maya's access ended as the manifest defines it. Mutation check: not run (the
policy is unchanged, `authz-2`); the new controls are pinned by the privilege, persona
and side-channel tests.

---

### Phase 10 — OIDC SSO, provisioning and hardening

§4 "Later phases" row 10 (OIDC (D-1), rate limits, security headers review, dependency
audit, threat-model review, pen-test style review of isolation), plus what Phase 9
assigned here: member provisioning and removal (SCIM, "Phase 10 with OIDC"), revisiting
D-19 (which attributes the identity provider owns) and the ADR-014 residual risk
(trigger-enforced audit is a "Phase 10 review candidate").

**Deliverables.**
1. *OIDC sign-in (D-1, ADR-015).* One OIDC connection per organization
   (`oidc_connections`, managed by the operator command `lattice sso`, audited):
   issuer, client ID, an optional client-secret *reference* (`env:` scheme only, as
   provider credentials), the claim that identifies a person (`sub` by default) and an
   opaque, unguessable login key. Authorization-code flow with PKCE (S256), `state`,
   `nonce` and a browser-binding cookie; the attempt is stored server-side, single use,
   ten minutes. Discovery and JWKS fetched with bounded time and size; the discovery
   document's issuer must equal the configured one; ID token signature (asymmetric
   algorithms only, never `none` or HMAC), `iss`, `aud`/`azp`, `exp`/`iat`, `nonce`
   checked. **Identity mapping is deterministic and organization-scoped:** (connection,
   claim value) → a provisioned directory identity of *that* organization → its
   membership. No just-in-time creation, no linking by e-mail, no lookup in any other
   organization; anything else (unknown, suspended, removed, disabled connection,
   suspended organization) fails with one generic result. Sign-in creates a normal
   server-side session (ADR-005): revocation, idle and absolute timeouts unchanged.
2. *SCIM 2.0 provisioning (D-20).* `/scim/v2` with a per-organization bearer token
   (`scim_tokens`: SHA-256 stored, shown once, revocable; `lattice scim token`).
   `Users` only: create, read, list with `userName eq` / `externalId eq` filters,
   replace, patch, delete; `ServiceProviderConfig`, `ResourceTypes`, `Schemas`.
   `externalId` is required and immutable: it is the value the OIDC claim must carry.
   The identity provider owns `userName`/e-mail, the name and `active`; Lattice owns all
   access attributes. A provisioned person is a user row *owned by the organization*
   (`users.home_organization_id`), never an existing user of another organization, so
   provisioning cannot read, link or modify anything outside the tenant and e-mail
   uniqueness is per organization for such users. New members get the least access the
   policy models (clearance `internal`, account scope `assigned` with no accounts, no
   departments, groups or grants, not an administrator). `active=false` → `suspended`,
   `active=true` → `active`, `DELETE` → `removed` (terminal); each revokes or restores
   on the next request and suspension/removal revokes the member's sessions in the same
   transaction. Create, replace, patch and delete are idempotent (an identical request
   writes nothing and records nothing); `meta.version` / `If-Match` (412).
   Every change is audited in its transaction (`access.member_changed`,
   `access.identity_changed`, actor `scim:<token id>`). The Access page shows
   provisioned members as managed by the identity provider and refuses lifecycle
   changes for them (`managed_by_identity_provider`); access attributes stay editable.
3. *Rate limits and resource protection (T19).* Server-side fixed-window limits:
   per-membership Asks per minute and per day and concurrent Asks, per-administrator
   access writes per minute, per-SCIM-token requests per minute (counters in PostgreSQL
   under row-level security, atomic upserts, one row per subject and bucket), and
   per-client limits on the pre-authentication endpoints (development sign-in, OIDC
   start and callback, failed SCIM authentication) in process memory with keyed-hash
   keys and a bounded table. 429 `rate_limited` with `Retry-After` (browser sign-in
   redirects to the sign-in page with a fixed error instead). Checked after
   authentication and the capability check and before anything that depends on a
   target, so a 429 reveals nothing about documents, accounts or other organizations.
   No exemption for administrators or any role; limits cannot be configured to zero.
   Metric labels are the bucket name only.
4. *Access-audit enforcement in the database (ADR-014 residual).* Deferred constraint
   triggers refuse to commit an access write by the API role unless the same
   transaction recorded an `access.*` audit event for that membership.
5. *Hardening reviews.* Security headers (API and web: a nonce-based script policy
   instead of `'unsafe-inline'`, cross-origin isolation headers, HSTS on the web origin
   in production); dependency audit (`make audit`, a CI job); operator command
   `lattice member admin` (the only way to change `is_admin`, audited, reason
   required) so an SSO organization can get its first administrator; threat-model
   review (new T22–T25, T3/T15/T19 revised) and a pen-test style isolation review backed
   by a route inventory test: every route classified, every non-public route refuses an
   anonymous caller, every administrator route refuses a member, and every route that
   takes an identifier answers another organization's identifier exactly as a missing
   one.
6. *Web.* Sign-in: SSO entry (`/sso/<key>`), fixed error states, no list of
   organizations or connections; 429 handling (Ask and generic); Access: "Managed by
   your identity provider", lifecycle controls withheld for such members, SCIM actors
   and identity changes in the audit view. Typed client regenerated.
7. *Database.* Migration 0013: the tables above; `users.home_organization_id` and
   per-organization e-mail uniqueness for organization-owned users; the API role's new
   writes, each fenced by restrictive policies; two pre-authentication lookups owned by
   `lattice_auth` (connection by login key, SCIM token by hash); `audit_events.txid` and
   the audit triggers.

**Tests.** OIDC against an in-process identity provider (real RSA/EC keys, real JWS):
happy path; every ID-token defect (issuer, audience, `azp`, expiry, future `iat`,
nonce, signature, unknown key, `alg: none`, HMAC with the public key, wrong claim type);
discovery issuer mismatch; replayed and expired `state`; missing or foreign
browser-binding cookie; attempt of another connection; unprovisioned, suspended and
removed members; disabled connection and suspended organization; the same external ID
provisioned in two organizations signs in only to the connection's organization; open
redirect refused. SCIM: contract (schemas, errors, filters, paging), idempotency of
every operation, `If-Match`, suspension/removal immediately ending sessions,
retrieval, traces and Access views, the least-access default, attribute ownership (no
access attribute writable), another organization's identifiers 404, a user of another
organization with the same e-mail neither found, linked nor revealed, token
revocation, the database refusing over-privileged inserts. Rate limits: each bucket,
`Retry-After`, concurrency (parallel requests never exceed the limit), no bypass by
role, 429 identical for existing and missing targets, organizations independent,
metric labels closed. Audit triggers: a write without an event, with an event for
another membership, or with an event of an earlier transaction is refused; owner and
worker unaffected. Route inventory and isolation sweep. Web: unit, Playwright desktop
and phone, axe.

**Non-goals (later phases).** SAML; SCIM `Groups` (groups and departments stay
administrator-defined); just-in-time provisioning; identity-provider-initiated logout
and back-channel logout; multiple connections per organization; organization
creation and self-service SSO configuration UI; time-bound grants; retention jobs,
alerting, backups, container images and deployment (Phase 11); the Accounts workspace
(Phase 12).

**Phase 10 as built.** Scope as planned above; nothing from Phase 11 pulled forward.

| Planned | As built | Why |
| ------- | -------- | --- |
| OIDC sign-in (D-1) | `lattice.identity.oidc`; `GET /v1/auth/oidc/{login_key}/start` and `/callback` (303 redirects; failures to `/sign-in?error=sso_failed|sso_unavailable|rate_limited`); attempts in `oidc_login_attempts` (hashes, ten minutes, consumed first, bound to the connection and the browser); asymmetric allow-list matched to the key type; discovery issuer check; JWKS cached ten minutes, refresh at most once a minute; bounded HTTP; client secrets by `env:LATTICE_OIDC_SECRET_*` reference; PyJWT[crypto] added | ADR-015; D-1 accepted: generic OIDC |
| Organization-scoped identity mapping | `directory_identities (connection, external_id) -> membership`, tenant table; sign-in matches the configured claim there only; no e-mail linking, no just-in-time membership | Global `external_identities` would let one organization's provider attach to another's person (Phase 10 review) |
| SCIM 2.0 provisioning (D-20) | `/scim/v2` `Users` (create, read, `userName`/`externalId eq` filters with paging, replace, patch incl. path-less and string booleans, delete) + `ServiceProviderConfig`, `ResourceTypes`, `Schemas`; bearer tokens (`scim_tokens`, hashed); organization-owned users (`users.home_organization_id`, per-organization e-mail uniqueness, a trigger keeps them out of other organizations); least-access memberships via column privileges; suspension/removal revoke sessions in the transaction; idempotent; `If-Match`; audited `access.member_changed` / `access.identity_changed` (actor `scim:<token>`); not in OpenAPI (SCIM discovers itself) | D-20; the provider owns identity and lifecycle, Lattice owns access |
| Access page and provisioned members | `managed_by: "identity_provider"` on member summaries; lifecycle changes refused (`422 managed_by_identity_provider`) in preview and change; access attributes stay editable | D-19 revisited |
| Rate limits and resource protection | `lattice.ratelimit`: PostgreSQL fixed windows per membership (Asks per minute/day), per administrator (access previews and writes), per SCIM token; in-process Asks in flight per membership; in-process per-client limits for development sign-in, OIDC start/callback and failed SCIM authentication (a client over the limit is refused before its token is checked). 429 `rate_limited` + `Retry-After`; `lattice_rate_limited_total{bucket}`; settings ≥ 1 | T19, T24; D-21 |
| Access audit in the database | `audit_events.txid` + deferred constraint triggers on the six access tables: an access write by `lattice_app` cannot commit without an `access.*` event of the same transaction for that membership | ADR-014 residual risk found real once SCIM became a second writer |
| Operator controls | `lattice sso add/show/disable`, `lattice scim token create/list/revoke`, `lattice member admin --grant/--revoke` (the only path to `is_admin`), each audited with a reason; no secret or reference in events | First administrator of an SSO organization; T25 |
| Security headers review | API: COOP, CORP, Permissions-Policy. Web: per-request nonce CSP in `src/proxy.ts` (`'strict-dynamic'`, no `'unsafe-inline'` scripts, `object-src 'none'`), root layout renders per request, COOP, Permissions-Policy, HSTS in production | The web app's `script-src 'unsafe-inline'` weakened T15 |
| Dependency audit | `make audit` (pip-audit 2.9.0 over the locked set with hashes; `pnpm audit`) and a `dependency-audit` CI job; no known vulnerabilities on 2026-10-02 | — |
| Threat-model review, pen-test style isolation review | threat model T3/T15/T16/T19 revised, T22–T25 added, findings recorded; `tests/security/test_route_inventory.py` classifies every served route and sweeps anonymous, member, SCIM and cross-organization access | — |
| Web | Sign-in SSO copy and fixed errors, `/sso/[key]` hand-over, 429 states (Ask, access changes), provisioned-member tag and note, SCIM actors in the audit log; typed client regenerated | Real APIs only |
| Database | Migration 0013 (round-trips; models match) | — |
| — (found on the way) | httpx logged outbound URLs at INFO (now WARNING); `SELECT … FOR UPDATE` cannot see a removed membership (the 0013 policy) so idempotent SCIM delete re-reads without the lock; the request reference on tinted callouts failed contrast (fixed); FastAPI now includes routers lazily (the inventory test walks them) | Tests, axe |
| Not built (later phases) | SAML; SCIM `Groups`; just-in-time provisioning; IdP-initiated and back-channel logout; several connections per organization; organization creation and a self-service SSO UI; alerting on provisioning bursts, retention, backups, images (Phase 11) | Phase boundaries |

**Final Phase 10 verification (2026-10-02).** Clean clone of
`phase/10-oidc-sso-hardening` (3a954bb) from GitHub with no external API key in the
environment, in an isolated Compose project (`lattice-clean10`, port 5440) with the
development database stopped: `make setup`, `db-up`, `migrate` (0001–0013), `seed`,
`check-db-role`, `lattice worker --once`, `dataset-lint`, `sync` twice (the second wrote
nothing), `eval` (fake; R@10 0.512, unchanged; every security count 0), `eval-answer`
(fake; every security count 0), `lint`, `typecheck` (mypy strict, 269 files),
**2,173 backend tests**; `web-setup`, `web-check` (ESLint, Stylelint, TypeScript,
**80 unit and component tests**), `web-build` (every route dynamic, the CSP proxy
active), `web-e2e` (**102 Playwright tests**, desktop and phone, axe), `make audit`
(no known vulnerabilities). Live drills against a real API process on that database:
security headers; Maya's eleventh Ask in a minute → 429 with `Retry-After`, identical
for a scope naming a hidden account; `lattice sso add` and `lattice scim token create`;
SCIM bad token 401, create 201 (with `roles: admin` in the request, the member has least
access and no capability), duplicate 409, filter, deactivate twice (one event), Lena's
Access view (suspended, managed by the identity provider), her reactivation refused
(`managed_by_identity_provider`) while a clearance change succeeds, the audit log
(provider, SCIM and operator events), OIDC start against an unreachable provider →
`sso_unavailable`, an unknown key → `sso_failed`, delete twice (204, 204) then 404,
`lattice member admin --grant` for a provisioned person, token revocation → 401,
`lattice sso disable`; the clean project was then removed. **Mutation check (by hand,
not committed):** seven defects — nonce check removed, browser binding not compared,
sign-in mapping without the connection filter, SCIM queries without it, the limiter
counting past its limit, the audit trigger never checking, the limit taken before the
capability check. Two survived at first (the connection filters: row-level security
still confined them to the organization); a90f505 adds the tests that catch them, and
all seven are caught. GitHub CI green (backend, dependency-audit, web; no annotations).
Secret scan of every commit on the branch: no key patterns; the real `.env` key appears
nowhere. The development database was migrated to 0013 (no data change).

---

### Phase 11 — Production Deployment, Identity Operations & Reliability

§4 "Later phases" row 11. The approved plan is [phase-11-plan.md](phase-11-plan.md)
(2026-10-03); the provider-independent contract is [deployment.md](deployment.md) and the
topology decision [ADR-016](decisions/016-production-topology.md). Decisions D-22–D-27 (§7).

**Objective.** Prove that what Phases 1–10 built operates correctly as a production
system: immutable images (one backend image for API, worker, migrations and operator
commands; one web image), the ingress routing `/api/*` straight to the API, shared limits
in PostgreSQL, safe migrations, backups with **tested** restore and PITR, enforced
retention, actionable alerts, audited operator procedures, and drills — on a local
production-shaped stack with two API instances. **D-22 amended 2026-10-05: zero paid
infrastructure** — real external staging validation is deferred (list in
[phase-11-plan](phase-11-plan.md) §21.1) and is a release gate for Phase 15. No Redis, Kubernetes, broker, pooler, secrets product or new observability
platform; no Phase 12–15 scope.

**Checkpoints** (plan §25; P11.2–P11.7 authorized 2026-10-05 to run in sequence):

| Step | Scope | Status |
| ---- | ----- | ------ |
| P11.0 | Decisions, deployment contract, staging-provider comparison, ADR-016, documentation corrections | Approved (4990064) |
| P11.1 | Packaging and runtime: images, web standalone on `0.0.0.0`, ingress routing and single trusted hop, uvicorn production command, worker heartbeat, build info, local reference stack (api ×2, Caddy), `lattice smoke` | Approved (b07ac8d) |
| P11.2 | Deployment and migrations: `lattice migrate` (lock, connection fence, pre-flight, exact verification), migration headers, managed-PostgreSQL bootstrap (verified locally against a CREATEROLE/CREATEDB administrator), privilege snapshot and `check-deployment`, `check-config`, GHCR `images` job, `deploy/deploy.sh` releases and rollbacks, two web instances (staging deferred, D-22 amended) | Delivered (ae03445 + fd0d1bc; plan §25.3) |
| P11.3 | Identity and session operations: sessions record their SSO connection, disable revokes them, `sso enable/update`, audited `member suspend/reinstate`, `org suspend/reactivate`, `session revoke`, clock skew, verification IdP, the identity test matrix (plan §15.3) | Delivered (1c68ed5; plan §25.4; 42/42 on the stack) |
| P11.4 | Shared in-flight and pre-authentication limits in PostgreSQL; retention classes with database-enforced floors | Delivered (9a50e27; plan §25.5) |
| P11.5 | Backups and restore verified locally with measured local durations (managed PITR and cloud RPO/RTO deferred, D-22 amended) | Delivered (0b7fe76; plan §25.6) |
| P11.6 | Metrics, alert rules with tests, a runbook action per alert | Delivered (f6025fe; plan §25.7) |
| P11.7 | Deployed authorization suite and failure drills on the local stack, closure (staging deferred) | Delivered (commit "P11.7: deployed authorization suite, failure drills and Phase 11 closure"; plan §25.8, acceptance §26.1) |

**Roadmap disposition recorded with Phase 11** (§4 table): scoped Ask already shipped
(Phase 9); account administration → Phase 12; account timeline workspace → post-v1 unless
deliberately pulled forward; next connectors → evaluated in Phase 12; retrieval and answer
quality → Phase 13; enterprise hardening and scale → Phase 14; GA/v1 closure → Phase 15.

---

### Phase 12 — Organization Setup, Identity Configuration & Integrations

§4 "Later phases" row 12. Plan: [phase-12-plan.md](phase-12-plan.md) (approved
2026-10-06; Part II holds the P12.0 contracts); mutation boundary
[ADR-017](decisions/017-setup-mutation-trust-boundary.md). Decisions D-28–D-37 (§7).

**Objective.** Make the accepted systems operable by an organization administrator:
after an operator creates the tenant trust root (`lattice org create`), identity,
members, administrators, accounts and mappings, providers and egress, export-based
sources and the first sync are configured in the product — no manifests, owner SQL or
undocumented CLI for the supported path. No new connector, no live integration, no paid
service; real external staging stays a Phase 15 gate.

| Step | Scope | Status |
| ---- | ----- | ------ |
| P12.0 | Decisions and contracts (plan Part II, ADR-017, ADR-011 amendment) | Delivered (docs only) |
| P12.1 | Foundations: ADR-017 fences, encrypted secret store, outbound policy, `org create`, setup status, Organization page | Delivered (local; phase-12-plan Part III) |
| P12.2 | Members, administrators, reinstatement | Delivered (local; phase-12-plan Part III) |
| P12.3 | Accounts and `account_external_refs` | Delivered (local; phase-12-plan Part III) |
| P12.4 | OIDC and SCIM setup | Delivered (local; phase-12-plan Part III) |
| P12.5 | Providers, credentials, egress with step-up | Delivered (local; phase-12-plan Part III) |
| P12.6 | Export-based sources, imports, first sync | Delivered (local; phase-12-plan Part III) |
| P12.7 | End-to-end onboarding journey | Delivered (local; `make prod-onboard`, docs/onboarding.md, phase-12-plan Part III) |
| P12.8 | Security, accessibility, closure | Delivered (local, awaiting review; migration 0024, expanded `make prod-authz`, deferred-capability register; phase-12-plan Part III) |

---

### Later phases (order to be revisited after the slice)

| Phase | Theme | Notes |
| ----- | ----- | ----- |
| 9 | Access management UX + audit events | **Built and merged** (8d3af3b; Phase 9 section above). Explanations, change previews (counts only over admin-readable documents), admin grants, full `audit_events` (the provider and AI egress settings UI is Phase 7, D-16) |
| 10 | OIDC SSO + hardening | **Built, verified and approved** (Phase 10 section above; decisions D-1, D-19 revised, D-20 and D-21 accepted 2026-10-02): OIDC (D-1), SCIM provisioning (D-20), rate limits (D-21), database-enforced access audit, security headers review, dependency audit, threat-model review, pen-test style review of isolation |
| — | Product-design overhaul (frontend only) | **Closed and merged** (27da421, 2026-10-03): D0–D7 complete and approved; `design-direction.md` is the historical record, `design-system.md` v2 is canonical. Inserted before Phase 11 |
| 11 | Production Deployment, Identity Operations & Reliability | **Closed and merged (143392f, 2026-10-06): built and verified on the local reference stack (P11.0–P11.7); external staging validation deferred to the Phase 15 gate.** **Plan approved 2026-10-03** ([phase-11-plan.md](phase-11-plan.md); decisions D-22–D-27; checkpoints P11.0–P11.7). Production deployment (images, single region, migrations in CI/CD, rollback), production identity and session operations, shared limits, jobs and retention, backups/PITR with tested restore, observability and actionable alerts (including `security_invariant_violated`), production runbooks and drills |
| 12 | Organization Setup, Identity Configuration & Integrations | **Plan approved and P12.0 contracts written (2026-10-06; [phase-12-plan.md](phase-12-plan.md)); P12.1–P12.7 delivered (2026-10-08 … 2026-10-19); P12.8 closure delivered (2026-10-20), awaiting review and the merge decision.** Customer-facing organization onboarding, SSO and SCIM setup, source/integration and provider (BYOK) setup, zero-to-first-question. Also: **account administration** (creating accounts and mapping them to source account references, today manifest-only — from the former "Accounts workspace"), **evaluation of the next connectors** (call transcripts, CRM notes, GitHub issues, release notes) within integration setup, ~~organization deletion/purge (D-26)~~ → Phase 15 (D-37A), ~~identity-provider migration with differing subjects~~ → post-v1 (D-37B) |
| 13 | Retrieval and answer quality | Retrieval and answer-quality improvement |
| 14 | Enterprise-scale hardening | Large-scale, load and adversarial hardening |
| 15 | GA / v1 closure | Final general-availability closure. **Release gate (D-22 amended 2026-10-05):** real external staging validation (phase-11-plan §21.1: cloud staging deployment, managed-PostgreSQL privileges, managed backup/PITR, public TLS/origin, external OIDC callback, provider-specific deploy/rollback, cloud RPO/RTO) must be completed before v1 GA / production-ready is declared. Also: executable **organization purge** with a platform deletion record (D-37A; semantics in phase-12-plan C14) |
| — | Former rows 12 (Accounts workspace) and 13 (Next connectors), placed 2026-10-03 (phase-11-plan §27) | *Accounts workspace* meant an account timeline + scoped Ask for account managers. Scoped Ask shipped in Phase 9; account administration → Phase 12 (above); the **account timeline workspace → post-v1, intentionally deferred**: the v1 workflow (product brief §5, §8) is Ask → cited answer → evidence, which account managers already have through scoped Ask, and Phase 0 recorded the timeline as not needed for the first release — the product owner may pull it into Phases 13–15. *Next connectors* → evaluated in Phase 12's integration scope; those not needed for v1 are recorded there as post-v1 |
| — | Only if evals demand | HNSW tuning, model reranker (a local cross-encoder was measured and rejected in Phase 5, evaluation-plan §11), query rewriting, simple-config tsvector |

## 5. Local development

Phase 1 (verified; the README is the authoritative walkthrough). Prereqs: Docker with
Compose v2, `uv`, `make`. Python 3.12 is installed by `uv`.
```
cp .env.example .env
make setup          # uv sync --locked
make db-up          # postgres 16 + pgvector; first start runs the bootstrap
make migrate        # alembic upgrade head (lattice_owner)
make seed           # Halden + Veridian manifests (idempotent)
make check-db-role  # runtime role cannot bypass RLS
make api            # uvicorn on 127.0.0.1:8000
make sync-dry-run   # what ingestion would do (Phase 4)
make sync           # ingest both organizations, fake embeddings; then admin grants
make test           # 1,300+ tests against the lattice_test database (incl. 500-example authz proof)
make lint typecheck
make dataset-lint   # the synthetic dataset and golden set (no database, no model)
make eval           # retrieval metrics + security checks as each persona (Phase 5)
```
Phase 7 added the web app (Node 22+, pnpm): `make web-setup`, `make web` (on :3000,
proxying `/api` to `make api`), `make web-build`, `make web-check`, `make web-e2e`,
`make api-contract`. Phase 8 added `make worker` (requested syncs, as `lattice_worker`;
a database created earlier needs `make db-up db-bootstrap migrate` once). Phase 9 needs
`make migrate` (0012) on an existing database; Phase 10 needs `make migrate` (0013) and
adds `make audit` (network: vulnerability databases).

## 6. Phase 1 traceability check

| Phase 1 task | Traces to | Keep? |
| ------------ | --------- | ----- |
| 1 git, ignore files, README skeleton | OPS-1 | Yes |
| 2 compose + Makefile | OPS-1, ARCH-2 | Yes (compose for Postgres only) |
| 3 uv, ruff, mypy strict, pytest | ARCH-1, OPS-1 | Yes |
| 4 config fail-fast + env guards | OPS-2, SEC-11 | Yes |
| 5 error taxonomy + envelope | ARCH-1, SEC-5 (404 semantics) | Yes |
| 6 structured, redaction-safe logging | OPS-3, SEC-8 | Yes — cheaper to start correct than retrofit |
| 7 db binding + Alembic + roles | ARCH-2, SEC-2 | Yes |
| 8 identity/tenancy tables with RLS | SEC-2, ARCH-2 | Yes |
| 9 dev identity + sessions + CSRF | SEC-11, PR-1 (need a user) | Yes |
| 10 health, dev-login, logout, me | PR-1, ARCH-4 | Yes |
| 11 seed CLI for org/users | OPS-1, EVAL-1 (personas) | Yes, minimal |
| 12 CI workflow | OPS-1, SEC-2 (tests must run) | Yes |
| *Re-verified after the post-review corrections (§9):* no Phase 1 task changes. Tasks 5–6 already implement the principles the corrections rely on (typed error codes, no free-text content in logs/errors — SEC-8, SEC-14). SEC-10, SEC-13, SEC-15 first apply in Phases 4, 6 and 8 | — | — |
| *Considered and removed:* web app scaffold | DS-1 — but nothing real to render until Phase 6 | **Moved to Phase 7** |
| *Considered and removed:* OIDC | SEC-11 — not needed until deployment | **Moved to Phase 10** |
| *Considered and removed:* jobs table/worker | PR-5 — CLI suffices until UI triggers syncs | **Moved to Phase 8** |
| *Considered and removed:* OpenTelemetry tracing | OPS-3 — query traces are domain data in Postgres; OTel adds infra without a consumer | **Deferred** |

## 7. Open decisions

| ID | Decision | Needed by | Recommendation | Owner |
| -- | -------- | --------- | -------------- | ----- |
| D-1 | Identity provider for first real deployment (generic OIDC; which IdP to test against) | Phase 10 | **Decided and accepted (Phase 10; accepted by the product owner 2026-10-02):** generic OIDC (authorization code + PKCE), one connection per organization, tested against an in-process provider with real keys; no vendor broker. Which IdP the first pilot uses is still the product owner's to confirm (Okta, Entra ID — claim `oid` — Keycloak and Google Workspace are covered by configuration, runbook §9) | Product owner — accepted |
| D-2 | Embedding provider: external API vs private/self-hosted | Phase 4 | **Decided (ADR-008):** OpenAI `text-embedding-3-small` (1024 dimensions), external API with no-training terms, approved for `internal` only (per D-3). Confidential/restricted content is lexical-only unless a private/self-hosted endpoint is approved later. The external provider receives all *internal* chunk text | Product owner — decided |
| D-3 | AI egress default for external providers (generation and embedding) | Phase 4 (embedding), Phase 6 (generation) | **Decided:** `internal` may be sent to an approved external provider; `confidential` and `restricted` are not sent unless the org explicitly opts in (audited) for a provider approved for that level. Egress is a data policy separate from authorization | Product owner — decided |
| D-4 | Default generation provider/model | Phase 6 | Anthropic Messages API with a current Claude model, chosen by eval quality/latency/cost; approved for `internal` only by default (D-3) | Product owner (engineering proposes) |
| D-5 | Admin access to other members' question/answer/evidence content | — | **Decided: OFF / deferred.** Admins get operational structure only. A future support/audit content capability would be designed separately (least privilege, explicit authorization, audit trail, clear UI, content re-authorization) | Product owner — decided |
| D-6 | Progress transport for Ask (SSE vs polling) | Phase 7 → open | SSE from FastAPI (`StreamingResponse`), proxied same-origin. **Not built in Phase 7**: the API answers in one response, so the UI shows honest elapsed-time progress and invents no stages | Engineering |
| D-7 | Design-customer domain (B2B SaaS "Halden") | Phase 3 | Keep unless you prefer a domain you know better (legal, healthcare, developer tools) | Product owner |
| D-8 | A document readable **only through a grant** belongs to an account outside the reader's scope: may its evidence card / inspector show that account's name? | Phase 5 (evidence), Phase 7 (UI) | **Decided: no.** The document and its content stay readable; its structured account field is omitted (no substitute), and the account never enters lists, filters, mention detection or autocomplete. Implemented as `authz.visible_document_account` (authz-2) | Product owner — decided |
| D-9 | Stable key for access groups | Phase 3 | **Decided:** `access_groups.key` (citext, unique per organization, slug format); stable across renames; a key change is an identity change; never an authorization input. Migration 0004 | Engineering — decided |
| D-10 | The chunk contextual header (embedded, and in the shared `tsv`) includes the document's account name, per document, not per reader. After D-8, a query naming an out-of-scope account would still match a granted document of that account through the header alone, hinting at the association | Phase 4 (header design) | **Decided:** structured account names are never added to shared search or embedding text; account terms are kept separate and used only when `account_visible` — at query time, over the reader's visible accounts (no stored per-account field is needed). Implemented in Phase 4 (header and `tsv`); the query side is Phase 5. Golden item `visibility-kestrel-latest-maya` pins the expected behaviour | Engineering + Product owner — decided |
| D-11 | Answer status for requests Lattice does not perform (draft an email, change access, update a ticket, read others' questions) | Phase 6 | **Decided:** add `unsupported_request` (reason `action_request`); never used for unreadable evidence. Decided first by code from the question alone (`answering.request_kind`), before the evidence and provider decisions, so it holds with no evidence and with no model available, and no model is called; the model's fixed rubric still covers phrasings the patterns do not list. Implemented with answering (Phase 6) | Product owner — decided |
| D-12 | How Phase 4 applies admin grants that exist before the Phase 9 access UI (the dataset's `admin-grants.yaml`) | Phase 4 | **Decided:** a separate post-sync command (`lattice apply-admin-grants`), never a connector, resolves each record key (optionally with its source) to a document and writes `origin = admin` membership grants; any unresolvable, ambiguous or duplicate entry fails the whole application with nothing written; idempotent; `--prune` revokes | Engineering — decided |
| D-13 | Query egress: who sets a question's sensitivity, and does generation obey it? | Phase 5 closure | **Decided and implemented:** `effective = more_restrictive(org_settings.default_query_sensitivity, per-question declaration)`. A user, client, provider key or provider selection can raise it, never lower it (unrecognised = `restricted`). External query embedding is skipped when policy forbids it; exact + lexical always run; a `local` provider approved for the sensitivity may still embed the question; no silent external fallback. Returned as `RetrievalResult.question_sensitivity`. **Phase 6 must apply the same effective sensitivity to the question in the generation prompt** (`evidence_only` without a model call when it may not be sent), under the same invocation rule (ADR-011). Phase 7 offers the per-question "more sensitive" control. Migration 0006; tests `test_question_sensitivity.py` | Product owner — decided |
| D-14 | Notice for a document superseded by one the reader cannot read | Phase 5 closure | **Decided and implemented:** drop any notice derived solely from an unreadable successor: no "newer document exists", no "may be outdated", no successor ID, count, title, timestamp, source or relationship. Readable ↔ readable explicit supersession is exposed; never inferred from age. Tests `test_successor_visibility.py` (the reader's whole result is identical with and without a hidden successor; losing access removes the link on the next request) | Product owner — decided |
| D-15 | Deployment trust classes: how is inference outside the Lattice deployment governed, and what does "private" mean? | Final Phase 5 closure | **Decided (2026-09-29, ADR-011):** "private" has no security meaning. The classes are `local` (inside the deployment; own approval), `organization_controlled` (outside Lattice, on organization-controlled infrastructure: not allowed until the organization sets a ceiling, and needs an explicit record approval), `external_api` (third party: external level, approval, data use) and `unknown` (fail closed). A credential satisfies only the credential requirement. Implemented in migration 0007 and `providers.egress` | Product owner — decided |
| D-16 | Organization-owned provider credentials (BYOK): when are the secret store, encryption at rest, rotation, revocation, masked admin UI and approval audit events built? | Final Phase 5 closure | **Decided (2026-09-29, timing only, ADR-011):** the Phase 6 backend builds the minimum organization-scoped provider configuration and secret abstraction (organization → provider configuration → trust class → provider/model → secret reference → invocation; secrets never in API responses, logs, traces, prompts or errors; rotation, replacement, revocation, disablement; environment variables stay as the development fallback). The Phase 7 frontend builds the admin UX (masked state, connection test, enable/disable, egress communication). Nothing in Phase 5. **Correction (2026-10-06):** Phase 7 shipped the provider page read-only by the product owner's instruction; configuration stayed CLI. The admin UX and encrypted-at-rest custody are Phase 12 (D-29, P12.5; ADR-011 amendment) | Product owner + Engineering — decided |
| D-17 | May an administrator change their own access through the Access page? | Phase 9 | **Decided (engineering default, Phase 9; for review): no.** Every change to the caller's own membership — including a grant to themselves — is refused (403 `own_access`); another administrator, the manifest or (Phase 10) the identity provider must do it. T3 stays detectable either way (audit log) | Product owner (review) |
| D-18 | What does an administrator see of a member's grants on documents — and of private groups — the administrator cannot see? | Phase 9 | **Decided (Phase 9 review closure, 2026-10-02):** on the member page, one yes/no sentence for each (`has_other_grants`, `has_hidden_groups`) — never a list, count, title, name, member list or identifier, and nothing about them can be edited from there. The audit view (`GET /v1/admin/audit-events`) does **not list** an event whose target document the viewer cannot read now (filtered in SQL with the viewer's predicate, also for the keyset anchor), so such events change neither its result set nor its paging; the events stay in the database for operators. *(The first version listed them one by one by opaque `rec_…`; withdrawn as an existence, timing and count side channel.)* | Product owner — decided |
| D-19 | Who is authoritative for a member's access when the manifest (later the IdP) and the Access page disagree? | Phase 9 | **Decided (Phase 9):** provisioning. Re-running `make seed` makes members match the manifest and records `operator:seed` events, so a reverted UI change is visible, never silent. **Revisited (Phase 10; accepted by the product owner 2026-10-02):** for members provisioned by an identity provider, the provider is authoritative for identity (user name, e-mail, name) and lifecycle (active / suspended / removed); Lattice is authoritative for access (clearance, scope, assignments, departments, groups, grants) and the administrator capability. The Access page refuses lifecycle changes for such members; SCIM never writes access | Product owner — accepted |
| D-20 | What may SCIM provisioning do, and with what default access? | Phase 10 | **Decided and accepted (Phase 10; accepted by the product owner 2026-10-02):** `Users` only (no `Groups`). `externalId` required, immutable, and equal to the OIDC identifying claim. A provisioned person is a user *owned by the organization* (never linked to an existing user; e-mail unique per organization). New members get least access: clearance `internal`, account scope `assigned` with no accounts, no departments, groups or grants, not an administrator. `active=false` suspends, `DELETE` removes (terminal; re-hire under a new `externalId` or by an operator). Identical requests are no-ops. Every change audited | Product owner — accepted |
| D-21 | Rate-limit scope and defaults | Phase 10 | **Decided and accepted (Phase 10; accepted by the product owner 2026-10-02):** per member 10 Asks/minute, 500/day, 2 in flight; per administrator 60 access previews/writes per minute; per SCIM token 600 requests/minute; per client address 30 pre-authentication requests/minute (per process). Fixed windows; no role exempt; no setting disables a limit. No per-organization Ask budget yet (one member cannot exhaust it for others; a whole organization can still spend its provider quota) | Product owner — accepted |
| D-22 | Where Phase 11 is verified | Phase 11 | **Decided (2026-10-03); amended by the product owner 2026-10-05 (zero paid infrastructure):** the production-shaped local reference stack (two API instances) is the verification environment, and Phase 11 closes on full local production-shaped verification. The real cloud staging deployment is **deferred because no deployment budget is currently available**, together with managed-PostgreSQL privilege verification, managed backup/PITR verification, public TLS/origin validation, real external OIDC callback validation, provider-specific deploy/rollback validation and cloud RPO/RTO measurement; none is claimed done. This external validation is a **release gate for Phase 15**: v1 GA / production-ready cannot be declared before it. DigitalOcean (deployment.md Appendix A) is the selected candidate for future staging validation; not provisioned or tested. The contract stays provider-independent ([phase-11-plan](phase-11-plan.md) §6.5, §21.1) | Product owner — decided (amended) |
| D-23 | Schema compatibility during deploys | Phase 11 | **Decided (2026-10-03):** keep the exact schema-revision guard; releases with a migration are deployed stop-the-world (stop api and worker, migrate with a pre-flight that refuses while application connections are open, start the new build) and accept a short unavailable interval; no expand/contract machinery unless proven necessary for correctness (phase-11-plan §6.3) | Product owner — decided |
| D-24 | Shared rate limits for multi-process deployment | Phase 11 | **Decided (2026-10-03):** in-flight Asks and the pre-authentication per-client limit move to PostgreSQL (leases; a `lattice_auth`-owned counter keyed by an HMAC of the client address taken only from the trusted ingress); no Redis; defaults unchanged; no process-local security limit remains (phase-11-plan §10) | Product owner — decided |
| D-25 | Sessions and lifecycle when an SSO connection is disabled | Phase 11 | **Decided (2026-10-03):** sessions record the connection that issued them and `lattice sso disable` revokes them; the connection can be re-enabled and updated in place (client ID, secret-reference rotation; issuer/claim only with an explicit subjects-unchanged assertion), and a new connection cannot orphan provisioned members by accident. Operator commands only; customer UI is Phase 12 (phase-11-plan §9.2) | Product owner — decided |
| D-26 | Organization deletion | Phase 11 | **Decided (2026-10-03):** destructive deletion/purge deferred to Phase 12; Phase 11 adds audited `lattice org suspend/reactivate` and replaces unaudited owner SQL in incident procedures with audited operator commands (phase-11-plan §9.3) | Product owner — decided |
| D-27 | Image publishing | Phase 11 | **Decided (2026-10-03):** one gated path — pushes to `main`, `v*` tags and manual dispatch build and publish both images to GHCR in the same CI run, after the existing jobs pass (no duplicate test runs, no images on development pushes); deployments use digests; revision and version visible internally at runtime (phase-11-plan §7) | Product owner — decided |
| D-28 | How is an organization created for v1? | Phase 12 | **Decided (product owner, 2026-10-06):** operator-assisted bootstrap. One audited, idempotent `lattice org create` creates the tenant trust root (organization, settings, OIDC connection, first administrator's membership and directory identity bound to their exact IdP identifier); everything after the first sign-in is administrator-operable. No self-service, no JIT or e-mail-matched membership (phase-12-plan C1) | Product owner — decided |
| D-29 | Custody of secrets administrators enter | Phase 12 | **Decided (product owner, 2026-10-06, at the security-property level):** recoverable credentials (OIDC client secrets, provider keys, future connector credentials) encrypted at rest in PostgreSQL (AES-256-GCM, `cryptography`, associated data binding organization/record/type/key, master keys only in the deployment environment) behind `SecretStore`; verification-only secrets (SCIM tokens) stay hashed; `env:` references stay; no external secret manager (C3; ADR-011 amendment) | Product owner — decided |
| D-30 | Members without SCIM | Phase 12 | **Decided (product owner, 2026-10-06):** administrators create members by hand, bound to the exact identifier of the connection's identifying claim, with SCIM's least-access defaults; no JIT, no fuzzy or e-mail matching (C2.1) | Product owner — decided |
| D-31 | Administrator capability in the product | Phase 12 | **Decided (product owner, 2026-10-06; corrected under delegation):** administrators grant/revoke it for others (never self), previewed, reasoned, audited; the last active administrator cannot be removed by any *product* action, safe under concurrency (advisory lock, count after lock, deferred trigger; ADR-017). The identity provider and operators are not bound by it: zero administrators → operator recovery (C2.2, C18) | Product owner — decided |
| D-32 | How exports reach Lattice | Phase 12 | **Decided (product owner, 2026-10-06):** administrators upload export archives through an `ImportStore` abstraction (filesystem backend first, object storage later), organization-scoped opaque import IDs, strict archive validation; operator-mounted locations stay supported; no paid storage (C8). **Built in P12.6 (2026-10-18):** streamed upload with its own API and ingress limits, `FilesystemImportStore`, entry-by-entry archive checks, a deterministic check job, the worker syncing from the import; operator-managed sources keep the `lattice sync` path (registered export locations not built) | Product owner — decided |
| D-33 | New connectors in Phase 12 | Phase 12 | **Decided (product owner, 2026-10-06):** none. The three export-based types (Markdown documents, ticket JSON Lines, Slack workspace export) become customer-operable and are named as exports, not live integrations. GitHub Issues is the first future live connector candidate (C7) | Product owner — decided |
| D-34 | Who may raise AI egress ceilings, and how? | Phase 12 | **Decided (engineering, under the product owner's delegation, 2026-10-06):** one administrator, with a Now→After preview, reason and typed confirmation, plus a **fresh identity-provider re-authentication** (`max_age=0`, `prompt=login`, `auth_time` ≤ 5 min, own identity) producing a single-use grant bound to the session and the exact change; lowering is immediate. Rejected: second-admin approval (blocks one-admin organizations, needs an approval subsystem) (C6). **Built in P12.5 (2026-10-17):** the database classifies escalations from the egress cells and consumes the grant (also bound to the policy version); a fresh sign-in is not reported as MFA unless an operator-configured `acr` was asserted | Product owner (delegated) |
| D-35 | Setup navigation | Phase 12 | **Decided (product owner, 2026-10-06):** durable surfaces — `/settings` (setup record), `/settings/identity`, the providers page, accounts under Access, sources with record pages; no wizard; backend-owned states, no percentages (C12) | Product owner — decided |
| D-36 | Re-hire after SCIM removal | Phase 12 | **Decided (product owner, 2026-10-06):** administrators reinstate the original membership and directory identity (never by e-mail; conflicts refused) with **least access**, `is_admin` never restored. The operator `member reinstate` keeps restoring prior access as the incident-reversal path — a deliberate, documented difference (C2.3, C18) | Product owner — decided |
| D-37 | Organization purge (A) and IdP migration with differing subjects (B) | Phase 12 | **B decided (product owner, 2026-10-06):** post-v1, stated in the identity UI. **A decided (engineering, under delegation):** semantics fixed now, executable purge built in **Phase 15** — coherent evidence needs a platform-level deletion record (new architecture) and backup-expiry commitments; suspension is the control until then (C14) | Product owner (A delegated) |

## 8. Self-review log (Phase 0)

Issues found while reviewing the first draft of these documents, and what changed:

| Area | Issue found | Correction |
| ---- | ----------- | ---------- |
| Authorization | "Private channel" and "restricted" were the same thing, so the AI egress policy would have blocked every private Slack channel from answers | Split *who* (`access_mode`: policy / grants_only) from *how sensitive* (`sensitivity`) |
| Authorization | Tripwire (E4) re-reading permissions after retrieval could false-alarm, or check different permissions than the search used, if access changed mid-request | Context, retrieval and E4 share one `REPEATABLE READ` snapshot |
| Security | Stored answer text in traces would let a revoked user re-read content derived from evidence they lost access to | Answer text is hidden when any evidence of that Ask (cited or not: supersession items and notices name uncited documents) is no longer readable by the viewer, or any account it depends on (clarification choices, prompt evidence accounts, scope; recorded as IDs, migration 0009) is no longer visible |
| Security | "Audit capability" was referenced but never modelled | Admins see operational trace structure only; D-5 later decided OFF (see §9) |
| Ingestion | Unmapped account on an account-scoped source would have made tickets visible to everyone (fail-open) | Such records fail closed and are reported |
| Data model | `tsv` was described as a generated column but needs document-level header terms | Computed at ingestion; header staleness after account changes documented as a ranking lag, never an access lag |
| Retrieval | Relevance floor required lexical AND semantic matches, which would refuse good paraphrase questions | Any of lexical / identifier / calibrated semantic match passes the floor |
| Design | Muted text, lavender and pink from the reference fail WCAG AA as text | Darker text variants measured and tokenized; bright accents limited to graphics |
| Design | First-run "example questions" would be placeholder AI content | Removed; the empty state shows the user's real accessible scope |
| Scope | Web scaffold, OIDC, job queue, OpenTelemetry were drifting into Phase 1 | Moved to the phases that need them (§6) |

Known weaknesses kept on purpose (watch list):
- Potential-conflict *detection* depends on the model; verification and
  presentation are deterministic, and only metadata can establish supersession.
  Measured by the conflict slice; if recall is weak, add a deterministic pre-pass
  (same account + topic terms + differing dates) that proposes candidates.
- Under the default egress policy, confidential/restricted documents are
  keyword-only in retrieval and never summarized; Leadership users will see more
  `evidence_only` / egress notices. Measured in the AI-egress eval slice.
- Rerank weights are guesses until Phase 5 evals.
- Timing side channels (T16) are accepted, not eliminated.
- The fake embedding provider makes CI semantic retrieval roughly lexical; retrieval
  *quality* metrics require the real provider.

## 9. Correction log (post-review, before Phase 1)

| # | Correction | Documents changed |
| - | ---------- | ----------------- |
| 1 | AI egress default is `internal` only; `confidential`/`restricted` need explicit, audited org opt-in for an approved provider; egress is distinct from authorization and the UI says which applies; new `evidence_only` status; `model_providers.approved_for_level` | security-model, data-model, retrieval-design, ingestion-design, architecture, authorization-model, trust-boundaries, threat-model, product-brief, design-system, evaluation-plan, implementation-plan |
| 2 | Citation validation is claim-level fail-closed: any failed citation invalidates the claim; regenerate once, then remove; no percentage threshold; withheld answer when no valid direct-answer claim remains; response invariant against dangling citations; five explicit tests | retrieval-design, architecture, security-model (SEC-13), trust-boundaries, threat-model, ADR-006, evaluation-plan, product-brief, design-system, implementation-plan |
| 3 | "Potential conflict" unless metadata establishes supersession; ranking/recency/authority never resolve truth | retrieval-design, data-model, design-system, evaluation-plan, product-brief, trust-boundaries, ADR-006, implementation-plan |
| 4 | D-5 OFF/deferred: no admin content access; admin trace view excludes question, answer, evidence, counts and scope | security-model (SEC-15), authorization-model, data-model, design-system, threat-model, implementation-plan |
| 5 | Operational-metadata side-channel rules per channel; account visibility limited to the member's account scope (A-3 revised); templated failures with opaque references | security-model §10 (SEC-14), data-model, retrieval-design, ingestion-design, authorization-model, design-system, threat-model (T20), evaluation-plan, implementation-plan |
