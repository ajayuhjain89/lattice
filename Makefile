# Lattice developer commands. Every target is a thin, visible wrapper; read it before
# running it. Backend commands load the repository .env via `uv run --env-file`.

UV_RUN := cd backend && uv run --env-file ../.env
UV := cd backend && uv run
MANIFESTS := --manifest ../data/synthetic/halden/org.yaml --manifest ../data/synthetic/veridian/org.yaml

.PHONY: help setup db-up db-down db-bootstrap db-destroy migrate seed api test lint format typecheck check-db-role

help:
	@grep -E '^[a-z-]+:.*## ' Makefile | sed 's/:.*## /\t/'

setup: ## Install backend dependencies (Python 3.12 via uv) from the lockfile
	cd backend && uv sync --locked

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

seed: ## Upsert the Halden and Veridian manifests (idempotent)
	$(UV_RUN) lattice seed $(MANIFESTS)

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
