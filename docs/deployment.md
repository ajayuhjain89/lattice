# Lattice — Deployment Contract

Status: **P11.6 (2026-10-05).** This is the provider-independent production contract that
Phase 11 builds towards. **Built in P11.1:** the two images, the ingress routing and the
single trusted hop, the API's trusted-proxy setting, worker liveness, graceful shutdown,
build identification, `lattice smoke` and the local reference stack (§15). **Built in
P11.2:** the managed-PostgreSQL bootstrap (§7), `lattice migrate` with its lock, fence and
pre-flight, migration headers, `lattice check-deployment` with the privilege snapshot
(§8), `lattice check-config` and `lattice version`, the scripted deployment and rollback
(§9, `deploy/deploy.sh`), the ingress maintenance switch, two web instances, and the GHCR
`images` job (§3). **Built in P11.3:** sessions recording their SSO connection, the
connection lifecycle and the audited incident commands (runbook §2, §9), the clock-skew
gauge, and the verification identity provider for the reference stack (§15). **Not built
yet:** nothing of Phase 11 (P11.7 is verification and closure). **Built in P11.6:**
metrics, alert rules with tests and the monitoring contract (§16). **Built in P11.5:** WAL archiving, base backups, restore and
`lattice verify-restore` on the reference stack (§11). **Built in P11.4:** every rate limit shared in
PostgreSQL (§12) and retention with database floors (§13). Every section says
what exists **today** and what is a **target** and in which checkpoint it lands
([phase-11-plan.md](phase-11-plan.md) §25); nothing marked *target* may be relied on
until its checkpoint is approved. Decision record:
[ADR-016](decisions/016-production-topology.md); decisions D-22–D-27 in
[implementation-plan.md](implementation-plan.md) §7.

Appendix A (the staging-provider comparison) is the only provider-specific part. The
application must never depend on it.

> **Zero paid infrastructure (D-22 amended 2026-10-05).** No cloud resource exists or is
> created in Phase 11. Every "staging" step in this contract is verified on the local
> reference stack (§15) only. Deferred because no deployment budget is currently
> available, and **not** claimed done: real cloud staging deployment; managed PostgreSQL
> privilege verification; managed backup/PITR verification; public TLS/origin validation;
> real external OIDC callback validation; provider-specific deploy/rollback validation;
> cloud RPO/RTO measurement. These are a release gate for Phase 15 (v1 GA)
> ([phase-11-plan.md](phase-11-plan.md) §21.1).

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
               api ×2 (backend image)     web ×2 (web image)
               :8000, metrics :9464         :3000
                      │
     worker ×1 (backend image), metrics :9465        migrate (backend image, one-off)
     operator commands (backend image, one-off)
                      │
               PostgreSQL 16 + pgvector (managed, PITR, TLS, private)
```

| | Today (P11.1) | Target |
| - | ------------- | ------ |
| `/api/*` routing | The ingress routes `/api/*` straight to the API (reference stack: Caddy, `deploy/caddy/Caddyfile`). The production web build has **no** `/api` route: the Next.js rewrite exists only under `next dev` (`apps/web/next.config.ts`), with a 150 s proxy timeout | unchanged; staging/production use their own ingress under §4 (P11.2) |
| API instances | Two in the reference stack (`api1`, `api2`); one under `make api` in development | ≥ 2 in staging and production |
| Packaging | `lattice-backend` and `lattice-web` images (§3), built locally (`make prod-build`); the CI `images` job (P11.2) builds, smokes and publishes them to GHCR from `main`, `v*` tags and manual dispatch only | unchanged |
| Web instances | Two in the reference stack (`web1`, `web2`, P11.2), so a release replaces one at a time | ≥ 2 wherever web releases must not show a gap |

The web server never calls the API server-side: every API call is the browser's,
same-origin, to `/api` (`apps/web/src/api/client.ts`). That is why the ingress can send
`/api/*` past it.

## 2. Processes

| Process | Command inside the image (P11.1) | Database role | Instances | Development |
| ------- | -------------------------------- | ------------- | --------- | ----------- |
| **api** | the image's default: `uvicorn lattice.api.app:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log --no-proxy-headers --timeout-graceful-shutdown 150` | `lattice_app` | ≥ 2 | `make api` (127.0.0.1, `--reload`, `--no-proxy-headers`) |
| **worker** | `lattice worker` | `lattice_worker` | 1 (more are safe: `SKIP LOCKED`) | `make worker` |
| **web** | the image's default: `node server.js` (Next.js standalone) on `0.0.0.0:3000` | none | ≥ 1 (≥ 2 for gap-free releases) | `make web` (`next dev`, 127.0.0.1) |
| **migrate** | `lattice migrate` (one-off job, P11.2: lock, fence, pre-flight, upgrade, exact verification; §9.2) | `lattice_owner` | one at a time (the lock refuses a second) | `make migrate` (plain `alembic upgrade head`, development only) |
| **operator** | `lattice <command>` (one-off) | `lattice_owner` | ad hoc | `uv run lattice …` on the host |

`--no-proxy-headers` matters: uvicorn's own default trusts forwarding headers from
127.0.0.1. Forwarding headers are trusted only through `LATTICE_TRUSTED_PROXIES`, inside
the application (§4), whatever command starts it.

## 3. Images (D-27)

| Image | Base | Contents | User / filesystem |
| ----- | ---- | -------- | ----------------- |
| `lattice-backend` | Python 3.12 slim, pinned by digest | the locked backend (`uv sync --locked --no-dev --extra local`: includes the ONNX runtime, never model weights), Alembic, the bootstrap SQL | non-root; read-only root filesystem; `/tmp` writable (tmpfs) |
| `lattice-web` | Node 22 slim, pinned by digest | Next.js standalone output; no persona manifests, no `.env`, no fixtures | non-root; read-only root filesystem |

- **Registry:** GHCR (D-27), published only from pushes to `main`, `v*` tags and manual
  dispatch. **As built (P11.2):** the `images` job of `.github/workflows/ci.yml`. On a
  push to `main` or a `v*` tag it `needs` the backend, web and dependency-audit jobs of
  the **same** run (nothing is re-tested). On `workflow_dispatch` (choose the branch or
  commit in the Actions UI, give a reason) the three test jobs are skipped and the job
  instead requires an earlier **successful push run of this exact commit** (GitHub API,
  `actions: read`), so a Phase 11 revision can be published for future staging without
  merging it and without re-running the suites. It builds both images with the commit
  as `LATTICE_BUILD_REVISION`, starts the reference stack from them (`make prod-up`),
  runs `make prod-smoke` and `make prod-check`, then pushes `sha-<commit>` (plus the
  release tag on `v*`) with the run's `GITHUB_TOKEN` (`packages: write` on this job only;
  no long-lived registry credential) and writes the digests to the job summary. A
  development-branch push builds no image. The repository is private: GHCR storage then
  counts against the account's free package allowance; nothing is published until a
  push to `main`, a tag or a deliberate dispatch.
- **Tags:** `sha-<40-char git sha>` for every published build; `vX.Y.Z` additionally for a
  release tag. Never `latest`. Tags are labels; **deployments reference the digest**
  (`…@sha256:…`), recorded in the deploy log.
- **Semantics:** an image from `main` is a *staging candidate*; an image from a `v*` tag is
  a *release artifact*. Only these may be deployed.
- **Metadata (P11.1):** OCI labels `org.opencontainers.image.title`, `.source`,
  `.revision`, `.version`; build arguments `LATTICE_BUILD_REVISION` /
  `LATTICE_BUILD_VERSION` baked into the environment (`make prod-build` passes the git
  revision). Reported only as `lattice_build_info{revision,version,schema_revision}` on
  the internal metrics listener and in one `startup` log line per api and worker process
  (`lattice.build`); unset or malformed values read `unknown`. Public endpoints never
  expose a version (Phase 8 rule; tested). `.created` is set by the `images` job.
- **As built (P11.1):** `backend/Dockerfile` (context: the repository root, limited by
  `backend/Dockerfile.dockerignore` to `backend/` sources, Alembic and the bootstrap SQL):
  a build stage with uv 0.12.19 and the locked dependencies, a runtime stage with only the
  virtual environment, `alembic/`, `alembic.ini` and `infra/postgres/*.sql`; user
  `lattice` (uid/gid 10001); about 566 MB, most of it the ONNX runtime and NumPy.
  `apps/web/Dockerfile` (context `apps/web`, `apps/web/.dockerignore`): `pnpm install
  --frozen-lockfile` and `next build` with `LATTICE_WEB_STANDALONE=1` in a build stage; the
  runtime stage holds only `.next/standalone`, `.next/static` and `public/`, root-owned and
  run as `node` (uid 1000); about 406 MB, mostly the Node.js base image. Neither image has
  a `HEALTHCHECK`: the backend image runs several processes with different health
  signals, so each deployment declares them per process (§10).
- **Never in an image:** secrets, `.env`, `data/synthetic`, test fixtures, model weights.

## 4. Ingress contract

| Requirement | Contract | Reference stack (P11.1, `deploy/caddy/Caddyfile`) |
| ----------- | -------- | ------------------------------------------------- |
| Routes | `/api/*` → api, with the `/api` prefix stripped (`/api/v1/ask` → `/v1/ask`; SCIM at `/api/scim/v2/…` → `/scim/v2/…`); every other path → web. Metrics, admin and database ports are never routed | `handle_path /api/*` → `api1:8000 api2:8000`, round robin, readiness-gated (`health_uri /v1/health/ready` every 5 s), a refused connection retried on the other instance for up to 5 s; `handle` → `web:3000`. The production web build has no `/api` route at all (a request reaching it directly gets the web 404) |
| TLS | HTTPS only at the public origin, HTTP redirected; HSTS is sent by the applications in production. The origin equals `LATTICE_PUBLIC_URL` exactly (scheme, host, port) | Caddy's internal CA (`tls internal`), on `https://localhost:8443` (and `https://caddy:8443` inside the network for `lattice smoke`); `http://localhost:8080` redirects (308) keeping the port. **Not** equivalent to a public certificate: browsers warn, nothing trusts it |
| OIDC callback origin | `LATTICE_PUBLIC_URL` + `/api/v1/auth/oidc/<login key>/callback` must reach the API unchanged (path and query) | `LATTICE_PUBLIC_URL=https://localhost:8443` |
| Client address | The ingress is the **only** trusted forwarding boundary. It discards any client-supplied `X-Forwarded-For`/`-Proto` (or appends to them) and states the peer it accepted. The API accepts forwarding headers only from `LATTICE_TRUSTED_PROXIES` (IP addresses or CIDR networks; never `*`), applied inside the application by uvicorn's `ProxyHeadersMiddleware`, which takes the right-most address not belonging to a trusted hop; from any other peer the TCP address is the client. Where a platform edge stands in front of the routing layer, the routing layer derives the client from that edge's documented header and is itself the only peer the API trusts (Appendix A) | Caddy has a fixed address (172.31.250.10) on the private network; `LATTICE_TRUSTED_PROXIES=172.31.250.10`. Caddy has no `trusted_proxies`, so it replaces client-supplied forwarding headers with the connection it accepted |
| Timeouts | Upstream response timeout to the API **≥ 150 s** (an Ask may spend `LATTICE_GENERATION_DEADLINE_SECONDS`, default 120 s, in generation plus retrieval). Connect timeout ≤ 5 s | `response_header_timeout 180s`, `dial_timeout 5s`; request header/body read 10 s / 30 s; web upstream 30 s |
| Body size | The API's own cap, `LATTICE_MAX_REQUEST_BYTES` (64 KiB, 413 `request_too_large`), is authoritative. The ingress may only stop larger uploads, above that cap and ≤ 10 MiB, so it never answers first for a body the API would accept. No route needs more than 64 KiB | `request_body max_size 1MB`: bodies between 64 KiB and 1 MB get the API's typed 413 |
| Health routing | The ingress (or platform) probes each api instance at `/v1/health/ready` on the instance port, not through the public route. Liveness `/v1/health/live`. The public `https://<origin>/api/v1/health/ready` is for the external availability probe | as stated; container health checks per process (§10) |
| Maintenance | During a migrating release (§9.2) the ingress answers `/api/*` with 503 and `Retry-After` | **P11.2:** while `deploy/maintenance/api` exists (mounted read-only at `/srv/maintenance`), `/api/*` answers 503, `Retry-After: 30`, the API's `default-src 'none'` CSP and the API's error envelope (`service_unavailable`); web pages keep serving |
| Ingress failures (**P11.7**) | When the ingress itself cannot answer `/api/*` — no instance passes readiness (the database is down), the chosen instance died mid-request, no response header within the timeout — the client gets what the API answers when it is unavailable: 503 `service_unavailable`, `Retry-After`, the API's error envelope; never the ingress's own empty error page | `handle_errors 502 503 504` for `/api/*` (matched on the original path): 503, `Retry-After: 5`, `default-src 'none'`, the envelope. A request that was in flight on a killed instance is **not** replayed (an Ask is not idempotent at the transport level); the client may retry |

**Development only:** `next dev` keeps proxying `/api/*` to `LATTICE_API_URL` (the
Next.js server is then the API's peer, and `make api` trusts no forwarding header), with
`experimental.proxyTimeout` 150 s so a long Ask is not cut at Next.js's 30 s default.

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
| `LATTICE_METRICS_ADDRESS` | deployment | `0.0.0.0:9464` on the private interface; **required in production** (P11.6) — scraped as job `lattice-api` (§16) |
| `LATTICE_RATE_LIMIT_*` (six) | config | defaults (§12) |
| `LATTICE_OIDC_HTTP_TIMEOUT_SECONDS` | config | 5 |
| `LATTICE_OIDC_SECRET_<NAME>` | secret | one per confidential OIDC client; referenced from the database as `env:LATTICE_OIDC_SECRET_<NAME>` |
| `LATTICE_PROVIDER_SECRET_<NAME>` | secret | one per organization provider credential; referenced as `env:…` |
| `LATTICE_GEMINI_API_KEY` / `GEMINI_API_KEY` / `LATTICE_OPENAI_API_KEY` | secret | legacy process-wide keys for query embedding only; avoid in production |
| `LATTICE_MODEL_DIR`, `LATTICE_LOCAL_EMBEDDING_MODEL`, `LATTICE_LOCAL_EMBEDDING_THREADS` | config | only with `local` embeddings; the model directory is a read-only volume filled by `lattice models fetch` |
| `LATTICE_CLIENT_KEY_SECRET` | secret | **P11.4, required in production:** ≥ 32 bytes (`openssl rand -hex 32`), identical on every api instance; HMAC key for the shared pre-authentication counters (§12). Rotation resets those counters only. Outside production a fixed development key is used |
| `LATTICE_BUILD_REVISION` / `LATTICE_BUILD_VERSION` | deployment | baked into the image at build (P11.1); never set by hand |
| `LATTICE_TRUSTED_PROXIES` | deployment | **P11.1:** the ingress addresses or networks (comma-separated IPs/CIDRs; `*` refused); unset = trust no forwarding header. Run uvicorn with `--no-proxy-headers` (the image does) |
| `LATTICE_FAKE_GENERATION_DELAY_SECONDS` | config | test double only: refused (must be 0) in production; the verification overlay uses it for the long-Ask proof |

### 5.2 worker (`WorkerSettings`)

| Variable | Class | Production value / rule |
| -------- | ----- | ----------------------- |
| `LATTICE_ENV` | config | `production` |
| `LATTICE_WORKER_DATABASE_URL` | secret | `lattice_worker` credential; the worker refuses an unsafe role |
| `LATTICE_SYNC_EMBEDDING` | config | the provider the corpus was embedded with (production refuses `fake`) |
| `LATTICE_WORKER_POLL_SECONDS` / `_LEASE_SECONDS` | config | 2 / 300 |
| `LATTICE_WORKER_HEARTBEAT_FILE` | deployment | **P11.1:** e.g. `/tmp/lattice-worker-heartbeat` (a writable tmpfs); the container health check reads its age (§10) |
| `LATTICE_DB_STATEMENT_TIMEOUT_MS` / `_CONNECT_TIMEOUT_SECONDS` | config | 300000 / 5 |
| `LATTICE_TRACE_RETENTION_DAYS` | config | **P11.4:** 90 (minimum 90; the database floor, §13) |
| `LATTICE_METRICS_ADDRESS` | deployment | `0.0.0.0:9465` private, **required in production** (P11.6); scraped as job `lattice-worker`; carries the heartbeat gauge `lattice smoke` reads |
| provider keys / `LATTICE_PROVIDER_SECRET_*`, model settings | secret / config | as the api, only for the embedding provider the worker uses |

### 5.3 migrate and operator commands (`MigrationSettings`)

| Variable | Class | Rule |
| -------- | ----- | ---- |
| `LATTICE_MIGRATION_DATABASE_URL` | secret | `lattice_owner` credential; available **only** to the migration job and operators, never to api, worker or web. **Enforced (P11.2):** a production api or worker refuses to start when `LATTICE_MIGRATION_DATABASE_URL`, `LATTICE_OWNER_PASSWORD` or `POSTGRES_PASSWORD` is in its environment (`lattice.config.owner_credential_problem`; names only, never values); `lattice migrate` refuses a credential that does not own the schema, a superuser and a BYPASSRLS role |
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

**Validating a configuration without starting anything (P11.2):** `lattice check-config
api|worker|migrate` loads exactly what the process would load from its environment,
applies every production guard (and, for api and worker, the owner-credential refusal)
and connects nowhere; `deploy/deploy.sh` runs it inside the new image with each
service's real environment before changing anything. `lattice version [--json]` prints
the build's revision, version and schema revision (no database).

### 5.5 bootstrap (once, by the platform's administrative role)

`LATTICE_OWNER_PASSWORD`, `LATTICE_APP_PASSWORD`, `LATTICE_WORKER_PASSWORD` (secrets) feed
`infra/postgres/bootstrap_production.sql` (managed PostgreSQL, P11.2) or
`infra/postgres/bootstrap.sql` (development and CI, superuser); see §7.

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
| `LATTICE_OIDC_SECRET_<NAME>` (OIDC client secret) | organization's IdP administrator + operator | api (token exchange) | IdP-driven (runbook §9): same reference with a new value — the provider keeps both secrets during the switch, rolling api restart, then the old one is removed at the provider; or a new reference — the variable into every api instance first (rolling restart), then `lattice sso update --client-secret-ref` (P11.3), then the old variable removed. No session impact | yes, for a new value | no: the database holds only the reference `env:…` |
| SCIM bearer tokens | operator (`lattice scim token create`) | the organization's IdP | create new → switch the IdP → `lattice scim token revoke` | no | hash only (SHA-256) |
| `LATTICE_PROVIDER_SECRET_<NAME>` (BYOK) | organization administrator via operator | api, worker | add new variable → rolling restart → `lattice provider credential set` (revokes the old reference) → remove old variable | yes, to add a variable | no: reference only |
| `LATTICE_CLIENT_KEY_SECRET` (P11.4) | operator | api | replace + rolling restart (resets pre-auth counters; during the roll the two key versions count separately, so a client may get up to twice the limit for a minute) | yes | no: the database holds only HMAC outputs |
| Session tokens | — (generated) | browsers | rotated at every sign-in | no | hash only |
| OIDC login state (state, nonce, PKCE verifier) | — (generated) | api | ten-minute single-use attempts | no | yes, transiently (`oidc_login_attempts`, deleted by retention, §11) |
| GHCR publishing | repository | the CI `images` job (`GITHUB_TOKEN`, `packages: write` on that job only; built P11.2) | automatic per run | — | — |
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

**Bootstraps (P11.2).** `infra/postgres/bootstrap.sql` (development, CI; a superuser,
also creates `lattice_test`) and **`infra/postgres/bootstrap_production.sql`** (managed
PostgreSQL; shipped in the backend image under `/opt/lattice/app/infra/postgres/`), run
with `psql` by the platform's administrative role, idempotent (re-run to rotate the three
passwords). How each step of the superuser bootstrap was made to work without a
superuser:

| Bootstrap step | Managed-PostgreSQL concern | As built (production variant) |
| -------------- | -------------------------- | ------------------------------ |
| `CREATE ROLE … LOGIN PASSWORD`, `CREATE ROLE lattice_auth NOLOGIN` | needs `CREATEROLE` | kept; the creator receives ADMIN OPTION on each role (PostgreSQL 16) |
| `ALTER ROLE … NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION` | PostgreSQL 16 lets only a role holding an attribute change it | **dropped**: `CREATE ROLE`'s defaults are already safe; instead a final check **fails the bootstrap** if any Lattice role has SUPERUSER, BYPASSRLS, CREATEROLE, CREATEDB or REPLICATION, if `lattice_auth` can log in, if a runtime role is a member of any role, or if `citext`/`vector` ≥ 0.8 are missing |
| `GRANT lattice_auth TO lattice_owner WITH INHERIT FALSE, SET TRUE` | needs ADMIN OPTION on `lattice_auth` | works (the creator holds it) |
| `CREATE DATABASE lattice OWNER lattice_owner` | needs CREATEDB and SET ROLE to `lattice_owner`; afterwards only the owner can connect (PUBLIC's CONNECT is revoked) | the administrator grants itself `lattice_owner` (INHERIT, SET) for the bootstrap and **revokes it at the end** |
| `CREATE DATABASE lattice_test …` | no place in production | not in the production variant |
| `CREATE EXTENSION citext` | trusted extension | created **as `lattice_owner`** (the database owner may create trusted extensions) |
| `CREATE EXTENSION vector` | **pgvector is not a trusted extension**: a non-superuser cannot create it; managed platforms provide it through their own extension mechanism for the admin role | created as the administrator with `IF NOT EXISTS`; where the platform needs its own step, enable it first (the statement then skips). The reference stack and CI emulate the platform by installing `vector` in `template1` as the container's superuser |
| `ALTER SCHEMA public OWNER TO lattice_owner`, `REVOKE CREATE ON SCHEMA public FROM PUBLIC`, grants | owner-level statements | made as `lattice_owner` (`SET ROLE`) |

**Verified locally (P11.2), not on a provider.** The reference stack's PostgreSQL creates
only a `platform_admin` role with LOGIN, CREATEROLE and CREATEDB — deliberately weaker
than DigitalOcean's documented `doadmin` (which also has BYPASSRLS and REPLICATION) —
and the `bootstrap` job runs the production variant as that role; CI does the same on
every push (a `ci_platform_admin` role) before the superuser bootstrap. The result passes
`lattice check-deployment` with the same privilege snapshot as a superuser-bootstrapped
database (§8). **Deferred (D-22 amended):** running it against a real managed provider's
administrative role.

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

A plan with ~22 usable backend connections (the smallest DigitalOcean plan) fits only
with `LATTICE_DB_POOL_SIZE=3` and leaves almost no operational headroom, so **staging uses
the 2 GiB plan (47 usable connections; D-22 as accepted for P11.1)**, which fits the
defaults with room for operators and drills; a plan with ~100 (Render) also fits. The
reference stack runs `LATTICE_DB_POOL_SIZE=3` per API instance on its own PostgreSQL
(default `max_connections` 100). No pooler is added (Phase 14 revisits).
`lattice check-deployment` computes it (P11.2): `max_connections` minus
`superuser_reserved_connections` and `reserved_connections` must cover `api instances × 2
× pool + worker × 4 + 3 operators + 10 headroom` (`--api-instances`, `--api-pool-size`,
`--worker-instances`).

**`lattice check-deployment` (P11.2)**, as the schema owner, read-only, catalogs only:
schema revision exact and no Alembic autogenerate difference; role attributes and
memberships; forced RLS on every table; database owner, no PUBLIC CONNECT/TEMPORARY, no
migration fence left behind, no migration holding the lock; the **privilege snapshot**
(policies, table and column grants, function owners/SECURITY DEFINER/settings/EXECUTE,
triggers, schema ACL, memberships among the Lattice roles — no OIDs, grantors or database
names) equal to `backend/src/lattice/db/privilege_snapshot.json`, which a test regenerates
from a freshly migrated database and compares (`make privilege-snapshot` after a
migration that changes privileges); and the connection budget. Exit 1 on any failure.

## 9. Deployment

**As built (P11.2): `deploy/deploy.sh`** drives this for the reference stack (any
platform needs the same steps; the script is the executable form of the procedure).
Every image reference is resolved to its **content-addressed image ID** before anything
changes; the ID is what runs (`deploy/state/images.env`, read by Compose and the Make
targets) and what the log records (`deploy/state/deploy.log`; the previous release in
`previous.env`). A registry deployment uses the digest the `images` job recorded.
Pre-flight, before anything changes: the Compose configuration is complete (a missing
secret fails closed), the backend image's OCI revision label equals the revision baked
into it (`lattice version`), backend and web come from the same commit, and equal
`--expect-revision` when given; `lattice check-config` passes for api, worker and migrate
inside the new image with each service's real environment; `lattice migrate --status`
(as the new build sees the database) decides the flow. Only one deployment runs at a
time (`deploy/state/lock`).

### 9.1 Release without a schema change (rolling, no downtime)

1. Pre-flight (above): the database revision equals the new build's `SCHEMA_REVISION`.
2. Replace `api1`, wait for readiness (Compose health = `/v1/health/ready`), wait for the
   ingress's next health check (7 s), then `api2` the same way; old instances get SIGTERM
   and drain (≥ 160 s grace).
3. Replace the worker (the old one finishes its current job; a killed job is recovered by
   its lease).
4. Replace `web1`, then `web2`.
5. `lattice smoke` with `--expect-revision`: the public path, both API instances, the
   worker, and `lattice_build_info{revision}` of every api and worker process. A failed
   smoke fails the deployment (the release is running; `deploy/deploy.sh rollback`).

If an instance does not become ready, the script restores **that instance's previous
image** and stops: the other instance never stopped serving.

Rollback: `deploy/deploy.sh rollback` — the previous release by image ID, the same
rolling way (it is a release whose schema equals the database's).

### 9.2 Release with a schema change (D-23, stop-the-world)

`deploy/deploy.sh release … --allow-migration` (without the flag a migrating release is
refused: the operator accepts the downtime explicitly).

| Step | Action | If it fails |
| ---- | ------ | ----------- |
| 1 | Pre-flight (§9): identity, revision, configuration, the database `pending` for the new build; **record the pre-deploy time** (PITR target once P11.5 exists) | stop; nothing changed |
| 2 | Ingress: `/api/*` answers 503 + `Retry-After` (`deploy/maintenance/api`; web keeps serving) | — |
| 3 | Stop **both** api instances | — |
| 4 | Stop the worker (it finishes or abandons its job to the lease) | — |
| 5 | `lattice migrate` (new image, `lattice_owner`): owner check; **migration lock** (`pg_try_advisory_lock`, waited ≤ `--lock-wait-seconds`, default 60: a concurrent migration refuses); **fence** (`REVOKE CONNECT` from `lattice_app`/`lattice_worker`, so nothing can connect mid-migration); **pre-flight**: no `lattice_app`/`lattice_worker` session on the database (refused otherwise — never migrated around); Alembic to head on the same connection with `lock_timeout` 5 s and `statement_timeout` 10 min, one transaction; **verify**: revision = the build's and no model/schema difference; fence lifted and lock released in every case | refused or failed → the transaction rolled back, the schema is the old revision: the script restarts the **old** build, lifts maintenance and fails. A migration killed hard leaves the fence (application roles cannot connect: fail closed) and `check-deployment` reports it; `lattice migrate --release-fence` (or a new `lattice migrate`) lifts it |
| 6 | Start the new worker and api instances; wait for readiness | they refuse to start on any role/schema/config problem: maintenance stays on — roll forward (the old build cannot serve the new schema) |
| 7 | Wait for the ingress health check, lift maintenance | — |
| 8 | Roll `web1`, `web2`; smoke with `--expect-revision` | as §9.1 |

**Measured (reference stack, M1 8 GB, P11.2):** API unavailable 19 s for a migrating
release whose migration takes 2 s (target under two minutes), 16 s for a downgrade; web
pages served throughout. Because the old build refuses the new schema and the new build
the old one, no step can leave an old instance serving a migrated schema.

**Rollback after a successful migration.** Roll forward with a fix by default.
`deploy/deploy.sh rollback --allow-downgrade` crosses a migration only if **every**
revision being undone declares `Reversible: yes` in its header: the running build (which
knows the newer revisions) checks first (`lattice migrate --downgrade-to <rev> --check`,
nothing is stopped if refused), then runs the same stop-the-world sequence with
`lattice migrate --downgrade-to <rev>` (lock, fence, pre-flight) and starts the previous
release. Every migration states `Reversible: yes|no (…)` and `Locks: …` in its header
(enforced by `tests/test_migration_safety.py`); of 0001–0013 only 0002, 0010 and 0012
have a data-safe downgrade, and 0013 does not, so every release on 0013 rolls forward.
Otherwise the last resort is a restore to the step-1 time (P11.5), which loses every
write after it; state that window before choosing it. Never edit `alembic_version`.

## 10. Health, startup and shutdown

| Contract | As built (P11.1 and earlier) | Reference-stack health check |
| -------- | ---------------------------- | ---------------------------- |
| Startup (api, worker) | Refuse to start on invalid configuration, unsafe role, schema at another revision, unreachable database: exit 1 with a one-line reason; the platform restarts with back-off. On a successful start each logs one `startup` event (process, revision, version, schema revision) | `restart: unless-stopped` |
| Liveness (api) | `GET /v1/health/live` — no dependency | — |
| Readiness (api) | `GET /v1/health/ready` — database answers and schema revision equals the build's; 503 + `Retry-After` otherwise; states only | `GET /v1/health/ready` every 10 s (Python's `urllib`, no extra tool in the image); Caddy also gates traffic on it |
| Liveness (worker, **P11.1**) | A heartbeat renewed at every loop iteration (every `LATTICE_WORKER_POLL_SECONDS`, also while the database is unreachable: the loop is alive and a restart would not help) and at every lease renewal while a job runs (every lease/3, 100 s by default): the gauge `lattice_worker_heartbeat_timestamp_seconds` and, with `LATTICE_WORKER_HEARTBEAT_FILE`, that file's modification time. A dead, frozen or event-loop-blocked worker stops renewing it. Carries a timestamp only | the heartbeat file is younger than 300 s (> the 100 s renewal interval during a job); `lattice smoke --worker-metrics` reads the gauge |
| Web | The landing page (`GET /`) — public, no API call | `GET /` every 10 s; Caddy gates traffic on it too |
| PostgreSQL | — | `pg_isready` |
| Ingress | — | Caddy's admin endpoint (`127.0.0.1:2019`, inside its container only) |
| Graceful api shutdown (**P11.1**) | SIGTERM: uvicorn stops accepting connections, waits up to 150 s (`--timeout-graceful-shutdown 150`) for in-flight requests (an Ask runs up to the 120 s generation deadline), then runs the application shutdown (database pool disposed) | `stop_grace_period: 160s` — **required: a platform stop grace of at least 160 s** |
| Worker shutdown | SIGTERM/SIGINT: claim nothing new, finish the current job, close the database pool, exit. A job still running when the grace ends is killed; its lease (`LATTICE_WORKER_LEASE_SECONDS`, 300 s, renewed every 100 s) expires and another poll retries it (3 attempts, then `job_abandoned`); a sync run it had opened is recorded `aborted` by the next sync of that source | `stop_grace_period: 120s` (longer if syncs are long; a shorter grace only delays the job by one lease) |
| Database failure | api: 503 `service_unavailable` + `Retry-After: 5` within the connect timeout — connection refused or lost, the host name not resolving or no route to it (**P11.7**: a stopped database service whose name no longer resolved answered 500 before), a pool wait, a cancelled statement; readiness fails, liveness passes; recovers without restart. worker: keeps polling with back-off | — (P11.7 drill: runbook §8) |
| Schema mismatch | api readiness 503 (`check="schema_revision"` metric); a starting api/worker exits 1 | — (alert: P11.6) |

Signals: the reference stack runs every process under Docker's `init` (tini), which
forwards SIGTERM; uvicorn and `lattice worker` install their own handlers. Platforms that
start the container's command as PID 1 are also safe for these two processes.

## 11. Backups, PITR and restore

**Requirement (provider-independent).** PostgreSQL with continuous WAL archiving and
point-in-time recovery, automated daily base backups, encryption at rest, restore into a
**new** instance in an isolated network, and a documented recovery window. A production
platform without PITR does not meet this contract.

**Restore verification (built P11.5).** Every restored copy must pass, before it serves:
`lattice verify-restore --manifest <backup-time manifest>` as the schema owner (every
`check-deployment` check — exact schema, safe roles, forced RLS, database access, the
privilege snapshot including triggers — plus audit integrity: the append-only and
access-audit triggers present **and enabled**, and per-organization row counts of every
tenant table and the global tables equal to the manifest taken at the recovery point);
`lattice check-db-role` as `lattice_app`; `lattice worker --once` as `lattice_worker`;
an api instance reaching readiness; and a scan of the backup, the WAL archive and the
restored copy for every configured secret value (expected: none — the database holds
SCRAM verifiers, hashes and `env:` references only, §6). `lattice verify-restore
--write-manifest` takes a manifest (counts and organization slugs only, no content).

**Local reference stack (built P11.5; `deploy/backup.sh`):** `archive_mode=on`,
`archive_command` copying each WAL segment to the `pgarchive` volume, `archive_timeout=60`;
`deploy/backup.sh backup` = `pg_basebackup` (fast checkpoint, WAL streamed) into the
`pgbackups` volume + a manifest; `deploy/backup.sh restore <id> [--target-time T]` stops the
stack (one PostgreSQL at a time), copies the base backup into a new volume, replays the
archive to the latest WAL or to `T`, starts the copy alone on an internal network and
runs every verification above.

| | Documented capability | Measured |
| - | --------------------- | -------- |
| Candidate staging platform (Appendix A: DigitalOcean Managed PostgreSQL; **not provisioned or tested**) | daily full backups, WAL kept for restore "to any point-in-time within the previous seven days"; restore creates a new cluster | **nothing: deferred** (phase-11-plan §21.1) — cloud RPO/RTO and managed PITR remain unverified and are a Phase 15 release gate |
| Local reference stack (P11.5, 2026-10-05; synthetic data, 60 MB) | WAL archive + base backups on the same Docker host | base backup 0.9 s; restore to latest and to a point in time each recovered in **2.4 s**, full verification ≈ 7 s more; data-loss bound for a lost primary: the WAL not yet archived — at most `archive_timeout` (60 s) of commits. **Local durations only, on one laptop, with backups on the same disk: not an RPO/RTO for any deployment** |

**Logical export** (`pg_dump`) is optional: it needs a role that bypasses RLS (a dump as
`lattice_owner` or a runtime role contains no tenant rows); some platform admin roles have
BYPASSRLS (DigitalOcean's `doadmin` does), others do not.

## 12. Rate-limit classes

As built in P11.4 (D-24): **every limit is shared by all API processes through
PostgreSQL**; defaults unchanged.

| Limit | Default | Class | How |
| ----- | ------- | ----- | --- |
| Asks per minute per membership | 10 | **shared/durable** | `rate_limit_counters` (RLS), fixed window, conditional upsert |
| Asks per day per membership | 500 | **shared/durable** | the same |
| Asks in flight per membership | 2 | **shared/durable** | `ask_slots` lease rows (RLS): a request takes a free or expired slot with a conditional upsert in its own short transaction and releases it by lease ID; the lease lasts the generation deadline + 60 s, so a crashed process's slot frees itself; expiry on the database clock |
| Access previews/writes per administrator | 60/min | **shared/durable** | `rate_limit_counters` |
| SCIM requests per token | 600/min | **shared/durable** | `rate_limit_counters` |
| Pre-authentication per client (development sign-in, OIDC start/callback, failed SCIM authentication — refused before the token is checked) | 30/min | **shared/durable** | `pre_auth_rate_limits` (no organization; no login role has a privilege on it), written only by `lattice_pre_auth_hit` (`SECURITY DEFINER`, owner `lattice_auth`, `EXECUTE` for `lattice_app`), keyed by HMAC-SHA-256(`LATTICE_CLIENT_KEY_SECRET`, client) where the client is the IPv4 address or the IPv6 /64, taken only from the trusted ingress (§4) |
| Request body, question length, statement timeout, generation deadline | as configured | per request (stateless) | — |

**Connection-local:** none. **Process-local:** none — only caches (OIDC discovery and
JWKS, ten minutes, refresh at most once a minute) and metrics, neither a security
control. Proven across two engines and two applications in `tests/test_shared_limits.py`
and on the reference stack (runbook §8, P11.4).

## 13. Retention classes

As built in P11.4. The **worker** deletes, hourly, in batches of 500, as `lattice_worker`;
every cutoff is computed from the database's `now()` inside the statement. The database
**floor** is a restrictive row-level policy on the worker's DELETE (migration 0015): a
row younger than it cannot be deleted by the worker even with a direct statement and an
organization bound. The worker reads only identifying and timestamp columns of these
tables, never content.

| Class | Data | Rule (deleted when) | Database floor | Deletes it |
| ----- | ---- | ------------------- | -------------- | ---------- |
| Durable security record | `audit_events` | never by retention (append-only, UPDATE refused by trigger); removed only with its organization (Phase 12) | no DELETE privilege for any runtime role | nobody |
| Identity and configuration tombstones | `directory_identities`, removed memberships, `scim_tokens`, `oidc_connections`, `provider_credentials` history | kept for the organization's life (D-20 terminal removal relies on them) | no privilege | nobody |
| User history (content-bearing) | `query_traces` | created more than `LATTICE_TRACE_RETENTION_DAYS` ago (worker setting, default **90**, minimum 90) | **90 days**: a shorter retention needs a migration and a product decision (per-organization retention is Phase 12) | worker |
| Operational history | `jobs` (finished) | finished more than 30 days ago | 30 days | worker |
| | `source_sync_runs` (+ their `sync_record_failures`, cascade) | started more than **180 days** ago **and** superseded by a newer run of the same source — the latest run of each source is always kept | the same condition | worker, organization by organization |
| Transient security state | `sessions` | expired, or revoked, more than **30 days** ago (decided P11.4: a month of sign-in history for incident review; an ended session authenticates nothing) | the same | worker |
| | `oidc_login_attempts` | expired more than **1 day** ago (decided P11.4; consumed attempts expire ten minutes after creation like the rest) | the same | worker (+ the per-organization cleanup when a sign-in starts) |
| | `rate_limit_counters` | window started more than 2 days ago (longest window: 1 day) | the same | worker |
| | `pre_auth_rate_limits` | window started more than 2 days ago | the same, inside `lattice_pre_auth_retention` (owner `lattice_auth`, `EXECUTE` for `lattice_worker` only) | worker |
| | `ask_slots` | lease expired more than 1 hour ago | the same | worker |
| Logs | stdout JSON | platform log retention; **duration to decide with the platform** (no content in logs, SEC-8) | — | platform |

Safety: each batch is its own transaction, deletes by key and re-checks eligibility, so a
repeated run deletes nothing and two workers never delete a row twice (a batch that met
the other worker's rows may come back short; the next run finishes). A failing task is
counted and logged and does not stop the others. Metrics per task:
`lattice_maintenance_rows_total`, `lattice_maintenance_last_success_timestamp_seconds`,
`lattice_maintenance_failures_total`; one `maintenance.retention` log event per run
(counts only). Tests: `tests/ingestion/test_retention.py`.

## 14. Volumes

| Volume | Needed by | Rule |
| ------ | --------- | ---- |
| Source exports | worker (read-only), operator `lattice sync` (writes the configuration that names the path) | Required while connectors read local exports (all three today). The worker syncs only the path the operator's last `lattice sync` recorded (ADR-013), so operator and worker must see the **same path**. Reference stack: `./data` mounted read-only at `/srv/lattice/data` in `worker` and `ops`. Staging handling: P11.2 |
| Model cache | api and worker, only with `local` embeddings | read-only, filled and verified by `lattice models fetch` |

## 15. Local reference stack (P11.1–P11.7)

`compose.production.yaml` (Compose project `lattice-reference`, separate from the
development `compose.yaml`) runs the production topology on one machine: PostgreSQL,
`api1` and `api2` (one backend image), `worker` (the same image), `web1` and `web2`, and
Caddy as the ingress, on one private network; only Caddy publishes ports, on 127.0.0.1
(8443 HTTPS, 8080 redirect). `bootstrap`, `migrate` and `ops` are one-off services. Every
container except PostgreSQL runs read-only with a `/tmp` tmpfs, all capabilities dropped
(Caddy keeps `NET_BIND_SERVICE`, which its binary's file capability requires),
`no-new-privileges`, no host network, no Docker socket and no privileged mode. Passwords
are generated into the git-ignored `deploy/.env.reference`; each service gets only the
credential it needs (the owner credential reaches `migrate`, `ops` and `bootstrap` only,
the platform administrator's only `postgres` and `bootstrap` — `tests/test_deployment_files.py`).

**Managed-PostgreSQL emulation (P11.2).** On an empty volume PostgreSQL creates only
`platform_admin` (LOGIN, CREATEROLE, CREATEDB; no superuser) and installs `vector` in
`template1` (`deploy/postgres/initdb-platform.sh`) — the stand-in for a provider's
administrative role and extension allow-list. The `bootstrap` job then runs
`bootstrap_production.sql` as `platform_admin`, exactly as on a managed platform.

| Command | What it does |
| ------- | ------------ |
| `make prod-build` | build `lattice-backend:local` and `lattice-web:local` with the git revision |
| `make prod-up` | PostgreSQL → `bootstrap` (platform admin) → `lattice migrate` → api ×2, worker, web ×2, Caddy, waiting for every health check |
| `make prod-smoke` | `lattice smoke` from inside the private network, including every api/worker process's build revision |
| `make prod-check` / `make prod-migrate` | `lattice check-deployment` / `lattice migrate --status` against the reference database |
| `deploy/deploy.sh status \| release \| rollback` | releases and rollbacks by image ID (§9) |
| `make prod-seed VERIFY=1` | the synthetic-only bootstrap: `lattice seed --require-synthetic` (refuses any manifest not declaring `data_origin: synthetic`; seeding is refused in production anyway), syncs with fake embeddings, admin grants |
| `make prod-ps`, `prod-down`, `prod-destroy` | status; stop (volumes kept; `--remove-orphans`, so the overlays' services stop too); stop and delete volumes |
| `VERIFY=1 [FAKE_DELAY=s]` | adds `deploy/compose.verify.yaml`: `LATTICE_ENV=test`, development sign-in, fake providers, a 30 s worker lease — the same images and topology, for proofs that need a signed-in member. Not a production configuration |
| `IDP=1` (P11.3) | adds `deploy/compose.idp.yaml`: the verification OIDC provider (`deploy/verification/idp.py`, run from the backend image with the script mounted; HTTPS from a throwaway CA made by `deploy/verification/make-certs.sh` into the git-ignored `deploy/.verification/`), the API instances trusting that CA alone (`SSL_CERT_FILE`) and holding the connection's secret reference, and each API instance's metrics listener published on 127.0.0.1 (19464, 19465) so the verification browser can see which instance served a step. The stack itself stays in production mode. Not a production configuration |
| `make prod-limits VERIFY=1 IDP=1` (P11.4) | shared in-flight and pre-authentication limits across both API instances, retention in the worker, worker killed and stopped mid-job (`deploy/verification/limits_check.py`; needs `FAKE_DELAY` ≥ 10 and `make prod-seed VERIFY=1 IDP=1`; sets Halden's generation provider to the fake model) |
| `make prod-identity IDP=1` | the identity verification of plan §15.3 (`deploy/verification/identity_check.py`, on the host): seeds Halden (an explicit test-mode operator run), connects it to the verification provider, provisions run-unique personas over SCIM through the ingress, and checks sign-in across both instances, replay, ID-token time boundaries, logout, operator suspension/reinstatement, SCIM deactivation/removal and token rotation, provider outage, SSO disable/enable, client-ID update and both secret rotations, and the clock-skew gauge (runbook §8) |
| `make prod-authz VERIFY=1 IDP=1` (P11.7) | the deployed authorization suite of plan §15.2 (`deploy/verification/authz_check.py`): the route sweep read from the API's own routers (anonymous 401, SCIM without a token 401, one identical 403 for a member on every administrator route, another organization's real identifiers answering exactly like random ones with nothing changed), administrator ≠ content on traces, an Access suspension ending the session on both instances, `check-deployment`, a requested sync per organization, and the `eval retrieval` / `eval answer` security gates as an operator job inside the stack. Grants Veridian's most privileged persona the administrator capability for the sweep (audited) and takes it back |
| `make prod-drills VERIFY=1 IDP=1` (P11.7) | the failure drills of plan §18 (`deploy/verification/drills_check.py`; `FAKE_DELAY=20`): an API instance killed and one stopped during an Ask, the database stopped and started, the generation provider unreachable (a `local` record on a loopback port where nothing listens), the dead process's Ask slot freed at lease expiry |
| `make prod-perf VERIFY=1 IDP=1` (P11.7) | performance sanity through the ingress (`deploy/verification/perf_check.py`, `FAKE_DELAY=0`): p50/p95 of `/api/v1/me`, `/api/v1/sources` and an Ask with the fake model — a local regression signal, not a capacity figure |

It runs the production defaults (`LATTICE_ENV=production`, `LATTICE_QUERY_EMBEDDING=none`,
no development sign-in). It is a verification environment, not a deployment recipe, and
backs up with `deploy/backup.sh` (§11, P11.5). `deploy/drills/` builds deliberately broken or different
releases on top of a real backend image (a relabelled build, a migrating build with a
reversible or irreversible drill revision numbered one above the image's head, a failing migration, an API that never
becomes ready, a production API that serves `/docs`, an invalid baked-in setting); they
exist only for the drills of runbook §8, CI never builds them and no release contains
them.

## 16. Monitoring and alerting (P11.6)

**Scrape (required).** `LATTICE_METRICS_ADDRESS` is **required in production** for the api
and the worker (the processes refuse to start without it, P11.6) and is bound to the
private network only. The platform's Prometheus-compatible scraper collects every api
instance as job `lattice-api` and the worker as job `lattice-worker`, and runs an external
black-box probe of `https://<public origin>/api/v1/health/ready` as job `lattice-probe`
(`probe_success`, `probe_ssl_earliest_cert_expiry`). Routing (pager, tickets) is the
platform's Alertmanager.

**Rules.** `deploy/observability/alerts.yml` (19 rules; runbook §10 has a section per
alert); `make alerts-test` runs `promtool check rules` and the rule unit tests in
`alerts.test.yml` (a firing and a quiet case for every alert) from a pinned Prometheus
image, also in CI. Storage thresholds are absolute: set them per deployment.

**Platform signals the contract also requires** (not Lattice metrics): container up and
restarts, readiness failures, database CPU, connections and storage, the platform's
backup and PITR status, and log collection (no content in logs). The local reference
stack has no Prometheus: its proofs are the rule tests and the drills of runbook §8.

---

## Appendix A — Staging provider selection (D-22)

> **Status (2026-10-05): DigitalOcean is the selected candidate for future staging
> validation; not provisioned or tested.** Nothing below has been created or verified;
> every capability is the provider's documentation, not an observation. Prices were
> retrieved 2026-10-03 and must be re-checked when a budget exists.

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
| Connection limits | S: 100 below 8 GB RAM | U (PgBouncer in front by default) | S | **S** on the 2 GiB plan (47 usable; accepted); **L** at 1 GiB (22 usable, too tight) |
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
| Cost if left running (approx.) | **≈ $42/month**: 5 Starter instances (Caddy, api ×2, web, worker) × $7 + Postgres Basic-256mb $6 + storage $0.30/GB *(instance and Postgres prices from a third-party July 2026 summary; storage price from Render's own article)* | **≈ $59/month**: MPG Basic $38 + 5 × shared-cpu-1x 512 MB $3.69 + IPv4 $2 + storage $0.28/GB | **≈ $50–70/month**: load balancer ≈ $18 + Cloud SQL `db-f1-micro` ≈ $9–13 *(third-party)* + storage/backups + api min-instances and worker pool billed continuously | **≈ $42/month**: Droplet 2 GiB $12 + Managed PostgreSQL **2 GiB $30.45** (30 GiB storage included; the 1 GiB plan, $15.15, was rejected for connection headroom) |
| Phase 11 window (≈ 3 deployment windows totalling ~7 days, database kept for ~1 month so PITR history survives) | ≈ $12–15 | ≈ $45 (MPG minimum dominates) | ≈ $30–40 | **≈ $34–36** (2 GiB database ~$30.45 for the month, Droplet ~$3, a restored cluster for a few hours ≈ $1) |
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
- **Connections:** the 1 GiB plan (22 usable) is too tight for two API instances plus
  operators; **the 2 GiB plan (47 usable, $30.45/month) is the accepted staging choice**
  (product owner, 2026-10-03), doubling the database cost.
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

**Estimated Phase 11 staging cost: ≈ $34–36** with the accepted 2 GiB database (database
kept ~1 month, Droplet only during the P11.2, P11.5 and P11.7 windows); ≈ $42/month if
everything is left running. Nothing exists yet: the account and resources are created in
P11.2.

**You would need to create or configure manually** (Claude creates nothing):
1. A DigitalOcean account and payment method; a project for Lattice staging.
2. A Managed PostgreSQL 16 cluster (**2 GiB**, one node, same region as the Droplet),
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
  [Managed database plans (2 GiB $30.45, retrieved 2026-10-03)](https://www.digitalocean.com/pricing/managed-databases),
  [Modify user privileges (`doadmin` attributes)](https://docs.digitalocean.com/products/databases/postgresql/how-to/modify-user-privileges/),
  [Droplet pricing](https://www.digitalocean.com/pricing/droplets),
  [App Platform limits](https://docs.digitalocean.com/products/app-platform/details/limits/),
  [App Platform pricing](https://docs.digitalocean.com/products/app-platform/details/pricing/),
  [App Platform 30 s default timeout](https://docs.digitalocean.com/support/my-php-app-is-timing-out-and-throwing-5xx-errors/),
  [App spec](https://docs.digitalocean.com/products/app-platform/reference/app-spec/),
  [community: 100 s hard limit](https://www.digitalocean.com/community/questions/app-platform-request-timeout).
