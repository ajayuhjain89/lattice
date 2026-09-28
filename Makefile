# Lattice developer commands. Every target is a thin, visible wrapper; read it before
# running it. Backend commands load the repository .env via `uv run --env-file`.

# `--extra local`: the optional local-inference runtime (onnxruntime, tokenizers, numpy;
# ADR-009). Model weights are never installed by it -- see `make models-fetch`.
UV_RUN := cd backend && uv run --extra local --env-file ../.env
UV := cd backend && uv run --extra local
MANIFESTS := --manifest ../data/synthetic/halden/org.yaml --manifest ../data/synthetic/veridian/org.yaml

EMBEDDING ?= fake
ORGS := halden veridian

.PHONY: help setup db-up db-down db-bootstrap db-destroy migrate seed api test lint format typecheck check-db-role dataset-lint dataset-generate sync sync-dry-run admin-grants eval eval-answer models-fetch

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
	  -f /bootstrap/bootstrap.sql'

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

test: ## Run the test suite against the lattice_test database
	$(UV_RUN) pytest

lint: ## Ruff lint + format check
	$(UV) ruff check . && uv run ruff format --check .

format: ## Apply ruff formatting and safe fixes
	$(UV) ruff format . && uv run ruff check --fix .

typecheck: ## mypy --strict
	$(UV) mypy
