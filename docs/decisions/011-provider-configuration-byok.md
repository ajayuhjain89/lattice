# ADR-011: Provider configuration, deployment modes and organization BYOK

Status: Accepted as the target architecture (Phase 5 closure, 2026-09-28). The
policy core is implemented. Organization-scoped credential storage and the settings UI
are **not** built yet; they are required before any multi-tenant deployment
(implementation-plan §7).

## Context

Lattice must not require a paid model API in order to work, and it must not assume it
owns every provider credential. Organizations will bring their own keys (BYOK) for
Gemini, OpenAI, Anthropic (Phase 6 generation), Voyage, or other approved providers.
Other organizations will run local or private models. At the same time, a credential
must never be mistaken for permission: holding a valid API key says nothing about
whether an organization's data may be sent to that provider.

## Decision

### Provider matrix

| Provider | Deployment | Inference leaves the deployment? | Purpose today |
| --- | --- | --- | --- |
| `fake` (`FakeEmbeddingProvider`) | test | no network; **declared `external_api`** so tests exercise the external policy | tests and CI; refused in production; not a semantic benchmark |
| `local` (`LocalEmbeddingProvider`, ADR-009) | `local` | no | real zero-cost semantic retrieval; private and offline deployments |
| `gemini` (`GeminiEmbeddingProvider`, ADR-010) | `external_api` | yes | optional BYOK; unpaid key = synthetic evaluation only |
| `openai` (`OpenAIEmbeddingProvider`, ADR-008) | `external_api` | yes | optional BYOK / managed deployments |
| private endpoint (future) | `private` | to the organization's own endpoint | enterprise-hosted models |
| Anthropic (Phase 6, generation) | `external_api` | yes | answer generation, BYOK |

The table describes *where inference runs*. It is not a security conclusion.
Eligibility always comes from the policy below, never from a provider's name.

### The invocation rule

A model provider may receive content -- a document chunk, a question, or (Phase 6)
evidence and a prompt -- only if **all** of the following hold:

1. the requesting user is **authorized** for that content. Authorization is decided
   first, in SQL. Egress only narrows what an authorized request may send;
2. the organization's **egress policy** permits the content's sensitivity for this
   deployment type: `external_egress_level` for `external_api` and `private`, and
   nothing extra for `local`;
3. the **provider configuration is approved** for that sensitivity
   (`model_providers.approved_for_level`, default `internal`, raised only by an
   explicit administrator action with a stated reason);
4. the provider's **declared deployment equals its recorded deployment**. A provider
   cannot claim `local` to escape the external rule;
5. the provider's **data-use terms are acceptable** for the organization's data. A
   provider that may use submitted content (`training_opt_out = false`, for example an
   unpaid Gemini key) is limited to `synthetic` organizations;
6. valid **credentials or configuration** exist for it.

Only then may the call happen. Item 6 is last on purpose: a credential is necessary,
never sufficient. The same rule applies to every external provider (Gemini, OpenAI,
Anthropic, Voyage, future ones) and to every call type (document embedding, query
embedding, generation).

Implemented as `lattice.providers.egress.provider_decision` (items 2–5) and applied by
ingestion (document chunks) and retrieval (questions). The effective level:

```
external_api, private : min(org_settings.external_egress_level, approved_for_level)
local                 : approved_for_level
```

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
LOCAL                                    BYOK (external)
organization                             organization
  → local / self-hosted model              → external provider
  → no external API credential              → the organization's own credential
  → no inference egress                     → egress policy + provider approval
                                              + data-use terms still apply
```

Lattice supports both, per organization and per purpose (embedding, generation).

### Configuration model (target)

```
organization
  └─ model provider configuration      (purpose, provider, model; today: model_providers)
       ├─ deployment type               (external_api | private | local)
       ├─ approval level                (internal | confidential | restricted)
       ├─ data-use terms                (training_opt_out, retention_mode)
       └─ secret reference | local configuration
            └─ provider invocation      (only through the invocation rule)
```

Today the configuration rows exist (`model_providers`, `org_settings`) and are
enforced. Credentials, however, are process-wide environment variables
(`LATTICE_OPENAI_API_KEY`, `LATTICE_GEMINI_API_KEY` / `GEMINI_API_KEY`) read by
operator commands. That is a single-deployment stop-gap. Phase 6 must not make it
permanent: the generation provider is resolved **per organization** through the same
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

## Not built in this closure

Organization-scoped secret storage, rotation, the provider-settings API and UI, audit
events for approval changes (today: a `provider.approval_changed` log event from the
operator CLI), and an Anthropic generation adapter (Phase 6). The architecture above is
what they must implement. No BYOK settings UI exists.

## Consequences

- Lattice runs with no external credential at all (local provider), and with any
  combination of external providers per organization.
- Egress decisions depend on recorded configuration and the organization's data,
  never on which keys happen to be present.
- Phase 6 inherits one decision function for generation instead of inventing a second
  policy.
