# ADR-011: Provider configuration, deployment trust classes and organization BYOK

Status: Accepted (Phase 5 closure, 2026-09-28; D-15 and D-16 resolved in the final
Phase 5 closure, 2026-09-29). The policy core, including the D-15 trust classes, is
implemented and tested. Organization-scoped credential storage is scheduled for the
Phase 6 backend and the administrative UX for Phase 7 (D-16, below). Neither is built
in Phase 5. *Since then:* configuration and credential references in Phase 6, the
read-only page in Phase 7, encrypted-at-rest custody amended 2026-10-06 (D-29), and the
administrator's providers, BYOK and egress with the D-34 step-up built in Phase 12 (P12.5;
reviewed in P12.8).

## Context

Lattice must not require a paid model API in order to work, and it must not assume it
owns every provider credential. Organizations will bring their own keys (BYOK) for
Gemini, OpenAI, Anthropic (Phase 6 generation), Voyage, or other approved providers.
Other organizations will run models locally or on infrastructure they control. At the same time, a credential
must never be mistaken for permission: holding a valid API key says nothing about
whether an organization's data may be sent to that provider.

## Decision

### Provider matrix

| Provider | Trust class | Inference leaves the deployment? | Purpose today |
| --- | --- | --- | --- |
| `fake` (`FakeEmbeddingProvider`) | test double; **declared `external_api`** so tests exercise the external policy | no network | tests and CI only; refused in production; never a semantic-quality figure |
| `local` (`LocalEmbeddingProvider`, ADR-009, `mxbai-embed-large-v1`) | `local` | no | real zero-cost / self-hosted semantic retrieval; offline deployments |
| `gemini` (`GeminiEmbeddingProvider`, ADR-010, `gemini-embedding-2` @ 1024) | `external_api` | yes | optional external provider / BYOK; the current unpaid key: synthetic data only |
| `openai` (`OpenAIEmbeddingProvider`, ADR-008) | `external_api` | yes | optional managed / BYOK provider; real evaluation not run (no credential, none required) |
| organization-controlled endpoint (future) | `organization_controlled` | to the organization's own endpoint | customer-owned model infrastructure; explicit organization policy required |
| Anthropic (Phase 6, generation) | `external_api` | yes | answer generation, BYOK |

### Deployment trust classes (D-15)

| Class | Meaning | Rule |
| --- | --- | --- |
| `local` | inference inside the Lattice deployment boundary; no inference content leaves it | not capped by an organization egress setting; capped by the provider's own approval (default `internal`). User authorization, organization policy and D-13 still apply |
| `organization_controlled` | outside the Lattice process/deployment, on infrastructure or an endpoint the organization controls or approved; may involve network transmission | **not allowed** until the organization sets `org_settings.organization_controlled_level`, and the record needs an explicit administrator approval (`approved_at`). Effective level `min(organization_controlled_level, approved_for_level)`. No implicit `confidential`/`restricted` |
| `external_api` | a third-party provider outside the organization's controlled boundary | `min(external_egress_level, approved_for_level)`; data-use and retention terms apply |
| `unknown` | missing, invalid or unrecognised (including the old label `private`) | fail closed: nothing is sent |

"Private" is not a class and carries no security meaning. Migration 0007 kept the
(Phase 5: nonexistent) `private` records as `organization_controlled`, which sends
nothing until both explicit settings exist. The database accepts only the three known
classes. The code treats any other value, recorded or declared, as `unknown`. An
unknown declared class is never recorded.

The table describes *where inference runs*. It is not a security conclusion.
Eligibility always comes from the policy below, never from a provider's name.

### The invocation rule

A model provider may receive content -- a document chunk, a question, or (Phase 6)
evidence and a prompt -- only if **all** of the following hold:

1. the requesting user is **authorized** for that content. Authorization is decided
   first, in SQL. Egress only narrows what an authorized request may send;
2. the organization **allows the deployment class**. `external_api` and `local` are
   allowed by default, within their ceilings; `organization_controlled` is not
   allowed until configured; `unknown` never is;
3. the organization's **sensitivity ceiling** for that class permits the content
   (`external_egress_level`, `organization_controlled_level`; none for `local`);
4. the **provider/deployment approval** permits it: `model_providers.approved_for_level`
   (default `internal`, raised only by an explicit administrator action with a stated
   reason), and for `organization_controlled` an explicit approval (`approved_at`).
   The record is this organization's (row-level security): another organization's
   configuration never applies. The implementation's **declared class equals the
   recorded class**: a provider cannot claim `local` or `organization_controlled` to
   escape a stricter rule;
5. the provider's **data-use and retention terms are acceptable** for the
   organization's data. A provider that may use submitted content
   (`training_opt_out = false`, for example an unpaid Gemini key) is limited to
   `synthetic` organizations;
6. valid **provider configuration** exists;
7. valid **credentials** exist, where the provider requires them.

Only then may the call happen. Items 6 and 7 are last on purpose: a credential is
necessary, never sufficient. It cannot raise a sensitivity clearance, authorize
content, bypass organization policy, or move a provider from `external_api` to
`organization_controlled` or from `organization_controlled` to `local`.
**Credential availability ≠ provider approval ≠ data-egress authorization.** The same
rule applies to every provider (Gemini, OpenAI, Anthropic, Voyage, future ones) and to
every call type (document embedding, query embedding, generation).

Implemented as `lattice.providers.egress.provider_decision` (items 2–5) and applied by
ingestion (document chunks) and retrieval (questions). The effective level:

```
external_api            : min(external_egress_level, approved_for_level)
organization_controlled : min(organization_controlled_level, approved_for_level)
                          only if organization_controlled_level is set and approved_at is set
local                   : approved_for_level
unknown                 : nothing
```

Tested in `backend/tests/providers/test_trust_classes.py` (the decision, every class
pair), `backend/tests/retrieval/test_trust_classes.py` (questions, observed with
recording providers and mock transports) and
`backend/tests/ingestion/test_provider_eligibility.py` (document chunks).

### Question sensitivity (D-13)

```
effective_question_sensitivity =
    more_restrictive(org_settings.default_query_sensitivity, per-question declaration)
```

A user, a client, a provider key or a provider selection can raise it, never lower it.
Unrecognised values count as `restricted`. When the effective sensitivity exceeds an
external provider's level, the question is not sent and external semantic retrieval
is skipped (`policy_excluded` / `query_classification`). Exact and lexical retrieval
always run, and a `local` provider approved for that sensitivity may still embed the
question. There is no silent fallback to another external provider. The effective
sensitivity is returned with every retrieval (`RetrievalResult.question_sensitivity`)
and **Phase 6 generation must obey it**: the question is not placed in an external
generation prompt when policy forbids it (then `evidence_only`, no model call). The
same framework governs query embedding, retrieved evidence and the generation prompt.

### Deployment modes

```
LOCAL                        ORGANIZATION_CONTROLLED           BYOK (EXTERNAL_API)
organization                 organization                      organization
  → local model                → its own model endpoint          → external provider
  → no external credential     → its own configuration/secret    → its own credential
  → no inference egress        → class allowed + ceiling          → egress level + approval
                               + explicit approval                + data-use terms
```

Lattice supports all three, per organization and per purpose (embedding, generation).

### Configuration model (target)

```
organization
  └─ model provider configuration      (purpose, provider, model; today: model_providers)
       ├─ deployment trust class        (local | organization_controlled | external_api)
       ├─ approval level                (internal | confidential | restricted)
       ├─ data-use terms                (training_opt_out, retention_mode)
       └─ secret reference | local configuration
            └─ provider invocation      (only through the invocation rule)
```

Today the configuration rows exist (`model_providers`, `org_settings`) and are
enforced. Credentials, however, are process-wide environment variables
(`LATTICE_OPENAI_API_KEY`, `LATTICE_GEMINI_API_KEY` / `GEMINI_API_KEY`) read by
operator commands. That is a single-deployment stop-gap until the Phase 6 backend
(D-16, below); Phase 6 must not make it permanent: the generation provider is resolved **per organization** through the same
configuration, so a later secret store plugs in without changing call sites.

### Credential security (requirements for the BYOK implementation)

Organization-owned provider credentials:

- belong to the **organization**, never to ordinary end users. They are managed by
  organization administrators;
- are never stored as plaintext configuration. They are encrypted at rest or kept in a
  secret manager, and the database holds only a reference;
- are never returned by any API response, and never appear in logs, traces, error
  messages or prompts;
- are replaceable, support rotation and revocation, and a provider can be disabled
  without deleting its history;
- are shown in the admin UI only as masked state, for example *Connected · Provider:
  Gemini · Credential: ••••••••1234*.

Revoking or disabling a provider must make the policy exclude it on the next request.
Today, removing its `model_providers` record does exactly that (`provider_unapproved`),
and lowering its approval does it for the content above the new level. The vectors it
produced are not deleted by that. Re-embedding with another provider replaces them,
per the Phase 4 reindex rules.

## D-16: when organization-owned credentials are built (decided 2026-09-29)

D-16 is a timing and architecture decision. Phase 5 builds no credential persistence.

**Phase 6 backend** introduces the minimum organization-scoped provider configuration
and secret-management abstraction that generation and BYOK need:

```
organization
  → provider configuration
      → deployment trust class
          → provider / model configuration
              → secret reference
                  → provider invocation (only through the invocation rule)
```

- the organization owns its external provider credentials; they are managed by
  organization administrators, never by ordinary end users;
- secret values are encrypted at rest or held through a secret-manager abstraction;
  the database holds a reference;
- they never appear in normal API responses, logs, traces, prompts or error messages;
- rotation, replacement and revocation are supported, and a provider can be disabled
  without deleting its history;
- environment variables (`LATTICE_GEMINI_API_KEY` / `GEMINI_API_KEY`,
  `LATTICE_OPENAI_API_KEY`) remain supported as the development and single-deployment
  fallback.

**Phase 7 frontend** implements the administrative provider-configuration UX:
provider settings, masked credential state (e.g. *Connected · Gemini · ••••1234*),
connection testing, enable/disable, model/provider selection where appropriate, and
clear security and egress communication. No UI is built before Phase 7.

## Not built in Phase 5

Organization-scoped secret storage, rotation, the provider-settings API and UI, an
operator command for the organization-level ceilings, audit events for approval
changes (today: a `provider.approval_changed` log event from the operator CLI), an
`organization_controlled` provider implementation, and an Anthropic generation
adapter (Phase 6). The architecture above is what they must implement.

## Consequences

- Lattice runs with no external credential at all (local provider), and with any
  combination of external providers per organization.
- Egress decisions depend on recorded configuration and the organization's data,
  never on which keys happen to be present.
- Phase 6 inherits one decision function for generation instead of inventing a second
  policy.

## Amendment (2026-10-06, Phase 12 P12.0): custody of administrator-entered secrets (D-29)

*Historical:* Phase 6 built the reference model (`env:`/`memory:` references; the database
holds only a reference). Phase 7 shipped the provider page **read-only** by the product
owner's instruction, so the "Phase 7 frontend" UX described above was not built.
*Current:* unchanged — values come only from the deployment environment.
*Phase 12 target (decided, not implemented):* recoverable credentials an administrator
enters (provider API keys, OIDC client secrets) may be stored **encrypted at rest** in
PostgreSQL — AES-256-GCM from `cryptography`, associated data binding each ciphertext to
its organization, record, credential type and key ID, master keys only in the
deployment's environment — as another `SecretStore` backend (`vault:<id>`); `env:`
references stay supported. Verification-only secrets (SCIM tokens) stay hashed. The
sentence "the database holds only a reference" becomes "the database never holds a
credential in plaintext". Contract: phase-12-plan C3; mutation path: ADR-017. The admin
UX this ADR assigned to Phase 7 is built in Phase 12 (P12.5).

### Implemented in P12.1 (2026-10-08)

The `vault:<id>` backend exists: `encrypted_secrets` (migration 0016), `lattice.vault`
(keyring from `LATTICE_SECRET_KEYS` / `LATTICE_SECRET_ACTIVE_KEY`, AES-256-GCM sealing with
the associated data above, `secrets rekey`). The `SecretStore` protocol gained an optional
`sealed` argument: the caller loads the row with the binding *it* expects (its
organization, its own record's ID, the credential kind it needs) and the store opens it
only with keys; a store without keys refuses `vault:` (`scheme_not_allowed`). Writers in
P12.1 are operator commands (`provider credential set --value-stdin`, `org create
--client-secret-stdin`, `sso update --client-secret-stdin`); the administrator UX stays
P12.5. Process access as verified on the call graph: the API opens OIDC client secrets
(token exchange) and generation-provider keys (after the invocation rule); the worker
presents no organization credential (its embedding provider is a deployment setting), so
it holds no keys and has no privilege on the rows. **The database never holds a credential
in plaintext** — and never a master key.

### Implemented in P12.5 (2026-10-17)

The administrator UX this ADR assigned to Phase 7 now exists (`/settings/providers`,
`/v1/admin/providers/*`; phase-12-plan Part III P12.5). What changed in this ADR's model:

- **Precedence of credentials** stays one rule: *the organization's own active credential
  for the provider, or nothing*. A credential row is per organization (forced RLS), so no
  organization's key — product-entered (`vault:`) or operator-recorded (`env:`) — is ever
  another's fallback, and a deployment-wide environment key implies nothing for any
  organization until an operator records a reference to it *for that organization*.
  Administrators can enter keys only as `vault:` ciphertext (never an `env:` reference);
  they can replace or revoke an operator's key for their organization.
- **Generation needs an explicit approval** for every trust class (`approved_at`); the
  operator's `provider configure` records it for `internal`, as configuring always meant;
  records created in the product start unapproved and disabled.
- **The invocation rule runs again immediately before every model call** (a fresh snapshot;
  `policy_changed` / `outbound_blocked` / `credential_revoked`; the organization-controlled
  endpoint's registration is read again too), and provider calls connect only through the
  organization's outbound policy (`outbound_blocked`).
- **Embedding** stays a deployment setting (`LATTICE_QUERY_EMBEDDING` /
  `LATTICE_SYNC_EMBEDDING`, its key in the API/worker environment); administrators change
  only the organization's approval of the recorded embedding provider. Per-organization
  embedding credentials would need the worker to open organization secrets (C3.4) and a
  re-index on change — not built (Phase 13/14 dependency).
