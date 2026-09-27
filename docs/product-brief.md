# Lattice — Product Brief

Status: Foundation draft (Phase 0). Nothing described here is implemented yet.
Owner: Product engineering. Last reviewed: 2026-09-27.

---

## 1. Customer

**Customer type.** B2B software companies of roughly 150–1,500 employees that sell a
technical product to enterprise and mid-market customers under contract, and run
account management, customer success, support, product and engineering as separate
functions with separate tools.

**Design customer (synthetic, used throughout development).** *Halden* — a 380-person
company selling *Halden Flow*, a data-sync and workflow-automation platform, to ~140
customer accounts. Halden's knowledge lives in:

| System (real-world analogue)      | What lives there                                   | Lattice source type (first slice) |
| --------------------------------- | -------------------------------------------------- | --------------------------------- |
| Google Drive / Notion             | Project plans, call notes, decision records, policies | `document` (document repository) |
| Zendesk-style ticketing           | Support tickets, internal ticket notes             | `ticket` (ticketing system)       |
| Slack                             | Account channels, product channels, escalations    | `chat` (chat export)              |
| Gong, Salesforce, GitHub, Jira    | Calls, CRM, issues                                 | Later connectors (not in slice)   |

A second synthetic organization, *Veridian Analytics*, exists only to prove tenant
isolation. It deliberately has a customer account also named "Acme Corp".

## 2. Users

| User                           | Typical question                                                | Access shape (see `authorization-model.md`) |
| ------------------------------ | --------------------------------------------------------------- | ------------------------------------------- |
| Account Manager (AM)           | "What did we promise Acme about SCIM before the renewal?"       | Assigned accounts only; internal clearance  |
| Customer Success Manager (CSM) | "What changed for Northpeak in the last month?"                 | Assigned accounts; internal clearance       |
| Support Engineer               | "Has anyone seen this SSO error at another customer?"           | All accounts; internal clearance            |
| Product Manager                | "Which customers are blocked on SCIM, and what did we tell them?" | All accounts; internal clearance            |
| Engineering Lead               | "Why was the directory-sync rewrite prioritised?"               | All accounts; internal; engineering dept    |
| Leadership (VP / C-level)      | "What is the real status of the Acme escalation?"               | All accounts; confidential clearance; restricted by explicit grant |
| Workspace Administrator        | "Why can't Maya see the Kestrel tickets?"                       | Manages access; **does not** gain data access by being admin |

## 3. Current workflow (what happens today without Lattice)

Concrete example, reconstructed from how AMs at companies like Halden prepare for a
renewal call:

1. The AM opens the CRM record for Acme and reads the last three notes (often stale).
2. Searches Slack for "acme scim" — gets 60 hits across 7 channels, most irrelevant;
   cannot see private escalation channels they aren't in, and doesn't know they exist.
3. Searches the ticketing system for Acme's open tickets; reads ticket HAL-4821, where a
   support engineer told Acme in August that SCIM was "expected in October".
4. Searches Drive for "SCIM roadmap" — finds a July roadmap doc (outdated), misses the
   September product decision record that moved SCIM GA to December.
5. DMs the product manager: "Is October still right for SCIM?" Waits hours.
6. Walks into the call with a mix of stale and current information.

Observed cost of this pattern (hypothesis to be validated with pilot users, not a
measured fact): 30–90 minutes per account-prep task, 4–6 systems searched, at least one
colleague interrupted, and a meaningful rate of customers being told outdated dates.

## 4. Product problem

Employees cannot quickly get a **trustworthy** answer to a question whose evidence is
spread across several systems, because:

- **Fragmentation.** Evidence for one answer lives in 3+ systems with different search
  behaviour.
- **Staleness.** Search ranks by text match, not by which record is newest or
  authoritative; old dates and old policies surface first.
- **Contradiction.** Sources disagree (support note says October, product decision says
  December) and nothing shows the disagreement.
- **Missing provenance.** Answers passed along in Slack have no link back to evidence.
- **Access mismatch.** Enterprise search tools either index too little (useless) or
  index too broadly and leak (unsafe). An AI layer that retrieves everything and asks
  the model to "not mention" restricted content is a data leak by design.

## 5. Core workflow Lattice supports (first release)

**Ask a question → receive a cited, permission-safe answer → inspect the evidence.**

1. Authenticated employee types a question, optionally scoped to an account, source
   type, or date range.
2. Lattice retrieves only evidence that employee is authorized to see, across the three
   source types, using lexical and semantic search together.
3. Lattice produces a short answer where every factual claim carries a citation, plus:
   dates of the evidence, a freshness statement, potential conflicts between sources
   (and deterministic supersession where source metadata establishes it),
   and honest uncertainty.
4. The employee opens any citation to see the exact supporting passage highlighted in
   its surrounding context, with source, date, author and a link to the original.
5. If evidence is insufficient, Lattice says so and shows what it searched, without
   revealing whether inaccessible evidence exists.

Everything else in the product (source status, access management, audit) exists to make
this workflow trustworthy and operable.

## 6. Value proposition

For account-facing and product teams at B2B software companies, Lattice answers
cross-system questions about customers and commitments in seconds, with every claim
traceable to evidence the asker is allowed to see — and it tells you when the evidence
is old or contradictory instead of hiding it.

The differentiator is not "AI search". It is: **permission-correct retrieval +
verifiable citations + explicit freshness and potential-conflict handling**.

## 7. Constraints

- **Security:** the model must never receive content the asking user cannot see
  (`security-model.md` SEC-1). Zero tolerance.
- **Tenancy:** SaaS-capable from day one; one database, organization-scoped rows.
- **AI data egress is a separate policy from authorization.** By default only
  `internal` content may be sent to an approved external model provider;
  `confidential` and `restricted` content stays inside Lattice unless the organization
  explicitly opts in for an approved provider (`security-model.md` §6). Users may
  still read such evidence in Lattice; it just isn't sent to the model.
- **Source systems remain the source of truth.** Lattice never writes back to sources.
- **Team size:** built by a very small team; operational surface must stay small
  (one API service, one worker process, one Postgres).
- **Cost:** per-question model cost must be predictable; no multi-call agent loops per
  question in the first release.

## 8. Success metrics

Baselines are hypotheses until measured in a pilot. Targets apply to the synthetic
evaluation set first, then to pilot usage.

| Metric                                       | How measured                                                 | Target (first release)        |
| -------------------------------------------- | ------------------------------------------------------------ | ----------------------------- |
| **Unauthorized context reaching the model**  | Security eval suite + canary scanning of provider payloads   | **0, always (release-blocking)** |
| Cross-organization leakage                   | Isolation regression suite                                   | **0, always (release-blocking)** |
| Retrieval Recall@10 (permitted evidence)     | Golden set, retrieval-only run                               | ≥ 0.90                        |
| Citation validity (citation resolves to real, permitted chunk and quote matches) | Deterministic validator | 100% (by construction)        |
| Visible claims with any failed citation      | Citation test suite + validator counters                     | **0, always (release-blocking)** |
| Citation support (cited passage supports claim) | LLM-judge + human spot check on golden set                | ≥ 0.95                        |
| Unsupported-claim rate                       | Judge over golden set                                        | ≤ 3% of claims                |
| Refusal correctness (insufficient / restricted cases) | Golden set                                          | ≥ 0.95                        |
| Potential conflict surfaced when golden set marks one | Golden set                                          | ≥ 0.90                        |
| Potential conflict presented as resolved (no supersession metadata) | Judge over golden set          | 0                             |
| Confidential/restricted content sent externally under default policy | Egress test suite (canaries)   | **0, always (release-blocking)** |
| Stale-evidence flagged when golden set marks staleness | Golden set                                         | ≥ 0.90                        |
| Median time-to-answer (p50 / p95)            | Trace latency                                                | ≤ 6 s / ≤ 15 s                |
| Time to first evidence shown                 | Trace latency (retrieval complete)                           | p50 ≤ 1.0 s                   |
| Pilot: systems manually searched per task    | Pilot survey / observation                                   | from ~4–6 → ≤ 1               |
| Pilot: user correction rate                  | "This is wrong" feedback per answered question               | Track; no target until baseline |

## 9. Product boundaries (what Lattice is)

- A read-only, permission-aware question-answering and evidence-inspection system over
  connected internal sources.
- An operational view of what Lattice knows (sources, sync health) and why a given
  answer was produced (traces), for authorized administrators.

## 10. Non-goals (first release)

| Non-goal                                              | Why deferred                                                        |
| ----------------------------------------------------- | ------------------------------------------------------------------- |
| Autonomous actions (emailing customers, filing tickets) | Different risk class; not needed to prove trusted retrieval       |
| Multi-turn conversational memory                      | Creates authorization-carryover risk; single-turn covers core workflow (ADR-007) |
| Writing back to, or "fixing", source systems          | Sources remain the source of truth                                  |
| Unrestricted enterprise-wide search across all tools  | Three source types prove the architecture                           |
| Analytics dashboards / usage KPIs UI                  | No user outcome until core workflow works                           |
| Explicit deny rules / ethical walls                   | Increases policy complexity; revisit with real customer need        |
| Real-time source webhooks                             | Scheduled / manual sync is enough for first release                 |
| Model-based reranker, query rewriting via LLM         | Add only if evaluation shows need (retrieval-design.md §2, §6)          |
| Dark theme                                            | Tokens are structured to allow it; not built in first release       |
| Password-based login                                  | Enterprises use SSO; dev login + OIDC (ADR-005)                     |
| Consumer chat UX (avatars, personas, chat bubbles)    | Wrong mental model for evidence work                                |

## 11. First vertical slice (summary)

Defined fully in `implementation-plan.md` §3. In one sentence: *two synthetic
organizations with realistic users and three source types are ingested idempotently;
an employee asks a question through the web app; retrieval runs inside their
authorization constraints; the answer is generated only from permitted evidence that
the AI egress policy allows to leave Lattice, with
claim-level verified citations, freshness, potential-conflict and refusal handling; the evidence is
inspectable; the query is traced; and automated tests prove unauthorized evidence never
reaches generation.*
