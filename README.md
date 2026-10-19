# Lattice

Lattice is a permission-aware enterprise knowledge system: employees ask questions
across internal documents, support tickets and Slack, and get answers that are
grounded, cited, aware of stale and potentially conflicting evidence, and built only from
information they are authorized to see.

**Status: Phases 1–11 are implemented, and the frontend product-design overhaul (D0–D7,
[design direction](docs/design-direction.md)) is merged; [design system
v2](docs/design-system.md) is canonical. Phase 11 (Production Deployment, Identity
Operations & Reliability) was verified on a local production-shaped stack only: real
external staging validation is deferred and gates v1 GA ([plan](docs/phase-11-plan.md)
§21.1). Phase 11 is merged (143392f). Phase 12 (Organization Setup, Identity
Configuration & Integrations) is in progress: P12.1 foundations are built (encrypted
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
backend suite; [docs/onboarding.md](docs/onboarding.md)) are delivered locally and await
review; P12.8 is not started. The three source types are **export-based**
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
[implementation plan](docs/implementation-plan.md).

## Local setup

Prerequisites: Docker with Compose v2, [`uv`](https://docs.astral.sh/uv/), `make`,
`git`; for the web app, Node.js 22+ and [`pnpm`](https://pnpm.io/). `uv` installs
Python 3.12 itself.

```bash
git clone <this repository> lattice && cd lattice
cp .env.example .env      # local-only placeholder credentials; review before use
make setup                # install backend dependencies from the lockfile
make db-up                # Postgres 16 + pgvector on 127.0.0.1:5433; first start bootstraps roles
make migrate              # apply migrations as lattice_owner
make seed                 # Halden and Veridian orgs, people, admin groups (safe to re-run)
make check-db-role        # confirm the runtime role cannot bypass row-level security
make sync-dry-run         # what ingestion would do, writing nothing
make sync                 # ingest both organizations (fake embeddings), then apply admin grants
make api                  # http://127.0.0.1:8000 (interactive docs at /docs)
make worker               # optional, in another terminal: runs syncs requested from the Sources page
```

A database created before Phase 8 needs the worker's role once: copy the
`LATTICE_WORKER_*` lines from `.env.example` into `.env`, then `make db-up db-bootstrap
migrate` ([runbook §1](docs/runbook.md)).

Try it (in a second terminal, while `make api` runs):

```bash
curl -s http://127.0.0.1:8000/v1/health/ready
```

```bash
curl -s -c /tmp/lattice.jar -X POST http://127.0.0.1:8000/v1/auth/dev-login -H 'content-type: application/json' -H 'X-Lattice-Request: 1' -d '{"subject": "maya.okafor@halden.example"}'
```

```bash
curl -s -b /tmp/lattice.jar http://127.0.0.1:8000/v1/me
```

```bash
curl -s -b /tmp/lattice.jar -X POST -H 'X-Lattice-Request: 1' http://127.0.0.1:8000/v1/auth/logout -o /dev/null -w '%{http_code}\n'
```

`make sync` reports one rejected record for Halden support: ticket HAL-4951 belongs
to a customer whose ticketing organization is deliberately not mapped to an account,
so ingestion refuses it (`access_unmappable:account`) rather than index it without an
account. Its run is `partial`, which `make sync` accepts; a source-level failure stops
it. Run it again: nothing is written the second time. `EMBEDDING=none` indexes
lexically only.

**Real semantic retrieval without any paid API** (ADR-009): `make models-fetch`
downloads the pinned open-weight model `mxbai-embed-large-v1` (Apache-2.0, 1024
dimensions, 1.34 GB, once, SHA-256 verified, into `~/.cache/lattice/models`, never
into the repository). Then `make sync EMBEDDING=local` and `make eval EMBEDDING=local`.
It runs on the CPU with ONNX Runtime and is fully offline after the download.
External providers are optional, and a key never makes one eligible: the AI egress
policy decides what each may receive. `EMBEDDING=gemini` needs `GEMINI_API_KEY`; an
unpaid key may receive only the synthetic organizations' data (ADR-010).
`EMBEDDING=openai` needs `LATTICE_OPENAI_API_KEY` (ADR-008). Tests and CI use the
deterministic fake provider and a tiny committed model; they never download weights.

**The web app** (in a second terminal, while `make api` runs):

```bash
make web-setup            # install the web app's dependencies from its lockfile
make web                  # http://127.0.0.1:3000 — pick a persona on the development sign-in
```

The browser talks only to the web app's origin; `/api/*` is proxied to the API, so the
session cookie stays first-party. With no generation provider recorded, Ask shows the
evidence it finds (`evidence_only`). To see drafted answers locally without any model or
key, record the development-only fake provider (audited; `lattice provider disable`
undoes it):

```bash
cd backend && uv run --env-file ../.env lattice provider configure --org halden --purpose generation --provider fake --model fake --deployment external_api --reason "local UI development"
```

Seeded personas are listed in [`data/synthetic/halden/org.yaml`](data/synthetic/halden/org.yaml)
and [`data/synthetic/veridian/org.yaml`](data/synthetic/veridian/org.yaml). Every
state-changing request needs the `X-Lattice-Request: 1` header (CSRF protection).

## Development workflow

| Command | What it does |
| ------- | ------------ |
| `make test` | All tests, against the separate `lattice_test` database (real PostgreSQL; the schema is rebuilt from a clean state each run). Includes the property-based authorization proof (500 examples; set `LATTICE_AUTHZ_EXAMPLES` to run more) |
| `make lint` | `ruff check` + `ruff format --check` |
| `make dataset-lint` | Validate the synthetic dataset and golden set against the reference policy (no database, no model) |
| `make sync-dry-run` / `make sync` / `make admin-grants` | Ingest the synthetic organizations ([ingestion design §9](docs/ingestion-design.md) documents `lattice sync`, `reindex`, `source disconnect`, `apply-admin-grants`) |
| `make eval` | Retrieval metrics and security checks over the golden set, as each persona (after `make sync`; `EMBEDDING=fake\|local\|gemini\|openai\|none`, matching the sync); reports in `evals/reports/` |
| `make worker` | The background worker (as `lattice_worker`): runs the syncs administrators request from the Sources page; `lattice worker --once` runs what is due and exits |
| `make models-fetch` | Download and verify the pinned local embedding model (explicit; the only command that downloads weights) |
| `make eval-answer` | Answer and security metrics over the golden set (after `make sync`; `GENERATION=fake` by default: plumbing and security, not answer quality); run it on an evaluation database, since it reconfigures the organizations' generation providers |
| `lattice ask --org halden --as maya.okafor@halden.example "When will SCIM ship?"` | Ask as a member with the organization's generation configuration (`--json`; development/test only) |
| `lattice provider configure\|approve\|credential\|enable\|disable\|list`, `lattice org egress` | Organization provider configuration and AI egress ceilings; every change needs `--reason` and is audited ([runbook §3](docs/runbook.md)) |
| `lattice search --org halden --as maya.okafor@halden.example "What did we promise Acme?"` | A member's permitted evidence with per-channel ranks (`--text`, `--pool`, `--json`; development/test only). Run from `backend/` with `uv run --env-file ../.env` |
| `make dataset-generate` | Regenerate the deterministic bulk distractor tickets after editing authored tickets |
| `make format` | Apply formatting and safe lint fixes |
| `make typecheck` | `mypy --strict` over source, tests and migrations |
| `make db-bootstrap` | Re-run the idempotent superuser bootstrap (e.g. after changing passwords in `.env`) |
| `make db-down` / `make db-destroy` | Stop Postgres / **delete** the local database volume |
| `make web` / `make web-build` | Web app dev server on :3000 / production build |
| `make web-check` | Web lint (ESLint with jsx-a11y, Stylelint: no raw colours outside `tokens.css`), type check, unit and component tests |
| `make web-e2e` | Browser tests against the production build with the API mocked from typed fixtures: flows, not-found safety, keyboard, axe WCAG 2.2 AA on every route, responsive layouts, and production sign-in on a second server with development sign-in off (after `make web-build`). `pnpm screens` in `apps/web` renders every screen for review; `pnpm screens:canonical` refreshes [docs/screens](docs/screens/README.md) |
| `make api-contract` | Re-export the OpenAPI schema and regenerate the web app's TypeScript types (CI fails if they drift) |
| `make help` | List all targets |

CI (`.github/workflows/ci.yml`, on a pinned `ubuntu-24.04` runner) runs lint, strict type
checking, the dataset lint, the bootstrap, a migration round-trip on a clean database
with a model-drift check, the seed (twice), the API's and the worker's startup checks, a
sync of the dataset (twice, the second writing nothing), the retrieval and answer
evaluations as every persona with fake providers (each fails on any security
violation), and the test suite against `pgvector/pgvector:0.8.1-pg16`. A second job checks the web app: the generated API
contract matches the backend, lint, type check, unit and component tests, the
production build, and the Playwright suite (desktop, phone, and production sign-in).

## How isolation works (Phase 1)

- The API connects as `lattice_app`, which owns nothing and cannot bypass row-level
  security. The API **refuses to start** otherwise.
- Each request's organization comes only from its server-side session and is bound
  for that transaction with `set_config('app.org_id', …, true)`. Every application
  table has RLS enabled and forced; another organization's rows behave as if they do
  not exist, and with no organization bound nothing is visible.
- Cross-organization references are rejected by composite foreign keys.

Details: [data model §8](docs/data-model.md), [security model §3–5](docs/security-model.md).

## How authorization works (Phase 2)

Inside an organization, what a member may read is decided by one small deterministic
policy ([authorization model §4](docs/authorization-model.md)) — never by a model —
implemented twice: `lattice.authz.readable()` in plain Python, and
`readable_documents_predicate()` as SQL that retrieval puts inside its queries (Phase 5),
so unauthorized rows never leave PostgreSQL. A property-based test proves the two
agree on thousands of generated cases each run. Administrator capability grants no
content access; `restricted` content is reachable only by explicit grant; anything
unknown or malformed is denied; permission changes apply to the very next check.

## How ingestion works (Phase 4)

`lattice sync` reads a source through its connector, which only interprets the export
and reports what it says about access. Ingestion maps those untrusted hints through the
organization's Lattice configuration and **fails closed**: an unknown department,
group or account fails the record, and an existing document whose access can no longer
be mapped is quarantined (unreadable), never left on stale permissions. Each document is
written with its chunks and source grants in one transaction; a second sync of
unchanged input writes nothing; an access-only change never re-chunks or re-embeds.
Only content at or below the AI egress level (default `internal`) is sent to the
embedding provider; the rest is searchable by keywords only. Removed records are
deleted unless the snapshot looks broken (the deletion guard). Administrator grants
are Lattice configuration, applied after ingestion and never touched by a sync.
Details: [ingestion design](docs/ingestion-design.md),
[connector design](docs/connector-design.md).

## How retrieval works (Phase 5)

A question becomes the best **permitted** evidence in one consistent authorization
snapshot. Three channels -- exact identifier (`HAL-4821`), PostgreSQL full-text search
and pgvector similarity -- each run one SQL statement that contains the tenant
condition, the lifecycle condition and the authorization predicate, so an unreadable
row never leaves PostgreSQL, however well it matches. Results are fused with
reciprocal rank fusion and reordered by a small deterministic reranker (recency and
source authority are signals, never truth). A tripwire re-checks every candidate with
the reference policy and aborts the request on any mismatch; it never filters. The
question is embedded only if the AI egress policy allows it. Its sensitivity is the
organization's default, which a user can raise but never lower (D-13); otherwise, or if
the provider fails, exact and lexical retrieval still answer. A local model can embed
questions that must not leave the deployment. Providers change relevance only, never
what is permitted, and a document superseded by one the reader cannot see carries no
hint of it (D-14). Every model provider has an explicit deployment trust class --
`local`, `organization_controlled` or `external_api`; anything else receives nothing
(D-15) -- and a provider credential never authorizes sending data.

Results on the synthetic golden set (Recall@10): exact + lexical 0.668, local
`mxbai-embed-large-v1` 0.695, Gemini `gemini-embedding-2` 0.726; every security metric
0. The product objective of Recall@10 ≥ 0.90 is **not yet achieved**. A local
cross-encoder reranker was measured and rejected for the default path. Retrieval is
frozen for Phase 5. No test or CI run needs an external API key. Details:
[retrieval design, Part I](docs/retrieval-design.md); results:
[evaluation plan §9–§12](docs/evaluation-plan.md).

## How answering works (Phase 6)

`POST /v1/ask` runs retrieval as above, then decides -- in the same authorization
snapshot -- whether any model may be called. Each organization configures its own
generation provider (a local Ollama model, Gemini with its own key, or others later).
A key is stored only as a reference to a secret held outside the database, and it never
authorizes anything: the provider's trust class, the organization's ceiling, the
provider's approval, its data-use terms and the question's sensitivity decide, and
evidence above the allowed level stays in the asker's list, marked "not used". The
prompt carries only evidence the asker may read, inside delimiters that retrieved text
cannot close. The model must return claims, each citing request-local handles with
verbatim quotes. A claim survives only if every citation checks out; one bad citation
removes the whole claim, and with no verified direct answer the answer is withheld.
Supersession comes only from explicit source metadata. A disagreement the model points
out is shown as a potential conflict, with both sides. Action requests ("draft an
email", "update the ticket") return `unsupported_request`; nothing is performed. Every
Ask writes a trace of identifiers, versions, codes and timings; the asker can reread it
(the answer is hidden once its sources become unreadable), administrators see only its
operational fields.

Evaluation: with fake providers, every golden item passes every security check
(unauthorized or cross-organization evidence in a payload or answer, egress level,
hidden accounts, canaries, injected payloads, unverified or dangling citations: all 0).
**Answer quality with a real model has not been measured**: the local model could not
run on the 8 GB reference machine, and no external quota was available
([ADR-012](docs/decisions/012-generation-providers.md)). Details:
[retrieval design, Part II and §13](docs/retrieval-design.md); results:
[evaluation plan §13](docs/evaluation-plan.md).

## How the web app works (Phase 7, redesigned in the design overhaul)

`apps/web` is a Next.js (App Router, TypeScript strict) app that renders and nothing
more: every permission decision is the API's. Its types are generated from the API's
OpenAPI schema; one client adds the CSRF header and normalizes errors; one module holds
every query. Ask sends the question with scope filters that can only narrow the search
(source types, dates, and a per-question sensitivity that can only be raised). An answer
is a brief: a status line, claims set in the record voice with numbered exhibit markers
in the margin, a claims × exhibits lattice when the relationship isn't one-to-one,
potential conflicts on a date axis with equal weight (a supersession only when the
source records it), what the evidence doesn't establish, and the exhibits — with the
evidence the AI data policy kept from the model shown, hatched, to the people who may
read it. Every other status has its own presentation. A claim citing evidence absent
from the response is not rendered. The evidence inspector (docked at ≥1280 px, a drawer
below, a bottom sheet on phones) reads `GET /v1/evidence/{id}` each time it opens; a 404
— nonexistent or unreadable — has one presentation. Traces are fetched fresh and never
filled in from the Ask page's memory. AI provider settings are editable by administrators since P12.5 (raising egress needs a fresh identity-provider sign-in); before that, configuration
is changed with the audited CLI. `/` is a static public landing page (no API call; a
labelled synthetic example) and `/sign-in` is the organization's identity provider, or
the persona picker where development sign-in is on. Details: [design
system](docs/design-system.md) (v2, the current reference), [design
direction](docs/design-direction.md) (how the overhaul decided it),
[implementation plan, Phase 7](docs/implementation-plan.md).

## How operations work (Phase 8)

Sources and Activity follow the operational-metadata rules of the [security
model](docs/security-model.md) §10: a member sees only sources they can read from, with
counts computed inside their access; an administrator sees every source's aggregate
totals and last sync, and a failed record's identifier only when they can read its
document (otherwise an opaque `rec_…` reference); administrators see who asked what
*when*, never the question, answer or evidence. *Sync now* queues a job; `lattice
worker` runs it as `lattice_worker`, a role that can write only what a sync writes, one
organization at a time ([ADR-013](docs/decisions/013-background-worker-role.md)).
`/v1/health/live` and `/v1/health/ready` report states only; with
`LATTICE_METRICS_ADDRESS` set, Prometheus metrics (closed label sets, no identifiers)
are served on a separate port the web app never proxies. The API and worker refuse to
start against a schema of another revision; a database outage returns 503 in seconds;
one Ask's model calls share a deadline; `LATTICE_GENERATION_ENABLED=false` stops every
model call. Operating it: [runbook §7](docs/runbook.md).

## How access management works (Phase 9)

Administrators open **Access** (sign in as `lena.brandt@halden.example`). Each member's
page explains their access in a sentence, lists the facts behind it and their groups —
private channels the administrator isn't in are only acknowledged, never named,
listed, counted or identified — and their direct grants on documents the administrator
can read; grants on anything else are acknowledged the same way, and the audit log
leaves out events about documents the administrator can't read. Every change
(clearance, account scope, accounts, departments, administrator-defined groups,
suspension, single-document grants) is previewed first — effects as rules, document
counts only among documents the administrator can read — needs a reason, is applied
against the version reviewed (409 if someone changed it meanwhile) and is written to
the append-only **audit log** in the same transaction. Nobody changes their own access
here. The API's database role can write exactly these changes and nothing else,
enforced by restrictive row-level policies ([ADR-014](docs/decisions/014-access-management-writes.md)).
`make seed` remains the provisioning source: re-running it makes members match the
manifest again, recorded as `operator:seed` events. A database created before Phase 9
needs `make migrate` (0012).

## How sign-in and provisioning work (Phase 10)

Production sign-in is OIDC (authorization code with PKCE) against the organization's
own identity provider; development keeps the persona sign-in. An operator connects the
provider when the organization is created, and its administrators manage it afterwards
in Settings → Identity (P12.4); SCIM tokens are issued there or by an operator
(`lattice sso add`, `lattice scim token create`; each
audited, the token shown once); the identity provider then provisions, updates,
deactivates and removes members over SCIM (`<public URL>/api/scim/v2`). A person signs
in only if the ID token's identifying claim matches a member that *this*
organization's provider provisioned — never by e-mail, never into another
organization. New members get the least access the policy models; administrators
grant the rest on the Access page, where such members are marked as managed by the
identity provider (it alone suspends or reactivates them). `lattice member admin`
gives an organization its first administrator; since P12.2 administrators add members
by hand (bound to the exact identifier the provider sends), choose other administrators
and reinstate removed members with least access on the Access page. Asks, access changes, SCIM and sign-in
are rate limited (429 with `Retry-After`; no role is exempt). Setup and incident
procedures: [runbook §2 and §9](docs/runbook.md); decisions: D-1, D-19, D-20 and
[ADR-015](docs/decisions/015-oidc-sign-in-and-scim-provisioning.md). A database created
before Phase 10 needs `make migrate` (0013); `make audit` checks the locked dependencies
for known vulnerabilities.

## How an organization is created (Phase 12, P12.1)

An operator creates the tenant's trust root with one audited command, `lattice org
create` (organization, safe settings, OIDC connection, and the first administrator bound
to their exact identity-provider identifier — never by e-mail); the first administrator's
first sign-in verifies the connection. Administrators then read the organization's setup
record on the **Organization** page (`/settings`, `GET /v1/admin/setup`): every state is
derived by the backend from live data. OIDC client secrets and provider keys may be stored
encrypted at rest (AES-256-GCM, master keys only in the deployment environment;
`lattice secrets rekey`), and private endpoints are reachable only when an operator
registers them (`lattice outbound allow`). Procedures: [runbook §9 and
§9.1](docs/runbook.md); contracts: [phase-12-plan](docs/phase-12-plan.md). A database created
before P12.1 needs `make migrate` (0016); P12.2 adds 0017 and P12.3 0018 (account
mappings move from `sources.config` into `account_external_refs`). Customer accounts and
their references are administered under **Access → Accounts**.

## How deployment works (Phase 11)

Lattice ships as two images ([ADR-016](docs/decisions/016-production-topology.md),
[deployment contract](docs/deployment.md)): `lattice-backend`, one image that runs the
API, the worker, migrations and operator commands, and `lattice-web`, the Next.js
standalone server. In deployment an ingress terminates TLS and sends `/api/*` straight to
the API (two or more instances) and everything else to the web server; it is the only
hop whose forwarding headers the API trusts (`LATTICE_TRUSTED_PROXIES`). Built in P11.1
and runnable locally:

```
make db-down        # one PostgreSQL at a time on a small machine
make prod-build     # lattice-backend:local and lattice-web:local
make prod-up        # PostgreSQL, bootstrap, lattice migrate, api ×2, worker, web ×2, Caddy (https://localhost:8443)
make prod-smoke     # lattice smoke: ingress, both API instances, worker heartbeat, build revisions
make prod-down
```

`lattice smoke --url <origin>` is the read-only "is this deployment usable?" check.
Releases go through `deploy/deploy.sh` (rolling without a schema change; stop-the-world
`lattice migrate` with a lock, a connection fence and a pre-flight when there is one;
rollback by image ID), and `lattice check-deployment` compares a database with what the
build requires, including a committed privilege snapshot. CI publishes images to GHCR
only from `main`, `v*` tags and manual dispatch. No cloud staging exists: real external
validation is deferred for lack of a deployment budget and gates v1 GA
([plan](docs/phase-11-plan.md) §21.1). P11.3 added identity operations: SSO disable
revokes the connection's sessions, the connection can be re-enabled and updated in place,
and suspension, reinstatement, session revocation and an organization stop are audited
operator commands (runbook §2, §9), verified across both API instances with a
verification identity provider (`make prod-up IDP=1 && make prod-identity`). P11.4 made
every rate limit shared in PostgreSQL and added retention with database-enforced floors;
P11.5 added WAL archiving, base backups and verified restores on the reference stack
(`deploy/backup.sh`, `lattice verify-restore`) — local durations only, managed PITR
unverified. P11.6 added metrics for identity, jobs, storage and WAL archiving, 19 alert
rules with `promtool` tests (`make alerts-test`) and a runbook action per alert. P11.7
closed the phase on the same stack: the deployed authorization suite (`make prod-authz`:
route sweep across organizations, revocation on both instances, roles, the evaluation
security gates as an operator job), the failure drills (`make prod-drills`: an API
instance killed or stopped during an Ask, the database and the model provider down) and
a performance sanity check (`make prod-perf`), recorded in runbook §8.

**What is not proven.** Everything above ran on one laptop. Real cloud staging, managed
PostgreSQL privileges, managed backup/PITR, public TLS and origin, a real external OIDC
callback, provider-specific deploy/rollback and cloud RPO/RTO are deferred for lack of a
deployment budget ([plan](docs/phase-11-plan.md) §21.1); they gate v1 GA (Phase 15), and
until then Lattice is not described as production-proven.

## Repository layout

```
backend/            FastAPI app (src/lattice), Alembic migrations, tests, Dockerfile
apps/web/           Next.js web app (src/: app routes, components, API client; e2e/: Playwright), Dockerfile
infra/postgres/     superuser bootstrap: roles, databases, extensions (also copied into the backend image)
data/synthetic/     synthetic organizations: manifests, raw source exports, ground truth
                    (see data/synthetic/README.md)
evals/golden/       golden question set (evals/reports/: evaluation output, not committed)
docs/               product, architecture, security and design documentation
compose.yaml        local Postgres only (development)
compose.production.yaml  production-shaped local reference stack (Phase 11; make prod-*)
deploy/             reference ingress (Caddy), deploy.sh, drills, verification overlays and identity provider
Makefile            developer commands (thin wrappers; read before running)
```

## Documentation

| Document | What it covers |
| -------- | -------------- |
| [Product brief](docs/product-brief.md) | Customer, users, problem, metrics, non-goals, first vertical slice |
| [Architecture](docs/architecture.md) | System shape, module boundaries, request lifecycle, error taxonomy |
| [Data model](docs/data-model.md) | Schema, per-field rationale, RLS, lifecycle propagation |
| [Authorization model](docs/authorization-model.md) | Auth vs authz, the policy, enforcement points, critical regression test |
| [Trust boundaries](docs/trust-boundaries.md) · [Threat model](docs/threat-model.md) · [Security model](docs/security-model.md) | Security design |
| [Connector design](docs/connector-design.md) · [Ingestion design](docs/ingestion-design.md) | Getting data in, idempotently and fail-closed |
| [Retrieval design](docs/retrieval-design.md) | Hybrid retrieval, ranking, generation, citations, freshness, conflicts, refusals |
| [Evaluation plan](docs/evaluation-plan.md) | Synthetic dataset, golden set, metrics, retrieval and answer results |
| [Synthetic dataset](data/synthetic/README.md) | Halden and Veridian: personas, storylines, timeline, access design, canaries, golden set |
| [Design system](docs/design-system.md) | Design system v2: foundations, signature language, components, patterns, accessibility and security-sensitive UI rules |
| [Design direction](docs/design-direction.md) | The design overhaul's accepted direction, decisions and D1–D7 implementation log |
| [Implementation plan](docs/implementation-plan.md) | Phases, traceability, open decisions, as-built notes per phase |
| [Phase 11 plan](docs/phase-11-plan.md) | Production deployment, identity operations and reliability: contract, decisions D-22–D-27, checkpoints P11.0–P11.7 |
| [Deployment contract](docs/deployment.md) | Provider-independent production contract (Phase 11 targets, marked against today's behaviour) and the staging-provider comparison |
| [Decisions](docs/decisions/README.md) | ADRs |
| [Runbook](docs/runbook.md) | Local development, procedures, operations (health, metrics, kill switches, the worker, deployment, backups, CI) and failure drills |
