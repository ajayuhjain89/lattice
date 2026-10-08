# Phase 12 — Organization Setup, Identity Configuration & Integrations: plan

Status: **Plan approved (2026-10-06). P12.0 (contracts, Part II) written; P12.1
(foundations) implemented 2026-10-08 (Part III).** Branch `phase/12-org-setup-integrations` from `main` 143392f. Decisions
D-28–D-33, D-35, D-36 and D-37B are approved (§22, with the contracts of Part II);
D-34 (raising egress ceilings: IdP step-up, C6) and D-37A (organization purge: semantics
now, executable purge in Phase 15, C14) were decided by engineering under the product
owner's delegation, as were corrections to D-31 and D-36 (C18). **P12.1 delivered
(2026-10-08, local commit; Part III).** P12.2 waits for the product owner's go-ahead.

Scope and roadmap position: `implementation-plan.md` §4 "Later phases", row 12. Phase 11
is closed (143392f); real external staging validation stays deferred and remains a
release gate before Phase 15 may declare v1 GA (phase-11-plan §21.1). Phase 13 owns
retrieval and answer quality, Phase 14 scale and adversarial hardening, Phase 15 GA.

---

## 1. Objective

Turn the operator-driven setup that exists today into a coherent setup and
administration experience an **organization administrator** can complete:

```
organization exists, first administrator signs in
  → identity verified → members provisioned (SCIM or by hand)
  → AI policy and provider chosen → sources connected → accounts mapped
  → first sync healthy → access checked → first trustworthy Ask → evidence
```

without hand-edited manifests, owner SQL, undocumented CLI sequences or engineering
help for the normal path. Phase 12 makes accepted systems **operable through the
product**. It does not redesign authorization, identity, retrieval or generation.

## 2. Non-goals

- Public self-service sign-up, billing, plans or a customer-management system (D-28).
- New authorization semantics: the reference policy, RLS, D-8/D-10/D-13/D-15/D-17/D-18
  and admin ≠ content are unchanged.
- Retrieval, ranking, chunking, answer quality, golden-set growth (Phase 13).
- Scale, large ACLs, source storms, DB failover, sustained outages (Phase 14).
- GA journey, fresh-environment release validation, external staging (Phase 15).
- SCIM `Groups`, multiple simultaneous IdPs per organization, per-organization embedding
  models (embedding stays a deployment choice: changing it is a re-index, Phase 13/14).
- The account timeline workspace (post-v1; §9.6).
- Any paid service: IdP, SaaS connector account, OAuth app, AI provider, database or
  deployment (§19).

## 3. Current-state inventory (inspected 2026-10-06, code and docs)

"Operable" means: an organization administrator can do it in the product today.

| Area | Customer-operable today | CLI (operator) only | Manifest only | Privileged / owner SQL / impossible |
| ---- | ----------------------- | ------------------- | ------------- | ----------------------------------- |
| **Organization** | read its name (masthead) | `org suspend/reactivate`, `org egress` | `lattice seed` creates orgs, departments, groups — **refused in production** | **creating an organization in production has no path at all** (no command; seed refuses `LATTICE_ENV=production`); `data_origin` (seed); deletion/purge not built (D-26) |
| **Members** | Access page: suspend/reactivate *non-provisioned* members, clearance, account scope, accounts, departments, admin-defined groups, grants (Phase 9, previewed, audited, D-17) | `member admin --grant/--revoke`, `member suspend/reinstate`, `session revoke` | seed (any member, `is_admin`) | the administrator capability is fenced from the API role by the database (0012); a member can be **created** only by seed or SCIM — an organization without SCIM cannot gain members in production |
| **Accounts** | assign existing accounts to members; scoped Ask (Phase 9) | — | create, rename, aliases (seed); source references (`sources.yaml`: `account_refs` for ticketing, `channels.<id>.account` for chat) | `lattice_app` has `SELECT` only on `accounts`; mappings live inside `sources.config` JSON |
| **OIDC** | sign-in at `/sso/<login key>` | `sso add/show/list/update/enable/disable` (P11.3, audited, session revocation) | — | the client secret is an `env:LATTICE_OIDC_SECRET_<NAME>` reference: its **value** must be placed in the API environment by an operator; no SSRF guard on the issuer (operator-entered today) |
| **SCIM** | the IdP calls `/api/scim/v2/Users` | `scim token create/list/revoke` (printed once, stored as a hash) | — | no `lattice_app` privilege on `scim_tokens`; no last-use time; removal is terminal for the IdP (D-20), re-hire = operator `member reinstate` |
| **Providers / BYOK** | `/settings/providers`: read-only egress lattice and provider ledger (Phase 7, read-only by the product owner's instruction) | `provider configure/approve/enable/disable`, `provider credential set/revoke` (reference only), `org egress` | — | credential **values** live only in the environment (`env:LATTICE_PROVIDER_SECRET_*`); embedding provider and key are deployment settings (`LATTICE_QUERY_EMBEDDING`, `LATTICE_SYNC_EMBEDDING`) — org records only approve |
| **Sources** | Sources register: health, failed records by opaque reference, *Sync now* (Phase 8); Organization runs | `lattice sync --sources sources.yaml` creates/updates a source and records its **absolute export path**; `source disconnect` (purge) | `sources.yaml` (key, type, name, path, defaults, `account_scoped`, authority, mappings, channel settings) | `lattice_app` has `SELECT` only on `sources`; the worker syncs only the path the last CLI sync recorded; exports must be on the worker's filesystem; `paused` status exists in the schema but nothing sets it |
| **Setup status** | none | — | — | — |

**Connectors actually implemented** (`lattice.connectors`): three readers of **local
export directories**, no live API connector:

| Type | Reads | Real-world fidelity | ACL |
| ---- | ----- | ------------------- | --- |
| `document_repository` | Markdown + YAML front matter (`id`, `sensitivity`, `departments`, `grants`, `grants_only`, `account`) | a Lattice interchange format "mirroring" a Drive/Notion export; a customer needs tooling to produce it | front matter, mapped and failing closed |
| `ticketing` | Zendesk-*style* JSON Lines (tickets with comments, `organization_id`) | a Lattice interchange format shaped like Zendesk, not Zendesk's own export | source defaults; `organization_id` → account (`account_scoped`, unmapped = not indexed) |
| `chat_export` | Slack's workspace export layout (`users.json`, `channels.json`, `groups.json`, day files) | Slack's own export format (private channels need a Slack plan that exports them) | private channels → `grants_only` groups of their members; channel → account/sensitivity by configuration |

`fake`/synthetic providers and the synthetic corpus are test infrastructure, never
customer integrations.

## 4. Findings that shape the plan

| # | Finding | Where | Disposition |
| - | ------- | ----- | ----------- |
| F1 | No production path creates an organization; the first administrator cannot sign in until an OIDC connection and their directory identity exist (production sign-in is OIDC only) | `seed.py`, runbook §9 | D-28, P12.1 |
| F2 | Without SCIM no member can be created in production | `access/changes.py`, 0012/0013 | D-30, P12.2 |
| F3 | The administrator capability is CLI-only by a database fence | 0012 | D-31, P12.2 |
| F4 | Every setup table is read-only to `lattice_app`; `lattice_app` may insert only `access.%` audit events as `member:%` | privilege snapshot, 0012/0013 | ADR-017 (§18), every checkpoint |
| F5 | Secret values (provider keys, OIDC client secrets) can only be supplied through the deployment environment | `providers/secrets.py`, ADR-011 | D-29 |
| F6 | Sources need a path on the worker's filesystem; an admin of a multi-tenant deployment has no way to put an export there | `jobs/worker.py` | D-32 |
| F7 | Account mappings are JSON inside `sources.config`; there is no mapping table, no uniqueness across sources, no "unmapped references" view (the connector design anticipated `account_external_refs` "once accounts are managed in Lattice") | `sources/config.py`, connector-design §3.2 | P12.3 |
| F8 | Issuer and provider endpoints are operator-entered today and have no private-address guard; admin-entered URLs that the API server fetches are an SSRF surface | `identity/admin.py:_check_issuer`, `ollama_generation.validate_endpoint` | §17, P12.1 |
| F9 | Unaudited setup mutations: organization creation (none exists), source create/update via `lattice sync`, source disconnect, account create/update (seed audits members only) | `ingestion/store.py`, `lifecycle.py`, `seed.py` | §16 |
| F10 | A naive mapping screen would list private Slack channel names to administrators who are not members (D-18) and content-derived "suggestions" would breach admin ≠ content / D-10 | §17 | engineering rule, P12.3 |
| F11 | Removal by SCIM is terminal (D-20); phase-11-plan §19 sent "customer-facing re-hire UX" here | D-20, runbook §9 | D-36 |
| F12 | The roadmap row also assigns **organization purge** (D-26) and **IdP migration with differing subjects** to Phase 12 | implementation-plan §4 row 12 | D-37 |

## 5. Setup model

> **Superseded by C12** (the backend-owned status contract: categories, states and reason codes).

A durable **setup record** for the organization, derived on every read from backend
data. Nothing is stored as "progress", nothing is a percentage, and every state carries
the reason code that produced it.

States: **Complete** · **Required** (not done, needed) · **Optional** (not used, and that
is fine) · **Blocked** (cannot proceed until something else is done — names it) ·
**Needs attention** (was working, is not now).

| Item | Required? | Complete when | Blocked when | Needs attention when |
| ---- | --------- | ------------- | ------------ | -------------------- |
| Organization | yes | status `active` and ≥ 1 active administrator | organization suspended | exactly one active administrator (losing them needs an operator, runbook §9) |
| Identity (OIDC) | yes (production) | an active connection **and** a recorded successful sign-in through it (a test or real sign-in; timestamp only) | — | connection disabled; last sign-in attempt failed `sso_unavailable` since the last success |
| Members | yes | ≥ 1 active member besides the first administrator | Identity not complete | — |
| Provisioning (SCIM) | optional | an unrevoked token and ≥ 1 change made by the identity provider | Identity not complete | every token revoked while provisioned members exist |
| AI policy | yes (a choice) | a generation provider enabled and approved for the organization's default sensitivity, **or** "evidence only" chosen explicitly (no generation) | — | the enabled provider is no longer allowed (credential revoked, ceiling lowered below the default) |
| Sources | yes | ≥ 1 active source whose latest run is `succeeded` or `partial` | — | latest run `failed`, or no export / unreachable |
| Accounts | required only when an account-scoped source or a channel mapping exists | no unmapped references in the latest run of each such source | Sources not complete | unmapped references appeared in the latest run |
| First Ask | yes | ≥ 1 trace in the organization with status `answered` or `partial` | Sources not complete | — |

Served by one administrator endpoint (`GET /v1/admin/setup`), shown as the
organization's setup record (§7). It is a status surface, not a wizard: each row links
to the page that owns the item, and remains useful after onboarding.

## 6. Organization bootstrap (platform) vs organization setup (admin)

> Binding contract: **C1**.

**Recommended (D-28 A):** operator-created organization, customer-configured after.

| Step | Who | How |
| ---- | --- | --- |
| Create the organization, its first OIDC connection and the first administrator's directory identity, in one transaction | platform operator | new audited command `lattice org create --slug --name --issuer --client-id [--client-secret-ref] [--subject-claim] --first-admin-email --first-admin-subject --reason` (prints the sign-in URL and redirect URI, like `sso show`). No seed, no SQL |
| Register the redirect URI in the IdP; sign in | first administrator | `/sso/<login key>` |
| Everything in §5 | organization administrators | the product |
| Suspend / reactivate / purge an organization; "all administrators lost"; data origin | platform operator | audited commands (P11.3; purge per D-37) |

Why not self-service: Lattice is enterprise software whose first act is trusting a
customer's identity provider; self-service would need email verification, abuse
controls, a tenant lifecycle and billing hooks — none of which exist or are in scope.
The hybrid (an emailed one-time bootstrap link) adds a second sign-in mechanism to the
production surface for one use per customer.

## 7. Information architecture (D-35)

**Recommended:** the Administration section gains **Organization** at `/settings`
(the setup record) beside the existing admin pages; setup items live where the thing
lives, so nothing is duplicated:

| Page | Route | Adds |
| ---- | ----- | ---- |
| Organization | `/settings` (new) | setup record (§5), organization name, administrators, AI policy summary |
| Identity | `/settings/identity` (new) | OIDC connection, sign-in test, SCIM tokens and provisioning activity |
| AI providers | `/settings/providers` (existing) | becomes editable (§11); the egress lattice stays the centre |
| Access | `/access` (existing) | *Add member* (D-30), administrator capability (D-31), reinstate (D-36), new **Accounts** view (`/access/accounts`) with mappings |
| Sources | `/sources` (existing) | *Connect a source*, source record pages (`/sources/<id>`): settings, export, mappings, runs, failures, pause, disconnect |

Masthead, Jump and the phone More sheet gain one destination (Organization); Jump lists
the new pages for administrators only. No stepper wizard: the setup record *is* the
checklist, with deterministic rows.

## 8. Organization administration (P12.1)

Admin-controlled, and only these: display **name**; **administrators** (D-31); **AI
policy** — default question sensitivity (D-13: may be raised freely; lowering is a
change preview with a reason), external egress ceiling and organization-controlled
ceiling (D-15, D-34). Read-only for admins: slug, status, data origin, the sign-in URL.
Operator-only: create, suspend/reactivate, purge, data origin. Every change: reason,
version check (409), audit event, previewed when it narrows or widens access or egress.

## 9. Accounts (P12.3)

### 9.1 Model
- Accounts: create (key, name, aliases), rename, edit aliases, **archive** (status
  `archived`: no new mappings or assignments, existing documents keep their account;
  no delete — documents, assignments and traces reference it).
- New table **`account_external_refs`** `(organization_id, source_id, external_ref,
  account_id)`, unique per `(source_id, external_ref)`: one reference maps to exactly
  one account (ambiguity is impossible by construction). Migration moves
  `sources.config.account_refs` and channel `account` settings into it; `sources.yaml`
  stays accepted by the CLI and is written into the table (audited, actor
  `operator:sync`).
- Ingestion reads mappings from the table; behaviour is unchanged (unmapped on an
  account-scoped source = not indexed, `access_unmappable:account`).

### 9.2 Unmapped references
From the latest run's failure records (`access_unmappable:account`), grouped by
reference: the **external reference value** and the number of records waiting. Nothing
from the records' content. For chat sources the reference is the channel ID; a channel
**name** is shown only for public channels (private channel names are hidden groups,
D-18). No suggestions computed from content or names (D-10, admin ≠ content).

### 9.3 Mapping a reference
Map to an existing account or create one inline; the change preview states what it
does in counts the API computes over documents **the administrator can read** plus "and
records not yet indexed" (never a title). Remapping or unmapping is a change preview
that names the effect: documents move from one account to another on the next sync.
Takes effect at the next sync; the page offers *Sync now*.

### 9.4 Visibility and D-10
Account names stay administrative metadata (already visible to administrators in the
Access directory). They never enter search text, embeddings, autocomplete for members,
setup previews shown to members, or suggestions. Member-facing `GET /v1/accounts` is
unchanged.

### 9.5 Scoped Ask
Unchanged (Phase 9). The account picker reads the same endpoint.

### 9.6 Account timeline
Not needed for onboarding: post-v1 as recorded (phase-11-plan §27).

## 10. Identity setup (P12.4)

> Binding contract: **C5**; D-37B limitation stated in the UI.

### 10.1 OIDC
- **Add** (when none is active): issuer, client ID, client secret (D-29) or public
  client with PKCE, identifying claim (default `sub`; `email` offered with the warning
  that it must be stable and verified). Shows the **redirect URI** and sign-in URL
  before saving, with copy buttons.
- **Test sign-in**: the administrator signs in through the connection in a new tab;
  the callback records success (timestamp) and shows only the administrator's *own*
  identifying claim value and whether it matched their directory identity. It never
  creates a session for anyone else and never shows other users' claims.
- **Update**: client ID and secret in place (sessions kept). **Issuer or identifying
  claim** is a separate, explicit action: a change preview that says every session from
  this connection ends, and that it is safe only if the provider's subjects are
  unchanged (the P11.3 `--subjects-unchanged` semantics), typed confirmation, reason.
- **Disable**: a preview stating that sign-in stops at once and *N* sessions end; reason.
  **Re-enable**: the same connection and members. **Add another** while a disabled one
  has provisioned members: refused with the P11.3 explanation (use re-enable); the
  `--new-identity-namespace` path stays operator-only (D-37).
- **Status**: active/disabled, last successful sign-in, last test, secret set/rotated
  date (never the value), discovery reachable (checked on demand, fixed error codes).
- One active connection per organization (unchanged).

### 10.2 SCIM
- Base URL and the identifying-claim rule ("`externalId` must equal the `<claim>` of
  the same person") shown with copy buttons.
- **Create token**: shown once in a dialog (`Cache-Control: no-store`, never in a URL,
  never re-readable), with the warning that the token does not expire until revoked,
  and *I have stored it* to close.
  **Revoke** with a preview ("the provider's next request is refused"). Overlap
  procedure for rotation (create, switch the provider, revoke) written on the page.
- Token list: created, revoked, connection — never a value or hash. A new column
  `last_used_at` (minute precision) makes "in use" truthful.
- **Provisioning activity**: the audit ledger filtered to identity-provider actions
  (creations, suspensions, removals, attribute changes). SCIM **refusals** (409
  conflicts, 400 schema errors) are not stored today; P12.4 records a minimal
  `scim.request_refused` audit event with the fixed SCIM error type only (no payload),
  so an admin can see "the provider tried to create a person whose e-mail already
  exists" without exposing the body.
- Users only (D-20 unchanged); least-access defaults unchanged.

## 11. Providers, BYOK and egress (P12.5)

> Binding contracts: **C3** (credentials) and **C6** (egress; D-34 step-up).

The egress lattice remains the page's centre; editing happens around it.

- **Generation provider**: choose from implemented adapters only — `gemini` (BYOK) and
  `ollama` (local or organization-controlled endpoint); `fake` never appears in
  production. Model from a fixed list per adapter. One enabled at a time (unchanged).
- **Credential**: entered once (D-29), shown afterwards only as *Set · ends ••••1234 ·
  rotated 6 Oct* (last four characters only if D-29 B stores them separately, else
  "set"); rotate (replace), revoke (provider excluded at the next request, ADR-011).
- **Test**: a minimal synthetic call ("reply with OK", no organization content, no
  evidence) through the invocation path, reporting a fixed code (`ok`, `credential_invalid`,
  `unreachable`, `quota`, `refused_in_production`). Never logs or traces the key.
- **Approval and ceilings** are separate controls with their own previews: what the
  provider is approved for (internal / confidential / restricted), the external and
  organization-controlled ceilings, the default question sensitivity. The lattice
  re-renders from the API's policy reading after each change.
- **Language rule**: a passing test says "the credential works"; the lattice says what
  may be sent. No green "connected" state on a provider that policy excludes; the row
  reads *Credential works · Sends nothing above Internal*.
- Embedding stays deployment-level: the page shows the deployment's embedding provider
  read-only with its approval for this organization (approve/withdraw is editable).

## 12. Sources and first sync (P12.6)

> Binding contracts: **C7** (supported types, named as exports) and **C8** (imports).

### 12.1 Supported in Phase 12 (D-32, D-33)
The three existing connector types, configured entirely in the product, fed by
**exports** (D-32): documents (Markdown interchange format), tickets (ticket JSON Lines
interchange format), Slack workspace export. Their formats are published as specs in
`connector-design.md` so a customer can produce them.

### 12.2 Per source
| Capability | Plan |
| ---------- | ---- |
| Create | type, name, key (derived, editable before save), default sensitivity, default departments, authority, account-scoped (tickets: on by default) |
| Export | D-32: upload an archive to the organization's export area, or pick a deployment-provided location; validated, size-capped |
| Check | *Check export*: the existing dry-run pipeline (no writes) → records found, would create/update/delete, rejected by code, unmapped references — counts and codes only |
| First sync | *Start first sync* after a passing check; a job like *Sync now* |
| Edit safe config | name, defaults, channel sensitivities, account-scoped toggle (preview: unmapped records stop/start being indexed); the connector type and key are immutable |
| Pause / resume | `paused` (existing status): requested syncs refused, content stays searchable |
| Disconnect | the existing purge (`lattice source disconnect`), now from the product with a preview of document and chunk counts, typed confirmation, reason |
| Status | the Sources register (Phase 8) plus a source record: last run, counts, failed records by opaque reference, unmapped references, export received at |
| Credentials | none for export sources (D-33 decides whether a live connector, and therefore source credentials, enter Phase 12) |

ACL semantics of Phase 4 are unchanged: fail closed, untrusted hints mapped by Lattice
configuration, deletion guard, no admin override of a document's access from source
setup.

### 12.3 First-sync experience
Source record shows, in order: export received (time, size) → check result → queued →
running (records seen so far, announced politely) → finished `succeeded` / `partial` /
`failed` with the run's counts, failure codes and unmapped references, and the next
action (*Map references*, *Replace export*, *Sync again*). Phase 8 wording is reused.
No document content is shown to diagnose a sync.

## 13. Connector selection (D-33)

| Candidate | Value | Auth | Ingestion complexity | ACL model | Account mapping | Local/free testability | Recommendation |
| --------- | ----- | ---- | -------------------- | --------- | --------------- | ---------------------- | -------------- |
| Existing three via exports | proves onboarding end to end; covers docs, tickets, chat | none (export) | none new | built (Phase 4) | built | fully local | **Phase 12** |
| Release notes / policies | high for AMs ("what shipped when") | none | none: a `document_repository` source with `authority=system_of_record` | built | optional | fully local | **Phase 12, as configuration** (no new connector) |
| GitHub Issues (REST, fine-grained PAT) | engineering status on customer asks | PAT (free) | moderate: pagination, rate limits, incremental by `updated_at`, deletions via snapshot | repo visibility → a Lattice group per private repo; collaborator lists need admin scope | label or repo → account (optional) | mock server + free account | **First live connector — post-v1 or a Phase 12 extension only if D-33 B** |
| Slack Web API | live chat instead of exports | bot token (free workspace) | high: rate tiers, history per channel, membership changes | channel membership (good fidelity) | channel → account | free workspace + mock | post-v1 |
| Notion API | docs | integration token (free) | moderate | **weak**: the API exposes only pages shared with the integration, not who may read them | page property | free + mock | not recommended (cannot honour ACLs) |
| Google Drive API | docs | OAuth app (free, verification friction) | high | permissions API (good) | folder → account | needs Google Cloud project; consent screen | post-v1 |
| Zendesk API | tickets | API token — **paid** plan/trial | moderate | org → account | built | trial only | defer (paid) |
| Gong / call transcripts | high for AMs | **paid** | moderate | participants | account | none free | defer (paid) |
| CRM notes (HubSpot free CRM, private-app token) | high for AMs | token (free) | moderate | owner/team based — partial | company → account | free account + mock | post-v1 candidate |

Recommendation: **no new connector type in Phase 12** (D-33 A). Onboarding is proven
with the three existing formats plus release notes as configuration; adding a live
connector also requires source credentials (D-29), worker egress and incremental sync —
enough to be its own checkpoint, and GitHub Issues is the recommended first one when
the product owner wants it (post-v1 or D-33 B).

## 14. Members, administrators, re-hire (P12.2)

> **Superseded in part by C2:** reinstatement resets to **least access** (C2.3), and the last-administrator rule binds product actions only (C2.2).

- **Add member** (D-30 B): e-mail, name, identifying-claim value (prefilled from the
  e-mail when the claim is `email`), created exactly as SCIM creates one — least access
  (D-20 defaults), a directory identity under the active connection, `managed_by`
  absent (the organization manages them). It coexists with SCIM: SCIM-created members
  stay IdP-managed (D-20); hand-added members are suspended and removed in Access.
- **Administrator capability** (D-31 B): grant/revoke for another member (never self,
  D-17), reason, preview ("administration grants no content access"), audited; the last
  active administrator cannot be revoked or suspended through the product.
- **Reinstate a removed member** (D-36 B): the product form of `member reinstate`;
  access as it was, the same directory identity, sessions not revived; audited; SCIM
  `DELETE` stays terminal for the IdP.

## 15. Setup errors (all fixed codes, all with a next step)

| Situation | Admin sees | Next step |
| --------- | ---------- | --------- |
| Issuer unreachable / discovery invalid | "The provider's discovery document could not be read (`discovery_unreachable` / `discovery_invalid`)" | check the issuer URL; it must match the discovery document exactly |
| Issuer resolves to a private address (§17) | `issuer_not_public` | use the provider's public issuer |
| Client secret rejected at the token endpoint | test sign-in `token_rejected` | re-enter the secret (rotate) |
| Test sign-in claim mismatch | "Signed in as a person this connection has not provisioned" | check the identifying claim / SCIM `externalId` |
| SCIM token revoked | provisioning activity shows `unauthenticated` requests counted (no token value) | create a token, update the provider |
| Provider credential invalid / quota / unreachable | test result code | rotate / check plan / check endpoint |
| Provider excluded by policy | the lattice row and *Sends nothing above …* | raise the approval or ceiling (preview) or choose another provider |
| Export invalid | check result: rejected records by code, archive errors (`archive_invalid`, `too_large`, `unsupported_layout`) | fix the export, upload again |
| Sync failed | run `failed` with its source-level code (Phase 8 wording) | per code |
| Unmapped references | count per reference | map or create accounts |
| Missing required configuration | setup record row *Required* with the reason | the linked page |

Never: secret values, stack traces, hosts or IPs resolved, other organizations,
names of hidden groups, titles of unreadable documents.

## 16. Audit

> **Superseded by C11** (final action families).

Existing and unchanged: `access.*`, `sso.connection_*`, `scim.token_*`,
`provider.*`, `credential.*`, `egress.levels_changed`, `session.revoked`,
`organization.suspended/reactivated`. Added (actor `member:<id>` from the product,
`operator:cli` from commands):

| New event | When |
| --------- | ---- |
| `organization.created` | `lattice org create` |
| `organization.updated` | name or default sensitivity changed |
| `account.created` / `account.updated` / `account.archived` | account lifecycle |
| `account.mapping_changed` | reference mapped, remapped or unmapped (before/after) |
| `source.created` / `source.updated` / `source.paused` / `source.resumed` / `source.disconnected` | source lifecycle (also when done by `lattice sync`/`source disconnect`) |
| `source.export_received` | an export accepted (size, digest; no content) |
| `member.created` | added by hand (D-30); SCIM creations stay `access.member_changed` from the identity provider |
| `scim.request_refused` | §10.2 |
| `sso.connection_tested` | only the *result* of a test sign-in, rate-limited per connection per hour — tests are otherwise not audited |

Not audited: viewing pages, *Check export* runs, provider tests (logged with codes;
metrics), navigation. The audit table stays append-only (trigger), `lattice_app` keeps
INSERT and SELECT only, with the insert policy widened from `access.%` to the listed
action families and still pinned to `member:%` actors (ADR-017).

## 17. Security review of the new surfaces

> The SSRF row is **superseded by C4** (operator-registered private targets, not a `LATTICE_OUTBOUND_ALLOW` setting); the export row by **C8.3**.

| Surface | Risk | Rule |
| ------- | ---- | ---- |
| Every setup endpoint | cross-tenant / enumeration | administrator capability checked before any read (E7); every identifier is tenant-scoped through RLS; foreign and random identifiers answer identically (route-inventory sweep extended to every new route, in-process and `make prod-authz`) |
| Admin ≠ content | setup previews leaking content | counts over documents the *administrator* can read plus "records not yet indexed"; no titles, excerpts or names from unreadable records; no content-derived suggestions |
| Hidden accounts (D-10) | mapping UI, autocomplete | account names only on admin surfaces that already show them; never in member autocomplete, search or embeddings |
| Hidden groups (D-18) | Slack private channel names in mapping candidates | private channels by ID only, unless the administrator is a member of the channel's group |
| Unreadable documents | disconnect/remap previews | counts only, as above |
| Source names | members enumerating sources | member `GET /v1/sources` unchanged (only readable sources); source records are admin routes |
| OIDC / provider URLs | **SSRF** from the API server | admin-entered issuer and endpoint URLs: `https` only, resolved and refused when any address is loopback, private, link-local, CGNAT, multicast or the metadata range, unless the deployment allow-lists it (`LATTICE_OUTBOUND_ALLOW` for verification stacks); re-checked at connect time (DNS rebinding); no redirects followed across hosts; fixed error codes, no response bodies echoed |
| Connection-test errors | oracle about internal hosts / other tenants | fixed codes only; timing bounded |
| Secrets in requests | logs, 422 echoes, traces | `SecretStr` request models; validation errors name fields never values (existing rule, tested per new route); request bodies of secret routes never logged; `Cache-Control: no-store` on any response that shows a one-time value |
| SCIM token display | shoulder-surfing, history | dialog only, never a URL or a stored response; copy button; closing discards it |
| Administrator capability | privilege escalation | never self (D-17), reason, audit, last-admin guard; the database fence for `is_admin` (ADR-017) admits only this write path, and self-grant and last-admin refusals are tested at the API and the database |
| Egress ceilings | exfiltration through a hijacked admin session | change preview naming what newly leaves, typed confirmation for raising, reason, audit, and the existing CSRF header; lowering is immediate |
| Export uploads (D-32 B) | zip-slip, zip bombs, symlinks, huge files, cross-tenant paths | size cap (configurable, default 200 MB), entry count and ratio caps, no symlinks/devices, normalised relative paths only, extraction into the organization's own directory by ID (never by name), worker reads only that directory |
| Audit events | revealing hidden resources | D-18 filtering extends to new events that reference documents |

## 18. Architecture changes proposed

1. **ADR-017: administrator setup writes for the runtime role.** The same discipline as
   ADR-014: column-level grants plus restrictive policies (or `SECURITY DEFINER`
   functions where a row must be created across a privilege boundary, e.g. a SCIM
   token hash) for `organizations.name`, `org_settings` (sensitivity and ceilings),
   `accounts`, `account_external_refs`, `sources` (safe columns), `model_providers`,
   `provider_credentials`, `oidc_connections`, `scim_tokens`, `memberships.is_admin`
   (others only); the audit insert policy widened to the new action families. The
   privilege snapshot records each change. No RLS policy is weakened; the worker's
   privileges are unchanged except reading `account_external_refs`.
2. **Secret store** (only if D-29 B): encrypted values in PostgreSQL behind the existing
   `SecretStore` protocol (§22 D-29).
3. **Export area** (only if D-32 B): a deployment volume shared by the API instances
   and the worker (deployment contract §14 changes).
4. **`account_external_refs`** table (§9.1).

No new service, broker, Redis, Kubernetes or external secret manager.

## 19. Zero-cost constraint

Everything is developed and verified on the local reference stack with synthetic
organizations, the verification OIDC provider (`deploy/verification/idp.py`), the fake
and loopback providers, and local export fixtures. No paid IdP, SaaS account, OAuth app,
AI provider, database or deployment. A Gemini test call is never made (the stack uses
the fake model; the adapter's test path is unit-tested against a local stub).

## 20. Test strategy

**Backend.** Every new route in the route inventory (anonymous 401, member 403,
foreign = random 404); admin authorization; ADR-017 privileges (role-access and RLS
inventories, privilege snapshot); OIDC lifecycle from the API including issuer-change
semantics and the test sign-in; SCIM token lifecycle and refusals; secret store (never
in responses, logs, 422 bodies, traces; rotation; revocation); SSRF guard (private
ranges, rebinding, redirects); account and mapping invariants (uniqueness, archive,
remap effects, ingestion reading the table, D-10 tests extended); source lifecycle
(create, check, pause, disconnect) and export validation (zip-slip, bombs, symlinks,
caps); setup-record derivation for every state; audit events per mutation and none for
views/tests; tenant isolation for every new table.

**Frontend.** Each flow's states (quiet, boundary, fault, wait), validation, change
previews, one-time secret dialogs; keyboard paths; desktop/tablet/phone; forced colours,
reduced motion, 200 % zoom and 320 px reflow; axe on every new page; the
not-available state identical for nonexistent and foreign identifiers.

**End-to-end** on the reference stack (`make prod-onboard`, new driver): a fresh
synthetic organization created by `lattice org create` → first administrator signs in
through the verification IdP → test sign-in → SCIM token and two provisioned personas,
one member added by hand → AI policy: fake model approved for internal, ceilings set →
three sources from uploaded fixture exports, checked, first sync → unmapped ticket
references mapped (one new account) → re-sync, zero unmapped → a member's access checked
on the Access page → the member asks, the answer cites evidence, the evidence opens.
Plus `make prod-authz` extended with every new route. Playwright covers the same journey
against the mocked API.

## 21. Checkpoints

| Step | Scope | Exit criteria |
| ---- | ----- | ------------- |
| **P12.0** Decisions and contracts | this plan approved; D-28–D-37 decided; ADR-017 written; API shapes for §5–§14 drafted in the plan | approval |
| **P12.1** Foundations and organization | ADR-017 migration skeleton (audit action families, privilege-snapshot workflow); SSRF guard (shared by OIDC and providers); secret store if D-29 B; `lattice org create` (D-28); `GET /v1/admin/setup`; `/settings` Organization page (setup record, name, AI policy summary); `organization.*` events | a fresh organization is created and its admin sees a truthful setup record; SSRF and secret tests green; all suites green |
| **P12.2** Members and administrators | *Add member* (D-30), administrator capability (D-31), reinstate (D-36) on the Access page; DB fences for each | §14 flows with previews and audit; route sweep extended; self/last-admin refusals proven in DB and API |
| **P12.3** Accounts and mappings | accounts lifecycle; `account_external_refs` + migration from `sources.config`; ingestion reads it; unmapped references; mapping previews; `/access/accounts` | migration round-trips with the synthetic data unchanged (same documents, same accounts); golden-set security metrics 0; D-10/D-18 tests extended |
| **P12.4** Identity setup | `/settings/identity`: OIDC add/test/update/issuer change/disable/enable; SCIM tokens, `last_used_at`, provisioning activity, `scim.request_refused` | every §15.3 (phase-11-plan) identity case reachable from the product on the stack; secrets never echoed |
| **P12.5** Providers and egress | editable AI providers page: provider, credential (D-29), test, approval, ceilings (D-34), default sensitivity | lattice re-renders from policy; a credential never widens egress (tests); provider test never sends organization content |
| **P12.6** Sources and first sync | source create/check/upload (D-32)/first sync/edit/pause/disconnect; source record page; `source.*` events; CLI paths audited | the three connector types set up entirely in the product on the stack; ACL behaviour identical to the CLI path (same documents and grants for the synthetic orgs) |
| **P12.7** End-to-end onboarding | `make prod-onboard`; Playwright journey; docs for customers (setup guide, export format specs) | the full §20 journey passes from an empty organization |
| **P12.8** Security, accessibility, closure | route sweep, mutation checks by hand, a11y matrix, GHSA re-check, full suites, docs, closure report | §23 all met |

Each checkpoint: backend and API work, frontend work, invariants, tests and non-goals
as stated in the sections it references; checkpoint as-built notes are added here as
each lands (as Phase 11 did).

## 22. Decisions (as proposed on 2026-10-06)

**Disposition (product owner, 2026-10-06):** D-28, D-30, D-31, D-33, D-35, D-37B
approved; D-29 approved at the security-property level (recoverable credentials
encrypted, verification-only secrets stay hashed — C3); D-32 approved with a storage
abstraction (C8); D-36 approved with identity safety and least-access reinstatement
(C2.3); D-34 and D-37A, left open, were then decided under the product owner's
delegation (C6.4, C14.3), with corrections to D-31 and D-36 (C18). The options below are
kept as proposed; Part II is the binding contract where they differ.

**D-28 — Organization bootstrap.** A: operator creates the organization, its first
OIDC connection and the first administrator's identity with one audited command; the
admin does everything else (recommended). B: public self-service. C: an emailed
one-time bootstrap link. *Trade-off:* A keeps production sign-in OIDC-only and adds no
tenant lifecycle; B/C add an authentication path and abuse surface. *Affects:* §6,
P12.1, the Phase 15 fresh-environment journey (an operator step remains).

**D-29 — Custody of secrets an administrator enters** (provider keys, OIDC client
secrets). A: reference only — the admin picks a reference and an operator places the
value (status quo; not customer-operable). B: encrypted at rest in PostgreSQL
(AES-256-GCM, one deployment key `LATTICE_SECRET_KEY` from the environment with a key
ID for rotation, a `vault:<id>` reference scheme behind the existing `SecretStore`;
values write-only through the API, decrypted only at invocation; backups hold
ciphertext) — **recommended**; ADR-011 already allows "encrypted at rest"; the
`secrets.py` sentence "the database never holds a credential" becomes "never holds a
credential in plaintext". C: an external secret manager (a new service; excluded by the
zero-cost and no-new-service rules). *Affects:* §10, §11, P12.1/P12.4/P12.5, backups,
deployment contract (one new required secret).

**D-30 — Members without SCIM.** A: SCIM required for every production organization.
B: administrators may also add a member by hand (e-mail, name, identifying-claim value;
SCIM's least-access defaults; audited) — **recommended**. C: just-in-time creation at
first sign-in for a verified domain (rejected for v1: silent membership growth).
*Affects:* P12.2, setup record "Members", identity tests.

**D-31 — Administrator capability in the product.** A: operator-only (status quo).
B: administrators grant/revoke it for others, never themselves, never removing the last
one, with reason and audit — **recommended**. *Trade-off:* a hijacked admin session can
create another admin (detectable in audit, revocable); A leaves every org dependent on
an operator for a routine change. *Affects:* the 0012 fence (changed in ADR-017).

**D-32 — How exports reach Lattice.** A: deployment-provided export locations
(operator-mounted folders per organization) that the admin selects. B: the admin uploads
an export archive into the organization's export area on a volume shared by the API
and worker (plus A for large or automated exports) — **recommended**. C: only live API
connectors (§13). *Trade-off:* A is not customer-operable on a shared deployment; B adds
an upload path with its archive-handling risks (§17) and a shared volume to the
deployment contract (object storage later behind the same interface); C needs new
connectors. *Affects:* P12.6, deployment.md §14, the Phase 15 journey.

**D-33 — New connectors in Phase 12.** A: none; the three existing types plus release
notes as configuration (recommended). B: also GitHub Issues as the first live connector
(PAT through D-29, worker egress, incremental sync; one extra checkpoint).
*Affects:* scope and duration of P12.6.

**D-34 — Who may raise AI egress ceilings.** A: operator only (status quo). B:
organization administrators, with a preview of what newly leaves, typed confirmation,
reason and audit; lowering immediate — **recommended**. C: B plus a second
administrator's approval for raising. *Affects:* P12.5, threat model (T-egress).

**D-35 — Setup navigation.** `/settings` *Organization* as the setup record, with
Identity at `/settings/identity`, Accounts under Access, sources on their own record
pages (§7) — recommended. Alternative: a dedicated `/setup` checklist route that is
hidden after onboarding (rejected: duplicates pages and goes stale).

**D-36 — Re-hire after SCIM removal.** A: stays operator-only (`member reinstate`).
B: administrators reinstate a removed member from Access (same identity, previous
access, sessions not revived, audited); SCIM `DELETE` stays terminal for the IdP —
**recommended**: small, uses the P11.3 semantics, removes an operator dependency for a
common HR event.

**D-37 — The two items the roadmap placed in Phase 12.** Organization purge (D-26): an
operator-only, audited, two-step `lattice org purge` (suspend → purge after a waiting
period, verification query, tombstone kept) in P12.8 — recommended in Phase 12 because
it is the last missing tenant-lifecycle step and v1 customers can ask to leave.
IdP migration with differing subjects: **defer to post-v1** (documented procedure: new
connection with `--new-identity-namespace`, re-provision, reinstate access) —
recommended, because it needs a mapping tool and is rare. *Alternative:* both deferred,
or both in Phase 12.

## 23. Completion criteria

Phase 12 closes only when, on the local reference stack:

1. A new organization is created by one audited operator command and everything after
   it in §5 is done in the product — no manifest, `sources.yaml`, owner SQL or
   undocumented CLI step for the supported path.
2. OIDC setup, test, update (incl. issuer-change semantics), disable/enable and secret
   rotation are administrator-operable.
3. SCIM tokens (create once, list, revoke, rotation procedure) and provisioning
   activity are administrator-operable; members can be added by hand (D-30).
4. Generation provider, credential, test, approval, ceilings and default sensitivity are
   administrator-operable, and no credential state widens egress.
5. The three supported source types are created, checked, synced, edited, paused and
   disconnected in the product, with ACL results identical to the CLI path.
6. Accounts and source-reference mappings required by those sources are
   administrator-operable, unambiguous by construction, with unmapped references visible.
7. Every setup mutation in §16 writes one audit event; views and tests do not.
8. Setup failures show fixed, actionable messages and leak none of §15's list.
9. The route sweep, cross-tenant and hidden-resource tests pass for every new route;
   SSRF tests pass; secrets never appear in responses, logs, traces or 422 bodies.
10. The full synthetic onboarding journey (§20) passes end to end on the stack and in
    Playwright.
11. Accessibility and responsive checks pass for every new page.
12. Existing Ask, evidence, access, identity and operations workflows and all suites
    stay green; eval security gates 0; `make audit` green with at most the existing
    GHSA-vfj7-8cjw-p6xm exception.

## 24. Documentation to update during Phase 12

README (setup story); implementation-plan (Phase 12 section, D-28–D-37); architecture
(setup surfaces, export area); security-model (§5 admin capabilities, §6 egress
changes by admins, §10 setup data visibility, SSRF); threat model (admin-session
hijack, egress raise, upload); data-model (`account_external_refs`, secret store,
`last_used_at`, new audit actions); deployment.md (`LATTICE_SECRET_KEY`, export volume,
`LATTICE_OUTBOUND_ALLOW`); runbook (§1 production bootstrap with `org create`, §2 org
purge, §3 providers from the product, §9 identity from the product, new setup
troubleshooting); ADR-011 (secret custody), ADR-015 (setup UX), new ADR-017;
connector-design (export format specs for customers, `account_external_refs`);
design-system only if a genuinely new reusable primitive appears (e.g. a one-time secret
dialog).

## 25. Carried forward

- GHSA-vfj7-8cjw-p6xm (braces, web dev toolchain): re-checked 2026-10-06, still 3.0.3,
  no patched release; the exception stays exactly as documented and is re-checked at
  every dependency change.
- External staging validation (phase-11-plan §21.1): unchanged; still a Phase 15 gate.
- D-25's phase-11-plan wording "no Phase 12 UI" is superseded by this plan once approved.

---

# Part II — P12.0 contracts (2026-10-06)

Binding for P12.1–P12.8. Where Part I's proposals differ, Part II wins. Each contract
marks **current behaviour** (what the code does today) and the **Phase 12 target**.
Decisions D-34 and D-37A, left open by the product owner, and the corrections to D-31
and D-36 were decided by engineering **under the product owner's delegation
(2026-10-06)**; each says so and gives its reasoning (C18).

## C1. Platform boundary and organization bootstrap (D-28)

| Platform operator (owner credential, audited commands) | Organization administrator (the product) |
| ------------------------------------------------------ | ---------------------------------------- |
| creates the tenant trust root: organization, initial settings, first OIDC connection, first administrator's membership and directory identity | everything after the first sign-in: identity settings, members, administrators, accounts, mappings, providers, credentials, egress (C6), sources, imports |
| suspend / reactivate / (Phase 15) purge an organization; `data_origin`; outbound targets for private endpoints (C4); "all administrators lost"; the `--new-identity-namespace` connection replacement; deployment keys | — |

**Command** (P12.1): `lattice org create --slug S --name N --issuer URL --client-id ID
[--client-secret-ref env:… | --client-secret-stdin] [--subject-claim sub]
--first-admin-email E --first-admin-identifier V [--first-admin-name …] --reason R`.

- **One transaction**: organization, `org_settings` (defaults: external ceiling
  `internal`, organization-controlled `NULL`, default sensitivity `internal`,
  `data_origin = customer`), OIDC connection (new opaque `login_key`), a **new** user owned
  by the organization, the membership (`is_admin = true`, clearance `internal`, scope
  `assigned`, nothing else), the directory identity `(connection, external_id = V)`, and
  `organization.created` + `sso.connection_created` + `access.member_changed` audit
  events (actor `operator:cli`). All or nothing.
- **Deterministic and safe on retry**: the slug is the idempotency key. Re-running with
  identical arguments prints *already exists, unchanged* and writes nothing; any
  difference is refused, naming the differing field, and changes nothing (no partial
  update — later changes go through the product or `sso update`).
- **Identities**: the first administrator is a new user of this organization even if the
  e-mail exists in another one (users are organization-owned, D-20); nothing is linked
  by e-mail; no membership is ever created from an e-mail match or at sign-in (no JIT).
  The identifier `V` is the exact value of the connection's identifying claim for that
  person (C2.1).
- **Output**: the sign-in URL and the redirect URI to register; never a secret (a
  `--client-secret-stdin` value is read from stdin, encrypted (C3) and never echoed);
  the issuer must pass the outbound policy (C4) unless the operator also registers it as
  a target.
- **First administrator**: signs in at `/sso/<login key>`; that sign-in marks the
  connection verified (C12). Until then the organization has no product access.

## C2. Members and administrators

### C2.1 Manual member creation (D-30)
Fields (all required unless marked): **e-mail** (contact and display; unique per
organization; never used to match a sign-in), **display name**, given/family name
(optional), **identifier** — the exact value the active connection's identifying claim
(`subject_claim`, shown on the form) carries for that person. Stored: a new organization
user, a membership with the D-20 least-access defaults (clearance `internal`, scope
`assigned`, no accounts, departments, groups, grants, not an administrator — the fenced
function writes them; the request cannot carry access), and a directory identity
`(connection_id, external_id = identifier, user_name = e-mail)`. The identifier is
compared byte-for-byte after NFC normalisation and trimming, case preserved — when the
claim is `email` it is still an identifier, not an e-mail match. Requires an active
connection (otherwise *Blocked: connect identity first*). Refused (409, fixed code) if
the identifier or the e-mail already belongs to a member of this organization; another
organization's identities are invisible and irrelevant. Hand-added members are managed
in the product (suspend/reactivate in Access); SCIM-created members stay IdP-managed. If
SCIM later sends `POST /Users` with an `externalId` a hand-added member holds, the
answer is SCIM `409 uniqueness`, recorded as `scim.request_refused` (C10) so the
administrator sees it; nothing is adopted silently.

### C2.2 Administrator capability (D-31, with the C18 correction)
Granted or revoked for **another** member of the same organization by an administrator,
through a preview ("administration grants no access to content"), a reason, the fenced
function `lattice_set_admin` and one `access.member_changed` event. Refused: self-grant,
self-revoke (D-17), a removed member, and any product action (revoke, suspend,
reinstate-to-least-access of an admin is not a reduction) that would leave **zero active
administrators** — the ADR-017 lock-then-count rule makes concurrent requests safe. The
identity provider and operators are not bound by the last-admin rule (C18).

### C2.3 Reinstating a removed member (D-36)
- **Identity**: the original membership row and its **original directory identity**
  `(connection_id, external_id)` are reactivated — found by membership ID, never by
  e-mail. Refused if the directory identity's connection is not the organization's active
  connection (a replaced namespace, D-37B) or if any other membership in the organization
  now holds that identifier or e-mail (conflict, no duplicate is created). Identities are
  organization-scoped: there is no cross-organization lookup to relink.
- **Access: least access.** The fenced function resets clearance to `internal`, scope to
  `assigned`, removes account assignments, departments, administrator-defined group
  memberships and administrator grants, and never restores `is_admin`. Source-synced
  group memberships follow the sources as always. The preview shows what the person had
  ("Before removal: Confidential · 3 accounts · 2 groups") and that it is **not**
  restored; the administrator re-grants through the normal Access change.
- Sessions are not revived. One `access.member_changed` event with before/after.
- SCIM after reinstatement: requests for that `externalId` resolve to the reinstated
  identity (`filter externalId eq …`); a `POST` for it answers `409 uniqueness`; the IdP
  stays authoritative for its later deactivation or removal.
- **Operator `member reinstate` (P11.3) keeps restoring prior access**: it is the
  incident-reversal path (e.g. an IdP that removed people in error). The difference is
  deliberate and documented (C18).

## C3. Secret model (D-29)

### C3.1 Classes
| Class | Examples | Storage | Recoverable |
| ----- | -------- | ------- | ----------- |
| **Recoverable credential** | OIDC client secret; generation-provider API key (Gemini); future connector credentials | encrypted at rest in PostgreSQL (`encrypted_secrets`) **or** an environment reference (`env:…`, unchanged) | yes, by the process that presents it |
| **Verification-only secret** | SCIM bearer tokens; session tokens; OIDC `state`/binding values | one-way hash (SHA-256 of a 256-bit random token), shown once | no |
| **Deployment secret** | database passwords, `LATTICE_CLIENT_KEY_SECRET`, the master keys below | the deployment's environment / platform secret store | n/a (operator) |

Verification-only secrets never move into decryptable storage.

### C3.2 Construction
- **AEAD: AES-256-GCM** from `cryptography` (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`;
  already locked at 50.0.2 through `pyjwt[crypto]`, becomes a direct dependency in P12.1).
  Chosen because it is a standard, audited AEAD with associated data, constant-time in
  OpenSSL, and available without new services. Fernet was rejected (no associated data,
  so a ciphertext could not be bound to its record). No custom construction.
- **Nonce**: 96 bits from `os.urandom` per encryption, stored with the ciphertext; never
  reused deliberately. Volume is tiny (a few writes per organization), far below the
  2³² random-nonce bound per key.
- **Associated data** (authenticated, not secret):
  `"lattice-secret/v1" | organization_id | secret_id | owner_kind | owner_id | key_id`.
  Moving a ciphertext to another organization, record or credential type fails
  authentication.
- **Row** `encrypted_secrets(id, organization_id, owner_kind, owner_id, key_id, nonce,
  ciphertext, hint, created_at, destroyed_at)`; `hint` = the last four characters only
  for provider keys whose format makes that non-sensitive (never for OIDC secrets).
  Reference scheme **`vault:<secret_id>`** behind the existing `SecretStore` protocol;
  `env:` and `memory:` stay. PostgreSQL is storage for ciphertext, not a secret manager.
- **Plaintext** is accepted only by create/replace/rotate request models typed
  `SecretStr`, encrypted before any write, never returned, and never in logs, traces,
  audit payloads, URLs, metrics, caches or validation errors (422 bodies name the field,
  never the value; secret routes' bodies are excluded from request logging). Status
  (*set*, *rotated on*, *hint*, *last test*) is exposed; the value is not.

### C3.3 Master keys
- `LATTICE_SECRET_KEYS` = `id1:<base64 32 bytes>,id2:…` and `LATTICE_SECRET_ACTIVE_KEY`
  = `id2`, provided by the deployment's environment; **never in PostgreSQL**, never in
  images, never logged. Production refuses to start an API without them once any
  `vault:` reference exists (check-deployment: every stored `key_id` is configured —
  IDs compared, keys never read by the check).
- **Lifecycle**: generated by the operator (`lattice secrets new-key` prints one, or
  `openssl rand -base64 32`), stored in the platform's secret mechanism, **backed up
  separately from the database** (a database restore without its key restores
  credentials that cannot be decrypted: they must be re-entered).
- **Master-key rotation**: add the new key, make it active, restart the API, run
  `lattice secrets rekey` (re-encrypts every live ciphertext under the active key, one
  `secret.rekeyed` operator event per organization with counts), verify, then remove the
  old key. No downtime; old backups need the old key — keep it as long as those backups.
- **Credential rotation** (administrator): a new `encrypted_secrets` row and a new
  reference; the old row is destroyed (ciphertext set to `NULL`, `destroyed_at`). Live
  data no longer holds it; backups do until they expire (C3.5).
- **Revocation**: the credential's status `revoked` excludes it at the next request
  (ADR-011) and its ciphertext is destroyed.

### C3.4 Who may decrypt what
| Process | Holds master keys | May decrypt | Why |
| ------- | ----------------- | ----------- | --- |
| api | yes | OIDC client secrets (token exchange at callback, test sign-in); generation-provider keys (Ask, provider test) | it presents them |
| worker | **no** (no keys, no privilege on `encrypted_secrets`) | nothing | Phase 12 adds no live connector; embedding keys stay deployment environment values |
| ops / operator commands | only for `secrets rekey` and `org create --client-secret-stdin` (encrypt) | rekey only | rotation |
| migrate | no | nothing | — |
| web | no | nothing; never receives plaintext after submission | — |
| tests | a fixed test key | everything in test databases | — |

### C3.5 Compromise cases
- **Ciphertext stolen without the key** (database dump, backup, replica): values stay
  confidential; tampering fails authentication. No action beyond the usual incident
  procedure.
- **Master key compromised** (with or without the ciphertext): treat every recoverable
  credential as exposed — anyone holding a backup can decrypt it. Rotate the master key
  *and* rotate every credential at its source (provider keys, OIDC client secrets);
  re-keying alone is not enough. Runbook procedure in P12.1.
- **API process compromised**: it holds keys and ciphertext — same as the `env:` model
  today. Out of scope for encryption at rest by definition.
- **Decryption/authentication failure** at use: fail closed with a fixed code
  (`credential_unavailable` for providers → `evidence_only`; `sso_unavailable` for
  sign-in), a `secret.decrypt_failed` log event with the secret ID and code (no value),
  a metric; never a retry with another key ID, never a fallback to another credential.

## C4. Outbound target policy (SSRF)

One module (`lattice.outbound`) used for every request Lattice makes to a URL an
administrator entered or a discovery document returned: OIDC discovery, JWKS, token
endpoint; generation-provider endpoints; future connector endpoints.

| Class | Allowed | Who registers |
| ----- | ------- | ------------- |
| **Public endpoint** (default for administrator-entered URLs) | `https` only; no userinfo, query or fragment in a base URL; port **443** unless the target is registered; every resolved address must be globally routable | the administrator, by entering the URL |
| **Registered private target** (organization-controlled servers under D-15, a verification IdP) | the exact `scheme://host:port` the operator registered for that organization and purpose (`oidc`, `generation`) | operator: `lattice outbound allow --org --url --purpose --reason` (audited, `outbound.target_*`) |
| **Deployment-local** (`local` trust class, e.g. loopback Ollama) | operator-configured only, as today | operator |

- **Address rules** (Python `ipaddress`, not string matching): refuse loopback, private
  (RFC 1918, `fc00::/7`), link-local (`169.254/16`, `fe80::/10`, incl. the cloud metadata
  addresses), CGNAT `100.64/10`, multicast, unspecified, reserved/documentation ranges,
  benchmarking `198.18/15`, and IPv4-mapped/compatible/NAT64/6to4 forms of any of these
  (unwrapped before checking). IP literals are judged the same way.
- **Resolve/connect consistency**: the check runs **at connect time** inside the HTTP
  client's network backend (a custom `httpcore` backend): every address actually
  connected to is checked, so DNS rebinding between a "check" and a "connect" cannot
  pass. TLS verifies the hostname as usual.
- **Redirects** are not followed (discovery, JWKS, token and provider calls do not need
  them); a 3xx is an error code.
- **Discovery documents**: `jwks_uri`, `token_endpoint` and `authorization_endpoint`
  must be `https` and are subject to the same class as the issuer (a public issuer
  cannot point Lattice at a private address). The authorization endpoint is only a
  browser redirect but is validated anyway.
- **Bounded**: 5 s connect, 10 s total, 1 MiB response cap; fixed error codes
  (`target_not_public`, `target_unreachable`, `tls_failed`, `redirect_refused`,
  `response_too_large`); never a response body, resolved address or timing detail in a
  message.
- **Current behaviour**: operator-entered URLs only, `https` (http for localhost)
  enforced, no address policy. **Target**: the policy above for all outbound calls; the
  existing operator-created connections with private issuers (the verification IdP)
  receive registered targets in the P12.1 migration's runbook step.

## C5. Identity setup (OIDC, SCIM) and D-37B

- **OIDC in the product** (P12.4): add (when none is active), test sign-in, update client
  ID and secret (rotation: new secret encrypted, old destroyed; sessions kept), change
  issuer or identifying claim (separate action: preview "every session from this
  connection ends", typed confirmation, reason — the P11.3 `--subjects-unchanged`
  semantics), disable (preview: sign-in stops, *N* sessions end), re-enable. One active
  connection per organization.
- **Not in the product** (D-37B): replacing a connection with a new identity namespace or
  migrating to an IdP whose subjects differ. The issuer-change dialog states it plainly:
  *"Only for the same identity provider at a new address. Moving to a different identity
  provider is not supported — contact your Lattice operator."* Post-v1.
- **Test sign-in**: a sign-in flow with purpose `test` bound to the administrator's
  session; on success it records `oidc_connections.last_success_at` and shows only the
  administrator's own identifying-claim value and whether it matches their directory
  identity; it creates no session.
- **SCIM**: tokens created in the product (256-bit random, shown once in a dialog with
  `Cache-Control: no-store`, stored as a SHA-256 hash), revoked with a preview; new
  column `scim_tokens.last_used_at` (minute precision, written by the SCIM path);
  rotation is create → switch the IdP → revoke. Provisioning activity = audit events by
  `identity_provider` actors plus `scim.request_refused`. Users only (D-20).

## C6. Providers and egress (D-34 — decided under delegation)

### C6.1 Seven separate facts, never merged in UI or code
1 credential exists · 2 credential test succeeded · 3 provider record approved for a
level · 4 provider trust class · 5 organization egress ceiling for that class · 6 question
sensitivity (D-13 default, raised per question) · 7 call-time authorization of each
evidence item. Only the invocation rule combines them, at call time. A credential and a
passing test change only facts 1–2 and never a cell of the egress lattice.

### C6.2 Escalation defined
A change is an **escalation** when the policy reading after it would allow some
(level, provider) cell that the reading before it did not — computed by the same function
that draws the egress lattice, on the Now and After configurations. Escalations include:
raising the external or organization-controlled ceiling, setting the latter from `NULL`,
approving a provider at a higher level, enabling a provider whose approved level is above
what is allowed now, lowering the default question sensitivity. Anything else (lowering,
disabling, revoking, renaming) is not, and applies at once.

### C6.3 Options evaluated
| | A — step-up | B — risk-tiered second admin | C — second admin for every raise |
| - | ----------- | ---------------------------- | -------------------------------- |
| Stolen session cookie alone | **insufficient** if step-up is a fresh IdP re-authentication | insufficient for high-risk tiers; sufficient for "small" raises (whose definition is itself a judgement) | insufficient |
| Compromised IdP account of an admin | sufficient (also defeats every model for one admin's account; detectable) | needs a second compromised admin for high risk | needs a second |
| One-admin organization | works | high-risk raises **impossible** without an operator | every raise impossible without an operator |
| Phase 15 onboarding | the first admin completes setup alone | needs two admins before choosing an external provider | needs two admins |
| Session architecture | an OIDC flow variant (`max_age=0`, `prompt=login`, `auth_time` check) bound to the session and the change | approval requests table, expiry, notification (none exists), self-approval rules | same as B |
| Cost | small: one flow purpose, one grant table | medium–large | medium–large |
| Audit | one event with the step-up time | request + approval + application events | same |

### C6.4 Decision: **A, with a real step-up**
High-impact = every escalation (C6.2). It requires, in order: the Now → After preview
naming each newly allowed cell ("Confidential content may now be sent to Gemini (external
API)"); a reason; typed confirmation of the organization's name; then a **fresh
re-authentication at the identity provider**: an OIDC authorization request with
`max_age=0` and `prompt=login`, `nonce` and PKCE as for sign-in, returning an ID token
whose `auth_time` is at most 5 minutes old and whose identifying claim equals the
caller's own directory identity. The callback creates a **single-use step-up grant**
bound to the session, the member and the SHA-256 digest of the exact previewed change,
valid for 5 minutes; `lattice_apply_egress_change` consumes it and refuses any other
change. One `egress.levels_changed` / `provider.approval_changed` event records the
change, reason and step-up time. Lowering is immediate and needs none of this.

**Why it is secure enough against session compromise**: a stolen session cookie (or a
browser left open) cannot complete the step-up without the person's IdP credentials and
whatever MFA the IdP enforces at `prompt=login`; the grant is bound to one previewed
change, one session and five minutes; CSRF protections already apply. Residual risk: an
attacker with the administrator's IdP account — which already controls everything that
administrator controls — is detected through the audit event and a new metric/alert
(`lattice_egress_escalations_total`, ticket severity, P11.6 style). An IdP that ignores
`max_age` is detected by the `auth_time` check (no fresh `auth_time` → refused,
`step_up_not_fresh`).

**One-admin organization**: fully self-sufficient. **Development**: the verification IdP
honours `max_age`; the development persona sign-in cannot step up (escalations are
refused in development unless `LATTICE_ENV=test`, where a test-only shortcut exists).
**Implementation impact** (P12.5): `oidc_login_attempts.purpose` (`sign_in`, `test`,
`step_up`) and `session_id`; `step_up_grants(id, organization_id, session_id,
membership_id, change_digest, created_at, expires_at, consumed_at)`; the verification IdP
emits `auth_time` and honours `max_age`; ADR-017's escalation function.

## C7. Sources and supported types (D-33)

| Name in the product | Connector type | It is | It is not |
| ------------------- | -------------- | ----- | --------- |
| **Documents (Markdown export)** | `document_repository` | Lattice's Markdown interchange format with YAML front matter for access | a Google Drive or Notion integration |
| **Tickets (JSON Lines export)** | `ticketing` | Lattice's ticket interchange format, shaped like a Zendesk export | a Zendesk integration |
| **Slack workspace export** | `chat_export` | Slack's own workspace export (a zip) | a live Slack integration |

Release notes and policies are a Documents source with authority *system of record*.
Every type is **export-based**: data arrives as an import (C8); syncs read the latest
ready import; nothing is fetched from the source system. Format specifications are
published in `connector-design.md` §3 (P12.6). No live connector in Phase 12; GitHub
Issues is the first future candidate.

Product operations (P12.6): create (type, name, key, defaults, authority,
account-scoped), upload/replace an import, *Check import* (the dry-run pipeline: counts
and codes only), first sync, edit safe settings, pause/resume (`paused`, refused
requested syncs; content stays searchable), disconnect (the existing purge: preview with
document and chunk counts, typed confirmation, reason). Connector type and key are
immutable. A source configured by an operator file (`lattice sync --sources`) stays
supported: re-running the file makes the source and its mappings match it again,
audited as `operator:sync` (the seed precedent) — never silently.

## C8. Imports and storage (D-32)

### C8.1 Contract
```
ImportStore (protocol)          filesystem backend (P12.6)      future: object storage
  begin(org_id, source_id)  →   <root>/<org_uuid>/<import_uuid>/archive.partial
  append(import_id, bytes)      streamed, size-capped, SHA-256 as it arrives
  seal(import_id) → digest
  extract(import_id, limits)→   <root>/<org_uuid>/<import_uuid>/tree/   (validated)
  tree(import_id) → read-only directory for the connector
  delete(import_id)
```
Domain state never holds a path: `source_imports(id, organization_id, source_id,
backend, status, size, sha256, entries, expanded_size, rejected_code, created_by,
created_at, ready_at, superseded_at, deleted_at)`; `sources.config` holds
`import_id` (the latest ready import) or `location_id`. The backend derives every
location from the organization and import UUIDs it generated; no user-supplied name,
filename or path reaches a host path, and no tenant is inferred from a path.

**Operator-mounted folders** stay supported as `export_locations(id, organization_id,
name, backend, location)` registered by `lattice source location add --org --name
--path` (path validated under `LATTICE_EXPORT_LOCATIONS_ROOT`, audited); administrators
pick a location by name, never type a path. Existing CLI-configured sources (absolute
path recorded by `lattice sync`) keep working unchanged (current behaviour).

### C8.2 Lifecycle
`receiving → validating → ready | rejected`; `ready → superseded` when a newer import of
the source becomes ready **and** a sync from it succeeded; `→ deleted` (files removed)
for rejected imports at once, superseded imports after 7 days, and every import of a
disconnected source. Deletion is done by the API on the transition and by an hourly
worker retention task for anything left (the worker's imports mount is read-write for
deletion only; the API's is read-write; the web has none). Uploaded files are never
served back, never public, never executed (written `0640`, no execute bits, owned by
the service user).

### C8.3 Archive security
Accepted: `.zip` (all types), a single `.jsonl` (tickets). Limits (configurable,
defaults): upload ≤ 200 MiB compressed; expanded total ≤ 1 GiB; ≤ 50,000 entries; each
file ≤ 100 MiB; per-entry compression ratio ≤ 100:1, checked while streaming (declared
sizes are not trusted); path depth ≤ 16; each name ≤ 255 bytes, valid UTF-8. Refused:
absolute paths, drive letters, `..` after normalisation, backslash separators, empty or
`.`/`..` components, symlinks, hard links and device entries (zip external attributes),
duplicate names (also case-insensitively), encrypted entries, nested archives (not
expanded). Extraction is entry by entry with these checks — never `extractall`. A
malformed archive is `rejected` with a fixed code (`archive_invalid`, `too_large`,
`too_many_entries`, `ratio_exceeded`, `unsafe_path`, `link_refused`,
`duplicate_name`, `unsupported_layout`) and its files are removed. The connector then
reads the tree as it reads a local export today.

### C8.4 Transport limits
The global API body cap (64 KiB) and the ingress cap (1 MiB) stay; the single upload
route (`POST /v1/admin/sources/{id}/imports`, streamed) has its own cap, and the ingress
contract gains that one route-specific limit (deployment.md §4, P12.6). The reference
stack mounts one `imports` volume read-write in api1, api2 and the worker.

## C9. Source and import privacy

Setup surfaces show operational metadata only: counts, codes, opaque references, sizes,
digests, times, export structure counts (files, records). Never: message, ticket or
document bodies; previews or snippets; file names from an import (they can be
protected titles); private channel names; content-derived mapping suggestions — unless
the existing authorization independently lets this administrator read that document
(the Phase 8 rule for failure identifiers).

## C10. Account mapping (`account_external_refs`)

```
account_external_refs(
  id uuid PK, organization_id uuid NOT NULL,
  source_id uuid NOT NULL,   FK (organization_id, source_id) → sources ON DELETE CASCADE
  ref_kind text NOT NULL CHECK (ref_kind IN ('ticketing_organization', 'chat_channel')),
  external_ref text NOT NULL CHECK (external_ref = btrim(external_ref) AND length 1..256),
  account_id uuid NOT NULL,  FK (organization_id, account_id) → accounts ON DELETE RESTRICT
  created_at, updated_at, created_by text
  UNIQUE (organization_id, source_id, ref_kind, external_ref)
)
```
- **Deterministic identifier**: the value the connector reports as `account_ref`
  (ticketing organization ID as decimal text; Slack channel ID), normalised as
  `external_id`s are (NFC, trimmed, case preserved). `ref_kind` must fit the source's
  connector type (CHECK through the fenced function).
- **Cardinality**: one reference → at most one account (the unique key); one account →
  many references, across sources. Cross-organization mapping is impossible (composite
  foreign keys, RLS).
- **Sync**: ingestion reads the table instead of `sources.config`; behaviour unchanged —
  an unmapped record of an account-scoped source is not indexed
  (`access_unmappable:account`); a previously indexed record whose reference loses its
  mapping is **quarantined** at the next sync (Phase 4 rule); a remap moves the
  documents to the new account at the next sync (access update).
- **Migration** (P12.3): `sources.config.account_refs` and `channels.*.account` move into
  the table; channel *sensitivity* stays in source settings. The synthetic organizations
  must index the same documents with the same accounts before and after (test).
- **Source disconnected**: its mappings are deleted with it (cascade); the
  `source.disconnected` event records how many. Accounts are untouched.
- **Account status**: today `accounts.status` is `active`, `churned` or `prospect` and
  has no effect anywhere (display only). Those three stay editable labels with no effect.
  P12.3 widens the CHECK with **`archived`**, the only status with setup consequences,
  set one-way by the product (un-archiving is operator-only).
- **Account archived**: no new mappings or assignments may name it; existing mappings
  keep resolving (its documents stay correctly scoped — removing them would quarantine
  them); member assignments stay (archiving is not an access change); the Ask account
  picker shows it as *archived*. Un-archiving is operator-only. Accounts are never
  deleted by the product.
- **Changes**: `account.mapping_changed` with before/after per reference, in the same
  transaction (ADR-017 coupling).
- **Unmapped references view**: from the latest run's `access_unmappable:account`
  failures, grouped by reference: the reference value and the count of records waiting.
  For chat sources the reference is the channel ID; the channel name is shown only for
  public channels (`channels.json`), never for private ones (`groups.json`), regardless
  of the viewer being an administrator (D-18). No suggestions from content or names.
- Scoped Ask unchanged; the account timeline stays post-v1.

## C11. Audit families

Unchanged: `access.*` (incl. `access.member_changed` for hand-created members,
reinstatement and administrator capability), `sso.connection_*`, `scim.token_*`,
`provider.*`, `credential.*`, `egress.levels_changed`, `session.revoked`,
`organization.suspended|reactivated`.

| New action | Mutation | Actor |
| ---------- | -------- | ----- |
| `organization.created` | `lattice org create` | operator |
| `organization.updated` | name; default question sensitivity | member |
| `account.created` · `account.updated` · `account.archived` | account lifecycle | member / operator:sync |
| `account.mapping_changed` | map, remap, unmap (before/after) | member / operator:sync |
| `source.created` · `source.updated` · `source.paused` · `source.resumed` · `source.disconnected` | source lifecycle, from the product **and** from `lattice sync` / `source disconnect` | member / operator |
| `source.import_accepted` · `source.import_rejected` | an import became ready / was rejected (size, digest, code — no names or content) | member |
| `sso.connection_tested` | a test sign-in result, at most one event per connection per hour per outcome | member |
| `scim.request_refused` | a SCIM request refused for a reason an administrator can act on (uniqueness, schema) — the SCIM error type only | identity_provider |
| `outbound.target_added` · `outbound.target_removed` | operator registers a private target | operator |
| `secret.rekeyed` | master-key rotation, per organization, counts | operator |

Not audited: page views, form validation, *Check import*, provider tests (logged and
counted, they change no trust state), failed step-up attempts beyond the existing
sign-in metrics. Every new action is pinned to `member:` actors for `lattice_app`
inserts (ADR-017); audit payloads never carry a secret, a path, a file name or content.

## C12. Setup status (backend-owned)

`GET /v1/admin/setup` (administrators) returns, for each category, `state`, `reason`,
`message` (fixed text keyed by reason), `facts` (counts, times — never names of hidden
resources) and `action` (`{kind: "link", href}` to the owning page, or `null` when only
an operator can act). The frontend renders it; it never infers a state.

States: `complete` · `required` · `optional` · `in_progress` · `attention` · `blocked`.

| Category | State · reason (condition) |
| -------- | -------------------------- |
| `organization` | `complete·active` · `attention·single_administrator` (exactly one active admin) · `blocked·suspended` · `blocked·no_administrator` (zero active admins; action null: operator) |
| `identity` | `required·no_connection` · `required·not_verified` (active, no `last_success_at`) · `complete·verified` · `attention·connection_disabled` · `attention·provider_unavailable` (last sign-in attempt `sso_unavailable` after the last success) |
| `members` | `blocked·identity_required` · `required·only_administrators` (no active non-admin member) · `complete·members_active` |
| `provisioning` | `optional·not_configured` · `optional·token_not_used_yet` · `complete·in_use` (an unrevoked token with `last_used_at`) · `attention·no_valid_token` (all revoked while IdP-managed members exist) |
| `ai_policy` | `optional·evidence_only` (no enabled generation provider: answers show evidence without a written answer) · `complete·generation_ready` · `attention·credential_failed` (last test or last call `credential_unavailable`) · `attention·policy_excludes_default` (the provider is not allowed for the default question sensitivity) |
| `sources` | `required·no_sources` · `required·awaiting_import` · `attention·import_rejected` · `in_progress·syncing` · `attention·sync_failed` (latest run `failed`) · `complete·synced` (≥ 1 active source with a `succeeded`/`partial` latest run) |
| `accounts` | `optional·not_needed` (no account-scoped source, no channel mapping) · `attention·unmapped_references` (count) · `complete·mapped` |
| `first_answer` | `blocked·sources_required` · `required·no_answer_yet` · `complete·answered` (≥ 1 trace `answered`/`partial`) |

No percentage, score or celebration. Each reason has a test that builds its condition.

## C13. Operator-only and post-v1

**Operator-only in v1**: organization create, suspend, reactivate, purge (Phase 15),
`data_origin`, un-archiving an account, outbound private targets, export locations,
master keys and `secrets rekey`, `sso add --new-identity-namespace`, appointing an
administrator when none is active, `member reinstate` with prior access (incident
reversal), retention and deployment settings, embedding provider choice.

**Post-v1**: live connectors (GitHub Issues first), IdP migration with differing
subjects (D-37B), the account timeline workspace, SCIM `Groups`, multiple active IdPs per
organization, self-service organizations, per-organization embedding models.

## C14. Organization purge (D-37A — decided under delegation)

### C14.1 What exists
`audit_events` and every tenant table reference `organizations` with `ON DELETE
CASCADE`; the audit trigger blocks `UPDATE` only. A `DELETE FROM organizations` as the
owner would therefore erase the tenant **including its whole audit trail**, leaving no
record that a purge happened; backups and the WAL archive would keep the data until they
expire.

### C14.2 Options
- **A — implement in Phase 12** with the existing architecture: impossible coherently —
  evidence that the purge happened must outlive the tenant, which needs a record outside
  every tenant (a platform-level table), plus a stated backup-expiry commitment.
- **B — define semantics now, build in Phase 15** (chosen).
- **C — post-v1**: rejected; an enterprise v1 needs a way to honour "delete our data" at
  contract end.

### C14.3 Decision: **B**
Executable purge is built in Phase 15 (GA closure: data-lifecycle commitments,
fresh-environment and offboarding journeys). Until then **suspension** is the control
(sign-in, SCIM, worker stopped; sessions revoked; P11.3), and runbook §2 says so.
Semantics fixed now:
1. Two operator steps: `org suspend` (existing) then `org purge --slug --confirm-slug
   --reason` after a waiting period (default 7 days from suspension, overridable only
   with an explicit flag).
2. Before deletion: a **platform deletion record** is written — a new table outside
   tenant scope, owner-written only, no RLS tenant, never readable by `lattice_app`:
   organization ID, a keyed hash of the slug, requested/executed times, operator,
   reason, per-table row counts. No content, names or identifiers of people. **This is
   new architecture** (a cross-tenant record) to be recorded in a Phase 15 ADR.
3. Deletion: the tenant's rows in dependency order (or the cascade) in one transaction,
   then imports and export locations removed from storage, then encrypted secrets are
   gone with their rows (no key change needed).
4. Verification query: zero rows for the organization ID in every tenant table; the
   result is stored on the deletion record.
5. Backups/WAL: the deployment's backup retention is the stated outer bound ("deleted
   from live systems at T, from backups by T + retention"); no backup surgery.
6. Platform evidence = the deletion record + the operator's log; the tenant audit trail
   is deleted with the tenant by design.

## C15. Mismatches between code and docs (tracked)

| Item | Historical | Current | Phase 12 target |
| ---- | ---------- | ------- | --------------- |
| D-16 "Phase 7 frontend builds the admin provider UX" | planned in Phase 5 closure | the Phase 7 page is **read-only** (product owner's instruction); configuration is CLI | P12.5 builds it; the register row is corrected (2026-10-06) |
| Production organization creation | — | **built (P12.1):** `lattice org create`; runbook §9 | — |
| `sources.status = 'paused'` | allowed since 0005 | nothing sets it | pause/resume (P12.6) |
| `account_external_refs` | deferred in connector-design §3.2 "until accounts are managed in Lattice" | mappings in `sources.config` JSON | the table (P12.3) |
| `secrets.py`: "the database never holds a credential" | true since Phase 6 | **changed (P12.1):** "never holds a credential in plaintext" (`vault:` built) | — |
| phase-11-plan D-25: "backend/operator only, no Phase 12 UI" | Phase 11 scope note | — | superseded by this plan (identity UX in P12.4) |

## C16. Answers to the P12.0 questions

| # | Answer (section) |
| - | ---------------- |
| 1 | A platform operator, with `lattice org create` (C1) |
| 2 | The operator binds the first admin's exact IdP identifier to a new membership in the bootstrap transaction; their first sign-in verifies the connection (C1) |
| 3 | An admin supplies e-mail, name and the exact identifier; a fenced function creates it with least access (C2.1) |
| 4 | The connection's `subject_claim` value, NFC-normalised and trimmed, case preserved, as `directory_identities.external_id` (C2.1) |
| 5 | Any organization administrator, for another member (C2.2) |
| 6 | An advisory lock per organization, count after the lock, refuse zero; a deferred trigger repeats it (ADR-017) |
| 7 | OIDC client secrets, provider API keys, future connector credentials (C3.1) |
| 8 | SCIM tokens, session tokens, OIDC state/binding values (C3.1) |
| 9 | `encrypted_secrets` in PostgreSQL as AES-256-GCM ciphertext, referenced as `vault:<id>` (C3.2) |
| 10 | AEAD confidentiality and integrity, binding to organization/record/type/key via associated data, random 96-bit nonces, key IDs (C3.2) |
| 11 | The deployment environment / platform secret store, never PostgreSQL (C3.3) |
| 12 | Values stay confidential; tampering fails closed (C3.5) |
| 13 | All recoverable credentials are exposed: rotate the key and every credential at its source (C3.5) |
| 14 | Master key: add, activate, `secrets rekey`, retire. Credential: new row, old destroyed (C3.3) |
| 15 | API decrypts OIDC and provider secrets; worker, web, migrate none; ops only to rekey (C3.4) |
| 16 | Streamed upload to the API → import store under generated IDs → validated tree → the worker reads it by import ID (C8) |
| 17 | `ImportStore` + `source_imports`; domain state holds IDs, never paths (C8.1) |
| 18 | Entry-by-entry extraction with the C8.3 limits and refusals |
| 19 | C10 |
| 20 | Its mappings are deleted with it; accounts stay (C10) |
| 21 | No new mappings/assignments; existing ones keep resolving (C10) |
| 22 | Private channels by ID only; no names from imports; counts and codes only (C9, C10) |
| 23 | The ADR-017 operation table |
| 24 | Column grants, restrictive policies, fenced functions and audit-coupling triggers; no broad grant or setup role (ADR-017) |
| 25 | C11 |
| 26 | Same membership and original `(connection, externalId)`; never by e-mail; conflicts refused (C2.3) |
| 27 | Least access; `is_admin` never restored (C2.3) |
| 28 | Product data only, per C12's conditions |
| 29 | Documents (Markdown export), Tickets (JSON Lines export), Slack workspace export (C7) |
| 30 | All three (C7) |
| 31 | The C4 policy, enforced at connect time |
| 32 | Only as operator-registered private targets (C4) |
| 33 | A: fresh IdP re-authentication bound to the exact change (C6.4) |
| 34 | A stolen session cannot pass the IdP re-authentication; the grant is single-use, change-bound and short (C6.4) |
| 35 | It works with one admin (C6.4); the last-admin rule protects it from product lock-out (C2.2) |
| 36 | B: semantics now, executable purge in Phase 15 (C14) |
| 37 | Phase 15: it needs a platform deletion record and backup-expiry commitments that belong to GA (C14) |
| 38 | Tenant audit is deleted with the tenant; a platform deletion record outlives it (C14) |
| 39 | C13 |
| 40 | C13 |

## C17. Checkpoint changes from Part I

- P12.1 adds: `encrypted_secrets` + `vault:` scheme, master-key checks and `secrets
  rekey`; the outbound policy module and `outbound_targets`; `org create`; setup status.
- P12.2 uses C2 (least-access reinstatement, last-admin rule bound to product actions).
- P12.5 adds the step-up flow and grants (C6.4).
- P12.6 uses the import store (C8) and the C8.4 route limit.
- P12.8 no longer contains organization purge (C14: Phase 15).

## C18. Review of the approved decisions (engineering, 2026-10-06)

The product owner asked for each decision to be checked. Result:

| Decision | Verdict | Note |
| -------- | ------- | ---- |
| D-28 | sound | Practical cost: the operator must obtain the first administrator's exact IdP identifier from the customer (easy with an `email` claim, a lookup with an opaque `sub`). Kept; documented in the runbook |
| D-29 | sound | Two additions: associated data includes the owning record (so a ciphertext cannot be moved between records), and the master key must be backed up **separately** — a restore without it loses recoverable credentials (they are re-entered) |
| D-30 | sound | Risk: an administrator who types another person's identifier binds that person to the record; contained by uniqueness per connection, the test sign-in mismatch message and the audit event. Mixed SCIM/manual conflicts answer SCIM 409 (C2.1) |
| D-31 | **corrected** | "Never suspend/remove the last active administrator" cannot bind the identity provider or operators: a departed last administrator must lose access. The invariant binds product actions; IdP/operator reductions to zero leave the organization in `blocked·no_administrator` until an operator appoints one (ADR-017, C12) |
| D-32 | sound | Needs a route-specific upload limit at the ingress and a read-write imports volume for the API and (deletion only) the worker — deployment-contract changes (C8.4) |
| D-33 | sound | — |
| D-34 | **decided: A with IdP step-up** | B and C make one-admin organizations depend on an operator and need an approval subsystem; a step-up that is only "session age" would not stop a stolen cookie, so A is specified as a real IdP re-authentication (C6.4) |
| D-35 | sound | — |
| D-36 | sound, **with a documented divergence** | Least-access reinstatement is right for re-hire. The existing operator `member reinstate` restores prior access and stays that way as the incident-reversal path; both are documented so the difference is intentional, not accidental |
| D-37A | **decided: B (Phase 15)** | Coherent purge evidence needs a new platform-level record — architecture that belongs with GA data-lifecycle commitments, not onboarding |
| D-37B | sound | The issuer-change UI states the limitation (C5) |

---

# Part III — As built

## P12.1 Foundations (2026-10-08)

Delivered on `phase/12-org-setup-integrations` (local commit, not pushed). Migration
**0016** (`SCHEMA_REVISION` 0016; irreversible: the downgrade drops the new tables).

| Contract | Built | Where |
| -------- | ----- | ----- |
| ADR-017 (first fence) | organization rename: column grant + restrictive policy + deferred audit-coupling trigger (`lattice_require_setup_audit`, reusable); audit insert policy widened by `organization.updated` only; every other setup table still unwritable by the API (tested statement by statement) | 0016, `lattice.setup.organization`, `PATCH /v1/admin/organization`, ADR-017 "Implemented in P12.1" |
| C3 secret model | `encrypted_secrets`, `vault:<id>`, AES-256-GCM with AAD `lattice-secret/v1\|org\|secret\|owner kind\|owner id\|key id`, keyring (`LATTICE_SECRET_KEYS`, `LATTICE_SECRET_ACTIVE_KEY`), production start-up key check, `check-deployment` "secret keys", `secrets new-key` / `secrets rekey` (audited `secret.rekeyed`), operator writers (`org create --client-secret-stdin`, `sso update --client-secret-stdin`, `provider credential set --value-stdin`), decrypt in the API's OIDC exchange and generation path, `lattice_secret_decrypt_failures_total` | `lattice.vault`, `lattice.cli_secrets`, `providers/secrets.py`, `identity/oidc.py`, `answering/policy.py` |
| C3.4 process access | API: keys + `SELECT` on `encrypted_secrets`; worker: no keys (production refuses them), no privilege; migrate: `check-config migrate` refuses keys in production; web: nothing; compose gives keys to api and ops only | `vault.keys.misplaced_keys_problem`, `compose.production.yaml`, `test_deployment_files` |
| C4 outbound policy | `lattice.outbound` (address classes, shape, connect-time `GuardedBackend`, no redirects/proxies, bounded, fixed codes); `outbound_targets` + `lattice outbound allow\|revoke\|list` (audited `outbound.target_added/removed`) | `lattice.outbound`, `lattice.orgs.outbound_targets`, `lattice.cli_org` |
| C1 bootstrap | `lattice org create` (one transaction, idempotent on the slug under an advisory lock, differing retry refused naming fields, IdP client of another organization refused, issuer public or `--register-private-issuer`) | `lattice.orgs.bootstrap`, `lattice.cli_org` |
| C12 setup status | `GET /v1/admin/setup`: pure `derive` (every reason tested) over `gather`ed facts read as the administrator's API role | `lattice.setup`, `api/routes/setup.py` |
| §7 Organization page | `/settings` "Organization" (Administration section, masthead, Jump, More sheet): the organization's facts + Rename (reason, version, audit), the setup record as a ruled ledger; no controls for P12.2+ | `apps/web/src/components/settings/OrganizationScreen.tsx` |

**Sign-in facts.** `oidc_connections.last_success_at` (set when an OIDC sign-in creates a
session — the first administrator's first sign-in verifies the connection) and
`last_failure_at` (discovery, keys, token exchange or client-secret failures; a person
cancelling at the provider is not one), at most once a minute; not audited.

**Deviations from Part II, and why**

1. **Outbound policy not yet wired into the runtime OIDC client or provider adapters.**
   C4 targets "every outbound call"; in P12.1 no URL is administrator-entered (issuers
   are operator-entered via `org create` — checked — and `sso add`/`update`). Wiring the
   connect-time backend into sign-in now would break existing private issuers (the
   verification IdP `https://idp:9443`) until they are registered, i.e. change Phase 11
   runtime behaviour for no P12.1 benefit. It is wired in P12.4 (OIDC settings) and P12.5
   (provider endpoints), together with the runbook's registration step for existing
   private issuers.
2. **Setup status reasons.** Three reasons C12 did not name, because P12.1's data has
   those states: `sources·required·not_synced` (a source with no run yet; C12's
   `awaiting_import`/`import_rejected` need imports, P12.6), `accounts·blocked·
   sources_required` (an account-scoped source not yet synced), `ai_policy·attention·
   provider_unavailable` (the enabled provider cannot be built as configured).
   Provisioning `in_use` is derived from audit events by the token's actor (C5's
   `scim_tokens.last_used_at` is P12.4); "IdP-managed members" = memberships the identity
   provider created (an `access.member_changed` creation event by a `scim:` actor) that are
   not removed.
3. **Bootstrap collision rule.** "Cross-org identity collision" is implemented as: an IdP
   client (issuer + client ID) already used by another organization is refused — so a
   token minted for one tenant's client never fits another's connection. E-mail never
   collides (users are organization-owned, C1).
4. **Identical-retry check of a `--client-secret-stdin` secret** opens the stored
   ciphertext in the operator process and compares in constant time — the operator just
   typed the value. C3.4 listed ops as "rekey only" for decryption; this is the one
   addition.
5. **`lattice_auth`** owns one more function, `lattice_secret_key_ids()` (key IDs only), for
   the API's start-up check (ADR-017 said "`lattice_auth` is unchanged except the step-up
   lookup").
6. **`encrypted_secrets`** has no `updated_at`/rotation timestamp beyond `created_at` /
   `destroyed_at` (rekey changes key ID, nonce and ciphertext in place; the event records
   it). `cryptography` became a direct dependency (C3.2), version unchanged (50.0.2).

**Not built (later checkpoints):** manual members, administrator capability, reinstatement
(P12.2); accounts and mappings (P12.3); OIDC/SCIM settings, `scim_tokens.last_used_at`,
test sign-in (P12.4); provider/credential/egress UX, step-up (P12.5); imports (P12.6).

