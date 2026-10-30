# Lattice — project history

This is the status log that opened the README until the personal v1 release (Phase 14,
D-46, 2026-10-30), kept here unchanged. Its links are relative to the repository root.
The current state of every phase is in the [implementation plan](implementation-plan.md)
§4; Phase 13's closure is in [phase-13-followups](phase-13-followups.md) §22.

**Status: Phases 1–13 are implemented and merged (Phase 12: 00a15d2, 2026-10-20). Phase 13
(Retrieval, Grounding and Answer Quality) was accepted 2026-10-27 with its quality criterion
unmet as recorded: P13.0 — the measured baseline,
known quality gaps, evaluation contracts, checkpoints and decisions D-38–D-45 — is in
[docs/phase-13-plan.md](phase-13-plan.md) and was approved 2026-10-21; P13.1 (the
evaluation foundation: new metrics, item-level baselines, `lattice eval compare`) is
approved; P13.2 (159 golden items with dev/held-out splits, label provenance and an owner
review packet in [docs/golden-review/](golden-review/)) is delivered, its held-out
review pending; P13.3 (the relevance gate: answers stop before any model call when no
permitted evidence addresses the question) is accepted; P13.4–P13.9 are delivered as far
as they can go without the product owner: no ranking change survived held-out evaluation,
so retrieval is unchanged, the second hop (P13.6) was built and measured without a gain,
and the answer validator now has an adversarial suite (phase-13-plan §27–28). History: Phases 1–11
and the frontend product-design overhaul (D0–D7,
[design direction](design-direction.md)) is merged; [design system
v2](design-system.md) is canonical. Phase 11 (Production Deployment, Identity
Operations & Reliability) was verified on a local production-shaped stack only: real
external staging validation is deferred ([plan](phase-11-plan.md) §21.1) and, since
Lattice is a personal project (D-46), not planned. Phase 11 is merged (143392f). Phase 12 (Organization Setup, Identity
Configuration & Integrations) delivered: P12.1 foundations are built (encrypted
credentials, the outbound-target policy, `lattice org create`, the backend-owned setup
record and the Organization page) and P12.2 members and administrators (members added by
hand on their exact identity-provider identifier, the administrator capability with a
last-administrator rule that holds under concurrency, least-access reinstatement) and
P12.3 accounts and their external-reference mappings (create, edit, archive and
reactivate accounts; map, remap and unmap exact ticketing-organization and channel IDs
with a preview and a reason, applied at the next sync; unmapped references — a missing
mapping never widens who can read a record) and P12.4 identity administration (Settings →
Identity: the OIDC connection — test sign-in, check, client secret sealed in the vault,
client ID, issuer or claim with the administrator's own matched test sign-in,
disable/enable — and SCIM tokens shown once, rotated, revoked; every OIDC request through
the outbound policy) and P12.5 providers, BYOK and egress with the D-34 step-up; P12.6
customer-operable export sources (Sources → *Add source*, a record per source, exports
uploaded with real progress into a filesystem import store with strict archive checks and
their own ingress limit, a deterministic check before any sync, pause/resume and
disconnect, the existing worker syncing from the import) and P12.7 end-to-end onboarding
(an organization from `lattice org create` to a member's first Ask, proved on the local
reference stack in production mode by `make prod-onboard` — API and browser — and in the
backend suite; [docs/onboarding.md](onboarding.md)) and P12.8 closure (a security
review that bounded the purge's audit exemption to the session's organization — migration
0024 — the deployed authorization sweep extended to every Phase 12 route family, a
product-consistency and accessibility review, and the deferred-capability register in
[phase-12-plan](phase-12-plan.md) Part III), merged to `main` as 00a15d2. The three source types are **export-based**
(Markdown documents, Lattice ticket JSON Lines, Slack workspace export) — not live
integrations.** What runs today: a FastAPI
backend with typed fail-fast configuration, PostgreSQL 16 + pgvector with forced
row-level security for organization isolation, server-side revocable sessions with a development-only persona
login, CSRF protection, a typed error contract, redacting structured logs, a seed tool
for the synthetic organizations, CI — (Phase 2) the authorization core: access
groups and grants, a deterministic reference policy, its SQL predicate for retrieval,
a property-based proof that the two agree, explanations, and the pre-generation
tripwire — and (Phase 3) a synthetic enterprise dataset of two organizations as raw
document, ticket and Slack exports, with a golden question set and a lint that checks
both against the real authorization policy — and (Phase 4) ingestion: three connectors
(Markdown documents, ticket JSON Lines, Slack export), an idempotent, fail-closed sync
pipeline with source-aware chunking, lexical indexing and embeddings under the AI egress
policy, a guarded deletion sweep, source disconnect, and administrator grants applied
as a separate step — and (Phase 5) permission-aware hybrid retrieval: exact
identifier, full-text and vector search, each with the authorization predicate inside
its SQL, fused and deterministically reranked into permitted evidence, with a
retrieval evaluation over the golden set — and (Phase 6) grounded answers from that
evidence only: organization-scoped generation providers under the same egress rule,
credentials stored by reference, claim-level fail-closed citations, deterministic
supersession and potential conflicts, query traces, and the Ask API — and (Phase 7) the
web app: Ask with every answer status designed, citation chips and an evidence
inspector, conflict and supersession presentation, traces, and read-only AI provider
settings — and (Phase 8) operability: a Sources page (members see only what they can
read; administrators see aggregates, failures by opaque reference, and can request a
sync), Activity (your own questions; an operational view for administrators), a
background worker with its own least-privilege database role, liveness and readiness
checks, Prometheus metrics, and fail-closed limits and kill switches — and (Phase 9)
access management: an Access page where administrators see each member's access
explained in sentences, preview every change (counted only over documents they can
read themselves), give a reason, and apply it; grants of single documents; an
append-only audit log of every access and AI-policy change; and on Ask, an account
picker and a first-run line listing only the person's own sources and accounts — and
(Phase 10) sign-in through each organization's own OIDC identity provider, SCIM
provisioning of members by that provider (least access by default, suspension and
removal effective on the next request), per-member and per-client rate limits, an
access audit the database itself enforces, a nonce-based script policy, a dependency
audit, and an isolation sweep over every route. See the
[implementation plan](implementation-plan.md).
