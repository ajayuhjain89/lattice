# Lattice developer commands. Every target is a thin, visible wrapper; read it before
# running it. Backend commands load the repository .env via `uv run --env-file`.

# `--extra local`: the optional local-inference runtime (onnxruntime, tokenizers, numpy;
# ADR-009). Model weights are never installed by it -- see `make models-fetch`.
UV_RUN := cd backend && uv run --extra local --env-file ../.env
UV := cd backend && uv run --extra local
MANIFESTS := --manifest ../data/synthetic/halden/org.yaml --manifest ../data/synthetic/veridian/org.yaml

EMBEDDING ?= fake
ORGS := halden veridian

.PHONY: help setup db-up db-down db-bootstrap db-destroy migrate seed api test lint format typecheck audit check-db-role worker dataset-lint dataset-generate sync sync-dry-run admin-grants eval eval-answer models-fetch web-setup web web-build web-check web-e2e api-contract

help:
	@grep -E '^[a-z-]+:.*## ' Makefile | sed 's/:.*## /\t/'

setup: ## Install backend dependencies (Python 3.12 via uv) from the lockfile, incl. the local-inference runtime
	cd backend && uv sync --locked --extra local

db-up: ## Start Postgres 16 + pgvector (first start runs the bootstrap)
	docker compose up -d --wait

db-down: ## Stop Postgres (data is kept)
	docker compose down

db-bootstrap: ## Re-run the idempotent superuser bootstrap (roles, databases, extensions)
	docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
	  -v owner_password="$$LATTICE_OWNER_PASSWORD" -v app_password="$$LATTICE_APP_PASSWORD" \
	  -v worker_password="$$LATTICE_WORKER_PASSWORD" -f /bootstrap/bootstrap.sql'

db-destroy: ## DELETE the local database volume (all local data)
	docker compose down -v

migrate: ## Apply migrations to the development database (as lattice_owner)
	$(UV_RUN) alembic upgrade head

seed: ## Upsert the Halden and Veridian manifests: people, accounts, admin groups (idempotent)
	$(UV_RUN) lattice seed $(MANIFESTS)

sync-dry-run: ## Show what `make sync` would create, update, reject and delete (writes nothing)
	@for org in $(ORGS); do \
	  (cd backend && uv run --env-file ../.env lattice sync --org $$org \
	    --sources ../data/synthetic/$$org/sources.yaml --dry-run) || exit $$?; \
	done

sync: ## Ingest both organizations' sources (EMBEDDING=fake|local|gemini|openai|none), then apply admin grants
	@for org in $(ORGS); do \
	  (cd backend && uv run --env-file ../.env lattice sync --org $$org \
	    --sources ../data/synthetic/$$org/sources.yaml --embedding $(EMBEDDING)); \
	  rc=$$?; [ $$rc -eq 0 ] || [ $$rc -eq 3 ] || exit $$rc; \
	done
	@$(MAKE) --no-print-directory admin-grants

admin-grants: ## Apply each organization's admin-grants.yaml (decision D-12; after sync)
	@for org in $(ORGS); do \
	  (cd backend && uv run --env-file ../.env lattice apply-admin-grants --org $$org \
	    --grants ../data/synthetic/$$org/admin-grants.yaml) || exit $$?; \
	done

eval: ## Retrieval metrics over the golden set as each persona (EMBEDDING=fake|local|gemini|openai|none; after `make sync`)
	$(UV_RUN) lattice eval retrieval --embedding $(EMBEDDING)

GENERATION ?= fake

eval-answer: ## Answer + security metrics over the golden set (GENERATION=fake|ollama; after `make sync`)
	$(UV_RUN) lattice eval answer --generation $(GENERATION) --embedding $(EMBEDDING)

models-fetch: ## Download and verify the pinned local embedding model (1.34 GB, once; ADR-009)
	cd backend && LATTICE_ENV=development uv run --extra local lattice models fetch mxbai-embed-large-v1

dataset-lint: ## Validate the synthetic dataset and golden set (no database, no model)
	$(UV) lattice dataset lint

dataset-generate: ## Regenerate the deterministic bulk distractor tickets
	$(UV) lattice dataset generate

check-db-role: ## Verify the runtime role cannot bypass row-level security
	$(UV_RUN) lattice check-db-role

api: ## Run the API on http://127.0.0.1:8000 with reload
	$(UV_RUN) uvicorn lattice.api.app:create_app --factory --reload \
	  --host 127.0.0.1 --port 8000 --no-access-log

worker: ## Run the background worker (the syncs requested from the Sources page) as lattice_worker
	$(UV_RUN) lattice worker

test: ## Run the test suite against the lattice_test database
	$(UV_RUN) pytest

lint: ## Ruff lint + format check
	$(UV) ruff check . && uv run ruff format --check .

format: ## Apply ruff formatting and safe fixes
	$(UV) ruff format . && uv run ruff check --fix .

typecheck: ## mypy --strict
	$(UV) mypy

# pip-audit reads the exact locked set (all extras, with hashes); pnpm audit the web lockfile.
PIP_AUDIT := pip-audit==2.9.0
audit: ## Known-vulnerability audit of the locked backend and web dependencies (network)
	cd backend && uv export --frozen --all-extras --no-emit-project --format requirements-txt -o .audit-requirements.txt >/dev/null \
	  && uvx --from $(PIP_AUDIT) pip-audit --strict --disable-pip --require-hashes -r .audit-requirements.txt; \
	  status=$$?; rm -f .audit-requirements.txt; exit $$status
	$(WEB) pnpm audit --audit-level=low

# --- Web app (Phase 7; Node 22+, pnpm) ---------------------------------------------------
# Development sign-in lists the seeded personas; the API refuses it outside development/test.
WEB := cd apps/web &&
WEB_ENV := LATTICE_WEB_DEV_SIGN_IN=true \
  LATTICE_DEV_PERSONA_MANIFESTS=../../data/synthetic/halden/org.yaml,../../data/synthetic/veridian/org.yaml

web-setup: ## Install the web app's dependencies from its lockfile
	$(WEB) pnpm install --frozen-lockfile

web: ## Run the web app on http://127.0.0.1:3000 (proxies /api to `make api`)
	$(WEB) $(WEB_ENV) pnpm dev

web-build: ## Production build of the web app
	$(WEB) NEXT_TELEMETRY_DISABLED=1 pnpm build

web-check: ## Web lint (ESLint, jsx-a11y, Stylelint), type check and unit/component tests
	$(WEB) pnpm lint && pnpm typecheck && pnpm test

web-e2e: ## Browser tests against the production build, API mocked (run `make web-build` first)
	$(WEB) pnpm exec playwright test --project=desktop --project=mobile

api-contract: ## Re-export the OpenAPI schema and regenerate the web app's TypeScript types
	$(WEB) pnpm api:schema && pnpm api:types
