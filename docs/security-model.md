# Lattice — Security Model

Status: Foundation draft (Phase 0). Implemented so far: §3–5 (Phase 1) and the
authorization controls behind SEC-3, SEC-4, SEC-5 (explanations), SEC-14 (account
visibility) and SEC-15 at the policy layer (Phase 2); see §9 for which suites exist.
Related: `authorization-model.md`, `trust-boundaries.md`, `threat-model.md`.

---

## 1. Security requirements (numbered, referenced by tests and the plan)

| ID | Requirement |
| -- | ----------- |
| **SEC-1** | The model never receives content, metadata or snippets the asking user is not authorized to read. |
| **SEC-2** | No object of organization B is retrievable, inspectable, enumerable, searchable, citable or inferable by a member of organization A. |
| **SEC-3** | Authorization is applied inside retrieval queries; there is no retrieve-then-filter path. |
| **SEC-4** | Permission changes (revocation, reassignment, clearance, group, grant, source ACL) take effect on the next request without re-embedding. |
| **SEC-5** | Unreadable and nonexistent resources are indistinguishable to the user. |
| **SEC-6** | Untrusted content (documents, questions, model output) never redefines instructions, authorization or configuration. |
| **SEC-7** | Every citation resolves to a real chunk the user may read, and its quote is a verbatim substring of that chunk. |
| **SEC-13** | Claim-level fail-closed citations: a factual claim is shown only if **every** citation attached to it validates. A claim with any invalid handle or unverifiable quote is rejected as a whole — never shown with the bad citation stripped. No percentage threshold allows an unverified claim to remain visible. |
| **SEC-8** | Logs never contain question text, document text, quotes, titles or provider payloads. |
| **SEC-9** | Traces and debug tools re-authorize references for the viewer. |
| **SEC-10** | AI data egress is a policy separate from authorization. By default only `internal` content may be sent to an approved external provider; `confidential` and `restricted` content is never sent externally unless the organization explicitly opts in for an approved provider. Payloads are minimized. |
| **SEC-11** | Sessions are revocable immediately; development-only auth cannot run in production. |
| **SEC-12** | Ingestion fails closed on missing/unmappable access metadata. |
| **SEC-14** | Operational metadata (counts, failures, trace structure, filters, account and source lists, filenames, URLs, ingestion status) never reveals inaccessible content or the existence of inaccessible accounts, documents or channels (§10). |
| **SEC-15** | Administrative capability grants no content access: admins cannot read other members' questions, answers or evidence by virtue of being admins. |

## 2. Defense in depth for SEC-1

```
 Session → active membership (E1)
   → RLS: only this org's rows exist (E2)
     → authz predicate in lexical AND semantic SQL (E3)
       → selected evidence re-verified by reference policy (E4, tripwire)
         → AI egress policy keeps non-approved sensitivities out of the prompt
           (a data-egress control applied to already-authorized evidence, §6)
           → provider call
```

Removing any single layer must not cause a leak in the tested scenarios; removing E3
must make the test suite fail (mutation check in CI: a test build with the predicate
replaced by `TRUE` must fail the security suite — proves the tests actually test).
Phase 2 performed this and eleven other policy mutations by hand; results are in
`implementation-plan.md` (Phase 2 as built). The automated nightly mutation job
arrives with retrieval (Phase 5), when there is a retrieval path to mutate.

## 3. Organization isolation

1. Org comes from the session only. (Implemented: the only source of tenant context is
   `lattice_resolve_session`; the dev-login `organization` field is a selector checked
   against the identity's own active memberships, never tenant authority.)
2. Composite `(organization_id, id)` foreign keys between tenant tables, and
   `sessions (organization_id, user_id) → memberships`. Tested as superuser, so the
   constraint — not RLS — is what rejects cross-org links.
3. RLS enabled **and forced** on all application tables, including `organizations`,
   `users` and `external_identities`; app role is non-owner without `BYPASSRLS`;
   the NOLOGIN definer role `lattice_auth` owns only the two pre-auth lookups, holds no
   object-creation privilege at rest and reads only what they need (`data-model.md` §8);
   `app.org_id` set with `set_config(..., true)` (≡ `SET LOCAL`) per transaction.
   Details and the `NULLIF` fail-closed finding: `data-model.md` §8.
4. No process-level caches keyed without org (none exist in Phase 1).
5. Tests (`backend/tests/test_rls.py`, `test_integrity.py`): Org A cannot select,
   select-by-ID, update or insert Org B rows in any table; with no org bound nothing is
   visible (also for the table owner on tenant tables); the binding does not survive
   COMMIT, ROLLBACK or a database error on a reused pooled connection; 40 interleaved
   concurrent transactions for two orgs never see each other's rows. The two test
   orgs share a customer account name and key. Document/ticket-level isolation tests
   arrive with those tables (Phases 4–6).

Mutation checks performed in Phase 1: replacing the tenant policy with `USING (true)`
fails 31 tests; removing `NULLIF` from `lattice_current_org_id()` fails every
"no organization context" test.

## 4. Authentication & sessions

See `authorization-model.md` §2. Implemented in Phase 1:

- 256-bit random token in the cookie; only its SHA-256 stored (tested by scanning the
  stored row for the raw and hex-encoded token).
- Every request re-checks: not revoked, before absolute expiry (7 d), not idle longer
  than 12 h (`last_seen_at` refreshed at most once a minute), and organization, user
  and membership all `active`. All failures return the identical
  `authentication_required` response and clear the cookie.
- Session rotation: signing in revokes any session presented with the request.
- Logout revokes server-side (a replayed token fails).
- Cookie: `HttpOnly; SameSite=Lax; Path=/`, no `Domain`; in production also `Secure`
  with the `__Host-` prefix (configuration refuses insecure cookies in production).
- Security headers on every response: `Content-Security-Policy: default-src 'none';
  frame-ancestors 'none'` (the dev-only API docs pages are exempt),
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: same-origin`, `Cache-Control: no-store`, and HSTS in production.
  The web app's own CSP (with its scripts) is defined in Phase 7.

CSRF (implemented): every non-GET/HEAD/OPTIONS request must send
`X-Lattice-Request: 1`, and requests marked `Sec-Fetch-Site: cross-site` are rejected
even with the header. It applies to all routes, including sign-in (login CSRF) and
logout, and is checked before authentication.

## 5. Secrets and configuration

- Secrets only via environment (local `.env`, never committed; `.env.example` has
  documented local-only placeholders). Deployment uses the platform's secret store.
- Database URLs are `SecretStr`: never in `repr`, logs or error messages. Configuration
  errors name the variable and the problem but never echo the value.
- Provider API keys (Phase 4+) are read by `providers` only and never logged, never
  placed in prompts, never returned by any endpoint.
- Startup validation (implemented) rejects: missing/invalid `LATTICE_*` settings;
  `LATTICE_ENV` unset; `LATTICE_ENV=production` with dev auth enabled or insecure
  cookies; a runtime DB role that is a superuser, has `BYPASSRLS`, is a member of such
  a role, or owns/can act as owner of application tables; any application table
  without forced RLS; an unmigrated schema. Dev login additionally is not routed in
  production and its provider refuses to construct there.
- In production the OpenAPI/docs endpoints are not served.

## 6. Model-provider data egress

Being authorized inside Lattice does not automatically mean content should leave
Lattice. The provider boundary is governed separately.

### What leaves Lattice

| Call | When | Data sent | Not sent |
| ---- | ---- | --------- | -------- |
| Embedding (documents) | Ingestion | Chunk text + contextual header (document title, kind, heading path, account name, date) — only for documents at or below the embedding provider's effective egress level (default: `internal`) | Confidential/restricted content under the default policy; internal IDs, access metadata, authors' emails |
| Embedding (query) | Each Ask | The question text | User identity, org name |
| Generation | Each Ask with evidence | Fixed instructions; the question; selected evidence at or below the effective egress level (default: `internal`; ≤ ~12 chunks, ≤ ~6k tokens) with handle, kind, title, display ref, account name, date, author display name | Evidence above the egress level; UUIDs, user identity/profile, org metadata, authz structures, sensitivity labels, unrelated evidence, secrets |

Note the asymmetry: **the embedding provider eventually receives the entire indexed
*internal* corpus**, while the generation provider receives only small,
question-specific excerpts. The embedding provider choice is therefore the larger egress decision
(open decision D-2).

### Egress policy (distinct from authorization)

Authorization answers *"may this person read this?"*. Egress policy answers *"may
this content be sent to this model provider?"*. They are evaluated separately and
the product never conflates them: a user may be fully authorized to read a
confidential document inside Lattice while the organization forbids sending it to
its external model provider.

**Default policy**

| Sensitivity | Sent to an approved external provider? |
| ----------- | -------------------------------------- |
| `internal` | Yes |
| `confidential` | **No** — unless the organization explicitly opts in *and* the configured provider is approved for `confidential` |
| `restricted` | **No** by default — same explicit opt-in + approval required |

**Mechanics**

- `org_settings.external_egress_level` ∈ {`internal`, `confidential`, `restricted`},
  default **`internal`**.
- Each configured provider record carries `approved_for_level` (default `internal`)
  and a `deployment` of `external_api` or `private` (private cloud endpoint /
  self-hosted). The effective level for a call is
  `min(org external_egress_level, provider.approved_for_level)`.
- Raising either value is an explicit admin action with a stated reason, recorded as
  an audit event and shown on the provider settings page. There is no implicit path
  (no "leadership users may send confidential content" shortcut).
- **Generation:** authorized evidence above the effective level is removed from the
  prompt only. It stays in the user's evidence list, labelled **"Not used to draft
  this answer"**, with a notice naming the reason: "2 Confidential sources you can
  access weren't sent to the AI model because of Halden's AI data policy. Review
  them below." If the excluded evidence is *newer* than all evidence used, the
  notice says so ("…including the most recent evidence"). If all relevant evidence
  is excluded, no model call is made and the answer status is `evidence_only`.
- **Embedding:** records above the effective level of the embedding provider are
  indexed lexically only (embedding NULL); they still participate in retrieval
  through FTS and the identifier path. Consequence: under the default policy,
  confidential/restricted documents have keyword-only recall (measured separately
  in evals).
- **Questions** are user-authored and always sent to the configured providers (query
  embedding and generation). Lattice cannot classify free text; this is stated in
  the provider settings page and the privacy documentation.
- Provider configuration records: provider name, endpoint, model, deployment,
  approved level, data-retention mode, training opt-out status. Visible to admins.

### Provider requirements

- Commercial API terms under which inputs are not used for training (the default for
  major providers' commercial APIs — verify at contract time).
- Zero-data-retention where the provider offers it and the chosen model supports it
  (some frontier models require standard retention; verify per model before
  enabling). Document the effective retention per provider in the runbook.
- The `GenerationProvider`/`EmbeddingProvider` Protocols accept any endpoint, so an
  org can use an approved external provider, a private cloud endpoint, or a
  self-hosted model without changes to retrieval or answering.

## 7. Prompt-injection resistance

See `trust-boundaries.md` §4. Architecture, not prompt wording, carries the weight:
no tools, only permitted evidence, schema-constrained output, handle-only citations,
verbatim quote verification, plain-text rendering.

## 8. Traces, audit and debugging

Administrative capability is not content access (SEC-15). In the first release
there is **no** capability for anyone to read another member's question, answer or
evidence (D-5: off/deferred).

- Traces store references, scores and timings; the asker's question and answer text
  are stored under retention (default 90 days) for the asker's own history.
- **Asker's view of their own trace:** question, answer, evidence and stages.
  Evidence references are re-authorized against the asker's *current* access. If any
  evidence the answer cited is no longer readable, the stored answer text is hidden
  ("This answer cited sources you no longer have access to") — otherwise traces would
  let revoked users re-read derived content.
- **Admin's operational view of other members' traces:** member, time, status,
  error code, stage durations, provider/model, prompt and policy versions, token
  usage. **Not shown:** question text, answer text, evidence references, candidate or
  evidence counts, account or source names, scope filters. (Counts and scope values
  are excluded because, combined with timing and the member's identity, they can
  reveal which accounts or sources were involved.)
- If a real customer later requires support/audit access to content, it will be
  designed as a new, explicit capability: least privilege, explicit per-grant
  authorization, audit trail of every view, clear UI, and content re-authorization.
  Not in the first vertical slice.
- Access-management changes (grants, clearance, assignments, group edits, egress
  policy changes) are recorded in an append-only `audit_events` table (Phase 9) with
  actor, target, before/after, reason.

## 9. Security testing strategy

| Suite | Runs | Contents |
| ----- | ---- | -------- |
| `tests/security/isolation` | CI, every PR | Cross-org matrix over every read endpoint and CLI search (Phase 1: table-level isolation in `tests/test_rls.py`, now covering the Phase 2 tables) |
| `tests/security/test_authz_equivalence.py` ✔ | CI | Property-based SQL ⇔ reference policy (500 examples) |
| `tests/security/test_authz_personas.py` ✔ | CI | Halden persona matrix with explicit denials; account visibility; filters only narrow |
| `tests/security/test_admin_not_content_access.py` ✔ | CI | SEC-15 at the policy layer: no capability field in the context; toggling admin changes no one's access; no explanation oracle |
| `tests/security/test_authz_fail_closed.py` ✔ | CI | Unknown/malformed/cross-tenant state denied, including with the schema's guards removed |
| `tests/security/test_authz_explain.py`, `test_authz_invariant.py`, `test_authz_context.py` ✔ | CI | Explanation semantics; E4 aborts and never filters; context resolution |
| `tests/security/critical_acme` | CI | The permanent scenario (`authorization-model.md` §10) |
| `tests/security/canary` | CI | Recording fake providers capture every payload; canary strings from restricted/confidential docs must never appear for unauthorized personas — in payloads, responses, traces or captured logs |
| `tests/security/test_authz_revocation.py` ✔ | CI | Each access change type → next check differs, no document rewritten; snapshot consistency |
| `tests/security/injection` | CI (deterministic parts) + eval harness (real model) | Schema validity, foreign-handle rejection, no instruction following |
| `tests/security/citations` | CI | Claim-level fail-closed: invalid handle → claim absent; fabricated quote → claim absent; mixed valid/invalid → only validated claims survive (and a claim with one valid + one invalid citation is absent); all material claims invalid → answer withheld (`insufficient_evidence`, reason `claims_unverified`); every rendered citation references an evidence item present in the response and readable by the user |
| `tests/security/egress` | CI | Default policy: recording provider never receives confidential/restricted chunk text or canaries, for any persona including Leadership; opt-in + approved provider: confidential reaches the provider, restricted still does not; excluded evidence still returned to the authorized user with the egress notice; `evidence_only` when all evidence excluded |
| `tests/security/operational_metadata` | CI | Every channel in §10 checked for each persona/admin: no inaccessible account names, source names, titles, filenames, URLs, external IDs, counts or statuses |
| Mutation check | CI nightly | Predicate disabled → suite must fail |

Release gate: any failure in these suites blocks merge. `unauthorized_context_retrieved`
in the eval harness must be exactly 0.

## 10. Operational metadata side channels

Operational access is not content access, and the *existence* of an account, source,
channel or document can itself be sensitive. Rules per channel:

| Channel | Members | Admins |
| ------- | ------- | ------ |
| **Account lists** (scope picker, account-mention detection, ambiguity choices) | Only accounts inside their account scope (`all` → all accounts; `assigned` → assigned only). An unassigned account named in a question is treated as plain text: no "Interpreted as", no boost, no ambiguity choice | The account directory, because assigning accounts is the admin's job. Account *content* is not visible. (Hiding an account's existence from admins is a future "restricted account" capability, not in the slice) |
| **Source names / list** | Only sources in which they can read ≥ 1 document | All sources (admins configure them) |
| **Source counts** | Computed inside their authorization predicate | Per-source aggregate totals only (documents, failures, last sync). **No** breakdown by account, sensitivity, department, group or document |
| **Ingestion status / "source out of date" notices** | Only for sources they can read | All sources |
| **Sync failure messages** | Not shown | Stage + error code + templated message (never free text from the record). The record's external ID / filename / title / URL is shown only if the admin can read the corresponding active document; otherwise an opaque reference (`rec_7f3a…`, a keyed hash) is shown. Raw identifiers are available only to infrastructure operators via CLI with database access. One exception: `access_unmappable:account` shows the unmapped *external account reference*, because mapping it is the admin's task and the account directory is already admin-visible — never the record's title or content |
| **Filenames, titles, external IDs, URLs** | Only for readable documents (evidence, inspector) | Same rule — admin pages never show document-level metadata for documents the admin can't read |
| **Access groups** (e.g. private channels) | Their own memberships only | Admin-created groups: name + members. Source-synced groups: name shown only if the admin is a member; otherwise "Private channel (synced from Slack)" + opaque reference + member list (needed to explain a member's access) |
| **Access-change previews** | — | Counts only over documents the admin can read; beyond that, effects are described as rules ("Maya will lose access to all Kestrel Bank content"), without counts |
| **Filters / facets** | Values and counts computed inside the predicate; source-type options only for types with readable documents | Same |
| **Autocomplete** | None in the slice. Any future suggestion (accounts, titles, people) must be computed inside the predicate and visibility rules above | Same |
| **Trace structure** | Own traces (§8) | Operational view without counts, scope, account or source names (§8) |
| **"No evidence" / refusal responses** | Identical for nonexistent and unreadable (SEC-5); "what was searched" lists only their own scope | — |
| **Logs** | — | IDs, codes, durations only (SEC-8); document IDs in logs are UUIDs, never titles/filenames |
