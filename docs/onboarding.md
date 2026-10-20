# Lattice — Organization onboarding guide

*Phase 12, P12.7 (2026-10-19). Verified on the local production-shaped reference stack
(`make prod-onboard`) and in the backend suite (`backend/tests/onboarding`). Not yet
verified on a cloud deployment: external staging remains a Phase 15 release gate
([deployment.md](deployment.md) Appendix A, phase-11-plan §21.1).*

This guide takes a customer organization from nothing to a member's first answered
question. It uses only the product and the operator commands that exist; there is no
setup wizard — the **setup record** on *Settings → Organization* (`/settings`, `GET
/v1/admin/setup`) is the checklist, and each of its rows links to the page that owns the
item. Lattice shows its work and its edges: every state below is derived by the backend
from real data, with a fixed reason; nothing is a percentage.

## 1. Who does what

| Step | Who | How |
| ---- | --- | --- |
| Create the organization, its OIDC connection and the first administrator's exact directory identity | **Platform operator** | `lattice org create` (one audited transaction; [runbook](runbook.md) §9, phase-12-plan C1) |
| Register a private identity provider's origins | **Platform operator** | `lattice outbound allow --purpose oidc` (§3) |
| Register the redirect URI with the identity provider | Customer's IdP administrator | the URI `org create` prints |
| Everything else in this guide | **Organization administrators**, in the product | Settings, Identity, Access, AI providers, Sources, Ask |
| Suspend/reactivate an organization, appoint an administrator when none is left, purge | Platform operator | audited commands (runbook §9; purge is not executable yet, §10) |

No self-signup exists, nothing is linked by e-mail and nobody is created at sign-in
(no just-in-time provisioning): a person signs in only through a directory identity an
operator, an administrator or the identity provider (SCIM) created, matched on the exact
value of the connection's identifying claim (case and spelling preserved).

**Administration is not content access.** An administrator configures identity, members,
accounts, providers and sources and reads none of the content those sources hold unless
their own membership grants it like anyone else's. Uploading or configuring a source gives
no access to it; setup and validation views show counts, codes, sizes and opaque
references only.

## 2. Operator bootstrap

```bash
lattice org create --slug wrenfield --name "Wrenfield Analytics" \
  --issuer https://idp.wrenfield.example --client-id lattice-wrenfield \
  --client-secret-stdin \
  --first-admin-email ada.quist@wrenfield.example --first-admin-identifier 00uAda \
  --reason "Customer onboarding #42"
```

* One transaction: the organization (safe defaults: external egress ceiling *internal*,
  questions *internal*, `data_origin = customer`), its OIDC connection (a new opaque
  login key), the first administrator's user, membership (administrator capability,
  least access otherwise: clearance *internal*, scope *assigned accounts*, nothing
  assigned) and directory identity, and the audit events `organization.created`,
  `sso.connection_created`, `access.member_changed` (and `outbound.target_added`).
* The client secret: `--client-secret-stdin` seals it in the vault for this connection
  (needs `LATTICE_SECRET_KEYS`, as the API); `--client-secret-ref env:LATTICE_OIDC_SECRET_<NAME>`
  names an environment secret instead; neither: a public PKCE client. It is never printed.
* **Retry is safe.** The slug is the idempotency key: an identical retry writes nothing
  and reports the organization as it is; any difference is refused, naming the field, and
  changes nothing; a failure rolls everything back. An identity provider client (issuer
  and client ID) already used by another organization is refused: each organization needs
  its own client.
* It prints the sign-in URL (`<public URL>/sso/<login key>`) and the redirect URI
  (`<public URL>/api/v1/auth/oidc/<login key>/callback`) to register with the provider.
  The first administrator's first sign-in verifies the connection.

## 3. Private identity providers (required configuration)

Lattice reaches identity providers only through its outbound policy (SSRF protection,
phase-12-plan C4). A provider on a public address on `https:443` needs nothing. A
provider on a private network, a non-standard port or `http` must be registered for the
organization by the operator, origin by origin:

* the **issuer** — `org create --register-private-issuer`, or `lattice outbound allow
  --org <slug> --url <scheme://host:port> --purpose oidc --reason …`;
* **every other origin its discovery document names**, the browser-only authorization
  endpoint included (P12.4: every discovery endpoint must pass the policy, though Lattice
  never connects to the authorization endpoint). A provider reached at one origin by the
  servers and another by browsers — the verification IdP: `https://idp:9443` and
  `https://localhost:9443` — needs both registered.

Without it the first sign-in fails as `sso_unavailable` and the setup record says
`identity·blocked·outbound_blocked`. Nothing in the product bypasses this rule.

## 4. The administrator's journey

The setup record after bootstrap, and what each row is for. Required rows must be done
for a usable organization; optional rows are complete enough as they are.

| Row | Right after bootstrap | Done when | Where |
| --- | --------------------- | --------- | ----- |
| Organization | needs attention · one administrator | a second administrator exists | Access → member → *Make administrator* (a preview states it changes no content access) |
| Identity provider | complete · verified (by the first sign-in) | — | Settings → Identity (test sign-in, client secret, disable/enable) |
| Members | required · only administrators | a non-administrator member is active | Access → *Add member* (exact identifier), or SCIM |
| Provisioning (SCIM) | **optional** · not configured | optional: a token the IdP has used | Settings → Identity → provisioning tokens (shown once) |
| AI policy | **optional** · evidence only | optional: a generation provider enabled and approved | Settings → AI providers (§5) |
| Sources | required · no sources | a source has synced | Sources (§6) |
| Account mapping | optional · not needed (until an account-scoped source exists) | every reference the latest syncs reported is mapped | Access → Accounts → *Unmapped references* |
| First answer | blocked · sources required | a member's question returned evidence | Ask |

`First answer` completes as `answered` (a written, cited answer) or — for an
organization that runs **evidence only**, a legitimate configuration — as
`evidence_returned` (P12.7): authorized evidence shown without a written answer.

**Members.** *Add member* takes the exact value the identity provider sends in the
connection's identifying claim (for `sub`, the provider's user ID — not the e-mail).
A new member has least access: clearance *internal*, scope *assigned accounts*, nothing
assigned. Grant accounts, departments or groups on the member's page (previewed, with a
reason). Suspending ends their sessions at once; reactivating restores sign-in, not old
sessions.

**Accounts.** Create the customer accounts your sources refer to (Access → Accounts):
key (immutable), name, aliases, business status; archive and reactivate keep their
mappings. Account documents and tickets are readable only by members whose scope
includes the account (D-10).

## 5. AI providers, BYOK and egress (optional)

Without a generation provider Lattice answers **evidence only**: members get the
authorized evidence for their question, no model is called, and the Ask says why. To add
written answers (Settings → AI providers):

1. Configure a provider (Gemini — an external API; or Ollama on a target your operator
   registered). It is created **disabled and unapproved**: nothing can be sent to it.
2. Set its credential (BYOK: encrypted at once, never shown again; *paid* or *unpaid*
   data use). **A credential is not permission**: it changes nothing about what may be
   sent.
3. Enable it, then **approve** it for a data level. Approving or raising what may leave
   Lattice is an *escalation*: it needs a **fresh re-authentication** at your identity
   provider (D-34: `max_age=0`, `auth_time` within 5 minutes), bound to that exact change,
   usable once, voided by any later policy change. Lowering never needs it.
4. *Test* proves only that the credential or endpoint works; it never sends organization
   content.

A provider whose trust class is unknown, that is disabled, unapproved, above the
organization's ceilings, or whose credential is missing or revoked receives nothing; the
AI policy row says which (`not_enabled`, `not_approved`, `credential_missing`, …) and
answers fall back to evidence only. Unauthorized evidence never reaches a prompt.

## 6. Sources, exports and the first sync

Supported source types are **export-based** — not live integrations:

| In the product | Upload | Format |
| -------------- | ------ | ------ |
| Documents (Markdown export) | a `.zip` of `.md` files | [connector-design.md](connector-design.md) §3.1 (front matter carries access) |
| Tickets (JSON Lines export) | a `.zip`, or one `.jsonl` page as is | §3.2 (Lattice's ticket interchange format; not a Zendesk integration) |
| Slack workspace export | Slack's own `.zip`, unchanged | §3.3 (not a live Slack integration) |

What each accepts, and the archive rules, are in connector-design §3.4 and
phase-12-plan C8.3 (as built in Part III, P12.6). Default limits: 100 MiB per upload,
512 MiB expanded, 20,000 entries, 32 MiB per file (an operator may raise them, with the
ingress limit, deployment.md §4).

The first sync, on the source's record:

1. **Add source** — type, name, key (immutable), default sensitivity, departments,
   authority, account scoping (on by default for Tickets), reason.
2. **Upload an export.** It is streamed, counted (the declared length is never trusted)
   and validated entry by entry; a refused upload says why with a fixed code and changes
   nothing. An accepted one becomes the source's current export and is **checked**
   automatically: the sync pipeline as a dry run (no writes, no model) — records read,
   would create/update/delete, invalid, held back, failure codes, and the exact account
   references that have no account.
3. **Map references.** Tickets name their customer organization by ID; Slack channels by
   channel ID. A reference without a mapping is **held back — never indexed without its
   account** (fail closed): map it in Access → Accounts → *Unmapped references* (to an
   existing account or a new one). Mapping can happen before the first sync (the check
   reports the references).
4. **Sync now.** The worker syncs exactly the export that was current when you asked;
   the record shows queued → running → the run's counts, failure codes and next step.
   Mappings made later apply at the next sync (the setup record says `sync_required`).

**Operator-managed sources** (an operator's `sources.yaml`, `lattice sync`) keep working
beside product-managed ones; they are read-only in the product (sync, pause, resume and
disconnect work), take no uploads, and a sources file can never take over a
product-managed source (`source_config:managed_in_product`) or touch its mappings. No
API accepts a server path.

## 7. The first question

A member signs in at the organization's sign-in URL and asks on *Ask*. Retrieval runs
inside their authorization predicate: they see only evidence they may read (their
clearance, departments, groups, private channels they belong to, and accounts in their
scope). The answer's exhibits open as evidence; *Trace* shows how it was produced.
Another member without access to that content gets *insufficient evidence* — no
snippets, no titles, the same 404 for its evidence and trace as for a random ID — and so
does an administrator without content access. Account names are never a side channel.

## 8. Recovery

| Situation | What the product shows | Recovery |
| --------- | ---------------------- | -------- |
| First sign-in fails `sso_unavailable` | Identity row: `outbound_blocked` or `provider_unavailable` | Operator registers the private origins (§3); check the IdP is reachable |
| A member cannot sign in | — (`sso_failed`, nothing created) | The identifier must be the exact claim value; suspended members are refused |
| An upload is refused | the record's *Upload* says why (fixed code); current export unchanged | fix the export and upload again |
| An accepted export cannot be read as a whole (e.g. a broken `users.json`) | the check's error; *Next: Replace the export*; Sync now refused (`import_invalid`) | upload a good export (see the limitation below); indexed evidence is untouched meanwhile |
| References without an account | Account mapping row and the check; records held back | map them, then sync |
| A sync failed | Sources row `sync_failed`; the run's codes; *Retry sync* | fix the cause; retry (the worker also retries transient errors) |
| No generation provider usable | AI policy row says why; Ask shows evidence only | §5 |
| A source is paused | syncs refused (`source_not_active`); content stays searchable | *Resume*, then sync |
| A change was made from a stale page | 409, nothing changed | reload and redo it |

## 9. Verification

`make prod-up IDP=1 && make prod-onboard IDP=1` runs the whole journey on the local
reference stack in production mode: two new organizations per run (no seed), the API
journey (`deploy/verification/onboard_check.py`: bootstrap and retries, OIDC sign-in,
SCIM, members, accounts, providers and the D-34 step-up, uploads through Caddy, the
worker's checks and syncs, fail-closed mapping, authorized and denied Asks, evidence and
trace, two tenants, ingress limits, import replacement, source lifecycle, operator
sources, member lifecycle, logs) and then the same journey in the real web app
(Playwright project `onboarding-stack`). Synthetic data only
(`data/synthetic/onboarding`); no model is called.

**Re-running and resetting.** Each run is additive: it creates two new run-unique
organizations (and their imports in the stack's `imports` volume) and never deletes the
previous runs' — organization purge is not executable before Phase 15, so there is no
"clean up after the drill" command. The reference stack is disposable verification
infrastructure: to start again from an empty database, `make prod-destroy` (deletes the
`lattice-reference` Compose project's volumes only — its database, imports, backups and
Caddy data; never the development project `lattice` or its database) and `make prod-up
IDP=1`. Nothing in the drill or the Makefile resets a development or production database.

## 10. Known limitations (P12.7, reviewed in P12.8)

* **A replaced export is not kept.** An accepted upload becomes current at once; the
  previous export is deleted once no queued or running job reads it (within about a
  minute). If the new export then proves unusable (the check fails as a whole), the
  administrator must upload a good export again — there is no "restore the previous
  export". Indexed evidence is not affected until a sync runs. The source's record says
  so beside the upload, marks such an export *Failed its check* and keeps *Sync now*
  inert with the reason; the Organization page shows `sources·attention·import_invalid`
  (P12.8).
* **An export whose records partly fail** still becomes current; the check reports the
  invalid and would-be-quarantined counts before any sync. Syncing it applies the
  existing fail-closed rules: records that cannot be identified quarantine the documents
  they might be (unreadable, not deleted) until a good export is synced.
* **Registered export locations** (C8.1 `export_locations`, `lattice source location
  add`) are **not built**: an operator-managed source reads the server path its
  operator's last `lattice sync` recorded; customers upload. No API takes a path.
* **No live connectors.** The three source types are exports; nothing is fetched from
  Slack, a help desk or a document system.
* **Organization purge** is decided (D-37A) but not executable before Phase 15.
* **Uploads are single requests** (no resumable or chunked protocol); one per source at
  a time.
* **Cloud deployment is not validated**: all of the above was verified on the local
  reference stack only.
