# Lattice — Authentication & Authorization Model

Status: §1–2 implemented in Phase 1 (dev identity provider, sessions). §3–7 and the
account-visibility rules of §9 are **implemented in Phase 2** (`backend/src/lattice/authz`,
migration 0003) and proven by the suites listed in §6; enforcement points E3 and E4
exist as library functions that retrieval (Phase 5) and answering (Phase 6) will call.
Phase 3 added decision D-8 (structured account metadata, §6 and §9, policy version
`authz-2`) and D-9 (a stable key for access groups, migration 0004).
Related: `data-model.md` §2–5, `security-model.md`, ADR-003, ADR-005.

---

## 1. Separation of concerns

| Question                                  | Answered by       | Output |
| ----------------------------------------- | ----------------- | ------ |
| *Who is this?*                            | Authentication (`identity`) | `User` + `Session` bound to one organization |
| *Which organization's data is in play?*   | Session (never the request) | `organization_id` → RLS setting |
| *What may they do?* (capabilities)        | `authz`           | e.g. `is_admin` → may manage sources/access |
| *What may they read?* (data access)       | `authz`           | `AuthorizationContext` → SQL predicate |

Capabilities and data access are separate on purpose: **being an administrator does
not grant access to document content.** An IT admin who manages Lattice access cannot
read executive-restricted documents unless explicitly granted, the same as in a
well-run Drive or Slack workspace.

## 2. Authentication (direction)

- **Server-side sessions** (ADR-005). Random 256-bit token in an `HttpOnly; Secure;
  SameSite=Lax; Path=/` cookie; only its SHA-256 is stored. Revocation is a row update
  and is effective on the next request. JWTs were rejected because immediate
  revocation is a requirement.
- **Identity providers** behind one interface returning a verified `(issuer,
  subject, email, name)`:
  - `DevIdentityProvider` (implemented) — sign in as a seeded persona: `POST
    /v1/auth/dev-login {"subject": "<email>", "organization": "<slug>"?}`. Available only
    when `LATTICE_ENV ∈ {development, test}` and `LATTICE_DEV_AUTH_ENABLED=true`;
    production with it enabled fails configuration, the route is not mounted, and the
    provider refuses to construct. `organization` is needed only when the persona has
    several memberships; it is a selector validated against the identity's own active
    memberships (via `lattice_identity_memberships`), not tenant authority. Every
    failure is one generic `authentication_failed`.
  - `OidcIdentityProvider` — generic OIDC authorization-code flow with PKCE (Okta,
    Entra ID, Google Workspace, Keycloak). Required before any non-local deployment.
- No passwords are stored by Lattice.
- CSRF: `SameSite=Lax` plus a required custom header (`X-Lattice-Request: 1`) on all
  state-changing requests, and rejection of `Sec-Fetch-Site: cross-site`; the API
  rejects cross-site form posts (`security-model.md` §4).
- Membership provisioning in the slice comes from the seed manifest. SCIM /
  just-in-time provisioning is a later phase.

## 3. Authorization concepts (only what is needed)

| Concept          | Where it lives                  | Role in policy |
| ---------------- | ------------------------------- | -------------- |
| Organization     | session                         | Hard boundary (RLS + predicates) |
| Membership       | `memberships`                   | Must be `active` |
| Clearance        | `memberships.clearance`         | `internal` < `confidential` |
| Account scope    | `memberships.account_scope` + `account_assignments` | `all`, or only assigned accounts |
| Department       | `membership_departments`        | Satisfies department-restricted documents |
| Access group     | `access_groups` + members       | Principal for grants (e.g. private channel members, "Exec staff"). Identified in manifests and sync by its stable organization-scoped `key` (D-9); the policy uses only its ID |
| Sensitivity      | `documents.sensitivity`         | *How sensitive*: `internal`, `confidential`, `restricted`. Checked against clearance. Separately used by the AI egress policy (not an authorization rule; `security-model.md` §6) |
| Access mode      | `documents.access_mode`         | *Who decides*: `policy` (rules below) or `grants_only` (explicit grants only). Restricted ⇒ grants_only |
| Department restriction | `documents.department_ids` | Empty = none |
| Account          | `documents.account_id`          | Null = not account-specific |
| Explicit grant   | `document_grants`               | To membership, department or group (one typed, foreign-keyed column per principal kind — `data-model.md` §5) |
| Role label       | `memberships.role_label`        | **Display only.** Role templates set defaults for the fields above |

Deliberately excluded for the first release: explicit deny rules, time-bound grants,
attribute-based conditions on arbitrary metadata, and per-chunk permissions. Each
adds reasoning complexity without a first-release workflow that needs it.

## 4. The policy

A member *m* may read document *d* if and only if:

```
readable(m, d) :=
      d.organization_id = m.organization_id
  AND m.status = 'active'
  AND d.status = 'active'
  AND (
        granted(m, d)                                  -- explicit grant path
     OR (
           d.access_mode = 'policy'                    -- grants_only docs: grants only
       AND d.sensitivity <> 'restricted'               -- implied by CHECK; kept as defense in depth
       AND rank(d.sensitivity) <= rank(m.clearance)    -- internal < confidential
       AND ( d.account_id IS NULL
             OR m.account_scope = 'all'
             OR d.account_id ∈ m.assigned_account_ids )
       AND ( d.department_ids = '{}'
             OR d.department_ids && m.department_ids )
        )
  )

granted(m, d) := ∃ grant g on d where
      (g.principal_type='membership' AND g.principal_id = m.id)
   OR (g.principal_type='department' AND g.principal_id ∈ m.department_ids)
   OR (g.principal_type='group'      AND g.principal_id ∈ m.group_ids)
```

Chunks inherit their document's readability. There is no chunk-level exception.

Implemented exactly as written, twice (§6). Details the formula leaves implicit, as
built:

- A **deleted** document is unreadable even to a grantee (`status` is checked before
  the grant path).
- A grant counts only if it belongs to the document's organization *and* the reader's
  (`g.organization_id` is checked in SQL and in Python, in addition to the composite
  foreign keys that make such a row impossible).
- Unknown values fail closed at every layer: the schema refuses them (CHECK
  constraints); the resolver refuses a membership with an unknown clearance or scope;
  the policy denies an unknown sensitivity, access mode, status or principal type;
  `restricted` + `policy` (forbidden by CHECK) is denied by both implementations.
- `AuthorizationContext` carries **no** administrator flag or role label, and the
  resolver does not read them, so the policy cannot consult them even by mistake.

Design properties:

- **Monotone and explainable.** Each clause maps to one human sentence in the access
  UI ("You can see this because you're assigned to Acme and it's Internal").
- **Fail closed.** Missing sensitivity → record rejected at ingestion; unknown
  department IDs → fewer readers; unmapped account on an account-scoped source →
  record not indexed; unset `app.org_id` → no rows.
- **Grants are the only way into `restricted`.** Clearance never reaches it, so no
  role change can accidentally expose restricted content.
- **"Who" and "how sensitive" are separate.** A private Slack channel for the Acme
  account team is `grants_only` (only channel members) but may be `internal` in
  sensitivity — so it can still be summarized by an approved external model under
  the default egress policy (internal only). An executive M&A memo is `restricted`
  (grants only), and confidential/restricted content is not sent to external models
  by default. Conflating the two would either over-share or make every private
  channel unusable for answers.
- **Authorization ≠ AI egress.** Authorization decides what a person may read. The
  egress policy decides what may be sent to a model provider. A Leadership member
  can read a confidential document in Lattice while the organization forbids sending
  it to its external provider; the UI states which case applies.

### Role templates (seed defaults, editable per member)

| Role label        | clearance     | account_scope | departments           | typical grants |
| ----------------- | ------------- | ------------- | --------------------- | -------------- |
| Account Manager   | internal      | assigned      | Sales                 | — |
| Customer Success  | internal      | assigned      | Customer Success      | — |
| Support           | internal      | all           | Support               | — |
| Product           | internal      | all           | Product               | — |
| Engineering       | internal      | all           | Engineering           | — |
| Leadership        | confidential  | all           | Leadership            | group "Exec staff" |
| Administrator     | internal      | assigned (none) | IT                  | — (is_admin = true) |

Mapping to the scenarios in the brief:

- *Support may see support info but not executive-only notes* → Support is `internal`;
  exec notes are `confidential` or `restricted`.
- *AM may see assigned accounts only* → `account_scope = assigned`.
- *Product sees product info across accounts, subject to confidentiality* →
  `account_scope = all`, `clearance = internal`.
- *Leadership broader* → `confidential` + Exec group grants for `restricted`.
- *Restricted only for explicit identities* → grants only.

## 5. Enforcement points

| # | Point | Mechanism | Purpose |
| - | ----- | --------- | ------- |
| E1 | Every request | Session → active membership check | Suspended/removed users stop immediately |
| E2 | Every DB transaction | RLS on `app.org_id` | Structural org isolation |
| E3 | Retrieval SQL | `authz.readable_documents_predicate(ctx, documents)` joined into **both** lexical and semantic queries | Unauthorized rows never leave Postgres |
| E4 | Before prompt construction | `authz.assert_all_readable(session, ctx, document_ids)` using the Python reference policy, re-reading access columns | Invariant check; a failure is a bug → abort, alert, no generation |
| E5 | Evidence / inspector endpoints | Same predicate on direct lookups by ID; unreadable = 404 | No enumeration via IDs |
| E6 | Trace reads | Trace references re-authorized against the *viewer's* current context | Debug tools can't bypass access |
| E7 | Admin routes | `is_admin` capability | Management actions |

E3 is the control; E4 is a tripwire that proves E3 in production. E4 never *filters*:
if it removes something, E3 was wrong and the request fails loudly. Context
resolution, retrieval and E4 run in one `REPEATABLE READ` read-only snapshot, so a
permission change committing mid-request cannot cause a false tripwire (or a
check against different permissions than the search used).

As built (Phase 2): the snapshot is `Database.tenant_snapshot(org_id)` (REPEATABLE
READ, READ ONLY, organization bound). `assert_all_readable` raises
`AuthorizationInvariantViolation` (`security_invariant_violated`, HTTP 500, fixed
message naming nothing) when any ID is unreadable **or cannot be found in the
reader's organization**, even if the other evidence is fine. It logs one
`security.invariant_violated` event with organization, membership, policy version,
counts and the offending document UUIDs — never titles, access attributes or policy
reasons (SEC-8). Alerting on that event is Phase 11.

## 6. One policy, two implementations, proven equal

- `lattice.authz.policy.readable(ctx, doc) -> bool` — the **reference**
  implementation, pure Python, no I/O, readable top to bottom. `DocumentAccess` keeps
  stored values as plain strings so that an unrecognised value can reach the policy
  and be denied.
- `lattice.authz.sql.readable_documents_predicate(ctx, documents=…)` — the SQLAlchemy
  expression used in queries; `documents` may be an alias (retrieval will join
  chunks → documents). It is written independently of the reference (its own
  clearance table, no shared helpers) so the equivalence test compares two
  implementations, not one with itself. Context values are bound parameters (arrays
  for ID sets); the SQL text depends only on the policy.
- **Equivalence test** (`tests/security/test_authz_equivalence.py`, `hypothesis`):
  generated two-organization worlds — members across status, clearance, scope,
  assignments, departments, groups and the admin flag; documents across sensitivity,
  access mode, status, account, departments (including departments deleted after the
  fact) and grants to applicable, unrelated and deleted principals. For every (member,
  document) pair: the resolved context equals the one implied by the spec; the SQL
  predicate as `lattice_app` under RLS equals `readable()` per row and as a WHERE
  set; the predicate with RLS bypassed still equals `readable()` for both
  organizations (so its own organization clause is tested); account visibility,
  the access loader and `explain()` agree too. 500 examples in CI (≈ 5,000 document
  decisions and ≈ 6,500 account decisions per run); the test fails if any policy path
  was exercised fewer than 10 times.
- **Explanations** (`lattice.authz.explain`), with the existence rules of §9:
  - `explain(ctx, doc)` — internal, every clause (granting and denying) as
    `AccessReason` codes, plus the matched grants and `policy_version`. Trusted
    callers only; never returned to an API caller directly. Its `readable` field is
    `readable()` itself, so an explanation cannot disagree with the decision.
  - `explain_own_access(ctx, doc | None)` — "why can I see this?": only granting
    reasons, no principal IDs or group names; `None` for unreadable **and** for
    missing documents, identically.
  - `explain_for_reviewer(reviewer, subject, doc | None)` — for the Phase 9 access
    UI: `None` unless the reviewer can read the document and both are in the same
    organization, so it is not an oracle over documents the reviewer cannot see.
    The administrator-capability check stays in the route (E7).
- **Account visibility** (§9, `security-model.md` §10): `account_visible(ctx,
  account)` and `visible_accounts_predicate(ctx)` — the account's organization, an
  active membership, and scope `all` or an assignment. Deliberately independent of
  `readable`: a grant on one document of an out-of-scope account does not put that
  account in the member's lists, filters, mention detection or autocomplete. Proven
  equivalent in the same property test. The admin account directory is a separate,
  capability-gated view (Phase 9).
- **Structured account metadata (decision D-8)**:
  `visible_document_account(ctx, doc)` returns a document's account only when the
  document is readable **and** `account_visible` holds; otherwise `None`. Evidence
  cards, the inspector's account field, the `account` attribute of prompt evidence and
  account facets use it, so an explicit grant on a document of an out-of-scope account
  makes the document (and the customer name in its text) readable without disclosing
  the structured account record or making the account discoverable. Nothing is
  substituted for the omitted account. Retrieval's SQL form is a `LEFT JOIN accounts`
  whose `ON` clause carries `visible_accounts_predicate`; both forms are tested against
  every persona × document pair (`tests/security/test_account_metadata.py`).
- **Policy version.** `POLICY_VERSION` (`"authz-2"` since D-8; `"authz-1"` was Phase 2) is carried by every
  `AuthorizationContext` and explanation, and will be stored in traces (Phase 6).
  `tests/test_authz_boundary.py` pins a fingerprint (SHA-256 of the docstring-free
  AST) of `context.py`, `policy.py`, `sql.py` and `resolve.py` to each version: any
  behavioural change to those modules fails CI until the version is bumped and the new
  fingerprint recorded. Comments and formatting do not count as changes.
- **Boundary.** Only `lattice.authz` builds access conditions. Enforced by
  `tests/test_authz_boundary.py` over every module outside `lattice/authz/`: imports
  only from the package's public `__all__`, and no attribute, keyword or SQL string
  naming the access columns or grant/group tables. A future writer of access state
  (ingestion) needs an explicit, reviewed allowance there.

## 7. Freshness of authorization

- The `AuthorizationContext` is **resolved from the database on every request.** No
  cache in the first release. As built: one statement (membership, user and
  organization status, and the three ID sets as array subqueries) —
  `lattice.authz.resolve_authorization_context`, given only the organization and
  membership IDs of the server-side session. It refuses (fails closed with
  `authentication_required`) when the membership is missing from the bound
  organization, is not active, its user or organization is not active, or an attribute
  is outside the vocabulary.
- Consequence: revocation, reassignment, clearance changes and group membership
  changes take effect on the very next request, including an in-flight user's next
  question. There is no stale-permission window to manage. Tested per change type in
  `tests/security/test_authz_revocation.py` (assignment, department membership, group
  membership, group deletion, member/department/group grant removal, clearance,
  account scope, suspension/removal/user disablement, organization suspension, and
  document-side sensitivity/mode/status/department changes), each a single row write
  with no document rewritten.
- If profiling ever justifies a cache: key = (org_id, membership_id,
  `authz_updated_at`), and every access-affecting write bumps `authz_updated_at` on the
  affected memberships (and an org-wide counter for group/grant changes) in the same
  transaction. Never cache across users.
- An Ask request resolves authorization once at its start. A revocation that commits
  during a single in-flight request (≤ ~15 s) applies from the next request. This
  window is accepted and documented.

## 8. Source permission propagation

| Change in source system                          | How Lattice learns                     | Effect |
| ------------------------------------------------ | -------------------------------------- | ------ |
| User removed from a private Slack channel        | ACL sync replaces source group members | Next query excludes that channel's threads |
| Document sensitivity/sharing changed             | Content or ACL sync: `access_hash` differs | Access columns updated; no re-embed |
| Record deleted                                   | Snapshot sweep                         | Document + chunks deleted |
| Source disconnected                              | Admin action                           | All source documents immediately `deleted`, purged async |

ACL sync (`mode=acl_only`) is cheap and can run more often than content sync.
Staleness between the source and Lattice equals the sync interval; this is surfaced
per source ("Permissions last synced 14 min ago") on the Sources page. For the slice,
syncs are manual/CLI; scheduling is Phase 8.

Assumption A-5: for sources that cannot export ACLs, the source's configured defaults
(sensitivity, departments, account mapping) are the policy. Admins must understand
this; the Sources page shows it.

## 9. Information that must not leak (existence and inference)

For an unreadable document, Lattice must not reveal: its content, title, filename,
source name, author, account, date, count, or existence. Concretely:

- "No accessible evidence" responses are identical whether evidence is nonexistent or
  unreadable. No "you may not have access" hints.
- Readable documents disclose their account as structured metadata only when the
  account is in the reader's scope (D-8, §6). An explicit grant is a decision to share
  one document, not the account record.
- Counts, facets and "sources searched" summaries are computed inside the user's
  predicate.
- Direct lookups return 404 (not 403) for unreadable IDs.
- Admins viewing other members' traces see operational structure only — no question,
  answer, evidence references, counts, scope, account or source names
  (`security-model.md` §8).
- Account lists, source lists, sync failures, filters and access-change previews
  follow the operational-metadata rules in `security-model.md` §10. Operational
  access is not content access, and existence can itself be sensitive.
- Timing: retrieval queries are bounded and dominated by the predicate-filtered scan;
  we do not add work proportional to unreadable matches. (Residual risk documented in
  `threat-model.md`.)

## 10. Critical regression scenario (permanent)

- Doc A: *Acme — Q3 success plan* (internal, account Acme).
- Doc B: *Acme — executive escalation notes* (confidential, account Acme, Leadership
  department restriction). Contains canary string `LATTICE-CANARY-B-7f3a9c`.
- User 1 (Maya, Account Manager, assigned Acme, internal) and User 2 (Grace,
  Leadership, confidential).
- Both ask "What commitments have we made to Acme?" Grace's account scope is `all`, so
  for her "Acme" also matches Acme Health and the unscoped question is `ambiguous`
  (retrieval-design §2); her run selects the Acme Corp scope (a filter that only
  narrows). For Maya, Acme Health is invisible, so her question is not ambiguous and
  must not offer it as a choice. The Phase 3 golden set encodes both
  (`evals/golden/ai_egress.yaml` `egress-acme-commitments-grace`,
  `account_visibility.yaml` `visibility-commitments-maya`), plus Priya and Daniel.

Assertions (automated, `tests/security/test_critical_acme.py`):

1. For User 1, Doc B's chunk IDs are absent from lexical candidates, semantic
   candidates, fused list, selected context, and the recorded provider payload.
2. The canary string is absent from User 1's provider payload, response, trace and logs.
3. For User 2, Doc B is retrieved and returned in the evidence list. Under the
   default egress policy (internal only) Doc B is **not** in the provider payload and
   is marked "Not used to draft this answer" with the egress notice; with the org
   opted in to `confidential` for an approved provider, Doc B is in the payload and
   citable. Both configurations are tested. User 1's result never changes with the
   egress setting.
4. User 1 requesting `/v1/evidence/{docB_chunk_id}` gets 404.
5. The same holds after Maya is re-assigned away from Acme (then Doc A disappears
   too) and after Grace's clearance is lowered (Doc B disappears) — without re-ingestion.

Phase 2 already proves the authorization half of this scenario, before any content
exists: Maya reads Doc A but not Doc B and Grace reads both
(`tests/security/test_authz_personas.py`), and assertion 5 at the policy level —
un-assigning Maya from Acme removes Doc A, lowering Grace's clearance removes Doc B —
in `tests/security/test_authz_revocation.py`. The retrieval, payload, canary and 404
assertions arrive with Phases 5–6.
