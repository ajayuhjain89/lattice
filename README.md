# Lattice

Lattice is a permission-aware enterprise knowledge system: employees ask questions
across internal documents, support tickets and Slack, and get answers that are
grounded, cited, aware of stale and potentially conflicting evidence, and built only from
information they are authorized to see.

**Status: Phases 1–4 are implemented** (Phase 4 awaits review). What runs today: a FastAPI backend with typed
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
as a separate step. There is no retrieval, answering or web UI yet — see the
[implementation plan](docs/implementation-plan.md).

## Local setup

Prerequisites: Docker with Compose v2, [`uv`](https://docs.astral.sh/uv/), `make`,
`git`. `uv` installs Python 3.12 itself.

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
```

Try it (in a second terminal, while `make api` runs):

```bash
curl -s http://127.0.0.1:8000/v1/health
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
it. Run it again: nothing is written the second time. `make sync EMBEDDING=openai`
uses the real provider (needs `LATTICE_OPENAI_API_KEY`, ADR-008); `EMBEDDING=none`
indexes lexically only.

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
| `make dataset-generate` | Regenerate the deterministic bulk distractor tickets after editing authored tickets |
| `make format` | Apply formatting and safe lint fixes |
| `make typecheck` | `mypy --strict` over source, tests and migrations |
| `make db-bootstrap` | Re-run the idempotent superuser bootstrap (e.g. after changing passwords in `.env`) |
| `make db-down` / `make db-destroy` | Stop Postgres / **delete** the local database volume |
| `make help` | List all targets |

CI (`.github/workflows/ci.yml`) runs lint, strict type checking, the dataset lint, the
bootstrap, a migration round-trip on a clean database with a model-drift check, the
seed (twice), the runtime-role check, a sync of the dataset (twice, the second writing
nothing), and the test suite against
`pgvector/pgvector:0.8.1-pg16`.

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
`readable_documents_predicate()` as SQL that retrieval will put inside its queries,
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

## Repository layout

```
backend/            FastAPI app (src/lattice), Alembic migrations, tests
infra/postgres/     superuser bootstrap: roles, databases, extensions
data/synthetic/     synthetic organizations: manifests, raw source exports, ground truth
                    (see data/synthetic/README.md)
evals/golden/       golden question set
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
| [Evaluation plan](docs/evaluation-plan.md) | Synthetic dataset, golden set, metrics |
| [Synthetic dataset](data/synthetic/README.md) | Halden and Veridian: personas, storylines, timeline, access design, canaries, golden set |
| [Design system](docs/design-system.md) | Reference analysis, tokens, components, states |
| [Implementation plan](docs/implementation-plan.md) | Phases, traceability, open decisions, as-built notes per phase |
| [Decisions](docs/decisions/README.md) | ADRs |
| [Runbook](docs/runbook.md) | Local operations (implemented) and later procedures (planned) |
