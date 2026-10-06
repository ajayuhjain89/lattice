# Phase 12 — Organization Setup, Identity Configuration & Integrations: plan

Status: **Draft for approval (2026-10-06). Nothing is implemented.** Branch
`phase/12-org-setup-integrations` from `main` 143392f. Decisions D-28–D-37 (§22) need the
product owner before P12.1; P12.0 is this document plus the ADR it calls for.

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

## 22. Decisions needing the product owner

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
