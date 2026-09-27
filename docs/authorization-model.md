# Lattice — Authentication & Authorization Model

Status: Foundation draft (Phase 0).
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
  - `DevIdentityProvider` — pick a seeded user; available only when
    `LATTICE_ENV ∈ {development, test}`; the app refuses to start with it enabled in
    production.
  - `OidcIdentityProvider` — generic OIDC authorization-code flow with PKCE (Okta,
    Entra ID, Google Workspace, Keycloak). Required before any non-local deployment.
- No passwords are stored by Lattice.
- CSRF: `SameSite=Lax` plus a required custom header (`X-Lattice-Request: 1`) on all
  state-changing requests; the API rejects cross-site form posts.
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
| Access group     | `access_groups` + members       | Principal for grants (e.g. private channel members, "Exec staff") |
| Sensitivity      | `documents.sensitivity`         | *How sensitive*: `internal`, `confidential`, `restricted`. Checked against clearance. Separately used by the AI egress policy (not an authorization rule; `security-model.md` §6) |
| Access mode      | `documents.access_mode`         | *Who decides*: `policy` (rules below) or `grants_only` (explicit grants only). Restricted ⇒ grants_only |
| Department restriction | `documents.department_ids` | Empty = none |
| Account          | `documents.account_id`          | Null = not account-specific |
| Explicit grant   | `document_grants`               | To membership, department or group |
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
| E3 | Retrieval SQL | `authz.readable_documents_predicate(ctx)` joined into **both** lexical and semantic queries | Unauthorized rows never leave Postgres |
| E4 | Before prompt construction | `authz.assert_all_readable(ctx, evidence)` using the Python reference policy, re-reading access columns | Invariant check; a failure is a bug → abort, alert, no generation |
| E5 | Evidence / inspector endpoints | Same predicate on direct lookups by ID; unreadable = 404 | No enumeration via IDs |
| E6 | Trace reads | Trace references re-authorized against the *viewer's* current context | Debug tools can't bypass access |
| E7 | Admin routes | `is_admin` capability | Management actions |

E3 is the control; E4 is a tripwire that proves E3 in production. E4 never *filters*:
if it removes something, E3 was wrong and the request fails loudly. Context
resolution, retrieval and E4 run in one `REPEATABLE READ` read-only snapshot, so a
permission change committing mid-request cannot cause a false tripwire (or a
check against different permissions than the search used).

## 6. One policy, two implementations, proven equal

- `authz.policy.readable(ctx, doc_access) -> bool` — the **reference** implementation,
  pure Python, trivially reviewable.
- `authz.sql.readable_documents_predicate(ctx)` — the SQLAlchemy expression used in
  queries.
- **Equivalence test** (property-based, `hypothesis`): generate random members
  (clearance, scope, assignments, departments, groups) and random documents
  (sensitivity, account, departments, grants), insert them, and assert the SQL result
  set equals the set the reference function accepts. Runs in CI on every change to
  `authz`.
- `authz.explain(ctx, doc) -> AccessExplanation` reuses the reference policy to
  produce the human reasons shown in the access UI.

`policy_version` (a constant bumped on any change to the policy code) is stored in
traces so behaviour changes are auditable.

## 7. Freshness of authorization

- The `AuthorizationContext` is **resolved from the database on every request.** No
  cache in the first release. Cost: ~3 indexed queries (membership, departments,
  assignments, groups — combinable into one), well under 5 ms.
- Consequence: revocation, reassignment, clearance changes and group membership
  changes take effect on the very next request, including an in-flight user's next
  question. There is no stale-permission window to manage.
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
- Both ask "What commitments have we made to Acme?"

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
