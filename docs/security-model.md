# Lattice — Security Model

Status: Foundation draft (Phase 0). Implemented so far: §3–5 (Phase 1) and the
authorization controls behind SEC-3, SEC-4, SEC-5 (explanations), SEC-14 (account
visibility, including D-8 structured account metadata) and SEC-15 at the policy layer
(Phase 2); see §9 for which suites exist. The Phase 3 synthetic dataset places 19
canaries for the canary suites (`data/synthetic/canaries.yaml`). Phase 4 implemented
the embedding side of §6; **Phase 5** implemented E3 in every retrieval path (SEC-3),
the query side of §6 (revised below), and the retrieval suites of §9. **Phase 6**
implemented generation under the same invocation rule (§6), organization-owned
credentials by reference (§5), prompt-injection defences (§7), query traces and their
re-authorized views (§8), and the citation, egress, injection and API suites (§9).
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
Phase 2 performed this and eleven other policy mutations by hand; Phase 5 removed the
predicate from each retrieval path in turn (and 15 other retrieval defects) -- every
one is caught; results are in `implementation-plan.md`. An automated nightly mutation
job is still planned (Phase 11).

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
  placed in prompts, never returned by any endpoint. Gemini (Phase 5 closure) reads
  `LATTICE_GEMINI_API_KEY` or Google's `GEMINI_API_KEY` and sends it only in the
  `x-goog-api-key` header (never in a URL). Today these are process-wide variables for
  operator commands. Organization-owned credentials (BYOK: encrypted at rest or in a
  secret manager, never returned or logged, masked in the UI, rotatable and revocable)
  are the target (ADR-011). **Phase 6 built the backend minimum (D-16):** a
  `provider_credentials` row holds only a *secret reference* (`env:LATTICE_PROVIDER_SECRET_<NAME>`
  or a documented legacy provider variable; the schema's CHECK and the code refuse
  anything else, so a reference can never name the database URL or another secret of
  the process), the key's declared data-use tier and its lifecycle (`active`/`revoked`,
  one active per organization and provider, history kept). A `SecretStore`
  (`lattice.providers.secrets`) resolves the reference only after the invocation rule
  has allowed the call; the value is a `SecretStr` end to end and never appears in API
  responses (the admin view says only whether a credential is configured and
  resolvable), logs, traces, prompts, errors or audit events (tested). Rotation,
  revocation and disablement take effect on the next request. A production secret
  manager is another scheme behind the same Protocol (not built). The embedding
  providers of the operator CLI and `LATTICE_QUERY_EMBEDDING` still read the Phase 5
  process variables.
- A credential is never an approval: whether a provider may receive anything is the
  egress policy's decision (§6), and it does not consult credentials.
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
| Embedding (documents) | Ingestion | Chunk text + contextual header (document kind, title, heading path — D-10: no account name) — only for documents at or below the embedding provider's effective egress level (default: `internal`) | Confidential/restricted content under the default policy; structured account names; internal IDs, display references, access metadata, authors' emails (Phase 4: asserted payload-for-payload with a recording provider) |
| Embedding (query) | Each retrieval (Phase 5) whose query the policy allows (below) | The normalized question text, `purpose = query` | User identity, org name, scope, any document content; nothing at all when the policy excludes the query |
| Generation | Each Ask with evidence | Fixed instructions; the question; selected evidence at or below the effective egress level (default: `internal`; ≤ ~12 chunks, ≤ ~6k tokens) with handle, kind, title, display ref, account name, date, author display name | Evidence above the egress level; UUIDs, user identity/profile, org metadata, authz structures, sensitivity labels, unrelated evidence, secrets |

Note the asymmetry: **the embedding provider eventually receives the entire indexed
*internal* corpus**, while the generation provider receives only small,
question-specific excerpts. The embedding provider choice is therefore the larger egress decision
(decision D-2; ADR-008: OpenAI `text-embedding-3-small`, approved for `internal`). A
`local` provider (ADR-009) removes this egress entirely.

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

**Deployment trust classes (D-15, final Phase 5 closure).** Every provider record
names where inference runs, as one of four classes. The word "private" carries no
security meaning in Lattice and is not a class.

| Class | Meaning | What it never implies |
| ----- | ------- | --------------------- |
| `local` | Inference runs inside the Lattice deployment boundary; no inference content leaves it (e.g. `LocalEmbeddingProvider`) | Unrestricted access: the reader's authorization, the organization's policy (including D-13) and an explicit approval for anything above `internal` still apply |
| `organization_controlled` | Inference runs outside the Lattice process/deployment, on infrastructure or an endpoint the organization controls or approved (its own model server, a private cloud endpoint). It may involve network transmission | Trust by default: nothing is sent until the organization allows the class with an explicit ceiling *and* an administrator approves the record explicitly. No implicit `confidential` or `restricted`; calling something "private" is never sufficient |
| `external_api` | A third-party provider outside the organization's controlled boundary (Gemini, OpenAI, Anthropic, Voyage) | That a credential authorizes anything: external egress level, provider approval, data-use and retention terms all apply |
| `unknown` | A missing, invalid or unrecognised classification (including the pre-D-15 label `private`) | Anything: **fail closed**, no question, chunk or evidence text is sent |

The class comes from the organization's record and the implementation's own
declaration, never from a provider's brand name. Where they differ, nothing is sent.

**Mechanics** (the invocation rule, ADR-011; `lattice.providers.egress`)

A provider receives content -- document chunks at ingestion, the question at
retrieval, and (Phase 6) evidence and the prompt -- only if **all** hold:

1. the user is authorized for the content (decided first, in SQL; egress only narrows);
2. the provider is recorded for *this* organization (`model_providers`, under
   row-level security; another organization's record never applies), with a known
   trust class that equals the class its implementation declares
   (`provider_unapproved`, `deployment_unknown`, `provider_deployment_mismatch`);
3. the organization allows the class, and the content is within the class ceiling
   (`deployment_not_allowed` otherwise);
4. the provider configuration is approved for the content's sensitivity
   (`model_providers.approved_for_level`). An `organization_controlled` record also
   needs an explicit administrator approval (`approved_at`);
5. its data-use and retention terms are acceptable for the organization's data
   (`provider_data_use` otherwise);
6. valid configuration exists, and valid credentials where the provider needs them.

A valid key satisfies only item 6. It never authorizes sending anything, never raises
a ceiling or an approval, and never turns `external_api` into
`organization_controlled` or `organization_controlled` into `local`. **Credential
availability ≠ provider approval ≠ data-egress authorization.**

- `org_settings.external_egress_level` ∈ {`internal`, `confidential`, `restricted`},
  default **`internal`**. `org_settings.organization_controlled_level` (D-15): the
  same levels or NULL, default **NULL (class not allowed)**.
  `org_settings.default_query_sensitivity` (D-13), default `internal`.
  `org_settings.data_origin` ∈ {`customer`, `synthetic`}, default **`customer`** (set
  to `synthetic` by the seed for Halden and Veridian only).
- Each configured provider record carries `approved_for_level` (default `internal`),
  `approved_at` (set only by an explicit approval), a `deployment` trust class, a
  `retention_mode` and `training_opt_out`. The database accepts only `local`,
  `organization_controlled` and `external_api` (migration 0007). The effective level:

  | Class | Effective level |
  | ----- | --------------- |
  | `external_api` | `min(external_egress_level, approved_for_level)` |
  | `organization_controlled` | `min(organization_controlled_level, approved_for_level)`, only when `organization_controlled_level` is set **and** `approved_at` is set; otherwise nothing |
  | `local` | `approved_for_level` (nothing leaves the deployment; the *external* level does not apply) |
  | `unknown` | nothing |

- **Data use.** A provider that may use submitted content to improve its products
  (`training_opt_out = false`; today an unpaid-tier Gemini key, ADR-010) may receive
  content only from a `synthetic` organization. For a `customer` organization it
  receives nothing: no chunk and no question. Where the provider's declaration and its
  record differ, the more restrictive reading wins. Such a key is also refused
  outright when `LATTICE_ENV=production`.
- Raising any of these values is an explicit admin action with a stated reason, recorded as
  an audit event and shown on the provider settings page. There is no implicit path
  (no "leadership users may send confidential content" shortcut). Implemented today as
  the operator command `lattice provider approve --org … --provider … --model …
  --level … --reason …`, which sets `approved_at` and logs `provider.approval_changed`.
  The organization-level ceilings have no operator command yet (set by the owner role;
  no `organization_controlled` provider exists in Phase 5). The settings API, page and
  audit table come with Phase 6/7 (ADR-011, D-16).
- **Generation (implemented, Phase 6; `lattice.answering.policy`):** the organization's
  one enabled generation configuration is resolved per request (never a global key),
  the invocation rule above is applied to it, the question's effective sensitivity
  (D-13) must be within the level or the question is not sent at all (`evidence_only`,
  `question_classification`), and only then is the credential reference resolved and
  the adapter built. Authorized evidence above the effective level is removed from the
  prompt only. It stays in the user's evidence list, labelled **"Not used to draft
  this answer"**, with a notice naming the reason: "2 Confidential sources you can
  access weren't sent to the AI model because of Halden's AI data policy. Review
  them below." If the excluded evidence is *newer* than all evidence used, the
  notice says so ("…including the most recent evidence"). If all relevant evidence
  is excluded, no model call is made and the answer status is `evidence_only`.
- **Embedding (implemented, Phase 4; deployment rule Phase 5 closure):** records above
  the effective level of the embedding provider are indexed lexically only (embedding
  NULL, `embedding_status = 'policy_excluded'`). They still participate in retrieval
  through FTS and the identifier path. A classification raised above the level removes
  the vectors in the same transaction as the access change. A lowered org level or
  provider approval removes them on the next sync of each source. A provider used for
  the first time is recorded in `model_providers` approved for `internal` only.
  Consequence: under the default policy, confidential/restricted documents have
  keyword-only recall with an external provider (measured separately in evals). A
  `local` provider approved for `restricted` embeds them without any egress.
- **Questions (D-13, resolved in the Phase 5 closure).** The question itself can carry
  confidential information, so query embedding is an egress decision too. Its
  effective sensitivity is
  `more_restrictive(org_settings.default_query_sensitivity, the asker's declaration)`.
  A user, client, provider key or provider choice can raise it, never lower it, and
  unrecognised values count as `restricted`. The question is sent only if items 1–6
  above hold for it at that sensitivity. Otherwise nothing is sent and semantic
  retrieval is `policy_excluded` (`query_classification`, `provider_unapproved`,
  `deployment_unknown`, `provider_deployment_mismatch`, `deployment_not_allowed`,
  `provider_data_use`). Exact and lexical retrieval
  continue. A `local` provider approved for the sensitivity may still embed it; the
  question is never silently sent to another external provider. The effective
  sensitivity is returned with every retrieval (`question_sensitivity`, also in the
  trace). **Phase 6 generation must apply it**: a question that may not be sent is not
  put in an external prompt (then `evidence_only`, no model call). Tested with
  recording providers and mock transports:
  `backend/tests/retrieval/test_question_sensitivity.py`,
  `test_semantic_degradation.py`; the trust classes (D-15) in
  `backend/tests/providers/test_trust_classes.py`,
  `backend/tests/retrieval/test_trust_classes.py` and
  `backend/tests/ingestion/test_provider_eligibility.py`.
- Provider configuration records: provider name, endpoint, model, deployment,
  approved level, data-retention mode, training opt-out status. Visible to admins.

**Providers (Phase 5 closure)**

| Provider | Deployment | External egress | Current purpose |
| -------- | ---------- | --------------- | --------------- |
| Fake | test double (declared `external_api`, so tests exercise the external rule; no network) | no | tests and CI only; refused in production |
| Local (`mxbai-embed-large-v1`, ADR-009) | `local` | no | real zero-cost / self-hosted semantic retrieval; offline deployments |
| Gemini (`gemini-embedding-2`, ADR-010) | `external_api` | yes | optional external provider / BYOK; the current unpaid key is synthetic-only |
| OpenAI (ADR-008) | `external_api` | yes | optional managed / BYOK provider; real evaluation not run |
| Organization-controlled endpoint (future) | `organization_controlled` | to the organization's own endpoint | customer-owned model infrastructure; explicit organization policy required |

This table says where inference runs. Whether a provider may receive a given piece of
content is only ever the policy's answer above, never a conclusion from its name.

### Provider requirements

- Commercial API terms under which inputs are not used for training (the default for
  major providers' commercial APIs — verify at contract time). A provider or tier that
  does not meet this (for example the Gemini API's unpaid tier, whose content Google
  uses to improve its products) is recorded with `training_opt_out = false` and is
  confined to synthetic organizations by the policy above.
- Zero-data-retention where the provider offers it and the chosen model supports it
  (some frontier models require standard retention; verify per model before
  enabling). Document the effective retention per provider in the runbook.
- The `GenerationProvider`/`EmbeddingProvider` Protocols accept any endpoint, so an
  org can use an approved external provider, an `organization_controlled` endpoint, or a
  self-hosted model without changes to retrieval or answering.

## 7. Prompt-injection resistance

See `trust-boundaries.md` §4. Architecture, not prompt wording, carries the weight:
no tools, only permitted evidence, schema-constrained output, handle-only citations,
verbatim quote verification, plain-text rendering.

As built (Phase 6): fixed versioned instructions (`answer-prompt-1`) that name the rules;
the question and every evidence block inside delimiters ending in a per-request random
nonce, so retrieved text cannot close its block; request-local handles (no IDs); no tools
in any provider payload (asserted per adapter); claims containing URLs, web or email
addresses, markdown or HTML rejected; model output never feeds authorization, retrieval,
the organization or provider configuration. `tests/answering/golden/test_golden_answers.py`
runs a model that *obeys* every injected instruction in the corpus (cite HAL-9999, load
the exfiltration image, confirm the planted approval, list confidential documents,
record a contractual date) and proves none of it reaches the asker.

**Retrieved content is untrusted data, never model instructions** -- a hard Phase 6
requirement with adversarial tests, listed in `trust-boundaries.md` §4.

## 8. Traces, audit and debugging

Administrative capability is not content access (SEC-15). In the first release
there is **no** capability for anyone to read another member's question, answer or
evidence (D-5: off/deferred).

- Traces store references, scores and timings; the asker's question and answer text
  are stored under retention (default 90 days) for the asker's own history. **As built
  (Phase 6):** `query_traces` (one INSERT per Ask as the runtime role, which has no
  UPDATE or DELETE) holds versions, the egress decision and reason code, validation
  counts and codes, statuses, timings, token counts, chunk IDs of permitted rows and the
  validated answer. Never the prompt, evidence text, rejected claim text, provider
  bodies, secrets or references. `GET /v1/traces/{id}` implements the three views below.
  Retention jobs are Phase 11.
- **Asker's view of their own trace:** question, answer, evidence and stages.
  Evidence references are re-authorized against the asker's *current* access. If any
  evidence of that Ask is no longer readable -- cited or not, since supersession items
  and notices name uncited documents -- the stored answer text is hidden
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
| `tests/security/test_account_metadata.py`, `test_access_group_key.py` ✔ | CI | D-8: structured account metadata only when readable and visible (reference and SQL join form); D-9: group keys never affect access |
| `tests/dataset/` ✔ | CI | The synthetic dataset and golden set validated against the reference policy; canaries unique and placed; the dataset's access oracle equals the seeded resolver |
| `tests/retrieval/golden/test_critical_acme.py` ✔ (retrieval half) | CI | The permanent scenario (`authorization-model.md` §10): Doc B absent from every stage for Maya, retrieved for Grace, revocation on the next request. Payload, response and 404 halves arrive with Phase 6 |
| `tests/retrieval/` ✔ (Phase 5) | CI | Each statement's WHERE clause structurally contains the reader's predicate, tenant and lifecycle conditions; adversarial strongest-match cases per channel; cross-org identifier collisions; D-8/D-10; query egress and semantic degradation with a recording provider; E4 aborts and never filters; snapshot consistency; no existence oracle (results identical with or without an unreadable match); a 200-world property test, also with RLS bypassed; raw SQL capture (the database returns only permitted rows, retrieval keeps exactly those); all 89 golden items with zero violations |
| `tests/answering/` ✔ (Phase 6) | CI | `test_validate_prompt.py` (every claim-rejection code, one bad citation rejects the claim, normalization not fuzzy, nonce delimiters, no IDs in prompts), `test_ask.py` (statuses, regeneration once, mixed claims, rejected text nowhere, supersession/potential conflicts, trace content), `test_generation_policy.py` (the invocation rule for generation: levels, approvals, D-13, trust classes, credentials last, rotation/revocation, cross-org configuration), `test_api.py` (contract, 404 equality, trace views, masked admin view), `golden/test_golden_answers.py` (every golden item through the harness's security gate, the critical Acme scenario's payload/response/lookup halves, an obedient model versus the injection fixtures); `tests/test_provider_configuration.py` (audit, append-only, references only) |
| `tests/security/canary` | CI | Recording fake providers capture every payload; canary strings from restricted/confidential docs must never appear for unauthorized personas — in payloads, responses, traces or captured logs |
| `tests/security/test_authz_revocation.py` ✔ | CI | Each access change type → next check differs, no document rewritten; snapshot consistency |
| `tests/security/injection` | CI (deterministic parts) + eval harness (real model) | Schema validity, foreign-handle rejection, no instruction following |
| `tests/security/citations` | CI | Claim-level fail-closed: invalid handle → claim absent; fabricated quote → claim absent; mixed valid/invalid → only validated claims survive (and a claim with one valid + one invalid citation is absent); all material claims invalid → answer withheld (`insufficient_evidence`, reason `claims_unverified`); every rendered citation references an evidence item present in the response and readable by the user |
| `tests/security/egress` | CI | Default policy: recording provider never receives confidential/restricted chunk text or canaries, for any persona including Leadership; opt-in + approved provider: confidential reaches the provider, restricted still does not; excluded evidence still returned to the authorized user with the egress notice; `evidence_only` when all evidence excluded |
| `tests/security/operational_metadata` | CI | Every channel in §10 checked for each persona/admin: no inaccessible account names, source names, titles, filenames, URLs, external IDs, counts or statuses |
| Mutation check | By hand per phase (Phase 5: 18 retrieval defects, `implementation-plan.md`); CI nightly job still planned | Predicate disabled → suite must fail |

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
| **Structured account metadata of readable documents** (decision D-8) | The account field of an evidence card or inspector, the `account` attribute of prompt evidence, account facets: only when the account is inside the member's scope (`authz.visible_document_account`). A document readable through an explicit grant on an out-of-scope account shows no account at all — no substitute name — and the grant does not put the account into lists, filters, mention detection or autocomplete. Text *inside* the document that names the customer is content, readable because the document is | Same rule for their own evidence |
| **Access groups** (e.g. private channels) | Their own memberships only | Admin-created groups: name + members. Source-synced groups: name shown only if the admin is a member; otherwise "Private channel (synced from Slack)" + opaque reference + member list (needed to explain a member's access) |
| **Access-change previews** | — | Counts only over documents the admin can read; beyond that, effects are described as rules ("Maya will lose access to all Kestrel Bank content"), without counts |
| **Filters / facets** | Values and counts computed inside the predicate; source-type options only for types with readable documents | Same |
| **Autocomplete** | None in the slice. Any future suggestion (accounts, titles, people) must be computed inside the predicate and visibility rules above | Same |
| **Trace structure** | Own traces (§8) | Operational view without counts, scope, account or source names (§8) |
| **"No evidence" / refusal responses** | Identical for nonexistent and unreadable (SEC-5); "what was searched" lists only their own scope | — |
| **Logs** | — | IDs, codes, durations only (SEC-8); document IDs in logs are UUIDs, never titles/filenames |
