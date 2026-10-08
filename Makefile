# Lattice developer commands. Every target is a thin, visible wrapper; read it before
# running it. Backend commands load the repository .env via `uv run --env-file`.

# `--extra local`: the optional local-inference runtime (onnxruntime, tokenizers, numpy;
# ADR-009). Model weights are never installed by it -- see `make models-fetch`.
UV_RUN := cd backend && uv run --extra local --env-file ../.env
UV := cd backend && uv run --extra local
MANIFESTS := --manifest ../data/synthetic/halden/org.yaml --manifest ../data/synthetic/veridian/org.yaml

EMBEDDING ?= fake
ORGS := halden veridian

.PHONY: help setup db-up db-down db-bootstrap db-destroy migrate seed api test lint format typecheck audit check-db-role worker dataset-lint dataset-generate sync sync-dry-run admin-grants eval eval-answer models-fetch web-setup web web-build web-check web-e2e api-contract prod-env prod-build prod-up prod-smoke prod-ps prod-down prod-destroy prod-migrate prod-check prod-seed prod-identity prod-limits prod-authz prod-drills prod-perf prod-backup alerts-test privilege-snapshot

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

# Prometheus rules (P11.6): `promtool check rules` and the rule unit tests, from a pinned
# image (no local Prometheus needed).
PROMTOOL_IMAGE := prom/prometheus:v3.5.0@sha256:63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996
alerts-test: ## Check deploy/observability/alerts.yml and run its promtool unit tests
	docker run --rm -v "$(CURDIR)/deploy/observability:/rules:ro" -w /rules --entrypoint promtool \
	  $(PROMTOOL_IMAGE) check rules alerts.yml
	docker run --rm -v "$(CURDIR)/deploy/observability:/rules:ro" -w /rules --entrypoint promtool \
	  $(PROMTOOL_IMAGE) test rules alerts.test.yml

privilege-snapshot: ## Regenerate the committed privilege snapshot from the freshly migrated test database
	cd backend && uv run --extra local --env-file ../.env sh -c \
	  'LATTICE_MIGRATION_DATABASE_URL="$$LATTICE_TEST_MIGRATION_DATABASE_URL" lattice check-deployment \
	    --write-privilege-snapshot src/lattice/db/privilege_snapshot.json'

check-db-role: ## Verify the runtime role cannot bypass row-level security
	$(UV_RUN) lattice check-db-role

api: ## Run the API on http://127.0.0.1:8000 with reload
	$(UV_RUN) uvicorn lattice.api.app:create_app --factory --reload \
	  --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers

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
	$(WEB) pnpm exec playwright test --project=desktop --project=mobile --project=production

api-contract: ## Re-export the OpenAPI schema and regenerate the web app's TypeScript types
	$(WEB) pnpm api:schema && pnpm api:types

# --- Production-shaped local reference stack (Phase 11, P11.1; docs/deployment.md) -------
# Its own Compose project (lattice-reference): it never touches the development database.
# Stop the development database first on a small machine (`make db-down`).
# VERIFY=1 adds deploy/compose.verify.yaml (test env, persona sign-in, fake providers);
# FAKE_DELAY=<seconds> makes the fake model wait (long-Ask proof).
# IDP=1 adds deploy/compose.idp.yaml: the verification OIDC provider (P11.3).
REFERENCE_ENV := deploy/.env.reference
# The images deploy/deploy.sh last deployed (by immutable ID), when there are any.
DEPLOYED_IMAGES := $(wildcard deploy/state/images.env)
VERIFY ?=
IDP ?=
FAKE_DELAY ?= 0
PROD_COMPOSE := FAKE_DELAY=$(FAKE_DELAY) docker compose --env-file $(REFERENCE_ENV) \
  $(if $(DEPLOYED_IMAGES),--env-file $(DEPLOYED_IMAGES)) \
  -f compose.production.yaml $(if $(VERIFY),-f deploy/compose.verify.yaml) \
  $(if $(IDP),-f deploy/compose.idp.yaml)
REVISION := $(shell git rev-parse HEAD 2>/dev/null || echo unknown)

prod-env: ## Generate deploy/.env.reference: random local-only passwords and drill secrets (git-ignored)
	@umask 077; touch $(REFERENCE_ENV); \
	for name in POSTGRES_PASSWORD PLATFORM_ADMIN_PASSWORD LATTICE_OWNER_PASSWORD \
	  LATTICE_APP_PASSWORD LATTICE_WORKER_PASSWORD LATTICE_CLIENT_KEY_SECRET VERIFY_OIDC_CLIENT_SECRET \
	  VERIFY_OIDC_CLIENT_SECRET_NEXT VERIFY_IDP_ADMIN_TOKEN; do \
	  grep -q "^$$name=" $(REFERENCE_ENV) || { echo "$$name=$$(openssl rand -hex 24)" >> $(REFERENCE_ENV); \
	    echo "added $$name to $(REFERENCE_ENV)"; }; \
	done; \
	grep -q "^LATTICE_SECRET_KEYS=" $(REFERENCE_ENV) || { \
	  echo "LATTICE_SECRET_KEYS=reference-1:$$(openssl rand -base64 32)" >> $(REFERENCE_ENV); \
	  echo "LATTICE_SECRET_ACTIVE_KEY=reference-1" >> $(REFERENCE_ENV); \
	  echo "added LATTICE_SECRET_KEYS and LATTICE_SECRET_ACTIVE_KEY to $(REFERENCE_ENV)"; }

prod-build: ## Build lattice-backend:local and lattice-web:local, labelled with the git revision
	docker build -f backend/Dockerfile --build-arg LATTICE_BUILD_REVISION=$(REVISION) -t lattice-backend:local .
	docker build -f apps/web/Dockerfile --build-arg LATTICE_BUILD_REVISION=$(REVISION) -t lattice-web:local apps/web

prod-up: prod-env ## Start the reference stack: PostgreSQL, bootstrap, migrations, api x2, worker, web x2, Caddy
	$(PROD_COMPOSE) up -d --wait postgres
	$(PROD_COMPOSE) run --rm bootstrap
	$(PROD_COMPOSE) run --rm migrate
	$(if $(IDP),deploy/verification/make-certs.sh)
	$(PROD_COMPOSE) up -d --wait api1 api2 worker web1 web2 caddy $(if $(IDP),idp)

prod-migrate: ## Status of the reference database against the deployed build (read-only)
	$(PROD_COMPOSE) run --rm --no-deps ops migrate --status

prod-check: ## lattice check-deployment against the reference database (read-only)
	$(PROD_COMPOSE) run --rm --no-deps ops check-deployment --api-pool-size 3

# Synthetic-only deployment bootstrap: the Halden and Veridian manifests (each declares
# data_origin: synthetic; --require-synthetic refuses anything else), their sources with
# fake embeddings, and the admin grants. Verification overlay only: seeding is refused when
# LATTICE_ENV=production.
prod-seed: ## Seed + sync the synthetic organizations into the reference stack (VERIFY=1 only)
	@[ -n "$(VERIFY)" ] || { echo "prod-seed needs VERIFY=1 (seeding is refused in production)"; exit 2; }
	$(PROD_COMPOSE) run --rm --no-deps ops seed --require-synthetic \
	  --manifest /srv/lattice/data/synthetic/halden/org.yaml \
	  --manifest /srv/lattice/data/synthetic/veridian/org.yaml
	@for org in $(ORGS); do \
	  $(PROD_COMPOSE) run --rm --no-deps ops sync --org $$org \
	    --sources /srv/lattice/data/synthetic/$$org/sources.yaml --embedding fake; \
	  rc=$$?; [ $$rc -eq 0 ] || [ $$rc -eq 3 ] || exit $$rc; \
	  $(PROD_COMPOSE) run --rm --no-deps ops apply-admin-grants --org $$org \
	    --grants /srv/lattice/data/synthetic/$$org/admin-grants.yaml || exit $$?; \
	done

REFERENCE_CA := deploy/.reference-ca.crt

prod-smoke: ## lattice smoke against the reference stack, from inside its private network
	@# Only the internal CA's public root certificate leaves the Caddy container.
	$(PROD_COMPOSE) cp caddy:/data/caddy/pki/authorities/local/root.crt $(REFERENCE_CA)
	@# Caddy stores it 0600; smoke runs as uid 10001, which on a Linux host cannot read a
	@# bind-mounted 0600 file owned by the caller (Docker Desktop hides this). Public data.
	chmod 0644 $(REFERENCE_CA)
	$(PROD_COMPOSE) run --rm -v $(CURDIR)/$(REFERENCE_CA):/srv/reference-ca.crt:ro ops \
	  smoke --url https://caddy:8443 --ca-file /srv/reference-ca.crt \
	  --instance http://api1:8000 --instance http://api2:8000 \
	  --worker-metrics http://worker:9465/metrics $(if $(VERIFY),,--production) \
	  --instance-metrics http://api1:9464/metrics --instance-metrics http://api2:9464/metrics \
	  --expect-revision $$($(PROD_COMPOSE) run --rm --no-deps -T ops version --json | \
	    python3 -c 'import json, sys; print(json.load(sys.stdin)["revision"])')

# The identity verification of plan §15.3 against the running stack (IDP=1): personas
# provisioned over SCIM sign in through the verification provider across both API
# instances; operator commands, rotation, outage and time-boundary cases. The synthetic
# organization is seeded first by an explicit test-mode operator run (seeding is refused
# in production; this is a verification stack).
prod-identity: ## Identity verification against the stack (needs `make prod-up IDP=1`)
	@[ -n "$(IDP)" ] || { echo "prod-identity needs IDP=1"; exit 2; }
	cd backend && LATTICE_COMPOSE="$(PROD_COMPOSE)" uv run --extra local python \
	  ../deploy/verification/identity_check.py

# Shared limits, retention and worker drills (P11.4) against the running stack:
# `make prod-up VERIFY=1 IDP=1 FAKE_DELAY=20 && make prod-seed VERIFY=1 IDP=1` first.
prod-limits: ## Shared-limit, retention and worker drills (needs VERIFY=1 IDP=1, FAKE_DELAY>=10)
	@[ -n "$(VERIFY)" ] && [ -n "$(IDP)" ] || { echo "prod-limits needs VERIFY=1 IDP=1"; exit 2; }
	cd backend && LATTICE_COMPOSE="$(PROD_COMPOSE)" uv run --extra local python \
	  ../deploy/verification/limits_check.py

# The deployed authorization suite (plan §15.2, P11.7) and the failure drills (§18):
# `make prod-up VERIFY=1 IDP=1 FAKE_DELAY=20 && make prod-seed VERIFY=1 IDP=1` first.
prod-authz: ## Deployed authorization suite: route sweep, revocation, roles, eval security gates
	@[ -n "$(VERIFY)" ] && [ -n "$(IDP)" ] || { echo "prod-authz needs VERIFY=1 IDP=1"; exit 2; }
	cd backend && LATTICE_COMPOSE="$(PROD_COMPOSE)" uv run --extra local python \
	  ../deploy/verification/authz_check.py

prod-drills: ## Failure drills: API kill/SIGTERM mid-Ask, database and provider outages
	@[ -n "$(VERIFY)" ] && [ -n "$(IDP)" ] || { echo "prod-drills needs VERIFY=1 IDP=1"; exit 2; }
	cd backend && LATTICE_COMPOSE="$(PROD_COMPOSE)" uv run --extra local python \
	  ../deploy/verification/drills_check.py

prod-perf: ## Performance sanity through the ingress (needs VERIFY=1 IDP=1, FAKE_DELAY=0)
	@[ -n "$(VERIFY)" ] && [ -n "$(IDP)" ] && [ "$(FAKE_DELAY)" = 0 ] || \
	  { echo "prod-perf needs VERIFY=1 IDP=1 and FAKE_DELAY=0"; exit 2; }
	cd backend && LATTICE_COMPOSE="$(PROD_COMPOSE)" uv run --extra local python \
	  ../deploy/verification/perf_check.py

# Backups (P11.5): restore and verification with deploy/backup.sh restore <id> [...].
prod-backup: ## Base backup of the reference database + row-count manifest (deploy/backup.sh)
	deploy/backup.sh backup

prod-ps: ## Show the reference stack's containers and health
	$(PROD_COMPOSE) ps

prod-down: prod-env ## Stop the reference stack (volumes kept)
	$(PROD_COMPOSE) down --remove-orphans

prod-destroy: prod-env ## DELETE the reference stack and its volumes
	$(PROD_COMPOSE) down -v
