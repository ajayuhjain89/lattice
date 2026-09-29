# Lattice

Lattice is a permission-aware enterprise knowledge system: employees ask questions
across internal documents, support tickets and Slack, and get answers that are
grounded, cited, aware of stale and potentially conflicting evidence, and built only from
information they are authorized to see.

**Status: Phases 1–7 are implemented and merged; Phase 8 (operability: Sources,
Activity, the background worker, health, metrics and operational limits) is implemented
on `phase/8-operational-polish`, awaiting review.** What runs today: a FastAPI backend with typed
fail-fast configuration, PostgreSQL 16 + pgvector with forced row-level security for
organization isolation, server-side revocable sessions with a development-only persona
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
checks, Prometheus metrics, and fail-closed limits and kill switches. See the
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
| `make web-e2e` | Browser tests against the production build with the API mocked from typed fixtures: flows, not-found safety, keyboard, axe WCAG 2.2 AA, responsive shell (after `make web-build`) |
| `make api-contract` | Re-export the OpenAPI schema and regenerate the web app's TypeScript types (CI fails if they drift) |
| `make help` | List all targets |

CI (`.github/workflows/ci.yml`, on a pinned `ubuntu-24.04` runner) runs lint, strict type
checking, the dataset lint, the bootstrap, a migration round-trip on a clean database
with a model-drift check, the seed (twice), the API's and the worker's startup checks, a
sync of the dataset (twice, the second writing nothing), the retrieval and answer
evaluations as every persona with fake providers (each fails on any security
violation), and the test suite against `pgvector/pgvector:0.8.1-pg16`. A second job checks the web app: the generated API
contract matches the backend, lint, type check, unit and component tests, the
production build, and the Playwright suite (desktop and phone).

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

## How the web app works (Phase 7)

`apps/web` is a Next.js (App Router, TypeScript strict) app that renders and nothing
more: every permission decision is the API's. Its types are generated from the API's
OpenAPI schema; one client adds the CSRF header and normalizes errors; one module holds
every query. Ask sends the question with scope filters that can only narrow the search
(source types, dates, and a per-question sensitivity that can only be raised). Each
answer status has its own presentation — an answer with a lead and supporting claims,
each followed by citation chips numbered in reading order; a partial answer with what
the evidence doesn't establish; a quiet "nothing you can access answers this"; evidence
without a summary when the AI data policy or a missing model stops generation; the
API's own clarification choices; an explanation when a request asks Lattice to act; a
safe failure with retry. A claim citing evidence absent from the response is not
rendered. Potential conflicts keep every position, oldest first, with equal weight; a
supersession appears only when the source records it. The evidence inspector (docked at
≥1280 px, a drawer below, a bottom sheet on phones) reads `GET /v1/evidence/{id}` each
time it opens; a 404 — nonexistent or unreadable — has one presentation. Traces are
fetched fresh and never filled in from the Ask page's memory. AI provider settings are
read-only: configuration is changed with the audited CLI. Details: [design
system](docs/design-system.md), [implementation plan, Phase 7](docs/implementation-plan.md).

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

## Repository layout

```
backend/            FastAPI app (src/lattice), Alembic migrations, tests
apps/web/           Next.js web app (src/: app routes, components, API client; e2e/: Playwright)
infra/postgres/     superuser bootstrap: roles, databases, extensions
data/synthetic/     synthetic organizations: manifests, raw source exports, ground truth
                    (see data/synthetic/README.md)
evals/golden/       golden question set (evals/reports/: evaluation output, not committed)
docs/               product, architecture, security and design documentation
compose.yaml        local Postgres only
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
| [Design system](docs/design-system.md) | Reference analysis, tokens, components, states |
| [Implementation plan](docs/implementation-plan.md) | Phases, traceability, open decisions, as-built notes per phase |
| [Decisions](docs/decisions/README.md) | ADRs |
| [Runbook](docs/runbook.md) | Local development, procedures, operations (health, metrics, kill switches, the worker, deployment, backups, CI) and failure drills |
