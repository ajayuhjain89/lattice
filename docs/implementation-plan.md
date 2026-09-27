# Lattice — Implementation Plan

Status: Phase 0 approved. Phase 1 approved and closed (after the `lattice_auth`
hardening below). **Phase 2 implemented** (awaiting review). Phase 3 not started.

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
| Seed | Unchanged: groups and documents exist only as test fixtures | Groups and content are the Phase 3 dataset |

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

---

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
- Injection fixtures (deterministic parts).

**Non-goals.** Streaming of answer text; multi-turn; any admin capability to read
other members' content.

---

### Phase 7 — Web app: Ask, evidence, inspector

**Deliverables.** Next.js (App Router, TS strict) app; tokens.css; primitives;
Radix-based overlays; dev login screen (persona picker, dev only); shell + sidebar;
Ask page with composer, scope chips, progress stages (SSE — decision D-6), answer
block, potential-conflict/supersession/stale/egress/removed-claims notice components,
evidence list (incl. "Not used to draft this answer" group), inspector; all §B13
states with fixtures; generated API client.

**Tests.** Component tests for states; a citation chip cannot render for evidence
absent from the response (and its claim is not rendered); Playwright flows for Maya vs
Grace on the Acme question (different evidence; Grace sees the egress notice under the
default policy), inspector keyboard flow; axe accessibility checks; visual QA at 5
widths.

**Non-goals.** Accounts workspace, Find mode, dark theme.

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

---

### Later phases (order to be revisited after the slice)

| Phase | Theme | Notes |
| ----- | ----- | ----- |
| 9 | Access management UX + audit events | Explanations, change previews (counts only over admin-readable documents), admin grants, full `audit_events`, AI egress settings UI (opt-in with reason, per-provider approval) |
| 10 | OIDC SSO + hardening | OIDC (D-1), rate limits, security headers review, dependency audit, threat-model review, pen-test style review of isolation |
| 11 | Deployment + operations | Container images, single-region deploy, backups/PITR, migrations in CI/CD, retention jobs, alerting on `security_invariant_violated` |
| 12 | Accounts workspace | Account timeline + scoped Ask (AM workflow) |
| 13 | Next connectors | Call transcripts, CRM notes, GitHub issues, release notes |
| — | Only if evals demand | HNSW tuning, model reranker, query rewriting, simple-config tsvector |

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
make test           # 378 tests against the lattice_test database (incl. 500-example authz proof)
make lint typecheck
```
Added in later phases: `make sync` (Phase 4), `make eval` (Phase 5), `make web`
(Phase 7, Node 22 LTS + pnpm).

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
| D-1 | Identity provider for first real deployment (generic OIDC; which IdP to test against) | Phase 10 | Generic OIDC + PKCE; test with the IdP your first pilot uses | Product owner |
| D-2 | Embedding provider: external API vs private/self-hosted | Phase 4 | External API with no-training terms, approved for `internal` only (per D-3). Confidential/restricted content is lexical-only unless a private/self-hosted endpoint is approved later. The external provider receives all *internal* chunk text | Product owner |
| D-3 | AI egress default for external providers (generation and embedding) | Phase 4 (embedding), Phase 6 (generation) | **Decided:** `internal` may be sent to an approved external provider; `confidential` and `restricted` are not sent unless the org explicitly opts in (audited) for a provider approved for that level. Egress is a data policy separate from authorization | Product owner — decided |
| D-4 | Default generation provider/model | Phase 6 | Anthropic Messages API with a current Claude model, chosen by eval quality/latency/cost; approved for `internal` only by default (D-3) | Product owner (engineering proposes) |
| D-5 | Admin access to other members' question/answer/evidence content | — | **Decided: OFF / deferred.** Admins get operational structure only. A future support/audit content capability would be designed separately (least privilege, explicit authorization, audit trail, clear UI, content re-authorization) | Product owner — decided |
| D-6 | Progress transport for Ask (SSE vs polling) | Phase 7 | SSE from FastAPI (`StreamingResponse`), proxied same-origin | Engineering |
| D-7 | Design-customer domain (B2B SaaS "Halden") | Phase 3 | Keep unless you prefer a domain you know better (legal, healthcare, developer tools) | Product owner |
| D-8 | A document readable **only through a grant** belongs to an account outside the reader's scope: may its evidence card / inspector show that account's name? (Account lists, filters and mention detection stay scope-bound either way — `account_visible`.) | Phase 5 (evidence), Phase 7 (UI) | Show it: a grant is an explicit decision to share that document, including its own metadata; never add the account to the reader's lists | Product owner |
| D-9 | Stable key for admin-defined access groups in seed manifests (Phase 3 needs idempotent group upserts; source groups will key on `(source_id, external_ref)` in Phase 4) | Phase 3 | Add `access_groups.key` (citext, unique per organization), mirroring departments and accounts | Engineering |

## 8. Self-review log (Phase 0)

Issues found while reviewing the first draft of these documents, and what changed:

| Area | Issue found | Correction |
| ---- | ----------- | ---------- |
| Authorization | "Private channel" and "restricted" were the same thing, so the AI egress policy would have blocked every private Slack channel from answers | Split *who* (`access_mode`: policy / grants_only) from *how sensitive* (`sensitivity`) |
| Authorization | Tripwire (E4) re-reading permissions after retrieval could false-alarm, or check different permissions than the search used, if access changed mid-request | Context, retrieval and E4 share one `REPEATABLE READ` snapshot |
| Security | Stored answer text in traces would let a revoked user re-read content derived from evidence they lost access to | Answer text is hidden when any cited evidence is no longer readable by the viewer |
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
