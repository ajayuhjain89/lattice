# Lattice — Deployment Contract

Status: **P11.0 (2026-10-03). This is the provider-independent production contract that
Phase 11 builds towards; most of it is not built yet.** Every section says what exists
**today** and what is the **target** and in which checkpoint it lands
([phase-11-plan.md](phase-11-plan.md) §25). Nothing marked *target* may be relied on
until its checkpoint is approved. Decision record: [ADR-016](decisions/016-production-topology.md);
decisions D-22–D-27 in [implementation-plan.md](implementation-plan.md) §7.

Appendix A (the staging-provider comparison) is the only provider-specific part. The
application must never depend on it.

---

## 1. Topology

```
                     Internet (HTTPS only)
                            │
               ┌────────────▼──────────────┐
               │ Ingress (TLS termination)  │  the only trusted forwarding boundary
               │   /api/*  → api (strip /api)│
               │   /*      → web             │
               └──────┬──────────────┬──────┘
           private    │              │
           network    ▼              ▼
               api ×2 (backend image)     web ×1+ (web image)
               :8000, metrics :9464         :3000
                      │
     worker ×1 (backend image), metrics :9465        migrate (backend image, one-off)
     operator commands (backend image, one-off)
                      │
               PostgreSQL 16 + pgvector (managed, PITR, TLS, private)
```

| | Today | Target |
| - | ----- | ------ |
| `/api/*` routing | The web server's Next.js rewrite proxies `/api/*` to `LATTICE_API_URL` (`apps/web/next.config.ts`); there is no ingress configuration in the repository | The ingress routes `/api/*` straight to the API; Next.js is not on the API path in production (P11.1). The rewrite remains for local development only |
| API instances | One process (`make api`) | Two or more in staging/production and always two in the local reference stack (P11.1) |
| Packaging | Processes run on the host from the source tree | Two images (§3) (P11.1) |

The web server never calls the API server-side: every API call is the browser's,
same-origin, to `/api` (`apps/web/src/api/client.ts`). That is why the ingress can send
`/api/*` past it.

## 2. Processes

| Process | Command (target, inside the image) | Database role | Instances | Today |
| ------- | ---------------------------------- | ------------- | --------- | ----- |
| **api** | `uvicorn lattice.api.app:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log --proxy-headers --forwarded-allow-ips=<ingress addresses> --timeout-graceful-shutdown 150` | `lattice_app` | ≥ 2 | `make api` (127.0.0.1, `--reload`, no proxy headers) |
| **worker** | `lattice worker` | `lattice_worker` | 1 (more are safe: `SKIP LOCKED`) | `make worker` |
| **web** | `node server.js` (Next.js standalone) on `0.0.0.0:3000` | none | ≥ 1 | `next start --hostname 127.0.0.1` (unreachable in a container; P11.1) |
| **migrate** | `alembic upgrade head` (one-off job) | `lattice_owner` | one at a time | `make migrate` |
| **operator** | `lattice <command>` (one-off) | `lattice_owner` | ad hoc | `uv run lattice …` on the host |

## 3. Images (D-27)

| Image | Base | Contents | User / filesystem |
| ----- | ---- | -------- | ----------------- |
| `lattice-backend` | Python 3.12 slim, pinned by digest | the locked backend (`uv sync --locked --no-dev --extra local`: includes the ONNX runtime, never model weights), Alembic, the bootstrap SQL | non-root; read-only root filesystem; `/tmp` writable (tmpfs) |
| `lattice-web` | Node 22 slim, pinned by digest | Next.js standalone output; no persona manifests, no `.env`, no fixtures | non-root; read-only root filesystem |

- **Registry:** GHCR (D-27), published only from pushes to `main`, `v*` tags and manual
  dispatch, after the existing CI jobs pass in the same run (P11.2).
- **Tags:** `sha-<40-char git sha>` for every published build; `vX.Y.Z` additionally for a
  release tag. Never `latest`. Tags are labels; **deployments reference the digest**
  (`…@sha256:…`), recorded in the deploy log.
- **Semantics:** an image from `main` is a *staging candidate*; an image from a `v*` tag is
  a *release artifact*. Only these may be deployed.
- **Metadata (target, P11.1):** OCI labels `org.opencontainers.image.source`, `.revision`,
  `.version`, `.created`; build arguments baked into the environment as
  `LATTICE_BUILD_REVISION` / `LATTICE_BUILD_VERSION`; exposed only as
  `lattice_build_info{revision,version,schema_revision}` on the internal metrics listener
  and in one `startup` log line. Public endpoints never expose a version (Phase 8 rule).
  *Today* nothing records a revision (`FastAPI(version="0.1.0")` is a constant).
- **Never in an image:** secrets, `.env`, `data/synthetic`, test fixtures, model weights.

## 4. Ingress contract

| Requirement | Contract |
| ----------- | -------- |
| Routes | `/api/*` → api, with the `/api` prefix stripped (`/api/v1/ask` → `/v1/ask`; SCIM at `/api/scim/v2/…` → `/scim/v2/…`); every other path → web. Metrics ports are never routed |
| TLS | HTTPS only at the public origin, HTTP redirected; HSTS is sent by the applications in production. The origin equals `LATTICE_PUBLIC_URL` exactly (scheme, host, port) |
| OIDC callback origin | `LATTICE_PUBLIC_URL` + `/api/v1/auth/oidc/<login key>/callback` must reach the API unchanged (path and query) |
| Client address | The ingress is the **only** trusted forwarding boundary. It **appends** the peer address it saw to `X-Forwarded-For` and sets `X-Forwarded-Proto`. The API trusts forwarding headers only from the ingress addresses (`--forwarded-allow-ips`) and takes the right-most address added by the trusted hop. A client-supplied `X-Forwarded-For` therefore never decides the client key. Where a platform edge stands in front of the routing layer, the routing layer derives the client address from that edge's documented header and is itself the only peer the API trusts (Appendix A notes which platforms need this) |
| Timeouts | Upstream read timeout to the API **≥ 150 s** (an Ask may spend `LATTICE_GENERATION_DEADLINE_SECONDS`, default 120 s, in generation plus retrieval). Connect timeout short (≤ 5 s) |
| Body size | Ingress limit ≥ 64 KiB (the API's own cap, `LATTICE_MAX_REQUEST_BYTES`, answers 413 `request_too_large`; the ingress must not cut first with a different error) and ≤ 10 MiB |
| Health routing | The ingress (or platform) probes each api instance at `/v1/health/ready` on the instance port, not through the public route. Liveness `/v1/health/live`. The public `https://<origin>/api/v1/health/ready` is used only by the external availability probe |
| Maintenance | During a migrating release (§9.2) the ingress answers `/api/*` with 503 and `Retry-After` |

**Today (incorrect for production; fixed in P11.1):** the only implemented path is the
Next.js rewrite, which (a) proxies with Next.js's default `proxyTimeout` of 30 s, below the
Ask deadline, and (b) makes the API see the Next.js server as the caller, while Next.js
passes an incoming `X-Forwarded-For` through unchanged. The runbook's advice to trust "the
proxy" therefore names the wrong hop until P11.1.

## 5. Configuration

Classification: **config** (normal setting), **secret** (from the secret store, never in
a file in git or an image), **deployment** (generated or derived per deployment, not
secret). Only settings that exist today are listed unless marked *target*.

### 5.1 api (`lattice.config.Settings`, plus provider settings)

| Variable | Class | Production value / rule |
| -------- | ----- | ----------------------- |
| `LATTICE_ENV` | config | `production` (required; no default) |
| `LATTICE_DATABASE_URL` | secret | `postgresql+asyncpg://lattice_app:…@…/lattice` (TLS); the API refuses any other role |
| `LATTICE_PUBLIC_URL` | deployment | the `https://` origin (required in production) |
| `LATTICE_SESSION_COOKIE_SECURE` | config | `true` (production refuses `false`) |
| `LATTICE_DEV_AUTH_ENABLED` | config | `false` (production refuses `true`) |
| `LATTICE_SESSION_IDLE_TIMEOUT_MINUTES` / `_ABSOLUTE_TIMEOUT_MINUTES` | config | 720 / 10080 (defaults) |
| `LATTICE_LOG_LEVEL` | config | `INFO` |
| `LATTICE_QUERY_EMBEDDING` | config | `none` or `local` (production refuses `fake`); `gemini`/`openai` only with an approved provider |
| `LATTICE_GENERATION_ENABLED` | config | `true`; `false` is the deployment kill switch |
| `LATTICE_GENERATION_DEADLINE_SECONDS` | config | 120 (the ingress timeout depends on it) |
| `LATTICE_MAX_REQUEST_BYTES` | config | 65536 |
| `LATTICE_DB_STATEMENT_TIMEOUT_MS` / `_CONNECT_TIMEOUT_SECONDS` / `_POOL_SIZE` | config | 30000 / 5 / per §8 |
| `LATTICE_METRICS_ADDRESS` | deployment | `0.0.0.0:9464` on the private interface (optional today; required in production as of P11.1) |
| `LATTICE_RATE_LIMIT_*` (six) | config | defaults (§12) |
| `LATTICE_OIDC_HTTP_TIMEOUT_SECONDS` | config | 5 |
| `LATTICE_OIDC_SECRET_<NAME>` | secret | one per confidential OIDC client; referenced from the database as `env:LATTICE_OIDC_SECRET_<NAME>` |
| `LATTICE_PROVIDER_SECRET_<NAME>` | secret | one per organization provider credential; referenced as `env:…` |
| `LATTICE_GEMINI_API_KEY` / `GEMINI_API_KEY` / `LATTICE_OPENAI_API_KEY` | secret | legacy process-wide keys for query embedding only; avoid in production |
| `LATTICE_MODEL_DIR`, `LATTICE_LOCAL_EMBEDDING_MODEL`, `LATTICE_LOCAL_EMBEDDING_THREADS` | config | only with `local` embeddings; the model directory is a read-only volume filled by `lattice models fetch` |
| `LATTICE_CLIENT_KEY_SECRET` | secret | **target, P11.4:** ≥ 32 random bytes, identical on every api instance; HMAC key for the shared pre-authentication counters (§12). Rotation resets those counters only |
| `LATTICE_BUILD_REVISION` / `LATTICE_BUILD_VERSION` | deployment | **target, P11.1:** baked into the image at build |
| trusted ingress addresses | deployment | uvicorn `--forwarded-allow-ips` (command line, not a `LATTICE_*` setting) |

### 5.2 worker (`WorkerSettings`)

| Variable | Class | Production value / rule |
| -------- | ----- | ----------------------- |
| `LATTICE_ENV` | config | `production` |
| `LATTICE_WORKER_DATABASE_URL` | secret | `lattice_worker` credential; the worker refuses an unsafe role |
| `LATTICE_SYNC_EMBEDDING` | config | the provider the corpus was embedded with (production refuses `fake`) |
| `LATTICE_WORKER_POLL_SECONDS` / `_LEASE_SECONDS` | config | 2 / 300 |
| `LATTICE_DB_STATEMENT_TIMEOUT_MS` / `_CONNECT_TIMEOUT_SECONDS` | config | 300000 / 5 |
| `LATTICE_METRICS_ADDRESS` | deployment | `0.0.0.0:9465` private (required in production as of P11.1) |
| provider keys / `LATTICE_PROVIDER_SECRET_*`, model settings | secret / config | as the api, only for the embedding provider the worker uses |

### 5.3 migrate and operator commands (`MigrationSettings`)

| Variable | Class | Rule |
| -------- | ----- | ---- |
| `LATTICE_MIGRATION_DATABASE_URL` | secret | `lattice_owner` credential; available **only** to the migration job and operators, never to api, worker or web |
| `LATTICE_PUBLIC_URL` | deployment | printed by `lattice sso …` for redirect and SCIM URLs |
| `LATTICE_ENV` | config | `production` for commands that load the full settings |

### 5.4 web

| Variable | Class | Rule |
| -------- | ----- | ---- |
| `NODE_ENV` | config | `production` |
| `LATTICE_WEB_DEV_SIGN_IN` | config | unset (the API also refuses development sign-in in production) |
| `LATTICE_DEV_PERSONA_MANIFESTS`, `LATTICE_WEB_SPECIMEN` | config | unset |
| `LATTICE_API_URL` | config | development rewrite target only. **Note:** it is read in `next.config.ts` rewrites, which Next.js evaluates at build time; in the target topology production does not use it |
| `NEXT_TELEMETRY_DISABLED` | config | `1` |

The web process holds **no secret** (architecture §2).

### 5.5 bootstrap (once, by the platform's administrative role)

`LATTICE_OWNER_PASSWORD`, `LATTICE_APP_PASSWORD`, `LATTICE_WORKER_PASSWORD` (secrets) feed
`infra/postgres/bootstrap.sql`; see §7.

## 6. Secrets

No secrets-management product is introduced: the platform's secret store injects
environment variables at process start (security-model §5). There is **no session signing
secret**: session tokens are random and stored as SHA-256 hashes.

| Secret | Owner | Consumers | Rotation | Restart needed | In backups |
| ------ | ----- | --------- | -------- | -------------- | ---------- |
| `lattice_owner` password | platform/DB administrator | migrate, operators | on staff change or suspicion; `ALTER ROLE` by the DB admin + update the secret | next job only | no (PostgreSQL stores only a SCRAM verifier) |
| `lattice_app` password | platform/DB administrator | api | as above | rolling api restart | no (verifier only) |
| `lattice_worker` password | platform/DB administrator | worker | as above | worker restart | no (verifier only) |
| Platform admin DB credential | platform owner | bootstrap, restore, logical export | per platform policy | — | no |
| `LATTICE_OIDC_SECRET_<NAME>` (OIDC client secret) | organization's IdP administrator + operator | api (token exchange) | IdP-driven; same reference with new value (rolling restart), or a new reference via `lattice sso update` (**target, P11.3**; today a reference can only be set when the connection is added) | yes, for a new value | no: the database holds only the reference `env:…` |
| SCIM bearer tokens | operator (`lattice scim token create`) | the organization's IdP | create new → switch the IdP → `lattice scim token revoke` | no | hash only (SHA-256) |
| `LATTICE_PROVIDER_SECRET_<NAME>` (BYOK) | organization administrator via operator | api, worker | add new variable → rolling restart → `lattice provider credential set` (revokes the old reference) → remove old variable | yes, to add a variable | no: reference only |
| `LATTICE_CLIENT_KEY_SECRET` (**target, P11.4**) | operator | api | replace + rolling restart (resets pre-auth counters) | yes | no |
| Session tokens | — (generated) | browsers | rotated at every sign-in | no | hash only |
| OIDC login state (state, nonce, PKCE verifier) | — (generated) | api | ten-minute single-use attempts | no | yes, transiently (`oidc_login_attempts`, deleted by retention, §11) |
| GHCR publishing | repository | the CI `images` job (`GITHUB_TOKEN`, `packages: write` on that job only, **target, P11.2**) | automatic per run | — | — |
| GHCR pull credential (if the package is private) | repository owner | the platform | per platform policy | — | — |

Rules (current code): a credential never raises a sensitivity, trust class or approval
(D-15, D-16); references are restricted to the `LATTICE_OIDC_SECRET_*` and
`LATTICE_PROVIDER_SECRET_*` namespaces (plus three legacy provider names), so a stored
reference cannot name a database URL; values never appear in API responses, logs, traces,
prompts, errors or audit events.

## 7. Database roles and bootstrap

| Role | Login | Used by | Privilege expectation (current code) |
| ---- | ----- | ------- | ------------------------------------ |
| `lattice_owner` | yes | migrate, operators | owns the schema and every object; NOSUPERUSER, NOBYPASSRLS, NOCREATEROLE, NOCREATEDB; subject to forced RLS (binds one organization per transaction); member of `lattice_auth` WITH INHERIT FALSE, SET TRUE (to hand function ownership over in migrations) |
| `lattice_app` | yes | api | no ownership, no BYPASSRLS, not a member of a privileged role, cannot write documents or grants; fenced writes per migrations 0001–0013; the API refuses to start otherwise (`lattice.db.role_check`) |
| `lattice_worker` | yes | worker | no ownership, no BYPASSRLS; writes ingestion tables through RLS one organization per job; sees every organization's jobs only; refuses to start otherwise |
| `lattice_auth` | **no** | nobody | owns the four `SECURITY DEFINER` pre-authentication lookups (`lattice_resolve_session`, `lattice_identity_memberships`, `lattice_oidc_connection`, `lattice_resolve_scim_token`); no `CREATE` anywhere at rest |
| platform administrative role | yes | bootstrap, restore, optional logical export | whatever the platform provides; never configured for api, worker or migrate |

**Bootstrap today** (`infra/postgres/bootstrap.sql`, documented "must run as a superuser")
and its compatibility with managed PostgreSQL, which normally provides no superuser:

| Bootstrap step | Managed-PostgreSQL concern | Expected in P11.2 |
| -------------- | -------------------------- | ----------------- |
| `CREATE ROLE … LOGIN PASSWORD`, `CREATE ROLE lattice_auth NOLOGIN` | needs `CREATEROLE` (most platforms grant it to the admin role) | keep; verify on the selected platform |
| `ALTER ROLE … NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION` | PostgreSQL 16 lets a non-superuser change `BYPASSRLS`/`REPLICATION` only if it holds them; many admin roles do not | drop the attributes that are already the defaults for new roles, keep the startup role check as the authority |
| `GRANT lattice_auth TO lattice_owner WITH INHERIT FALSE, SET TRUE` | PostgreSQL 16 grants need ADMIN OPTION on `lattice_auth`, which a `CREATEROLE` creator has | verify |
| `CREATE DATABASE lattice OWNER lattice_owner` | needs `CREATEDB` and the ability to `SET ROLE lattice_owner` (PostgreSQL 16) | grant the admin role membership in `lattice_owner` for the bootstrap, then revoke; or let the platform create the database and transfer ownership |
| `CREATE DATABASE lattice_test …` | a test database has no place in production | removed from the production variant |
| `CREATE EXTENSION citext, vector` | platform-managed extension allow-lists; some need a platform command | verify `vector` ≥ 0.8 and `citext` on the platform |
| `ALTER SCHEMA public OWNER TO lattice_owner`, `REVOKE CREATE ON SCHEMA public FROM PUBLIC` | in PostgreSQL 15+ `public` is owned by `pg_database_owner`, i.e. the database owner; the statement may be unnecessary or refused | verify; keep the revoke |
| `REVOKE ALL ON DATABASE … FROM PUBLIC`, `GRANT CONNECT …`, `GRANT USAGE ON SCHEMA public …` (`bootstrap_database.sql`) | ordinary owner-level grants | verify |

Whatever the platform forces, the startup checks (`lattice check-db-role`, `lattice worker
--once`) remain the authority: a role with BYPASSRLS, superuser, ownership or content
writes is refused.

## 8. Connection budget

Per process today: every engine allows `pool_size` **plus the same number of overflow**
connections (`max_overflow = pool_size`, `lattice/db/engine.py`): api `LATTICE_DB_POOL_SIZE`
(default 5) → up to 10; worker pool 2 → up to 4; operator commands pool 1 → up to 2.

| Consumer | Connections (defaults) | Staging on a small managed plan |
| -------- | ---------------------- | ------------------------------- |
| api × 2 | 2 × (5 + 5) = 20 | 2 × (3 + 3) = 12 (`LATTICE_DB_POOL_SIZE=3`) |
| worker × 1 | ≤ 4 | ≤ 4 |
| migrate (stopped-world, alone) | 1 | 1 |
| operator commands, smoke, `check-deployment` (P11.2) | ≤ 4 | ≤ 2 |
| platform monitoring / admin | ≤ 2 | ≤ 2 |
| **Total at peak** | ≤ 31 | ≤ 21 |
| Required headroom | ≥ 10 below the usable limit | ≥ 1 at absolute peak (peaks are not simultaneous: migrate runs only with api and worker stopped, so the realistic peak is ≤ 20) |

A plan with ~22 usable backend connections (the smallest DigitalOcean plan, Appendix A)
fits only with `LATTICE_DB_POOL_SIZE=3` and is tight; the 2 GiB plan (47) and a plan with
~100 (Render) fit the defaults. No pooler is added (Phase 14 revisits). `lattice check-deployment` will compute this against
`SHOW max_connections` (**target, P11.2**).

## 9. Deployment

### 9.1 Release without a schema change (rolling, no downtime)

1. Pre-flight: the target digests exist and come from `main` or a `v*` tag with green CI;
   the database revision equals the new build's `SCHEMA_REVISION`.
2. Start new api instances; each must pass readiness; the ingress shifts traffic; old
   instances get SIGTERM and drain (≥ 160 s grace).
3. Replace the worker (the old one finishes its current job; a killed job is recovered by
   its lease).
4. Replace web instances.
5. Smoke (`lattice smoke`, **target, P11.1**) and watch alerts for 15 minutes.

Rollback: redeploy the previous digests the same way.

### 9.2 Release with a schema change (D-23, stop-the-world)

| Step | Action | If it fails |
| ---- | ------ | ----------- |
| 1 | Pre-flight: digests, CI, backups healthy, **record the pre-deploy time** (PITR target), no other deploy running | stop; nothing changed |
| 2 | Ingress: `/api/*` answers 503 + `Retry-After` (web may keep serving pages; they show the API as unavailable) | stop; restore routing |
| 3 | Stop **all** api instances | restart them; restore routing |
| 4 | Stop the worker (it finishes or abandons its job to the lease) | as above |
| 5 | Prove no `lattice_app` / `lattice_worker` session remains (`pg_stat_activity` by role; the migration's own pre-flight refuses otherwise, **target, P11.2**) | find and stop the remaining process; never migrate around it |
| 6 | Run `migrate` with the new image (lock and statement timeouts, **target, P11.2**) | a failed Alembic migration rolls back its transaction: the schema is still the old revision → restart the **old** build (steps 7–9 with the old digests). A migration that committed partially (non-transactional steps) is handled per its header: roll forward, or restore to the step-1 time |
| 7 | Start the new worker and api instances | they refuse to start on any role/schema/config problem; fix forward or roll back (below) |
| 8 | Wait for readiness on every api instance (database + exact schema revision) | as step 7 |
| 9 | Restore `/api/*` routing | — |
| 10 | Smoke; roll web; watch alerts 15 minutes | — |

Expected unavailable interval: steps 2–9, measured in P11.2 (target under two minutes).
Because the old build refuses the new schema and the new build the old one, no step can
leave an old instance serving a migrated schema: the old instances are stopped before step
6 and could not become ready again afterwards.

**Rollback after a successful migration.** Roll forward with a fix by default. A
downgrade (`alembic downgrade <previous>`, then start the previous digests) is allowed
only when the migration's header declares it reversible **and** data-safe (**target,
P11.2**: header and test); none of 0001–0013 carries that declaration today, so treat them
as roll-forward only. Otherwise the last resort is a point-in-time restore to the step-1
time, which loses every write after it; state that window before choosing it.

## 10. Health, startup and shutdown

| Contract | Today | Target |
| -------- | ----- | ------ |
| Startup (api, worker) | Refuse to start on invalid configuration, unsafe role, schema at another revision, unreachable database: exit 1 with a one-line reason | unchanged; platform restarts with back-off |
| Liveness (api) | `GET /v1/health/live` — no dependency | unchanged |
| Readiness (api) | `GET /v1/health/ready` — database answers and schema revision equals the build's; 503 + `Retry-After` otherwise; states only | unchanged |
| Liveness (worker) | **none** (no HTTP; metrics listener optional) | heartbeat file touched every poll, checked by the platform; metrics listener required (**P11.1**) |
| Graceful api shutdown | uvicorn's default on SIGTERM | `--timeout-graceful-shutdown 150` and platform stop grace ≥ 160 s (**P11.1**) |
| Worker shutdown | SIGTERM/SIGINT: finish the current job, then exit | unchanged; stop grace per platform; a killed job's lease (300 s, renewed every 100 s) expires and another poll retries it (3 attempts, then `job_abandoned`); the interrupted run is recorded `aborted` by the next sync |
| Database failure | api: 503 `service_unavailable` + `Retry-After: 5` within the connect timeout; readiness fails, liveness passes; recovers without restart. worker: keeps polling with back-off | unchanged |
| Schema mismatch | api readiness 503 (`check="schema_revision"` metric); a starting api/worker exits 1 | unchanged; alert (P11.6) |

## 11. Backups, PITR and restore

**Requirement (provider-independent).** PostgreSQL with continuous WAL archiving and
point-in-time recovery, automated daily base backups, encryption at rest, restore into a
**new** instance in an isolated network, and a documented recovery window. A production
platform without PITR does not meet this contract.

**Restore verification (P11.5 closure):** restore latest and to a chosen time; on each
restored copy run `lattice verify-restore` (**target, P11.5**), `lattice check-db-role`,
`lattice worker --once` and an api instance to readiness; scan the copy for every
configured secret value (expected: none, §6); record measured RPO/RTO.

**P11.0 records capability and assumptions only — nothing is measured yet:**

| | Documented capability | Provisional assumption (to be replaced by P11.5 measurements) |
| - | --------------------- | ------------------------------------------------------------ |
| Selected staging platform (Appendix A: DigitalOcean Managed PostgreSQL) | daily full backups, WAL kept for restore "to any point-in-time within the previous seven days"; restore creates a new cluster | RPO: minutes (WAL-based), not measured; RTO: the time to create and verify a restored cluster plus a connection-string switch — expected tens of minutes to hours for staging-sized data, not measured |
| Local reference stack (P11.5) | PostgreSQL's own `archive_command` + `pg_basebackup`, `recovery_target_time` | none until measured |

**Logical export** (`pg_dump`) is optional: it needs a role that bypasses RLS (a dump as
`lattice_owner` or a runtime role contains no tenant rows); some platform admin roles have
BYPASSRLS (DigitalOcean's `doadmin` does), others do not.

**Today:** no backup is configured anywhere; runbook §7.7 describes a self-managed
`pg_dump` as a superuser.

## 12. Rate-limit classes

| Limit | Default | Today | P11.4 target |
| ----- | ------- | ----- | ------------ |
| Asks per minute per membership | 10 | **shared/durable** (PostgreSQL `rate_limit_counters`, RLS) | unchanged |
| Asks per day per membership | 500 | **shared/durable** | unchanged |
| Asks in flight per membership | 2 | **process-local** (`ConcurrencyLimiter`) | **shared/durable**: PostgreSQL lease rows, expiry = generation deadline + margin |
| Access previews/writes per administrator | 60/min | **shared/durable** | unchanged |
| SCIM requests per token | 600/min | **shared/durable** | unchanged |
| Pre-authentication per client (development sign-in, OIDC start/callback, failed SCIM authentication) | 30/min | **process-local** (`ClientLimiter`, keyed hash with a per-process random key) | **shared/durable**: non-tenant counter reachable only through `lattice_auth` functions, keyed by HMAC(client address, `LATTICE_CLIENT_KEY_SECRET`), address from the trusted ingress only |
| Request body, question length, statement timeout, generation deadline | as configured | per request (stateless) | unchanged |

**Connection-local:** none, today or after P11.4. After P11.4 no rate limit is
process-local; only caches (OIDC discovery/JWKS, ten minutes) and metrics are per process.

## 13. Retention classes

Durations below come from the canonical documents; where none is decided it says so.

| Data | Current behaviour | Phase 11 target (P11.4) | Deletes it | Minimum-age safety |
| ---- | ----------------- | ----------------------- | ---------- | ------------------ |
| `audit_events` | never deleted (append-only; UPDATE refused by trigger; no runtime role may DELETE); removed only with its organization | unchanged — separate durability/security requirement | nobody (organization purge is Phase 12) | no DELETE privilege for any runtime role |
| `query_traces` | never deleted (90-day retention documented, not enforced) | delete whole rows after `LATTICE_TRACE_RETENTION_DAYS`, default **90** (data-model, security-model §8) | worker | database floor; **the floor value (proposed 30 days) needs product-owner confirmation in P11.4** |
| `jobs` (finished) | deleted after **30 days**, hourly, in batches of 500 | unchanged | worker | database policy permits nothing younger than 30 days (exists) |
| `source_sync_runs` (+ `sync_record_failures`, cascade) | never deleted | **180 days** for runs (data-model), always keeping each source's latest run; failures live and die with their run | worker | database floor at the configured age |
| `sessions` | never deleted (expired/revoked sessions authenticate nothing) | delete some time after `expires_at` / `revoked_at` — **duration not decided in the canonical docs: needs a decision in P11.4** (forensic value vs. growth) | worker | never before the session ended |
| `oidc_login_attempts` | deleted per organization when that organization's next sign-in starts (consumed or expired ones only) | also deleted globally after expiry/consumption — **grace duration to decide in P11.4** | worker (+ the existing per-start cleanup) | never before `expires_at` |
| `rate_limit_counters` | one row per (organization, bucket, subject), overwritten per window, never deleted | delete rows whose window ended more than the longest window (1 day) ago | worker | never within the longest window |
| Pre-auth counters, in-flight Ask leases (both new in P11.4) | — (process memory today) | delete after window end / lease expiry | worker (pre-auth via a `lattice_auth` function) | never within the window / lease |
| Identity tombstones: `directory_identities`, removed memberships, `scim_tokens`, `oidc_connections`, `provider_credentials` history | kept | kept for the organization's life (D-20 terminal removal relies on them) | nobody | — |
| Logs (stdout JSON) | wherever the process runs | platform log retention; **duration to decide with the platform** (no content in logs, SEC-8) | platform | — |

All P11.4 deletions: cutoffs computed from the database `now()`, bounded batches,
idempotent under retry and concurrent runs, metered per task, refused by the database
below the floor (phase-11-plan §12.2).

## 14. Volumes

| Volume | Needed by | Rule |
| ------ | --------- | ---- |
| Source exports | worker (read-only), operator `lattice sync` (writes the configuration that names the path) | Required while connectors read local exports (all three today). The worker syncs only the path the operator's last `lattice sync` recorded (ADR-013), so operator and worker must see the **same path**. Staging handling: open item for P11.2 (Appendix A) |
| Model cache | api and worker, only with `local` embeddings | read-only, filled and verified by `lattice models fetch` |

---

## Appendix A — Staging provider selection (D-22)

Researched 2026-10-03 from the providers' current official documentation (sources at the
end; where only a non-official source gave a number, it is marked). Requirements are those
of D-22. **Nothing has been created.** Values: **S** supported · **L** supported with a
limitation · **N** not supported · **U** unknown / requires validation.

### A.1 Options

1. **Render** — web/private services, background worker, one-off jobs, Render Postgres.
2. **Fly.io** — Machines (apps) + Fly Managed Postgres (MPG).
3. **Google Cloud** — Cloud Run (services, worker pools, jobs) + Cloud SQL for PostgreSQL.
4. **DigitalOcean** — one Droplet running the reference Compose stack + DigitalOcean
   Managed PostgreSQL. (DigitalOcean App Platform was examined and rejected: its documented
   default request timeout is 30 s and the reported hard cap 100 s, below the Ask deadline.)

### A.2 Comparison

| Requirement | Render | Fly.io + MPG | Cloud Run + Cloud SQL | DO Droplet + Managed PG |
| ----------- | ------ | ------------ | --------------------- | ----------------------- |
| Web service | S | S | S | S (Compose on the VM) |
| API service, ≥ 2 instances | S (private service, manual scaling; built-in balancing) | S | S (min instances 2, billed idle) | S (two containers behind Caddy) |
| Long-running worker | S (background worker) | S | S (worker pools) | S |
| One-off migration job | S (one-off jobs API; pre-deploy command exists but runs while old instances serve, so not used for D-23) | L (`release_command` runs during deploy; a stop-the-world run must be scripted with machine stop/start) | S (Cloud Run jobs) | S (`docker compose run`) |
| Image deploy from GHCR | S (private GHCR with credentials) | S | L (public GHCR only; private needs an Artifact Registry remote repository) | S |
| Deploy by digest | S | S | S | S |
| Graceful restart / zero-downtime | S (new instance healthy → switch → 60 s → SIGTERM; shutdown delay up to 300 s) | U (validate stop grace) | S | L (Docker restart policy; rolling replacement is scripted by us) |
| Deployment history / rollback | S (rollback needs the image still in the registry) | L (releases; rollback = redeploy image) | S (revisions, traffic to a previous revision) | L (our deploy log + previous digest) |
| PostgreSQL 16 | S (13–18) | S (16) | S | S (14–18) |
| pgvector | S | S (shipped in every image) | S (0.8.5 per docs) | S |
| citext | S | U | S | S |
| Privilege model vs bootstrap (§7) | U: default user "not a superuser" with many privileges; `CREATE USER` works; CREATEROLE/BYPASSRLS not documented | **L/N**: users are created through `flyctl` with fixed roles (Schema Admin, Writer, Reader); custom roles with set attributes and a NOLOGIN function owner (`lattice_auth`) are undocumented; extensions only through a platform command | L: `cloudsqlsuperuser` has CREATEROLE and CREATEDB, no SUPERUSER, BYPASSRLS not documented | **S/L**: `doadmin` has CREATEROLE, CREATEDB, REPLICATION and BYPASSRLS (no superuser) — the closest to the bootstrap's needs; still to validate (§7) |
| Backups | S (daily logical exports kept 7 days) | S (rolling, 10 days) | S | S (daily, 7 days) |
| PITR | **L**: Hobby workspace 3 days, Pro 7 days; not within 10 minutes of now; restore creates a new instance | S (new cluster; window ends at the last commit — idle clusters do not extend it) | S (clone to a new instance; window configurable) | S (7 days; restore creates a new cluster; destroying a cluster destroys its backups) |
| Restore | new instance, then repoint services | new cluster | new instance | new cluster, then repoint |
| Connection limits | S: 100 below 8 GB RAM | U (PgBouncer in front by default) | S | **L**: 22 usable at 1 GiB, 47 at 2 GiB → `LATTICE_DB_POOL_SIZE=3` for 1 GiB (§8) |
| HTTPS / custom origin | S | S | L (custom origin + path routing need a global external Application Load Balancer, ~$18/month for its forwarding rule) | S (Caddy, Let's Encrypt) |
| `/api/*` → API, rest → web on one origin | **L**: no path routing between services; needs our own Caddy web service in front (extra service) | **L**: no path routing; needs a Caddy app | L: load balancer URL map (above) | S (Caddy, as the local stack) |
| Stable OIDC callback URL | S | S | S | S |
| Forwarded client address / single trusted hop | L: platform edge (Cloudflare + load balancer) then our Caddy; Caddy must trust the platform's private range and the API only Caddy | L: Fly proxy (`Fly-Client-IP`) then Caddy | L: Google front end / load balancer then Cloud Run | **S**: Caddy is the only hop, exactly the contract |
| Request timeout ≥ 150 s | S (responses up to 100 minutes, per Render) | U (validate fly-proxy limits) | S (default 300 s, max 3,600 s) | S (no platform proxy) |
| Body size ≥ 64 KiB | U (not documented; small bodies only) | U | S | S |
| Private networking | S | S | L (Cloud SQL private IP needs VPC setup) | S (VPC between Droplet and database; database trusted sources) |
| Secrets | S (environment groups) | S (`fly secrets`) | S (Secret Manager) | **L**: no managed secret store for a Droplet; root-only env file on the VM |
| Logs | L (7 days on Hobby; 6,000 lines/min/instance cap) | S | S | L (`docker logs` on the VM only; nothing retained by the platform) |
| Prometheus metrics (internal listener) | L: no scraping service; manual scrape over the private network/shell, or our own scraper service | S (managed Prometheus scrapes `[metrics]`) | L (sidecar / managed service, extra setup) | S (Prometheus in the Compose stack) |
| Health checks | S | S | S | L (Docker healthchecks; no platform health routing) |
| Export volume shared by operator and worker | L: a disk attaches to one service and disables zero-downtime deploys; one-off jobs cannot see disks | L (volume per machine) | L (no shared volume without extra storage) | S (host directory) |
| CLI / API deploy | S | S | S | S (ssh + compose) |
| Cost if left running (approx.) | **≈ $42/month**: 5 Starter instances (Caddy, api ×2, web, worker) × $7 + Postgres Basic-256mb $6 + storage $0.30/GB *(instance and Postgres prices from a third-party July 2026 summary; storage price from Render's own article)* | **≈ $59/month**: MPG Basic $38 + 5 × shared-cpu-1x 512 MB $3.69 + IPv4 $2 + storage $0.28/GB | **≈ $50–70/month**: load balancer ≈ $18 + Cloud SQL `db-f1-micro` ≈ $9–13 *(third-party)* + storage/backups + api min-instances and worker pool billed continuously | **≈ $27/month**: Droplet 2 GiB $12 + Managed PostgreSQL 1 GiB $15 (10 GiB storage included) |
| Phase 11 window (≈ 3 deployment windows totalling ~7 days, database kept for ~1 month so PITR history survives) | ≈ $12–15 | ≈ $45 (MPG minimum dominates) | ≈ $30–40 | **≈ $18–20** (database ~$15 for the month, Droplet ~$3, a restored cluster for a few hours < $1) |
| Unavoidable minimums | none (Hobby workspace $0); paid Postgres needed for PITR | MPG plan $38/month | load balancer forwarding rule | Droplet per second (60 s minimum); database hourly (1 h minimum) |
| Backup/PITR cost | included in the Postgres plan | included; storage $0.28/GB | separate backup and WAL storage charges | included in the plan |
| Free-tier limitations | free Postgres has no PITR/backups; Hobby: 3-day PITR, 7-day logs, 5 GB bandwidth | none relevant | free tier covers small request volumes, not min instances or the load balancer | none |

### A.3 Recommendation

**Recommended: DigitalOcean — one Droplet running the reference Compose stack, with
DigitalOcean Managed PostgreSQL 16.**

Why it is preferable for Lattice (not merely cheapest):
- **It meets every hard requirement directly.** No platform proxy with a request ceiling,
  so the 120 s Ask deadline and the ≥ 150 s ingress timeout are ours to set; Caddy is the
  single trusted hop, exactly as §4 specifies; `/api/*` routing is the same configuration
  as the local stack.
- **The database privilege model is documented and closest to the bootstrap:** `doadmin`
  has CREATEROLE, CREATEDB and BYPASSRLS, so the four Lattice roles, the NOLOGIN function
  owner and the extensions can be created, and a logical export of tenant rows is
  possible. Fly's fixed user roles and Render's undocumented attributes are the largest
  risks in the other options.
- **Real managed PITR** (seven days, restore to a new cluster) for the P11.5 drill.
- **Staging = the local reference stack plus managed PostgreSQL, real TLS and a real public
  origin**, so P11.7's staging verification tests the contract, not a platform adapter, and
  nothing in the application learns about the provider.
- Lowest recurring cost, per-second Droplet billing; it can be torn down between windows.

What it still fails or compromises:
- **We operate the VM** (OS updates, Docker, firewall): acceptable for a temporary staging
  environment, not a production recommendation.
- **No managed secret store**: secrets live in a root-only (0600) env file on the VM.
- **No platform log retention or managed rollout:** logs stay on the VM (`docker logs`);
  rolling replacement and rollback are our scripted procedure (§9), which is also what
  P11.2 must prove anyway.
- **22 usable connections on the 1 GiB plan**: run with `LATTICE_DB_POOL_SIZE=3` (§8), or
  choose the 2 GiB database plan (47 connections) at a higher cost (not priced here; check
  before creating).
- Destroying the database cluster destroys its backups: keep the cluster for the whole
  Phase 11 period, tear down only the Droplet between windows.
- Validation still owed in P11.2: every bootstrap statement of §7 against `doadmin`,
  `vector` version, and the `pg_stat_activity` visibility the migration pre-flight needs.

**Runner-up: Render.** Strongest managed-platform experience (deploy history, rollback by
digest, graceful shutdown up to 300 s, 100-minute request limit, built-in PITR). It loses
on: an undocumented database privilege model (bootstrap risk), a three-day PITR window on
the free Hobby workspace, a second hop (Render edge → our Caddy service) before the API,
no metrics scraping, and disks that cannot be shared between the operator job and the
worker.

**Estimated Phase 11 staging cost: ≈ $18–20** (database kept ~1 month, Droplet only during
P11.2, P11.5 and P11.7 windows); ≈ $27/month if everything is left running.

**You would need to create or configure manually** (Claude creates nothing):
1. A DigitalOcean account and payment method; a project for Lattice staging.
2. A Managed PostgreSQL 16 cluster (1 GiB or 2 GiB, one node, same region as the Droplet),
   with "trusted sources" limited to the Droplet.
3. A Droplet (2 GiB, Ubuntu LTS) with an SSH key you control, and the DigitalOcean cloud
   firewall allowing only 22 (your address), 80 and 443.
4. A DNS name (e.g. `staging.<your domain>`) pointing at the Droplet, for Caddy's
   certificate and the OIDC callback.
5. GHCR pull access for the Droplet if the packages are private (a read-only token).
6. The staging secrets (§6) placed in the Droplet's env file by you or by a script you run;
   never pasted into chat.
7. An OIDC application at your chosen test identity provider with the staging callback URL
   (or the verification IdP of phase-11-plan §15.2, which needs nothing external).

### A.4 Sources (retrieved 2026-10-03)

- Render: [Postgres recovery and backups](https://render.com/docs/postgresql-backups),
  [Postgres extensions](https://render.com/docs/postgresql-extensions),
  [Create & connect (versions, connection limits)](https://render.com/docs/postgresql-creating-connecting),
  [Deploy an image (GHCR, digests, rollback)](https://render.com/docs/deploying-an-image),
  [Deploys (zero-downtime, shutdown delay, pre-deploy)](https://render.com/docs/deploys),
  [One-off jobs](https://render.com/docs/one-off-jobs),
  [Private network](https://render.com/docs/private-network),
  [Logging](https://render.com/docs/logging),
  [Hosting cost article, 2026-07-08](https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses),
  [Long-running tasks (100-minute responses)](https://render.com/articles/how-to-trigger-a-long-running-task-from-a-web-service-on-render),
  [FastAPI production guide (X-Forwarded-For)](https://render.com/articles/fastapi-production-best-practices);
  instance prices from a third-party summary: [kuberns.com](https://kuberns.com/blogs/render-pricing/).
- Fly.io: [Managed Postgres](https://docs.fly.io/mpg), [Pricing](https://docs.fly.io/about/pricing),
  [MPG cluster configuration (user roles)](https://docs.fly.io/mpg/cluster-configuration),
  [community: PITR window, 2026-09-15](https://community.fly.io/t/managed-postgres-pitr-time-refused-while-backup-id-restore-works-how-do-i-read-the-recovery-window/28638),
  [community: MPG superuser and extensions, 2026-07](https://community.fly.io/t/mpg-superuser-permissions-for-extensions/28124).
- Google Cloud: [Cloud SQL users (`cloudsqlsuperuser`)](https://docs.cloud.google.com/sql/docs/postgres/users),
  [Cloud SQL PITR](https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/pitr),
  [Cloud SQL extensions](https://docs.cloud.google.com/sql/docs/postgres/extensions),
  [Cloud Run request timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout),
  [Cloud Run worker pools](https://docs.cloud.google.com/run/docs/deploy-worker-pools),
  [Cloud Run deploying (registries, digests)](https://docs.cloud.google.com/run/docs/deploying),
  [Load balancing pricing](https://cloud.google.com/load-balancing/pricing);
  `db-f1-micro` price from a third-party source: [bytebase.com](https://www.bytebase.com/dbcost/cloudsql/instance/db-f1-micro/).
- DigitalOcean: [PostgreSQL features (7-day PITR)](https://docs.digitalocean.com/products/databases/postgresql/details/features/),
  [Restore from backups](https://docs.digitalocean.com/products/databases/postgresql/how-to/restore-from-backups/),
  [Supported extensions](https://docs.digitalocean.com/products/databases/postgresql/details/supported-extensions/),
  [Limits (connections)](https://docs.digitalocean.com/products/databases/postgresql/details/limits/),
  [Database pricing](https://docs.digitalocean.com/products/databases/postgresql/details/pricing/),
  [Modify user privileges (`doadmin` attributes)](https://docs.digitalocean.com/products/databases/postgresql/how-to/modify-user-privileges/),
  [Droplet pricing](https://www.digitalocean.com/pricing/droplets),
  [App Platform limits](https://docs.digitalocean.com/products/app-platform/details/limits/),
  [App Platform pricing](https://docs.digitalocean.com/products/app-platform/details/pricing/),
  [App Platform 30 s default timeout](https://docs.digitalocean.com/support/my-php-app-is-timing-out-and-throwing-5xx-errors/),
  [App spec](https://docs.digitalocean.com/products/app-platform/reference/app-spec/),
  [community: 100 s hard limit](https://www.digitalocean.com/community/questions/app-platform-request-timeout).
