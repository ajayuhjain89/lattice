# Phase 11 — Production Deployment, Identity Operations & Reliability: plan

Status: **Approved (2026-10-03) with decisions D-22 to D-27 and the product owner's
amendments (§21). P11.0 approved (4990064). P11.1 approved (b07ac8d). D-22 amended
2026-10-05 (§21.1): zero paid infrastructure — Phase 11 closes on full local
production-shaped verification; real external staging validation is deferred and gates
Phase 15 (v1 GA). P11.2–P11.7 authorized to run in sequence without a review stop per
checkpoint. P11.2 delivered as ae03445 + test fix fd0d1bc (§25.3). P11.3 delivered (§25.4).** Branch `phase/11-production-operations` from `main` 27da421.

Scope and roadmap position: `implementation-plan.md` §4 "Later phases", row 11. Phase 12
owns customer-facing onboarding (organization, SSO, SCIM, sources/integrations,
providers/BYOK, zero-to-first-question), Phase 13 retrieval and answer quality, Phase 14
large-scale, load and adversarial hardening, Phase 15 GA/v1 closure. Nothing from them is
pulled in. The disposition of the roadmap items displaced on 2026-10-03 is in §27.

---

## 1. Objective

Prove that what Phases 1–10 built operates correctly as a real production system:
packaged as immutable images, deployed in a documented order, migrated safely, backed
up and **restored**, observable, alerting on things an operator can act on, and
recovering from the failures it will meet — with identity, sessions, authorization,
rate limits, jobs, secrets and retention all behaving correctly when there is more than
one process and real TLS in front.

The deployment target stays deliberately simple: one region, production-shaped, the
existing modular monolith (one backend image running as API, worker and migration job;
one web image), PostgreSQL as the only durable store.

## 2. Non-goals

- Coupling the application to a hosting provider. The contract (§6) is provider-independent;
  the staging target (D-22) is chosen in P11.0 by comparison and stays replaceable.
- Kubernetes, Redis, Kafka, Celery, RabbitMQ, Elasticsearch, a secrets platform, a new
  observability platform, service decomposition, an unnecessary connection pooler. A later
  checkpoint that finds one necessary stops for approval first.
- Compatibility-floor / expand-contract deployment machinery (D-23), unless inspection
  proves it is required for correctness.
- Multi-region, active-active, HA beyond what a managed PostgreSQL offers (Phase 14/15).
- Load, scale and adversarial testing (Phase 14). Phase 11 runs operability drills.
- Any authentication redesign: no break-glass login, no SAML, no IdP-initiated or
  back-channel logout, no JIT provisioning, no e-mail linking. OIDC + SCIM stay the
  identity model (D-1, D-20); the authorization model is unchanged.
- Customer-facing UX (Phase 12): SSO/SCIM setup screens, organization creation, account
  administration, organization-configurable retention, provider setup.
- Destructive organization deletion/purge (D-26: Phase 12).
- Retrieval or answer quality (Phase 13); R@10 and the real-model answer evaluation stay
  open as recorded in evaluation-plan §12 and ADR-012.

## 3. Current-state inventory (inspected 2026-10-03)

### 3.1 What already exists and is production-relevant

| Area | As built | Where |
| ---- | -------- | ----- |
| Processes | API (`uvicorn … --factory`), worker (`lattice worker`), web (`next start`), migrations (`alembic upgrade head` as `lattice_owner`) | runbook §7.1 |
| Startup guards | API and worker refuse: invalid config, unsafe role (superuser, BYPASSRLS, member of such, table owner; API also any content-write privilege), schema ≠ `SCHEMA_REVISION` (exact match). Production refuses dev login, insecure cookies, non-https `LATTICE_PUBLIC_URL`, fake embeddings | `config.py`, `db/role_check.py`, `db/schema.py` |
| Health | `/v1/health/live` (no dependency), `/v1/health/ready` (DB + exact schema revision), states only | `api/routes/health.py` |
| Metrics | Separate Prometheus listener (`LATTICE_METRICS_ADDRESS`) in API and worker, closed label sets; HTTP, Ask, generation, retrieval, claims, auth, SSO, SCIM, rate limits, sync, jobs, maintenance, dependency failures, security invariant | `metrics.py`, runbook §7.3 |
| Logs | JSON lines to stdout, redacted, no content | `log.py` |
| DB roles | `lattice_owner` (migrations/operators), `lattice_app` (API), `lattice_worker` (worker), `lattice_auth` (NOLOGIN, owns 4 pre-auth lookups); forced RLS everywhere | `infra/postgres/bootstrap.sql`, migrations 0001–0013 |
| Audit | `audit_events` append-only (UPDATE trigger), no DELETE for runtime roles, access writes by `lattice_app` cannot commit without an event (deferred triggers) | 0008, 0012, 0013 |
| Sessions | Server-side, SHA-256 token, idle 12 h / absolute 7 d, every request re-checks org/user/membership `active`, logout revokes, rotation on sign-in, `__Host-` + Secure in production | `identity/sessions.py` |
| OIDC | PKCE, state, nonce, browser binding; attempts in PostgreSQL (single use, 10 min); 60 s leeway; discovery/JWKS cached per process 10 min | `identity/oidc.py` |
| SCIM | `/scim/v2/Users`, hashed tokens, least access, suspension/removal revoke sessions in the transaction, removal terminal | `provisioning/` |
| Rate limits | PostgreSQL-shared: Ask/min, Ask/day, access writes, SCIM. **Per process:** Ask in flight, pre-auth per client | `ratelimit/__init__.py` |
| Worker | Jobs table, `SKIP LOCKED`, lease 300 s renewed every 100 s, 3 attempts, back-off, abandoned → `job_abandoned`, SIGTERM finishes the current job; hourly deletion of finished jobs > 30 days (DB policy floor) | `jobs/worker.py`, 0011 |
| Kill switches | `LATTICE_GENERATION_ENABLED`, `LATTICE_QUERY_EMBEDDING=none`, `lattice provider disable` | runbook §7.4 |
| Secrets | Env only; provider credentials and OIDC client secrets stored as `env:` *references* in narrow namespaces; SCIM and session tokens hashed | `providers/secrets.py`, security-model §5 |
| CI | One workflow (`on: push`, `pull_request`): backend, web, dependency-audit | `.github/workflows/ci.yml` |
| Local infra | `compose.yaml`: PostgreSQL only; API, worker and web run on the host | `compose.yaml`, Makefile |

### 3.2 What does not exist

No container images, no production compose/manifests, no reverse-proxy configuration,
no image registry or release identification, no deployment or rollback procedure beyond
runbook §7.6, no backup/PITR tooling or restore verification, no retention beyond jobs,
no alert rules, no deployed-environment verification, no multi-process tests, no staging
environment.

### 3.3 Findings accepted as Phase 11 work

The product owner accepted all of these (2026-10-03). They are resolved inside the
existing architecture, not used to broaden it.

| # | Finding | Evidence | Resolved in |
| - | ------- | -------- | ----------- |
| F1 | Next.js `/api` rewrite proxies with a default 30 s `proxyTimeout`; an Ask may legitimately run the 120 s generation deadline plus retrieval | `apps/web/next.config.ts`; `next/dist/server/lib/router-utils/proxy-request.js` | P11.1 (ingress routes `/api/*` to the API; dev rewrite gets an explicit timeout) |
| F2 | Through the rewrite, the API's peer is the Next.js server, so every browser is one "client" | `api/deps.py:client_address` | P11.1 (topology), P11.3 (tests) |
| F3 | Forwarded-address trust is wrong: Next.js passes an incoming `X-Forwarded-For` through unchanged (`??=`), and the runbook's `--forwarded-allow-ips=<proxy>` names the wrong hop | `next/dist/server/base-server.js`, runbook §7.4 | P11.1, P11.3 (§10.3) |
| F4 | Sessions do not record the SSO connection that issued them | `sessions` (0001) | P11.3 (D-25) |
| F5 | SSO lifecycle is incomplete: `sso disable` is one-way; no in-place update of client ID or secret reference; `sso add` creates a new connection ID and directory identities are keyed by connection, so re-provisioning collides on per-organization e-mail (409) and the population is orphaned | `identity/admin.py`, `cli_identity.py`, 0013 | P11.3 (§9.2) |
| F6 | Ask in-flight limit is process-local | `ConcurrencyLimiter` | P11.4 (D-24) |
| F7 | Pre-auth per-client limit is process-local | `ClientLimiter` | P11.4 (D-24 amended) |
| F8 | Retention documented but not enforced (`sessions`, `oidc_login_attempts` — cleaned only per organization at its next start —, `rate_limit_counters`, `query_traces`, `source_sync_runs`/`sync_record_failures`) | migrations 0001, 0005, 0008, 0013 | P11.4 (§12) |
| F9 | Worker has no production liveness signal (no HTTP; metrics listener optional) | `cli_worker.py` | P11.1 |
| F10 | `bootstrap.sql` assumes a real superuser and always creates `lattice_test` | `infra/postgres/bootstrap.sql` | P11.2 |
| F11 | `next start` binds `127.0.0.1`; no standalone output | `apps/web/package.json` | P11.1 |
| F12 | Incident procedures use unaudited owner SQL (urgent suspension, re-hire after removal, organization stop) | runbook §2, §9 | P11.3 (§9.3) |
| F13 | Backup/restore assumes a superuser / BYPASSRLS role that a managed PostgreSQL may not provide | runbook §7.7 | P11.0 (selection), P11.5 |
| F14 | No release identification (public endpoints rightly expose none; nothing internal does either) | `api/app.py` | P11.1 / P11.2 (D-27) |
| F15 | No metric separates SCIM suspensions/removals from other patches, so provisioning bursts cannot be alerted on precisely | `SCIM_OPERATIONS_SET` | P11.6 |

## 4. Production architecture (approved)

Unchanged shape (ADR-001, ADR-002, ADR-013): one backend codebase, one web app, one
PostgreSQL. Production adds packaging, the ingress the architecture already assumed
(architecture §2: "the reverse proxy in deployment"), and operator procedures.

```
                    Internet (HTTPS only)
                           │
              ┌────────────▼──────────────┐
              │ Ingress / TLS reverse proxy │  platform ingress (staging/production)
              │  /api/*  → api  (strip /api)│  Caddy (local reference stack only)
              │  /*      → web              │  sets X-Forwarded-For/-Proto; read timeout ≥ 150 s
              └──────┬──────────────┬──────┘
          private    │              │
          network    ▼              ▼
              api ×≥2 (backend image)    web ×≥1 (web image)
              uvicorn, lattice_app        node server.js (standalone), no secrets
              :8000 via ingress only      :3000
              :9464 metrics (internal)
                     │
   worker ×1 (backend image, `lattice worker`, lattice_worker, :9465 metrics internal)
   migrate (backend image, one-off job, lattice_owner)   operator CLI (one-off, lattice_owner)
                     │
              PostgreSQL 16 + pgvector 0.8.1 (managed, PITR, TLS, private)
   Volumes: source exports (read-only for the worker; written by operators)
            model cache (read-only; only if LATTICE_QUERY/SYNC_EMBEDDING=local)
   Outbound only: identity providers (api), model providers (api, worker)
```

The ingress routes `/api/*` **directly to the API**. The web app never calls the API
from the server (every call is browser → same-origin `/api`), so Next.js leaves the API
path entirely in production. The Next.js rewrite remains for development only, with an
explicit `proxyTimeout` above the generation deadline.

## 5. Trust boundaries (production additions to trust-boundaries.md)

| Boundary | Crosses it | Control |
| -------- | ---------- | ------- |
| Internet → ingress | Browsers, IdP redirects, SCIM clients | TLS only; HSTS; only `/api/*` and web paths routed; metrics ports never routed; ingress body limit ≥ the API's 64 KiB cap |
| Ingress → api | Requests carrying `X-Forwarded-For`/`-Proto` | The **only** trusted hop: uvicorn `--proxy-headers --forwarded-allow-ips=<ingress addresses>`. The ingress appends the peer it saw; the API takes the right-most address added by a trusted hop; any value from an untrusted peer (including a client-supplied header and the Next.js server in development) is ignored (§10.3) |
| Private network | api, web, worker, metrics scraper, DB | No public address; DB accepts only these workloads (TLS required) |
| Operator → DB | Owner credential (migration job, operator CLI) | Separate secret, available only to the migration job and operators; never in api/worker/web env; normal incident procedures are audited commands (§9.3), not SQL |
| Secret store → processes | DB passwords, `LATTICE_OIDC_SECRET_*`, `LATTICE_PROVIDER_SECRET_*`, `LATTICE_CLIENT_KEY_SECRET` (new, §10.2) | Injected as env at start; never in images, files in git, logs or backups (§13) |
| api/worker → outside | IdP discovery/JWKS/token, model providers | Existing bounds (timeouts, size caps, https in production); egress policy unchanged |
| Backups | Physical snapshots/WAL | Same sensitivity as the primary; encrypted at rest; restore only into an isolated network (§11) |

## 6. Deployment contract (provider-independent)

Any platform that meets this contract can run Lattice. The local reference stack (§6.4)
and the staging target (D-22) both meet it; nothing in the application knows which.

### 6.1 Images

| Image | Contents | Runs as | Commands |
| ----- | -------- | ------- | -------- |
| `lattice-backend` | Python 3.12 slim (pinned by digest), `uv sync --locked --no-dev --extra local` (ONNX runtime, no model weights), `lattice` package, Alembic, bootstrap SQL | non-root UID, read-only root FS, `/tmp` tmpfs | `api` (uvicorn), `worker`, `migrate`, any `lattice …` operator command |
| `lattice-web` | Node 22 slim (pinned by digest), Next.js `output: "standalone"`, no persona manifests, no dev sign-in | non-root, read-only root FS | `node server.js` on `0.0.0.0:3000` |

- Immutable: deployments reference the image **digest**; tags are `sha-<git sha>` (from
  `main`) and `vX.Y.Z` (release tags); never `latest`. OCI labels: source, revision,
  version, created.
- Release identification at runtime (D-27): `LATTICE_BUILD_REVISION` and
  `LATTICE_BUILD_VERSION` are baked in at build; exposed as
  `lattice_build_info{revision,version,schema_revision}` on the internal metrics listener
  and in one `startup` log line per process; never on public endpoints (Phase 8 rule).
- No secrets, `.env`, synthetic data or test fixtures in images (`.dockerignore` + an
  image-content test).

### 6.2 Configuration

All `LATTICE_*` settings as today (fail-fast), plus the per-process matrix in
`docs/deployment.md` (P11.0). Production profile: `LATTICE_ENV=production`,
`LATTICE_PUBLIC_URL=https://…`, `LATTICE_METRICS_ADDRESS` on the private interface
(required in production for api and worker), `LATTICE_CLIENT_KEY_SECRET` (§10.2), DB pool
sizing per §11.4, trusted ingress addresses. Web: `LATTICE_WEB_DEV_SIGN_IN` unset (the API
refuses dev login in production regardless).

### 6.3 Deployment order, health and shutdown (D-23)

The exact schema-revision guard stays. Because a running instance of the old build
would otherwise serve requests against a newer schema until its readiness probe next
runs, **a release that contains a migration is deployed stop-the-world**:

| Release kind | Order |
| ------------ | ----- |
| **With a migration** | 1. pre-flight (§7) → 2. stop routing API traffic (ingress maintenance response for `/api/*`) and stop **all** api and worker instances → 3. `migrate` job; its pre-flight refuses to run while any `lattice-api` / `lattice-worker` connection is open (application name carries the build's schema revision; verified feasible under the managed-PostgreSQL privilege model in P11.2) → 4. start the new api and worker → 5. readiness passes → 6. restore routing → 7. roll web → 8. smoke |
| **Without a migration** | rolling: new api instances (readiness-gated) replace old ones, then worker, then web; zero downtime |

The accepted cost is a short unavailable interval (target: under two minutes, measured
in P11.2) for migrating releases. Readiness: `/v1/health/ready`; liveness:
`/v1/health/live`. Worker liveness: a heartbeat file touched every poll
(`/tmp/lattice-worker-heartbeat`, checked by the platform), plus its metrics.
Graceful shutdown: api `--timeout-graceful-shutdown 150` and a stop grace ≥ 160 s; worker
SIGTERM finishes the current job, and a killed job is recovered by its lease (≤ 300 s) and
recorded `aborted` by the next sync (existing). Restart policy: always, with back-off;
startup guards make a wrong order fail closed.

### 6.4 Local reference stack (primary repeatable verification environment)

`deploy/`: `compose.production.yaml` (PostgreSQL with WAL archiving, `migrate`, **api ×2**,
worker, web, Caddy with an internal CA), `Caddyfile`, and a `test-idp` service in the
verification profile only (§15). Make targets `prod-up`, `prod-smoke`, `prod-down`. Two
API instances are always present, so every multi-process behaviour (limits, OIDC across
instances, revocation) is exercised. Sized for the reference machine (M1 8 GB, Docker
3 GB): one PostgreSQL at a time, the development database stopped while it runs, no test
suite in parallel.

### 6.5 Staging deployment (D-22, amended 2026-10-05)

**Deferred.** The original plan was one real cloud staging deployment of the same images
under the same contract before P11.7 closed. On 2026-10-05 the product owner stated that
no budget for paid infrastructure exists (§21.1): no cloud resource is created in Phase 11,
and every step below that names staging runs on the local reference stack only. The
external validation list in §21.1 stays open and is a **release gate for Phase 15**. The
DigitalOcean option of deployment.md Appendix A is the *selected candidate for future
staging validation; not provisioned or tested*.

## 7. CI/CD sequence (D-27)

No second CI system, no new workflow file, no duplicate test runs.

| Trigger | Jobs |
| ------- | ---- |
| Every push (as today) | backend, web, dependency-audit — unchanged, plus three cheap checks inside existing jobs: migration headers (§8), privilege snapshot compared with the committed one (§15.1), alert-rule tests (`promtool test rules`, pinned image) |
| Push to `main`, tag `v*`, manual dispatch | + `images`: `needs: [backend, web, dependency-audit]` in the **same** run (the tests that gate it are the ones already running; nothing is re-run); builds both images (buildx, GHA cache), starts the reference stack headless and runs `lattice smoke` against it (container-level checks the test jobs cannot do), pushes to GHCR (`packages: write` on this job only), writes digests and tags to the job summary |
| Deploy | Scripted operator procedure (§6.3), rehearsed on the local reference stack from P11.2 on (staging deferred, §21.1). `main` images are staging candidates; `v*` images are release artifacts. Automating deploy as a dispatch job is left for when a production target exists |

Branch pushes build no images. Rollback:
- Release without a migration: redeploy the previous digest (rolling, zero downtime).
- Release with a migration: roll forward with a fix by default; a downgrade only for a
  migration whose header declares it reversible and data-safe; otherwise restore to the
  pre-deploy point (PITR where the platform supports it) as the last resort, with the
  data-loss window stated. Never edit `alembic_version` (runbook §1).

Main-branch policy: `main` is always deployable; only approved, merged phase work reaches
it; only images built from `main` or a `v*` tag may be deployed.

## 8. Migration strategy

- Migrations run only in the `migrate` job, as `lattice_owner`, never at api/worker startup.
- The migration session sets `lock_timeout` (5 s) and a configurable `statement_timeout`.
  Long data changes are batched in their own migrations.
- Every migration header states: reversible (downgrade data-safe) yes/no, expected lock
  level. A test enforces the header.
- CI keeps: clean apply, `alembic check`, round-trip; adds: apply on top of the seeded and
  synced dataset, and the privilege snapshot (§15.1).
- Exact-match guard kept (D-23); order per §6.3.

## 9. Identity and session operational model

Phase 10 identity is not redesigned; the authorization model is unchanged.

### 9.1 Behaviour in production

| Concern | Production behaviour |
| ------- | -------------------- |
| OIDC callback/origin | Redirect URI = `LATTICE_PUBLIC_URL` + `/api/v1/auth/oidc/<key>/callback`; the ingress serves exactly that origin |
| Secure cookies / TLS / proxy | `__Host-` cookies; HTTPS at the browser; `X-Forwarded-Proto` honoured only from the trusted ingress |
| Session lifetime | Unchanged: idle 12 h, absolute 7 d |
| Logout | Unchanged (server-side revoke) |
| One-time, browser-bound login | Already correct across processes (attempts consumed atomically in PostgreSQL); proven across the two API instances |
| Clock skew | Hosts run NTP (contract); new gauge `lattice_clock_skew_seconds` (app clock − DB `now()`); OIDC leeway stays 60 s; boundary tests at ±59/±61 s for `iat`/`exp` and at session idle/absolute edges |
| IdP outage | Start/callback fail as `sso_unavailable`; existing sessions continue (server-side sessions do not depend on the IdP); no break-glass |
| First admin / operator recovery | `lattice member admin` (existing); runbook adds "all administrators lost" and "IdP lost" procedures with audited commands |

### 9.2 SSO connection lifecycle (D-25)

Minimum model change: `sessions.oidc_connection_id` (nullable; NULL for development
sign-in), set when an OIDC sign-in creates the session.

| Operation | Command (owner, `--reason`, audited, no secret or reference value in the event) | Effect |
| --------- | ------------------------------------------------------------------------------- | ------ |
| Disable | `lattice sso disable` | status `disabled`; in the same transaction every unrevoked session issued through the connection is revoked (count recorded in the event); its SCIM tokens stop working (existing) |
| Re-enable | `lattice sso enable` | the **same** connection (same ID, login key and directory identities) becomes `active` again, if no other connection of the organization is active; revoked sessions stay revoked; SCIM tokens not revoked meanwhile work again |
| Rotate the client secret | same reference, new value: secret store + rolling api restart; or new reference: put `LATTICE_OIDC_SECRET_<NEW>` in every api instance first, then `lattice sso update --client-secret-ref env:LATTICE_OIDC_SECRET_<NEW>` | no session impact; the old variable is removed afterwards |
| Update client ID | `lattice sso update --client-id …` | in place; no session impact |
| Change issuer or identifying claim | `lattice sso update --issuer … \| --subject-claim … --subjects-unchanged` | an identity-namespace change: allowed in place only with the explicit assertion that subjects keep their values (e.g. an issuer URL rename); revokes the connection's sessions. Migrating to a different identity provider whose subjects differ needs an explicit identity mapping — Phase 12 |
| New connection | `lattice sso add` | refused while the organization has a disabled connection with provisioned members, unless `--new-identity-namespace` is given (stating that those members will not map); the normal path is `enable` / `update`, so the provisioned population is never orphaned by accident |

### 9.3 Audited operator commands replacing owner SQL (D-26)

| Procedure (runbook today) | Phase 11 command |
| ------------------------- | ---------------- |
| Urgent suspension by owner-role `UPDATE` (§2) | `lattice member suspend --org … --email … --reason …` (revokes sessions in the transaction) |
| Re-hire after SCIM removal by owner-role `UPDATE` "audited by hand" (§9) | `lattice member reinstate …` (explicit operator act; D-20 terminal removal by SCIM is unchanged) |
| Organization emergency stop (none) | `lattice org suspend \| reactivate` (sessions of a suspended organization already fail on the next request; its SSO and SCIM stop) |
| End sessions | `lattice session revoke --org … (--email … \| --all)` |

Each writes an `audit_events` row (actor `operator:<command>`) in the same transaction.
Destructive organization purge: Phase 12.

## 10. Rate-limit model (D-24, amended)

### 10.1 Target state: no process-local limit in the production topology

| Limit | Default (unchanged) | Today | Phase 11 | Class after Phase 11 |
| ----- | ------------------- | ----- | -------- | -------------------- |
| Asks per minute / per day per membership | 10 / 500 | PostgreSQL | unchanged, proven across two instances | PostgreSQL, shared |
| Asks in flight per membership | 2 | process | `ask_slots` lease rows (organization, membership, slot, lease expiry = generation deadline + retrieval margin) taken with the conditional-insert pattern under RLS, released in `finally`; a crashed process frees its slot by expiry | PostgreSQL, shared |
| Access previews/writes per administrator | 60/min | PostgreSQL | unchanged | PostgreSQL, shared |
| SCIM requests per token | 600/min | PostgreSQL | unchanged | PostgreSQL, shared |
| Pre-auth per client (development sign-in, OIDC start/callback, failed SCIM authentication) | 30/min | process | PostgreSQL (§10.2); "refused before the token is checked" kept | PostgreSQL, shared |
| Request body cap, question length, statement timeout, generation deadline | as today | per request | unchanged | per request (stateless) |

**Connection-local:** none. **Process-local:** no limit; only caches remain per process
(OIDC discovery and JWKS, 10 min, refresh ≤ 1/min) and metrics, which are not security
controls. This table moves into runbook §7.4 and security-model §11 when built.

### 10.2 Shared pre-auth limit

No organization is known before authentication, so the counters are not a tenant table.
Following the existing pre-auth pattern (ADR-015, runbook §1): a table
`pre_auth_rate_limits (bucket, client_key, window_start, count)` with no privilege for
any login role, written only through a `SECURITY DEFINER` function owned by `lattice_auth`
(`lattice_pre_auth_hit(bucket, client_key, limit, window)`; `EXECUTE` for `lattice_app`),
with the same conditional upsert semantics as the tenant counters. `client_key` is an
HMAC-SHA-256 of the client address under `LATTICE_CLIENT_KEY_SECRET` (a new deployment
secret, ≥ 32 bytes, required in production; rotating it only resets counters), so neither
the table nor a backup holds an address and a database dump cannot be reversed by
enumerating the address space. Retention through a second `lattice_auth` function callable
by the worker (§12). Defaults unchanged; Redis not used.

### 10.3 Client address

The address comes only from the trusted ingress boundary (§5). Tests: a client-supplied
`X-Forwarded-For` through the ingress does not change the key; a request from a
non-ingress peer has its forwarding headers ignored; two clients behind the ingress get
two keys; IPv6 normalised.

## 11. Database operations

### 11.1 Roles and bootstrap
Unchanged four roles. A production bootstrap variant (F10): no `lattice_test`; run once by
the platform's administrative role; P11.2 verifies each statement against the selected
platform's privilege model (extension creation, role attributes such as `NOBYPASSRLS`,
`GRANT … WITH INHERIT FALSE, SET TRUE`) and documents the platform-specific substitute
where one is needed. The startup role checks remain the authority: whatever the
bootstrap does, api and worker refuse an unsafe role.

### 11.2 Backup model and PITR
- Contract: PostgreSQL 16 + pgvector 0.8.1 with continuous WAL archiving / PITR, daily
  snapshots, encryption at rest. A production platform without PITR does not meet the
  contract.
- Staging (D-22): PITR capability is a selection criterion in P11.0. If the cheapest
  sensible option lacks it, the comparison says so explicitly and the product owner
  decides before P11.1; no RPO is claimed that the platform cannot deliver.
- Local reference stack: PostgreSQL's own WAL archiving and `pg_basebackup`; PITR by
  `recovery_target_time`. No new backup tool.
- RPO/RTO: stated as assumptions in P11.0 from the selected platform's documented
  capabilities, then replaced by measured values in P11.5. Nothing stronger is claimed.
- Logical `pg_dump` stays an optional portability export (needs a role that bypasses RLS);
  F13 is resolved by documenting what the selected platform's administrative role can do.

### 11.3 Restore verification (P11.5 closure requires all of these)
1. Documented backup model and restore procedure (runbook §7.7 rewritten).
2. Actual restores: latest, and to a chosen point in time, on the local stack **and** on
   staging (to the extent the staging platform supports PITR; a snapshot restore at least).
3. `lattice verify-restore` (owner, read-only) passes on each restored copy: schema revision
   equals the image's; role attributes safe; every tenant table has forced RLS; privilege
   snapshot matches (§15.1); audit immutability and access-audit triggers present;
   per-organization row counts (owner binds each organization) match a manifest taken at
   backup time.
4. Evidence that the restored database passes the startup checks: `lattice check-db-role`,
   `lattice worker --once`, and an api instance reaching readiness against it.
5. A scan of the restored copy for every configured secret value finds none.
6. Measured RPO/RTO recorded against the stated assumptions.

### 11.4 Connection budget and maintenance
No pooler. Budget: api instances × (pool 5 + overflow 5) + worker × 2 + migrate/operators
(≤ 3) + platform monitoring ≤ `max_connections` − 10; `lattice check-deployment` verifies
it. Autovacuum defaults; retention deletes in batches; `ANALYZE` after large purges;
`lattice_database_size_bytes` exported by the worker hourly.

## 12. Retention

### 12.1 Retention classes

| Class | Data | Rule | Database floor | Who deletes |
| ----- | ---- | ---- | -------------- | ----------- |
| **Durable security record** | `audit_events` | never by retention; governed by its own durability requirements (append-only; removed only with its organization, Phase 12) | no DELETE privilege for any runtime role; UPDATE refused by trigger | nobody |
| **Identity and configuration tombstones** | `directory_identities`, removed memberships, `scim_tokens`, `oidc_connections`, `provider_credentials` history | kept for the life of the organization | — | nobody |
| **User history (content-bearing)** | `query_traces` | `LATTICE_TRACE_RETENTION_DAYS`, default 90 (decided: data-model, security-model §8), whole row | proposed 30 days — to confirm in P11.4 | worker |
| **Operational history** | `jobs` (finished) | 30 days (exists) | 30 days | worker |
| | `source_sync_runs` (+ their `sync_record_failures`, cascade) | 180 days, always keeping each source's latest run | 180 days | worker |
| **Transient security state** | `sessions` | proposed: 30 days after `expires_at` or `revoked_at` — duration not decided in the canonical docs; to decide in P11.4 | never before the session ended | worker |
| | `oidc_login_attempts` | proposed: 1 day after expiry or consumption — to decide in P11.4 | after expiry | worker (the per-organization cleanup at start is kept) |
| | `rate_limit_counters` | window ended > 2 days ago | 2 days | worker |
| | `pre_auth_rate_limits` | window ended > 2 days ago | 2 days | worker, through a `lattice_auth` function |
| | `ask_slots` | lease expired > 1 hour ago | expired | worker |
| **Logs** | stdout JSON | platform retention; duration to decide with the platform (30 days suggested) | — | platform |

The data-model sentence "sync failures 30 days after resolution" is replaced: the schema
has no resolution; failures live and die with their run.

### 12.2 Safeguards
- **Database-enforced floors:** the worker's DELETE is permitted only by age-gated
  restrictive policies (the Phase 8 `worker_retention` pattern); a bug or a wrong setting
  cannot delete a row younger than its floor, even by a direct statement as
  `lattice_worker`. The worker gains no SELECT on content.
- **Deterministic:** cutoffs are computed from the database's `now()` in the statement,
  never from the worker's clock; each task deletes by primary key in bounded batches.
- **Safe under retry:** each batch is its own transaction and idempotent (a repeated or
  concurrent run deletes nothing twice; two workers may run the same task).
- **Observable:** per task `lattice_maintenance_rows_total{task}`,
  `lattice_maintenance_last_success_timestamp_seconds{task}`,
  `lattice_maintenance_failures_total{task}`; a `maintenance.retention` log event per run.
- **Testable:** per rule, across two organizations: deletes exactly the eligible rows,
  keeps the latest run per source, refuses younger rows at the database, never touches
  `audit_events` or tombstones, survives retry and concurrent runs.

## 13. Secret handling

| Secret | Lives in | Rotation | Revocation |
| ------ | -------- | -------- | ---------- |
| DB passwords (owner, app, worker) | platform secret store → env | `ALTER ROLE … PASSWORD` by the platform admin, update the secret, rolling restart | same |
| `LATTICE_OIDC_SECRET_*` | secret store → env; DB holds the reference | §9.2 | IdP side + `sso disable` |
| `LATTICE_PROVIDER_SECRET_*` | secret store → env; DB holds the reference | add new variable, rolling restart, `provider credential set` (revokes the old reference), remove old variable | `provider credential revoke` (next request) |
| `LATTICE_CLIENT_KEY_SECRET` (new) | secret store → env | replace + rolling restart (resets pre-auth counters only) | — |
| SCIM tokens | SHA-256 in DB; plaintext only in the IdP | create → switch IdP → revoke | `scim token revoke` |
| Session tokens | SHA-256 in DB, cookie | sign-in rotation | logout, `session revoke`, suspension, `sso disable` |
| Registry push | `GITHUB_TOKEN`, `images` job only | — | — |

No new secrets platform: the platform's secret store injecting env satisfies D-16 and the
`env:` reference namespaces. D-15/D-16 preserved: a credential never raises a sensitivity,
trust class or approval. Backups hold hashes and references, never values (verified,
§11.3). Logs collected during drills are scanned for every configured secret value.

## 14. Worker and job operational model

One worker instance by default (more are safe: `SKIP LOCKED`). Unchanged semantics. New:
heartbeat file and `lattice_worker_heartbeat_timestamp_seconds` (liveness, built in
P11.1), `lattice_jobs_oldest_waiting_seconds`, the retention tasks of §12 in the
maintenance loop. Source exports come from a mounted volume (ADR-013: the worker reads
only what the operator's last `lattice sync` recorded). Least privilege unchanged except
the age-gated DELETE policies and the pre-auth cleanup function.

## 15. Security and authorization verification in deployed environments

### 15.1 Deployment checks (production-safe, no content)
`lattice check-deployment` (operator, read-only): role attributes and memberships; forced
RLS on every tenant table; **privilege snapshot** — a normalised dump of policies,
table/column grants, function owners and `SECURITY DEFINER` settings, generated in CI from
the migrated database and committed, compared with the deployment (detects drift);
connection budget; schema revision. Plus black-box `lattice smoke` against the public URL:
health, headers, 401s, CSP, metrics unreachable publicly, SCIM 401 on a bad token, OIDC
start redirect.

### 15.2 Authorization suite (local stack; staging deferred, §21.1)
Synthetic organizations (`data_origin: synthetic`) seeded from the manifests and linked to
the verification IdP (a small OIDC provider built from the existing in-process test IdP;
verification profile only, never in production images). Personas sign in through real
OIDC over TLS, across both API instances. Each check against reference-policy values:
tenant isolation and RLS (route-inventory sweep over HTTP: another organization's
identifiers answer as missing); application and worker roles (`check-deployment` + a
requested sync per organization); admin ≠ content (SEC-15, D-5); revocation (SCIM
deactivation and an Access change apply on the next request); trace re-authorization;
D-8; hidden groups/accounts (D-18); suspended/deactivated users with a live session (next
request 401 everywhere); and the `lattice eval retrieval` / `eval answer` security gates run
inside the stack as an operator job (every security count 0).

### 15.3 Identity verification (P11.3; local stack; staging deferred, §21.1)

| Case | Expected |
| ---- | -------- |
| Successful OIDC login | session created, `oidc_connection_id` recorded, start on one instance and callback on the other |
| Login replay | a used `state` refused on either instance; a foreign or missing binding cookie refused |
| Session expiry | idle and absolute boundaries (just before / just after) |
| Logout | token unusable on both instances |
| SSO disable | sign-in refused at once; SCIM tokens refused |
| Session revocation after SSO disable | that connection's sessions 401 on the next request; development-sign-in sessions of other organizations untouched |
| SSO reactivation / config update | `enable` restores sign-in for the same provisioned members; `update --client-id` works; issuer/claim change requires `--subjects-unchanged` and revokes sessions; `sso add` refuses to orphan |
| Secret rotation | OIDC client secret (same reference/new value, and new reference); provider credential; client-key secret |
| Suspended user with an existing session | `member suspend` → next request 401 on every route |
| SCIM suspend / deactivate | sessions revoked in the transaction; Ask, traces and Access views refuse |
| SCIM token rotation / revocation | overlap procedure; a revoked token 401 at once |
| IdP outage | `sso_unavailable`; existing sessions continue |
| Clock skew boundaries | ID token `iat`/`exp` at ±59 s / ±61 s; skew gauge and alert |
| Multi-process | every case above with requests spread over both API instances |

## 16. Observability

Existing metrics (runbook §7.3) stay. Additions, closed labels, no content:

| Metric | Purpose |
| ------ | ------- |
| `lattice_build_info{revision,version,schema_revision}` | release identification (internal) |
| `lattice_clock_skew_seconds` | clock skew vs DB |
| `lattice_jobs_oldest_waiting_seconds`; `lattice_worker_heartbeat_timestamp_seconds` (built in P11.1) | backlog, stuck worker |
| `lattice_maintenance_last_success_timestamp_seconds{task}`, `lattice_maintenance_failures_total{task}` | retention health |
| `lattice_member_lifecycle_total{change,actor_kind}` (`created/suspended/reactivated/removed` × `scim/administrator/operator`) | provisioning bursts (F15) |
| `lattice_sessions_revoked_total{reason}` | revocation visibility |
| `lattice_database_size_bytes` | storage growth |

Platform signals the contract requires: container up/restarts, readiness failures, DB
CPU/connections/storage, backup and WAL-archive status, TLS certificate expiry, an
external probe of `https://<public>/api/v1/health/ready`.

## 17. Alerting

Prometheus rule file `deploy/observability/alerts.yml` with `promtool` unit tests;
routing is the platform's. Every alert names a runbook section (new runbook §10).

| Alert | Condition (sketch) | Severity | Runbook action |
| ----- | ------------------ | -------- | -------------- |
| Service unavailable | external probe of public readiness failing 2 min (suppressed during a declared migrating deploy) | page | §10.1 |
| High 5xx ratio | 5xx / all > 5 % for 10 min | page | §10.2 |
| Database unavailable | `lattice_dependency_failures_total{dependency="database"}` increasing for 2 min | page | §2 "Database outage" |
| Schema mismatch | readiness failing with `check="schema_revision"` for 10 min | page | §10.3: finish or roll back the deploy (§6.3, §7) |
| Worker stopped/stuck | no worker scrape, or no poll for 5 min | page | §7.5 |
| Job backlog | oldest waiting job > 15 min | ticket | §7.5 |
| Jobs abandoned | any `outcome="abandoned"` in 1 h | ticket | §7.5 |
| Repeated sync failures | ≥ 3 failed runs in 6 h | ticket | §2 |
| Backup / WAL archiving failing | platform signal (or `pg_stat_archiver` on the reference stack) | page | §10.4 |
| PITR window short / restore drill overdue | platform PITR window below the contract; last recorded drill > 35 days | ticket | §10.4 |
| Provisioning burst | SCIM suspensions + removals > N in 10 min, or creations > M | page (security) | §10.5: T23 — revoke the SCIM token, `sso disable`, review audit |
| Repeated authentication failures | `token_invalid`/`invalid_state` sign-ins, SCIM `unauthenticated` or `sign_in` refusals above baseline | ticket | §2, §9 |
| Security invariant violation | any increase of `lattice_security_invariant_violations_total` | page (immediate) | §2 incident procedure |
| Provider failure surge | non-success generation calls > 50 % for 10 min, or semantic `provider_failed` surge | ticket | §2 "Provider outage" |
| Sustained rate-limit pressure | any bucket refusing > X/min for 30 min | ticket | §2 "429" |
| Retention failing | no successful run of a task for 3 h | ticket | §10.6 |
| Storage high | DB storage > 80 % (ticket), > 90 % (page) | ticket/page | §10.7 |
| Clock skew | \|skew\| > 5 s for 10 min | ticket | §10.8 |
| TLS certificate expiring | < 14 days | ticket | platform |

N/M/X are set from drill baselines in P11.6 and recorded.

## 18. Failure drills (local stack; the starred ones were to be repeated on staging — deferred, §21.1)

| Drill | Expected |
| ----- | -------- |
| API instance restart / kill during an Ask | the other instance serves; the Ask fails cleanly or completes within grace; its slot lease is freed |
| Worker restart / kill mid-job | lease expiry → retried; run `aborted`; a second sync writes nothing |
| DB temporarily unavailable | 503 + `Retry-After`, readiness fails, liveness passes, recovery without restart; alert fires and resolves |
| Provider unavailable | `failed`/`provider_unavailable` with evidence within the deadline; provider-surge alert |
| Source sync failure | job `failed` with code; Sources page shows it; alert after repetition |
| Migration failure* | a deliberately failing migration image: the deploy stops at step 3, the database is unchanged, the previous build is restarted and serves |
| Backup restore + PITR* | §11.3 |
| Revoked secret* | provider credential revoked → `credential_unavailable`; wrong OIDC secret → `sso_unavailable`; revoked SCIM token → 401 |
| Disabled SSO connection* | sign-in refused; its sessions end; `sso enable` restores sign-in for the same members |
| Suspended user with a live session* | next request 401 on every route |
| Deployment rollback* | a no-migration release rolled back with no failed request at the ingress |
| Clock skew (simulated) | gauge and alert |

## 19. Known deferred items: disposition

| Item | Disposition | Why |
| ---- | ----------- | --- |
| Process-local Ask concurrency | **Phase 11** (P11.4) | D-24 |
| Process-local pre-auth limit | **Phase 11** (P11.4, shared in PostgreSQL) | D-24 amended: no process-local limit may remain |
| Sessions surviving a disabled SSO connection | **Phase 11** (P11.3) | D-25 |
| SSO connection lifecycle (F5) | **Phase 11** (P11.3) | D-25 |
| Retention | **Phase 11** (P11.4) | documented as Phase 11 throughout |
| Alerting, incl. provisioning bursts and `security_invariant_violated` | **Phase 11** (P11.6) | roadmap row 11; threat model T23 residual |
| Container/deployment packaging | **Phase 11** (P11.1–P11.2) | — |
| Backups/PITR and restore verification | **Phase 11** (P11.5) | §11.3 |
| Production migration flow | **Phase 11** (P11.2) | D-23 |
| Operational secret rotation | **Phase 11** (P11.3) | §13 |
| Multi-process identity/rate-limit correctness | **Phase 11** (P11.3–P11.4) | api ×2 everywhere |
| Unaudited owner SQL in incident procedures | **Phase 11** (P11.3) | D-26 |
| SCIM: removed person cannot be re-provisioned under the same `externalId` | **Still true; intentional** (D-20). Operator path becomes `member reinstate` (P11.3); customer-facing re-hire UX → Phase 12 | decided behaviour; the unaudited operator path was the gap |
| One-time browser-bound OIDC login across processes | **Already resolved** (attempts in PostgreSQL); proven in P11.3 | — |
| Ask/min, Ask/day, admin-write and SCIM limits across processes | **Already resolved** (PostgreSQL); proven in P11.4 | — |
| Organization deletion/purge | **Phase 12** | D-26 |
| Identity-provider migration with differing subjects | **Phase 12** | needs an explicit identity mapping (customer operation) |
| Per-organization trace retention, `ask_enabled`, freshness thresholds | **Phase 12** | customer-facing settings |
| Per-organization Ask budget (D-21 note), fixed-window 2× burst | **Phase 14** | scale/adversarial hardening |
| Connection pooler, HA beyond managed PostgreSQL, multi-region, image OS-package scanning | **Phase 14** | not required at single-region pilot scale |
| D-4 default generation provider; real-model answer evaluation; R@10 ≥ 0.90 | **Phase 13** (provider setup UX in Phase 12) | quality, not operability |
| Accounts workspace; next connectors | §27 | roadmap correction |

### 19.1 Temporary advisory
Only `GHSA-vfj7-8cjw-p6xm` is carried forward, with its existing narrow treatment
(`apps/web/pnpm-workspace.yaml` `ignoreGhsas`, `src/test/audit-exceptions.test.ts`,
runbook §7.8 row). Checked 2026-10-03: the latest `braces` is still 3.0.3 (affected). At
the start of every checkpoint that updates dependencies (P11.1 images, P11.2 CI) the chain
is re-checked; if a patched version is reachable safely, the exception is removed and
`make audit` must stay green. The allowlist is never broadened.

### 19.2 Known flaky tests
Carried forward: `apps/web/e2e/shell.spec.ts:309`, `apps/web/e2e/primitives.spec.ts:85`.
Each isolated retry during Phase 11 is recorded in the checkpoint report; if either fails
more than once in Phase 11, its underlying race is fixed in that checkpoint. Retries are
never increased beyond the existing CI policy (one).

## 20. Architecture changes

Approved shape (§4). Changes relative to the plan proposed on 2026-10-03, all inside it:
1. **Pre-auth limit shared in PostgreSQL** (D-24 amendment): a non-tenant counter table
   reachable only through `lattice_auth` `SECURITY DEFINER` functions (the existing
   pre-auth pattern), and one new deployment secret `LATTICE_CLIENT_KEY_SECRET` for the
   keyed hash. No new service.
2. **SSO lifecycle commands** (D-25): `sso enable`, `sso update`, guarded `sso add`;
   `sessions.oidc_connection_id`.
3. **Stop-the-world order for migrating releases** with a migration pre-flight that
   refuses while application connections are open (D-23).
4. ~~A real cloud staging deployment~~ (D-22): **deferred** by the 2026-10-05 amendment
   (§21.1); the provider comparison stays as the candidate for future validation.
5. **`images` job depends on the existing test jobs in the same run** and publishes by
   digest (D-27).

Unchanged from the proposal: two Dockerfiles and `deploy/` (Caddy for the local ingress
only); ingress routes `/api/*` to the API; migrations 0014+ (one per sub-phase that needs
one); operator commands; verification-only test IdP; `promtool` from a pinned image.
Still not needed: Redis, PgBouncer, a secrets manager, Kubernetes, a broker, a new
observability platform, service decomposition.

## 21. Decisions (decided 2026-10-03)

| ID | Decision | Notes |
| -- | -------- | ----- |
| D-22 | **Verification environment:** the production-shaped local reference stack (two API instances) is the primary repeatable environment; **plus one real cloud staging deployment before P11.7 closes**, not necessarily kept online | Provider chosen in P11.0 by comparing a small number of low-cost options against: web, API, worker, migration job, managed PostgreSQL (16 + pgvector), TLS/custom origin, OIDC callback, secrets, backups/PITR, logs/metrics access, deployment/rollback, cost (incl. avoiding recurring cost). Contract stays provider-independent; no application coupling |
| D-23 | **Schema guard:** keep the exact-match guard; accept a short unavailable interval for releases with migrations | Stop-the-world order and migration pre-flight (§6.3); no expand/contract machinery unless proven necessary for correctness |
| D-24 | **Shared limits:** PostgreSQL leases for in-flight Asks; **the pre-auth limit is shared too** (PostgreSQL, keyed hash, trusted ingress address only); no Redis; defaults unchanged | §10; documentation lists every limit as shared / connection-local / process-local, with no process-local security limit left |
| D-25 | **SSO disable revokes the sessions it issued**; the connection lifecycle is completed (re-enable, in-place update, secret-reference rotation, guarded replacement) | §9.2; backend/operator only, no Phase 12 UI |
| D-26 | **Organization purge deferred to Phase 12**; Phase 11 adds audited `org suspend/reactivate` and replaces unaudited owner SQL in incident procedures with audited commands | §9.3 |
| D-27 | **One gated image path:** `main` and `v*` tags (and manual dispatch) publish to GHCR from the same CI run after the existing jobs pass; digests immutable; revision/version visible at runtime internally; no images on development pushes; no duplicate test runs | §7 |

### 21.1 D-22 amendment (product owner, 2026-10-05): zero paid infrastructure

No budget is currently available for paid deployment or cloud infrastructure. Phase 11
therefore creates and requires **no** paid resource: no DigitalOcean (or other) account
resources, paid database, VM, domain, monitoring or backup service.

- **Phase 11 closes on full local production-shaped verification**: the reference stack
  (Caddy, web, two API instances, worker, PostgreSQL, the production images, fake AI
  providers, synthetic Halden/Veridian data only) carries every deployment, identity,
  limit, retention, backup/restore, alert and drill proof.
- **Deferred external validation** — reason: *Deferred because no deployment budget is
  currently available.* None of these is claimed as done:
  1. real cloud staging deployment;
  2. managed PostgreSQL privilege verification (the bootstrap against a provider's
     administrative role);
  3. managed backup/PITR verification;
  4. public TLS/origin validation;
  5. real external OIDC callback validation;
  6. provider-specific deploy/rollback validation;
  7. cloud RPO/RTO measurement.
- **Release gate:** Lattice must complete real external staging validation before Phase 15
  can declare v1 GA / production-ready. Until then Lattice is not described as
  production-proven.
- DigitalOcean (deployment.md Appendix A) remains documented as the **selected candidate
  for future staging validation; not provisioned or tested**.
- Everything in this plan that says "on staging" is read as "deferred (§21.1)" unless a
  later amendment funds it.

## 22. Test strategy

- Unit/integration (pytest, real PostgreSQL) for every new behaviour: `ask_slots` and
  pre-auth counters (expiry, parallel requests over two engines, refused requests count
  nothing), retention rules and floors (§12.2), operator commands (one audit event,
  reason required, no secret), SSO lifecycle (§9.2), clock-skew gauge, metric label sets,
  migration headers and `lock_timeout`, migration pre-flight.
- Multi-process: two API processes on one database for limits, OIDC start/callback,
  revocation; the local stack runs api ×2 for every drill.
- Image tests: contents (no `.env`, fixtures, manifests), non-root, read-only FS, startup
  refusal on bad configuration, every command's `--help`.
- Alert rules: `promtool test rules` for every alert.
- Deployed suites: §15 on the local stack each checkpoint that touches them (staging
  deferred, §21.1). Drills: §18.
- All existing suites stay green; mutation check by hand at closure (not committed).

## 23. Documentation and runbooks

New: `docs/deployment.md` (contract), ADR-016 (production topology), the staging
selection record (P11.0). Updated as each piece is built: runbook §1 (production
bootstrap), §2 (audited commands, D-26), §7.4 (limit classes), §7.5–§7.8, §8 drills, §9
identity operations, new §10 alert responses; architecture §1/§2; data-model retention;
security-model §5/§8/§11; threat model T19/T23/T24/T25; README "How deployment works".

**Doc ↔ code mismatches to correct** (P11.0 for the documentation-only ones; the rest
when the code changes):
1. architecture §1: "server components forward the session cookie" — the web app never
   calls the API from the server.
2. runbook §7.1/§7.4: the web app proxying `/api/*` and uvicorn trusting "the proxy"
   (F1–F3).
3. data-model: "sync failures 30 days after resolution" — no resolution exists (§12.1).
4. runbook §9 "Stop SSO": disabling is currently irreversible and a new connection orphans
   provisioned members (F5) — documented as current behaviour in P11.0, removed in P11.3.
5. runbook §2: organization deletion "planned, Phase 11" → Phase 12 (D-26).
6. runbook §7.7: backup advice assumes a superuser (F13).
7. *(Fixed in 23217b4: README and design-direction closure sentences; implementation-plan
   status and roadmap rows.)*

## 24. Rollout of Phase 11 itself

Checkpoints are committed on `phase/11-production-operations`, each stopped for review.
Migrations are additive and round-trip; the development database is migrated only after a
checkpoint is approved. The branch stays local unless a push is genuinely needed (CI
evidence for a checkpoint, or the staging deployment needing published images from
`main`/a tag), and is merged `--no-ff` only on explicit approval.

## 25. Checkpoints

| Step | Scope | Exit criteria |
| ---- | ----- | ------------- |
| **P11.0** Decisions, contract and staging selection | §25.1 | §25.1 |
| **P11.1** Packaging and runtime | Dockerfiles, `.dockerignore`; web standalone, `0.0.0.0`, dev `proxyTimeout` (F1, F11); ingress routing and trusted-hop configuration (F2, F3); uvicorn production command (proxy headers, graceful timeout); worker heartbeat (F9); `lattice_build_info` and build arguments (F14); metrics address required in production; `deploy/` reference stack with api ×2 and Caddy; `lattice smoke`; image tests; GHSA re-check | `make prod-up prod-smoke` green; an Ask lasting > 30 s succeeds through the ingress; client address correct and unspoofable through the stack; all existing suites green |
| **P11.2** Deployment, migrations, CI images (staging deploy deferred, §21.1) — *delivered, §25.3* | `migrate` job; migration pre-flight (open application connections) and `lock_timeout`; migration headers + test; seeded-data migration check; production bootstrap variant verified against the selected platform (F10); privilege snapshot + `check-deployment`; `images` job (D-27); scripted deploy and rollback (§6.3, §7); **first staging deployment** | Migrating and non-migrating deploys and a no-migration rollback rehearsed on the local stack, unavailable interval measured; staging serves the release by digest and passes `smoke` and `check-deployment` |
| **P11.3** Identity and session operations | `sessions.oidc_connection_id`; `sso disable` revokes (D-25); `sso enable/update`, guarded `sso add`; `member suspend/reinstate`, `org suspend/reactivate`, `session revoke` (D-26, F12); clock-skew gauge; verification IdP; rotation runbooks | Every §15.3 case passes on the local stack across both API instances |
| **P11.4** Shared limits, jobs, retention | `ask_slots`; shared pre-auth limit (§10.2) with `LATTICE_CLIENT_KEY_SECRET`; retention classes and safeguards (§12); job/maintenance metrics | Limit and retention tests green; §10.1 table true and documented; no process-local security limit left; worker drills pass |
| **P11.5** Backups, PITR, restore | Backup model; WAL archiving in the local stack; `verify-restore`; restores per §11.3 on the local stack (staging deferred, §21.1); secret-free backup scan; measured local backup/restore durations | All six items of §11.3 met with recorded evidence; no RPO/RTO claimed beyond the platform's capability |
| **P11.6** Observability, alerts, runbooks | New metrics (§16, F15); `alerts.yml` + promtool tests; runbook §10 per alert; thresholds from baselines | Every alert fires in a rule test and maps to a runbook action |
| **P11.7** Drills and closure | §15.2 suite and §18 drills on the local stack; final staging deployment with §15.2–§15.3 and the starred drills; clean-clone verification; docs/README; mutation check; GHSA re-check; flaky-test record; push, CI, closure report; staging may then be torn down | §26 all met |

### 25.1 P11.0 — exact acceptance criteria

P11.0 is documentation and selection only: **no product code, migration, Dockerfile, CI or
infrastructure change**, and no cloud resource created.

1. **Staging selection record** (`docs/deployment.md` appendix or `docs/staging.md`):
   three to four sensible low-cost options compared in one table against every D-22
   requirement — web, API, worker (long-running, with a mounted export volume), one-off
   migration job, managed PostgreSQL 16 with pgvector 0.8.x and the privileges the
   bootstrap needs, TLS with a custom origin, OIDC callback on that origin, secrets
   injection, backups and PITR, logs and metrics access (scraping the internal metrics
   port), deployment and rollback by digest, and cost (monthly and for a Phase 11 window
   with the environment down between uses). Sources are the providers' current
   documentation, cited with dates. One recommendation with its rationale and the
   requirements it fails, if any (PITR in particular), stated plainly. The product owner
   approves the choice and creates the account; nothing is created by Claude.
2. **`docs/deployment.md` — provider-independent contract:** images and tags/digests;
   per-process configuration matrix (api, worker, migrate, web, operator) with every
   required variable and whether it is a secret; secrets inventory (§13); ingress rules
   (routes, the single trusted hop, forwarded headers, timeouts, body limit); deployment
   order for migrating and non-migrating releases (§6.3) and rollback (§7); health,
   liveness, shutdown and restart rules; volumes; connection budget; backup/PITR contract
   with **RPO/RTO stated as assumptions** tied to the selected staging platform's
   documented capability (to be replaced by measurements in P11.5); retention classes
   (§12.1); limit classes (§10.1, marked "target, P11.4").
3. **ADR-016** (production topology: one backend image with several commands, ingress
   routes `/api/*` to the API, shared limits in PostgreSQL, provider-independent contract,
   local stack + temporary staging) added to `docs/decisions/` and its README index.
4. **implementation-plan.md:** a Phase 11 section (scope, checkpoints, link to this plan)
   and decisions D-22 to D-27 present in §7 (recorded with this plan's commit).
5. **Documentation-only mismatches corrected:** items 1, 3 and 5 of §23; item 4 documented
   as current behaviour with its fix pointed at P11.3; items 2 and 6 annotated as
   "changing in P11.1 / P11.5". Every touched document still describes the code as it is.
6. **Roadmap disposition** of the Accounts workspace and next connectors present in
   `implementation-plan.md` §4 (recorded with this plan's commit) and consistent with §27.
7. Checks: GHSA-vfj7-8cjw-p6xm chain re-checked and the result recorded; `make lint` and
   `make web-check` unaffected (no code touched); links in changed documents resolve.
8. Committed locally on `phase/11-production-operations` (not pushed), then **stop for the
   product owner's review** of the staging choice and the contract before P11.1.

### 25.2 P11.1 — as built (2026-10-03)

| Planned (§25 row P11.1) | As built |
| ----------------------- | -------- |
| Dockerfiles, `.dockerignore` | `backend/Dockerfile` (+ `Dockerfile.dockerignore`, root context, allow-list) and `apps/web/Dockerfile` (+ `.dockerignore`); base images pinned by digest; non-root (uid 10001 / `node`); no `HEALTHCHECK` in the images (per-process checks in Compose) |
| Web standalone, `0.0.0.0`, dev `proxyTimeout` (F1, F11) | `output: "standalone"` only when `LATTICE_WEB_STANDALONE=1` (the image build), so local `next start` is unchanged; the image's `node server.js` binds `0.0.0.0:3000`; the `/api` rewrite is generated only under `next dev`, with `experimental.proxyTimeout` 150 s; the production build has none |
| Ingress routing and trusted hop (F2, F3) | `deploy/caddy/Caddyfile`; new setting `LATTICE_TRUSTED_PROXIES` (validated IPs/CIDRs, `*` refused) installs uvicorn's `ProxyHeadersMiddleware` inside the app; uvicorn runs with `--no-proxy-headers` (also `make api`) |
| uvicorn production command | the backend image's default command (graceful timeout 150 s) |
| Worker heartbeat (F9) | `lattice_worker_heartbeat_timestamp_seconds` + `LATTICE_WORKER_HEARTBEAT_FILE`; no migration |
| `lattice_build_info` (F14) | `lattice.build`: build-argument revision/version → metric and `startup` log line |
| Reference stack (api ×2, Caddy) | `compose.production.yaml`, `deploy/compose.verify.yaml`, `make prod-*` |
| `lattice smoke` | `lattice.smoke`: web, API liveness/readiness through the ingress, authentication enforced, production surface, each API instance, worker heartbeat; an authenticated Ask waits for P11.3's verification IdP |
| Test-only long-Ask mechanism | `LATTICE_FAKE_GENERATION_DELAY_SECONDS` on the fake provider (refused in production) |
| Metrics address required in production | **Deferred to P11.6** (with the monitoring contract): every deployment sets it, but making it mandatory now would change every production configuration and the test harness for no P11.1 benefit |
| GHSA re-check | still unpatched; `pnpm-workspace.yaml` comment corrected to both chains |
| Staging database | the 2 GiB DigitalOcean plan (product owner, 2026-10-03); Appendix A re-costed |

### 25.3 P11.2 — as built (2026-10-05)

| Planned | As built |
| ------- | -------- |
| `migrate` job, pre-flight, `lock_timeout` | `lattice migrate` (`lattice.db.migrate`): owner check (refuses a runtime credential, a superuser, BYPASSRLS); session advisory lock with a bounded wait; a **connection fence** (CONNECT revoked from `lattice_app`/`lattice_worker` while it runs — closes the race of an old build starting mid-migration; lifted in every case, reported by `check-deployment` if a hard kill leaves it); pre-flight refusing any application session (by role, so it needs no `pg_read_all_stats`); one-transaction upgrade with `lock_timeout` 5 s / `statement_timeout` 10 min; exact verification (revision and no autogenerate difference). `--status [--json]`, `--release-fence`, `--downgrade-to REV [--check]` |
| Migration headers + test | `Reversible: yes\|no (…)` and `Locks: …` in 0001–0013; `tests/test_migration_safety.py`; downgrade allowed only over revisions declared reversible |
| Seeded-data migration check | CI runs `lattice migrate` on the clean database and again on the seeded and synced data (no-op, verified), then `check-deployment` |
| Production bootstrap (F10) | `infra/postgres/bootstrap_production.sql`, proved with a CREATEROLE + CREATEDB administrator in the reference stack and on every CI push; pgvector, being untrusted, comes from the platform's extension mechanism (emulated by `template1`) — deployment.md §7. Live-provider verification deferred (§21.1) |
| Privilege snapshot + `check-deployment` | `lattice check-deployment` (deployment.md §8); snapshot committed as `backend/src/lattice/db/privilege_snapshot.json`, regenerated by `make privilege-snapshot`, pinned by a test |
| `images` job (D-27) | in `ci.yml`: push to `main`/`v*` after the same run's jobs; `workflow_dispatch` skips the test jobs and requires an earlier green push run of the commit; reference stack + smoke + check before pushing; `GITHUB_TOKEN` only. Remote proof deferred to the final branch validation |
| Scripted deploy and rollback | `deploy/deploy.sh status\|release\|rollback`; immutable image IDs; pre-flight (identity, revision consistency, `check-config` in the new image, schema state); rolling and stop-the-world flows; per-instance restore on failed readiness; smoke with `--expect-revision`; rollback across a reversible migration only with `--allow-downgrade` |
| Additions found necessary | `lattice check-config`, `lattice version`, `lattice smoke --instance-metrics/--expect-revision`; production api/worker refuse the owner credential in their environment; ingress maintenance switch; **two web instances** (a single one cannot be replaced without a gap — measured); `lattice seed --require-synthetic` and `make prod-seed` (synthetic-only bootstrap); `deploy/drills/` |
| First staging deploy | **Deferred** (D-22 amended, §21.1) |
| Evidence | runbook §8 "P11.2 drills": every refusal and flow above, 0 failed requests over rolling releases and rollbacks, 19 s unavailable for a migrating release |

Commits: ae03445; fd0d1bc fixed three tests found by the full suite (the authz boundary
test forbids `lattice.authz.models` in `db/models_registry.py` — now the public package —
and two production-mode app tests now run without the owner credential that `make test`
loads from `.env`, through a shared `production_runtime_environ` fixture; the startup
refusal is unchanged).

### 25.4 P11.3 — as built (2026-10-05)

| Planned (§9, §15.3, §25 row P11.3) | As built |
| ----------------------------------- | -------- |
| `sessions.oidc_connection_id` | migration 0014 (`Reversible: no`): nullable column, composite FK to `oidc_connections (organization_id, id)`, partial index on live sessions; `lattice_app` gains `SELECT (id, organization_id, status)` on `oidc_connections` (tenant RLS as before) — the privilege snapshot records it |
| `sso disable` revokes (D-25) | in the transaction of the status change, under the connection's advisory lock held **exclusively**; OIDC session creation holds it **shared**, re-reads the status and refuses a non-active connection (`LatticeError` → `sso_failed`/`inactive`), so a sign-in racing a disable either commits first and is revoked or creates nothing. The count is in the event and printed |
| `sso enable/update`, guarded `sso add` | `sso list`; `sso enable [--connection-id]` (the same connection, refused while another is active); `sso update` (`--client-id`, `--client-secret-ref` / `--no-client-secret`, `--issuer` / `--subject-claim` only with `--subjects-unchanged`, which revokes the connection's sessions); `sso add` refused while a disabled connection has provisioned, non-removed members unless `--new-identity-namespace` |
| `member suspend/reinstate`, `org suspend/reactivate`, `session revoke` (D-26, F12) | `lattice.identity.admin` + `cli_identity`; one transaction, one audit event each (`access.member_changed` with before/after snapshots, `session.revoked`, `organization.suspended`/`reactivated`); suspension and organization suspension revoke sessions; reinstatement revives none; the worker fails a suspended organization's jobs with `organization_suspended`. Runbook §2 and §9 no longer contain owner-role SQL for any incident procedure |
| Clock skew | `lattice_clock_skew_seconds` (app clock − database `clock_timestamp()`, round-trip midpoint): API readiness and every worker poll; never a readiness condition |
| Verification IdP | `deploy/verification/idp.py` (stdlib HTTPS server in the backend image, mounted, never built in): discovery, JWKS with a per-start key ID, PKCE S256, client_secret_basic with several secrets per client (rotation overlap), RS256 ID tokens, a bearer-protected `PUT /admin/state` for clients and `iat`/`exp` offsets. `deploy/compose.idp.yaml` (`IDP=1`): its own throwaway CA (`make-certs.sh`), `SSL_CERT_FILE` in the API instances, the `env:` secret references, per-instance metrics on 127.0.0.1 |
| Identity matrix (§15.3) | in-process `tests/identity/test_two_instances.py` (two applications, one database, one cookie jar: 21 cases incl. the disable race and the expiry edges) and `tests/identity/test_lifecycle.py` (11: audit events, refusals, every command through `lattice …`); on the stack `make prod-identity` — **42/42** across api1/api2 in production mode, `check-deployment` 7/7, smoke 13/13 (runbook §8) |
| Rotation runbooks | runbook §9: client secret (same reference with the provider's two-secret overlap and a rolling restart; new reference), client ID, issuer/claim, SCIM token, "all administrators lost", "identity provider lost" |
| Web | audit-log labels for the new actions (`sso.connection_enabled/updated`, `session.revoked`, `organization.suspended/reactivated`) |
| Not done here | the stack-level race (in-process only: it needs a held transaction); the issuer rename on the stack (the verification provider's issuer is fixed; in-process covers it); real external OIDC callback validation stays deferred (§21.1) |

Found and fixed on the way: `deploy/drills/` hard-coded 0013/0014 and now numbers its
drill revision one above the base image's head; the previous session's relaxation of
`_check_issuer` (http for any host in development/test) was removed — the stack's API
refuses non-https providers anyway, so the verification provider uses TLS.

## 26. Phase 11 acceptance criteria

1. Both images build reproducibly from a locked tree, run non-root on a read-only root
   filesystem, contain no secrets or test data, and report revision and version internally.
2. A fresh deployment reaches readiness only in the documented order; every startup guard
   still refuses unsafe configuration inside containers; a migrating release cannot leave
   an old instance serving the new schema (§6.3).
3. An Ask longer than 30 s succeeds through the production path; graceful shutdown loses
   no completed Ask.
4. Client addresses come only from the trusted ingress; spoofed forwarding headers are
   ignored.
5. Every limit is PostgreSQL-shared or per request; none is process-local; proven across
   two API instances; defaults unchanged.
6. SSO disable revokes the connection's sessions; the connection can be re-enabled,
   updated and its secret reference rotated without orphaning provisioned members; every
   operator identity and incident command is audited; no runbook procedure for normal
   incidents relies on unaudited owner SQL.
7. Retention deletes exactly the documented data per class, never audit events or
   tombstones; the database refuses deletions younger than each floor; retention is safe
   under retry and observable.
8. P11.5 closure (§11.3) met on the local stack, with local backup/restore durations
   measured and never presented as cloud RPO/RTO (managed PITR and cloud RPO/RTO deferred,
   §21.1).
9. Every alert has a passing rule test and a runbook action; the security-invariant alert
   fires on a synthetic violation.
10. §15.2 and §15.3 pass on the local stack; `check-deployment` reports no drift; eval
    security gates report 0.
11. Every §18 drill is run on the local stack and recorded in runbook §8.
12. ~~One real staging deployment was performed and verified before closure.~~ Replaced
    (2026-10-05, §21.1): the deferred external-validation list is recorded explicitly and
    carried as a Phase 15 release gate.
13. All existing suites stay green (backend, web unit, Playwright desktop/mobile/production,
    dependency audit with only GHSA-vfj7-8cjw-p6xm excepted); flaky-test occurrences
    recorded; no infrastructure beyond §20.

## 27. Roadmap correction: displaced items

On 2026-10-03 the roadmap rows 12 (Accounts workspace) and 13 (Next connectors) were
replaced by the product owner's Phases 12–15. Their scope is kept and placed:

**Accounts workspace.** In the Phase 0 design (design-system v1, "Accounts, Saved
deferred") it meant *an account timeline plus scoped Ask for account managers* — "the
strongest next workflow for AMs, but it isn't needed to prove the slice"; roadmap row 12
"Account timeline + scoped Ask (AM workflow)". Since then, scoped Ask itself has shipped
(Phase 9: `GET /v1/accounts`, the Ask account picker and scope line). What remains splits
in two:
- **Account administration — Phase 12.** Accounts and their mapping to source account
  references exist only through the seed manifest and `sources.yaml`; no API or UI creates,
  renames or maps them (Phase 9 non-goal). A customer cannot be onboarded or operate
  account-scoped sources without it, so it belongs to Phase 12's organization/source setup.
- **Account timeline workspace — post-v1, intentionally deferred.** The v1 definition
  (product brief §5 core workflow and §8 metrics) is Ask → cited answer → evidence, which
  account managers already have through scoped Ask. The timeline is an additional
  workspace with its own authorization surface (every listed item re-authorized, D-8/D-10
  rules for account metadata) and was never part of the first-release workflow. Recorded
  so it is not lost; the product owner may pull it into Phase 13–15 if v1 should include it.

**Next connectors** (call transcripts, CRM notes, GitHub issues, release notes) — **Phase
12 evaluates them** as part of its integration/setup scope: which source systems a first
customer needs connected, and turning today's export-based connectors into setup-able
integrations. Connectors that Phase 12 finds necessary for v1 are built there; the rest are
recorded there as post-v1.
