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
**Phase 8** implemented the operational-metadata rules of §10 for Sources, sync
failures, requested syncs and Activity, with their side-channel suite; the worker's
least-privilege role (ADR-013); and fail-closed operational controls (§5). **Phase 10**
implemented OIDC sign-in and SCIM provisioning scoped to each organization (§4, ADR-015),
the access audit enforced by the database (§8), rate limits (§11), the security-headers
review (§4) and the route-inventory isolation sweep (§9).
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

**Phase 10 headers review.** The API adds `Cross-Origin-Opener-Policy: same-origin`,
`Cross-Origin-Resource-Policy: same-origin` and a restrictive `Permissions-Policy`; it
grants no CORS. The web app sets its Content-Security-Policy per request in
`apps/web/src/proxy.ts`: `script-src 'self' 'nonce-<fresh>' 'strict-dynamic'` (no
`'unsafe-inline'`; `'unsafe-eval'` only in development), `object-src 'none'`,
`frame-ancestors 'none'`, `base-uri`/`form-action 'self'`; `style-src` keeps
`'unsafe-inline'` for inline style attributes. Every page renders per request so
Next.js can put the nonce on its scripts. Also COOP, Permissions-Policy and HSTS in
production. Verified in Playwright (every script carries the nonce, a fresh nonce per
load, no violation).

**OIDC sign-in (Phase 10, ADR-015, D-1).** Authorization code with PKCE (S256), `state`,
`nonce`, and a browser-binding cookie (`__Host-lattice_oidc`, HttpOnly, SameSite=Lax,
ten minutes); the attempt is stored server-side (hashes) and consumed before anything
else. The sign-in URL carries an opaque login key, never the organization (pre-auth
lookup `lattice_oidc_connection`). ID tokens are verified against the provider's JWKS
with an allow-list of asymmetric algorithms matched to the key type (never `none` or
HMAC), `iss` (also checked in discovery), `aud`/`azp`, `exp`/`iat` (60 s leeway) and the
`nonce`. The configured claim maps to a *directory identity* of that connection in that
organization only; no e-mail linking, no just-in-time membership. Every failure is one
redirect (`/sign-in?error=sso_failed`; `sso_unavailable` when the provider cannot be
reached) with a fixed outcome in metrics and logs; nothing from the provider is logged.
A successful sign-in rotates the session like any other.

**SCIM provisioning (Phase 10, D-20).** `/scim/v2/Users` with a per-organization bearer
token (SHA-256 stored; pre-auth lookup `lattice_resolve_scim_token`). The provider owns
user name, e-mail, name and `active`; no access attribute is ever read. Provisioned
people are users owned by the organization, so they cannot collide with, reveal or
modify another organization's users. New members get least access; suspension and
removal revoke sessions in the same transaction and take effect on the next request
everywhere (sessions, Ask, retrieval, traces, Access views). Identical requests are
no-ops; `If-Match` supported; every change audited (actor `scim:<token>`).

**Connection lifecycle and incident controls (Phase 11, P11.3; D-25, D-26).** A session
records the OIDC connection whose sign-in created it (`sessions.oidc_connection_id`, NULL
for development sign-in). `lattice sso disable` revokes every unrevoked session of the
connection in the same transaction as the status change. The race with a sign-in in
flight is closed by a per-connection advisory lock: session creation for an OIDC sign-in
holds it *shared* while it re-reads the connection's status (`lattice_app` may read only
`id`, `organization_id` and `status` of its own organization's connections, migration
0014) and inserts the session; the disable (and an identity-shape `sso update`) holds it
*exclusively*, so a sign-in either commits first and is revoked, or waits and sees
`disabled`. `sso enable` re-activates the same connection (same directory identities);
`sso update` changes the client ID or secret reference in place without touching
sessions, and the issuer or identifying claim only with the operator's explicit
`--subjects-unchanged` assertion (then revoking the connection's sessions). `sso add`
refuses to start a new identity namespace while a disabled connection still has
provisioned members, unless told so. Operator incident commands replace owner-role SQL:
`lattice member suspend|reinstate`, `lattice session revoke`, `lattice org
suspend|reactivate` — each one transaction with one audit event (`operator:<user>`, a
reason, no secret or reference). A suspended organization's sessions are revoked, its
sign-in and SCIM fail (the pre-auth lookups require an active organization), and the
worker runs none of its syncs. Verified in-process across two API applications and on
the reference stack across both API instances (runbook §8, P11.3).

CSRF (implemented): every non-GET/HEAD/OPTIONS request must send
`X-Lattice-Request: 1`, and requests marked `Sec-Fetch-Site: cross-site` are rejected
even with the header. It applies to all routes, including sign-in (login CSRF) and
logout, and is checked before authentication. SCIM (`/scim/v2/`) is the one exception
to the header rule: it is authenticated by a bearer token in `Authorization` only and
never reads the cookie, which a cross-site page can neither send nor read; cross-site
fetch metadata is still refused there.

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
- **Phase 12 P12.1:** a credential may also be stored encrypted at rest (`vault:<id>`,
  AES-256-GCM, §12); the database never holds a credential in plaintext nor a master key.
- A credential is never an approval: whether a provider may receive anything is the
  egress policy's decision (§6), and it does not consult credentials.
- Startup validation (implemented) rejects: missing/invalid `LATTICE_*` settings;
  `LATTICE_ENV` unset; `LATTICE_ENV=production` with dev auth enabled or insecure
  cookies; a runtime DB role that is a superuser, has `BYPASSRLS`, is a member of such
  a role, or owns/can act as owner of application tables; any application table
  without forced RLS; an unmigrated schema. Dev login additionally is not routed in
  production and its provider refuses to construct there.
- In production the OpenAPI/docs endpoints are not served.
- **Phase 8 startup and runtime controls.** The API and the worker refuse to start on a
  schema at another revision than their code, and the API refuses a role that can write
  documents or grants (the worker's credential cannot be used for the API). Production
  also refuses the fake query and sync embedding providers. Runtime connections carry
  a statement timeout and fail fast when the database is unreachable (503 with
  `Retry-After`, never a stack trace or a hang); request bodies are capped (413); one
  Ask's generation calls share a deadline. `LATTICE_GENERATION_ENABLED=false` stops
  every model call deployment-wide (no credential resolved, nothing sent); the
  per-organization stop is still `lattice provider disable`.
- **Phase 10.** Production also requires `LATTICE_PUBLIC_URL` (https), from which OIDC
  redirect URIs and the SCIM base URL are built. An OIDC client secret is only ever a
  reference `env:LATTICE_OIDC_SECRET_<NAME>` (a separate namespace from provider
  credentials, refused by the schema and the code otherwise), resolved at the token
  exchange. httpx's own request logging is set to WARNING (it logs URLs).
- **Operational signals never carry content.** Health endpoints report states only.
  Metrics are served on a separate listener that the web app never proxies, and every
  label comes from a closed set: no organization, member, trace, document or source
  identifier, no text, no secret (`lattice.metrics`, tested). Logs keep the SEC-8 rules.

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

Since P12.5: a **generation** record needs an explicit approval (`approved_at`) whatever
its class (records an administrator creates start unapproved and disabled; the operator's
`lattice provider configure` approves for `internal` as before, recorded); an
`organization_controlled` generation endpoint must be permitted by the organization's
outbound policy (`outbound_blocked`); `external_egress_level` may be NULL — external APIs
not allowed; and the whole rule is re-checked immediately before each model call (§12).

- `org_settings.external_egress_level` ∈ {`internal`, `confidential`, `restricted`} or
  NULL (P12.5: external APIs not allowed), default **`internal`**. `org_settings.organization_controlled_level` (D-15): the
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
  (no "leadership users may send confidential content" shortcut). Since P12.5 an
  administrator raises them in the product only with a fresh identity-provider sign-in
  bound to the exact change (D-34, §12); operators keep the commands below. Implemented today as
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
  **Retention (P11.4):** the worker deletes whole trace rows after
  `LATTICE_TRACE_RETENTION_DAYS` (default 90); the database refuses deleting a trace
  younger than 90 days, and the worker can read only a trace's ID, organization and
  creation time, never its question or answer (deployment.md §13).
- **Asker's view of their own trace:** question, answer, evidence and stages.
  Evidence references are re-authorized against the asker's *current* access. If any
  evidence of that Ask is no longer readable -- cited or not, since supersession items
  and notices name uncited documents -- or any account whose metadata the answer may
  carry (recorded as IDs: clarification choices, the account attribute of prompt
  evidence) or the account the request was scoped to is no longer visible to the asker
  or no longer exists, the stored answer text is hidden (and the scope's account
  withheld); a trace without recorded account dependencies hides it too
  ("This answer cited sources you no longer have access to") — otherwise traces would
  let revoked users re-read derived content.
- **Activity (Phase 8):** `GET /v1/activity` lists the member's *own* questions (time,
  status, the question they typed); opening one is the re-authorized asker view below.
  `GET /v1/admin/activity` lists the organization's Asks for administrators with the
  operational fields of the next bullet only (and the member's display name).
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
  policy changes) are recorded in an append-only `audit_events` table with actor,
  target, before/after, reason. **As built (Phase 9):** the API, the seed and
  `apply-admin-grants` write `access.member_changed`, `access.grant_created` and
  `access.grant_revoked` in the same transaction as the change (data-model §7);
  `GET /v1/admin/audit-events` shows administrators the events newest first; an event
  about a document is listed only while the viewer can read that document (D-18), so
  events about other documents change neither the result nor its paging. Reasons are free text written by administrators and visible to every
  administrator. **Since Phase 10 the database enforces it:** a deferred constraint
  trigger refuses to commit any access write by the API role unless the same transaction
  recorded an `access.*` event for that membership (`audit_events.txid`, migration 0013;
  ADR-015). SCIM writes `access.member_changed` and `access.identity_changed` (actor
  `scim:<token id>`); operator commands write `sso.*`, `scim.token_*` and the
  administrator-capability change (`access.member_changed` by `operator:<user>`).
- **Access management (Phase 9):** administrators review and change members' access on
  the Access page. Capability first (403 identical for any target); never their own
  access; changes previewed (effects as rules, counts only over documents the
  administrator can read), with a reason, against the version reviewed (409 if
  stale). Grants only of documents the administrator can read; a member's grants on
  other documents, and their membership of private groups the administrator isn't in,
  are acknowledged as a yes/no, never listed, counted or identified (D-18). Members
  provisioned by the identity provider are marked as such; their suspension and
  reactivation belong to the provider (422 `managed_by_identity_provider`), their access
  stays editable here (D-19 revisited, D-20).

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
| `tests/security/test_operational_metadata.py`, `tests/security/operational/` ✔ (Phase 8) | CI | Sources: every persona of both organizations sees exactly the sources the reference policy lets them read from, with counts equal to the reference policy's (independent of the SQL predicate); a source a member cannot read from is byte-for-byte absent; revocation removes it on the next request; administrators get aggregates only, every source, no titles, paths or file names; a failed record is named only when the administrator can read its document, otherwise `rec_…`; parameters allow-listed; `access_unmappable:account` shows the external account reference only. Requested syncs and administrator views: 403 for non-admins whether or not the source exists; another organization's sources are 404 and never listed. Activity: members list only their own questions; administrators' rows carry no question, answer, scope or evidence; organizations never see each other's rows. Plus `tests/test_worker_role.py` (the worker's exact privileges and restrictive policies) and `tests/test_operations.py` (health, readiness, limits, metrics without identifiers). Phase 9: `tests/security/test_access_management.py` (403 equality for non-administrators, other organizations' members/grants/events 404 and never listed, search and explanations only over readable documents, unreadable grants neither listed nor counted, private channels shown only to their members, previews identical with or without documents the administrator can't read, audit naming documents only while readable, one event per change and none when refused, CSRF), `tests/security/test_access_private_objects.py` (review closure: nothing about a private channel the admin isn't in — name, members, count, identifier — on member pages, explanations, the directory or refusals; grant/revoke on an unreadable document leaves the viewer's audit result set and paging identical, a hidden event ID as anchor behaves like a missing one, readable events appear normally; becoming admin reveals nothing; organizations separate), `tests/security/operational/test_access_personas.py` (every persona's `/v1/accounts` equals the reference `account_visible`; for every administrator × member: no unreadable title, grants exactly the readable ones, channel naming, suspension previews equal the reference policy's count), `tests/security/test_authz_review.py`, `tests/test_app_role_access.py` (the API role's exact privileges and restrictive policies) |
| Phase 10 suites ✔ | CI | `tests/identity/test_oidc.py` (flow, every ID-token defect, replay, binding, expiry, connection mix-up, organization-scoped mapping, provider faults, logs), `tests/provisioning/` (SCIM contract, idempotency, attribute ownership, cross-organization isolation, revocation effective everywhere), `tests/identity/test_admin.py` (operator commands audited, no secrets in events), `tests/test_rate_limits.py`, `tests/test_access_audit_enforcement.py` (unaudited writes refused at commit), `tests/test_app_role_access.py` (provisioning fences), `tests/security/test_route_inventory.py` (every route classified; anonymous/member/SCIM/cross-organization sweeps; closed metric labels), `e2e/identity.spec.ts` (CSP nonce, SSO errors, 429 and provisioned-member states, axe) |
| Mutation check | By hand per phase (Phase 5: 18 retrieval defects, `implementation-plan.md`); CI nightly job still planned | Predicate disabled → suite must fail |

Release gate: any failure in these suites blocks merge. `unauthorized_context_retrieved`
in the eval harness must be exactly 0.

## 10. Operational metadata side channels

Operational access is not content access, and the *existence* of an account, source,
channel or document can itself be sensitive. Rules per channel:

| Channel | Members | Admins |
| ------- | ------- | ------ |
| **Account lists** (scope picker, account-mention detection, ambiguity choices) | Only accounts inside their account scope (`all` → all accounts; `assigned` → assigned only). An unassigned account named in a question is treated as plain text: no "Interpreted as", no boost, no ambiguity choice. *Phase 9:* `GET /v1/accounts` (the Ask picker and first-run line) lists exactly `visible_accounts_predicate`, the set a scope may name | The account directory, because assigning accounts is the admin's job (`GET /v1/admin/access/directory`, Phase 9). Account *content* is not visible. (Hiding an account's existence from admins is a future "restricted account" capability, not in the slice) |
| **Source names / list** | Only sources in which they can read ≥ 1 document | All sources (admins configure them) |
| **Source counts** | Computed inside their authorization predicate | Per-source aggregate totals only (documents, failures, last sync). **No** breakdown by account, sensitivity, department, group or document |
| **Ingestion status / "source out of date" notices** | Only for sources they can read | All sources |
| **Sync failure messages** | Not shown | Stage + error code + templated message (never free text from the record). The record's external ID / filename / title / URL is shown only if the admin can read the corresponding active document; otherwise an opaque reference (`rec_7f3a…`, a keyed hash) is shown. Raw identifiers are available only to infrastructure operators via CLI with database access. One exception: `access_unmappable:account` shows the unmapped *external account reference*, because mapping it is the admin's task and the account directory is already admin-visible — never the record's title or content |
| **Filenames, titles, external IDs, URLs** | Only for readable documents (evidence, inspector) | Same rule — admin pages never show document-level metadata for documents the admin can't read |
| **Structured account metadata of readable documents** (decision D-8) | The account field of an evidence card or inspector, the `account` attribute of prompt evidence, account facets: only when the account is inside the member's scope (`authz.visible_document_account`). A document readable through an explicit grant on an out-of-scope account shows no account at all — no substitute name — and the grant does not put the account into lists, filters, mention detection or autocomplete. Text *inside* the document that names the customer is content, readable because the document is | Same rule for their own evidence |
| **Access groups** (e.g. private channels) | Their own memberships only | Admin-created groups: name + members. Source-synced (private) groups: shown — name, members — only if the admin is a member. Otherwise nothing: no name, member list, member count or identifier; a member's access says only that they are in private channels the admin isn't in, and such a group's ID is refused like an unknown one. *Phase 9 review closure* (the first version showed an opaque reference and the member list) |
| **Access-change previews** | — | Counts only over documents the admin can read; beyond that, effects are described as rules ("Maya will lose access to all Kestrel Bank content"), without counts. *Built in Phase 9* (`POST /v1/admin/access/members/{id}/preview`; the viewer's predicate is in the counting statement) |
| **Audit log** (Phase 9) | — | Newest first, keyset pages, no totals. An event about a document is listed only while the viewing admin can read that document (SQL, the viewer's predicate, also for the paging anchor): events about other documents are not enumerated, so they reveal no existence, timing or count. They stay in the database; an admin who can read the document, or an operator, sees them (D-18) |
| **Filters / facets** | Values and counts computed inside the predicate; source-type options only for types with readable documents | Same |
| **Autocomplete** | None in the slice. Any future suggestion (accounts, titles, people) must be computed inside the predicate and visibility rules above | Same |
| **Trace structure** | Own traces (§8) | Operational view without counts, scope, account or source names (§8) |
| **"No evidence" / refusal responses** | Identical for nonexistent and unreadable (SEC-5); "what was searched" lists only their own scope | — |
| **Logs** | — | IDs, codes, durations only (SEC-8); document IDs in logs are UUIDs, never titles/filenames |

## 11. Rate limits and resource protection (Phase 10; shared since P11.4)

Server-side only (`lattice.ratelimit`; T19, T24), and **every limit is shared by all API
processes through PostgreSQL** (D-24; deployment.md §12). Per membership: Asks per minute
and per day (counters under row-level security) and in flight (`ask_slots` lease rows,
freed by expiry if a process dies); per administrator: access previews and writes; per
SCIM token: requests; per client before authentication: development sign-in, OIDC start
and callback, failed SCIM authentication (`pre_auth_rate_limits`, which no login role can
read or write — only the `lattice_auth` functions `lattice_pre_auth_hit` and
`lattice_pre_auth_retention` — keyed by HMAC-SHA-256 of the client's IPv4 address or IPv6
/64 under `LATTICE_CLIENT_KEY_SECRET`, so neither the table nor a backup holds an address
or can be reversed by enumerating addresses; a client over the limit is refused before
its SCIM token is even checked). A refusal is 429
`rate_limited` with `Retry-After`; browser sign-in redirects with `error=rate_limited`.

Rules: the subject comes from the session or token, never the request; a limit is taken
after authentication and the capability check and before anything that depends on the
target, so a 429 says nothing about documents, accounts, members or other organizations;
all limits of one request are taken in one transaction and nothing is counted when one
refuses; parallel requests never exceed a limit (conditional upsert on a locked row);
every role is limited, administrators included, and no setting disables a limit (each
must be ≥ 1); the only metric label is the bucket name; no address, identifier or
content is logged or stored.

## 12. Phase 12 setup surfaces

Decided in P12.0 (2026-10-06, [phase-12-plan](phase-12-plan.md) Part II and
[ADR-017](decisions/017-setup-mutation-trust-boundary.md)). **P12.1 (2026-10-08) built the
foundations below, P12.2 (2026-10-09) members and administrators, P12.3 (2026-10-12)
accounts and their external references, P12.4 identity administration, P12.5 providers and
egress, and P12.6 (2026-10-18) export sources and imports; the rest is contract, not yet
implemented.**

**Built in P12.1:**

- **Mutation boundary (first fence).** The runtime role may rename its own active
  organization (column grant + restrictive policy) and the change cannot commit without an
  `organization.updated` event in the same transaction (deferred constraint trigger); the
  audit insert policy admits that one action from `member:` actors. Every other setup
  table stays unwritable by the API (tested statement by statement); no setup role, no
  broad grant.
- **Recoverable credentials.** `encrypted_secrets` (`vault:<id>`): AES-256-GCM, a fresh
  96-bit nonce per encryption, associated data binding organization, secret row, owner
  record, credential kind and key ID — a ciphertext moved between rows, organizations,
  records or kinds, relabelled with another key ID or altered in any byte fails closed.
  Master keys (`LATTICE_SECRET_KEYS`, one active) only in the deployment environment;
  the production API refuses to start when a stored key ID is not configured;
  `lattice secrets rekey` re-encrypts under the active key (audited, counts only).
  Opened only by the API, only when it presents the credential (OIDC token exchange;
  generation after the invocation rule allowed the call); the worker, migration job and
  web hold no keys and the worker has no privilege on the rows. SCIM tokens and session
  tokens stay one-way hashes. Plaintext is accepted only by operator commands reading
  stdin (never echoed) and never appears in output, logs, audit payloads, errors or API
  responses (tested).
- **Outbound policy** (`lattice.outbound`). Public targets: `https`, port 443, no
  userinfo/query/fragment, every resolved address globally routable (IPv4 and IPv6
  private, loopback, link-local/metadata, CGNAT, multicast, reserved, documentation,
  benchmarking ranges and IPv4-embedding IPv6 forms refused; non-canonical IPv4 literals
  refused). Enforced at connect time inside the HTTP client's network backend (the
  address checked is the address connected to — DNS rebinding cannot pass), no redirects,
  no environment proxies, bounded time and size, fixed codes. Private targets only when
  an operator registered the exact origin for that organization and purpose
  (`outbound_targets`, audited); never link-local or reserved. Used by `org create`,
  and since P12.4 by every OIDC request — sign-in, test sign-in, the connection check —
  under the organization's `oidc` policy (a revoked registration stops sign-in at once);
  since P12.5 by every provider call and provider test under the `generation` policy.
- **Organization bootstrap.** `lattice org create`: one audited transaction, exact IdP
  identifier, no e-mail linking or JIT membership, an IdP client registered to another
  organization refused, identical retries idempotent and differing ones refused.
- **Setup status** (`GET /v1/admin/setup`): administrators only, derived from live data
  on every read (never stored progress), counts, times and fixed messages only — no
  names of people, sources, channels or documents, no secret or secret reference; reads
  are not audited.

**Built in P12.2 (migration 0017):**

- **Members added by hand** (D-30, C2.1). Administrators only; bound to the exact value
  of the active connection's identifying claim (NFC-normalised, trimmed, case kept,
  compared byte for byte — an `email` claim is still an identifier, never an e-mail
  match); least access written by the fenced `lattice_create_member` (the request cannot
  carry access: unknown fields are 422); the identifier or e-mail of a member of this
  organization — removed ones included — is 409 with the field named; another
  organization's people are never read, so they neither collide nor show (owned users
  and per-connection identifiers make that structural; tested). Not an invitation; no
  just-in-time creation. The creation event records the bound identifier and e-mail.
- **Administrator capability** (D-31, C2.2). Granted or revoked for another member only
  (own membership: 403 at the API and in the function), never for a removed member,
  through `lattice_set_admin`; content access never changes (the authorization context
  is identical before and after; tested). The capability check of the actor is
  repeated inside the function under the lock.
- **Last active administrator** (ADR-017). Product writes that take an active
  administrator out of the set (revoke, suspend) take the organization's advisory lock,
  re-check the actor and count after the lock; a deferred trigger takes the same lock at
  commit for any API-role write that does so and refuses zero. Two administrators
  removing each other at once: exactly one succeeds (tested for revoke/revoke,
  suspend/suspend, revoke/suspend, and for two direct API-role statements). The identity
  provider (SCIM) and operators are not bound: the setup record then reads *No active
  administrator* and only an operator appoints one.
- **Reinstatement** (D-36, C2.3). A removed member on their original directory identity
  (refused when that identity's connection is no longer active, when the user is
  disabled or when another member has their e-mail), reset to least access, never an
  administrator, sessions not revived; SCIM finds the same person by `externalId`
  afterwards. The operator's `lattice member reinstate` still restores prior access
  (incident reversal).
- **Suspension in Access** now ends the member's sessions in the same transaction, as SCIM
  and operator suspension do; reactivation restores the rules, never a session.
- **Privileges.** `lattice_app` gains `EXECUTE` on the three functions and `SELECT
  (issuer, subject_claim)` on `oidc_connections`; directory identities it inserts are
  restricted to `managed_by = identity_provider`; `removed` only for those. No table or
  column grant on memberships was added.

**Built in P12.3 (migration 0018):**

- **Accounts.** Administrators create accounts and edit their name, aliases and business
  status (column grants `INSERT (organization_id, key, name, aliases, status)`, `UPDATE
  (name, aliases, status)`; the key, the organization and the lifecycle are never
  writable directly); archive and reactivate only through the fenced
  `lattice_set_account_archived` (active administrator re-checked, row lock). An
  archived account is read-only to the API role (restrictive policy). Creating an account
  assigns it to nobody; archiving deletes, hides and revokes nothing (documents keep their
  account, assignments stay, existing mappings keep resolving). Accounts are never
  deleted by the product.
- **External references** (`account_external_refs`). Written by the API only through the
  fenced `lattice_set_account_ref` with the mapping the administrator reviewed (409 when
  it moved on); `lattice_app` and the worker read it, nothing else. Composite foreign keys
  and forced RLS keep source and account in the row's organization; another
  organization's source or account answers exactly as a random ID. A check trigger binds
  every writer, operators included: kind fits the connector, no change of identity, no
  new mapping to an archived account. Exact, source-provided identifiers only — no fuzzy
  match, no suggestion from names or content.
- **Admin ≠ content.** The mapping views show the reference as the source sent it (a
  ticketing organization ID, a channel **ID** — never a channel name, public or private,
  D-18), counts of records waiting and operational times; never titles, text, file names
  or content-derived suggestions (C9, D-10). Mapping changes which account a record
  belongs to at the next sync; the record's own rules (grants-only private channels,
  sensitivity, departments, the tenant) still decide who may read it, and the
  administrator gains nothing (tested over the synthetic export). **A missing account
  mapping never widens access** (P12.3 correction, migration 0019): unmapping leaves the
  reference *unresolved* and its records quarantined at the next sync on every source,
  account-scoped or not, until a valid mapping exists; a ticket whose organization has no
  mapping is never indexed without an account. The preview says the records become
  unresolved and unreadable; mapping again restores them. Tested with the authorization
  predicate for every member (`test_missing_account_mapping_never_widens_read_access`).
- **D-10.** Names and aliases stay administrative metadata: editing them touches no
  document, chunk, search text or embedding (tested); mention detection still matches
  only accounts the asker may see.
- **Audit coupling.** Deferred triggers refuse to commit an API-role account or mapping
  write without the matching `account.created` / `updated` / `archived` / `reactivated` /
  `mapping_changed` event of the same transaction; the audit insert policy admits those
  five actions from `member:` actors only.

**Identity administration (P12.4, phase-12-plan Part III).**
- **Administrators only**, capability checked before any read; another organization's
  connection, token or test answers as a random ID (route inventory). Writes only through
  the 0020 fenced functions, each audit-coupled; the version (409) and a reason on every
  change.
- **Client secret**: plaintext only in the request (`SecretStr`), sealed at once
  (AES-256-GCM, bound to the connection), the previous ciphertext destroyed; never in a
  response, log, audit event, trace or 422 body (tested with a marker value). Responses
  say only whether it is set, how it is kept and when it was set.
- **SCIM tokens** stay verification-only: SHA-256 plus the last four characters; the
  value only in the creating response; rotation revokes the old token in the same
  transaction; revocation is final. `last_used_at` at minute precision.
- **Issuer or claim change** needs the administrator's own matched test sign-in under
  exactly the new values (database-checked, single use, ten minutes), a typed
  confirmation and a reason, and ends every session of the connection. A provider whose
  subjects differ is refused (changed-subject migration, post-v1); nobody is remapped by
  e-mail. Disabling ends the connection's sessions at once; client-ID and secret changes
  keep them; organization suspension still overrides everything.
- **Test sign-in** creates no session, is bound to the administrator's membership and
  browser, forces a fresh authentication, and shows only their own identifier. A rejected
  authorization code never marks the provider unavailable.
- **`scim.request_refused`** only for authenticated, actionable refusals (uniqueness,
  `externalId` changes), with the reason and the colliding member, never a request value;
  rate-limited in SQL so it cannot become a logging flood.

**Providers, BYOK and egress (P12.5, migration 0021; phase-12-plan Part III).**
- **Seven facts apart** (C6.1): a credential and its test change no record and no setting;
  approval, enablement, data terms, the ceilings and the questions' default are the egress
  *policy*. The egress lattice the page draws is the server's (`lattice` in `GET
  /v1/admin/providers`); the database computes the same cells (`lattice_egress_cells`,
  tested equal over random configurations).
- **Writes only through fenced functions** (`lattice_preview_egress_change`,
  `lattice_apply_egress_change`, `lattice_create_provider`, `lattice_set_provider_endpoint`,
  `lattice_set_provider_credential`, `lattice_revoke_provider_credential`,
  `lattice_create_step_up_grant`), each re-checking an active administrator of an active
  organization, each audit-coupled; the API role has no table write on `org_settings`,
  `model_providers` (test facts excepted), `provider_credentials`, `encrypted_secrets`,
  `step_up_grants` or `egress_policy_versions` (tested statement by statement).
- **Escalation is the database's verdict** (D-34, C6.2): a change that newly lets a level
  reach a provider record at a destination, or lowers the questions' default, commits only
  with a single-use step-up grant of the same administrator and session, for exactly that
  change, on exactly that policy version (every writer, operators included, raises it),
  within five minutes of a fresh identity-provider authentication (`max_age=0`,
  `prompt=login`, a verified `auth_time` not before the attempt and at most five minutes
  old, the identifier equal to the administrator's own directory identity; the callback
  must come back to the same Lattice session). A stolen session cookie alone cannot pass:
  it cannot authenticate at the identity provider. Lowering needs no grant and applies to
  the next request. Grants are consumed under a row lock (concurrent reuse: one commit).
  A fresh sign-in is **not** multi-factor authentication: Lattice reports an assurance
  (`acr`) only when the operator configured a requirement for the connection
  (`lattice sso update --step-up-acr`) and the provider asserted it.
- **API keys** entered in the product are sealed in the API (AES-256-GCM, bound to the
  credential row) and stored as `vault:`; an administrator can never name an environment
  variable (only operators record `env:` references); replacement and revocation destroy
  the previous ciphertext whoever managed it. Never in a response, log, audit event, trace
  or 422 body (tested). Shown as: who holds it, its last four characters (product keys),
  when it was set, the declared tier, the latest test.
- **Destinations**: an organization-controlled endpoint is an operator-registered
  `generation` target (`lattice outbound allow --purpose generation`), never a typed URL;
  its trust class follows it (loopback = `local` only by the operator's registration;
  plain http only for loopback); moving withdraws the approval; the class never changes.
  Every provider call and test connects through the outbound guard (connect-time address
  check, no redirects, no proxies).
- **Call time**: the invocation rule is repeated in a fresh snapshot immediately before
  every model call (and every regeneration): a provider disabled, unapproved or moved, a
  lowered ceiling or raised default, a reclassified or no-longer-readable prompt
  document, a revoked operator registration of an organization-controlled endpoint, a
  revoked or replaced credential stops the call (`policy_changed`, `outbound_blocked`,
  `credential_revoked`). A request already in flight is not cancelled.
- **Tests** send no organization content and generate nothing (Gemini: model metadata;
  Ollama: its model list); results are facts, never approvals; not audited (C11).

**Export sources and imports (P12.6, migration 0022; phase-12-plan Part III).**
- **Administrators only; writes only through fenced functions** (`lattice_create_source`,
  `lattice_update_source`, `lattice_set_source_paused`, `lattice_disconnect_source`,
  `lattice_purge_source`, the five `lattice_*_import` functions), each re-checking an
  active administrator, each audit-coupled (`source.*`); the API role has no table write on
  `sources` or `source_imports` (tested statement by statement). Another organization's
  source answers every route exactly as a random ID (route inventory, upload included).
- **No path from the product.** Sources and imports are identified by server-generated
  UUIDs; storage locations are derived from the organization and import UUIDs by the
  import store under `LATTICE_IMPORTS_ROOT`; an organization-managed source's
  configuration cannot hold a path (CHECK). No file name, MIME type or `Content-Length`
  is trusted: the format is decided by content, the body is counted as it streams.
- **Archive safety** (C8.3 as built): checked entry by entry before anything is written —
  EOCD entry-count pre-check; UTF-8 names; absolute paths, drive letters, `..`,
  backslashes, empty components, excessive depth or length refused (`unsafe_path`);
  symlinks, hard links, devices, FIFOs, sockets and Windows reparse points refused
  (`link_refused`); duplicate names, also ignoring case and Unicode form
  (`duplicate_name`); overlapping entries and encrypted or non-deflate/stored entries
  (`archive_invalid`); per-entry ratio, per-file and total expanded size counted while
  streaming, never trusted from headers (`ratio_exceeded`, `too_large`); entry count
  (`too_many_entries`). Only files the connector reads are expanded, each created
  exclusively (`O_EXCL | O_NOFOLLOW`, mode 0640, directories 0750) in a fresh staging
  directory published by one rename. Uploaded files are never served, never executable,
  and reachable only by the API instances and the worker (deployment.md §4).
- **Bounded resources.** A route-specific body cap (default 100 MiB, at the API and the
  ingress) and a 600 s budget for receiving and validating; the ordinary 64 KiB / 1 MB
  limits are unchanged for every other route; two concurrent uploads per API process; one
  upload per source at a time; a 2 GiB per-organization quota of retained imports and a
  256 MiB free-space reserve checked before accepting (`storage_full`). Interrupted,
  timed-out, oversized or refused uploads leave nothing available (partial files
  removed; abandoned rows expire after an hour).
- **Admin ≠ content** (C9). The record, the check and the register show counts, fixed
  codes, sizes, a SHA-256, times, IDs and the exact account references (ticketing
  organization IDs, channel **IDs**) — never document, ticket or message text, file names
  from an import, private channel names or content-derived suggestions. Creating or
  configuring a source grants nobody anything; source settings only fill in what a record
  leaves out, and a settings change that would let more people read records at the next
  sync must be acknowledged after the server's preview (`widening_not_acknowledged`).
  Imported records keep the Phase 4 rules: private Slack channels stay grants-only to
  their members; a record without trustworthy access information, or with an account
  reference that has no account, is held back (fail closed, P12.3) — tested with the
  authorization predicate (`test_first_sync.py`).
- **The worker** reads an organization-managed source's import through the store by the
  job's import ID (the import current when the sync was requested); it gains no
  organization-admin authority — only `SELECT` on `source_imports` and two functions of
  its own (check results, expiry).

**Contracts not yet implemented:** operator-registered export locations (C8.1 —
operator-managed sources keep the `lattice sync` path workflow); and on every setup
surface administrators stay non-readers of content (C9, C10).
