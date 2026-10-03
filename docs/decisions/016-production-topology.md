# ADR-016: Production topology and deployment contract

Status: Accepted for Phase 11 (2026-10-03; decisions D-22–D-27). **Partly implemented**:
P11.1 built the images, the ingress routing, the trusted-hop setting and the local
reference stack (implementation notes below); the contract is
[deployment.md](../deployment.md); checkpoints P11.2–P11.7 of
[phase-11-plan.md](../phase-11-plan.md) build the rest.

## Context

Phases 1–10 run as host processes from the source tree: one API process, one worker, the
Next.js server proxying `/api/*` to the API, and PostgreSQL in a local container. Nothing
is packaged, deployed, backed up or restored, and several behaviours are only correct for a
single process on one machine:

- the in-flight Ask limit and the pre-authentication per-client limit are counted in
  process memory;
- the Next.js rewrite proxies `/api/*` with a 30-second default timeout, below the 120 s
  generation deadline, and makes the API see the Next.js server as every client;
- the schema guard is an exact revision match, so instances of an old build cannot keep
  serving once a migration runs.

Phase 11 must make Lattice operate as a production system without changing what it is: a
modular monolith on one PostgreSQL (ADR-001, ADR-002), built by a very small team with a
small operational surface (product brief §7), with no hosting provider chosen.

## Decision

1. **The modular monolith remains.** No service decomposition.
2. **Two images.** `lattice-web` (Next.js standalone, no secrets) and `lattice-backend`.
   The backend image is **reused with different commands** for the API, the worker, the
   one-off migration job and operator commands, so the code that migrates a schema is the
   code that serves it.
3. **PostgreSQL remains the only durable system of record**, including for the shared
   rate limits: the in-flight Ask limit and the pre-authentication limit move to
   PostgreSQL (D-24), like every other limit.
4. **The ingress routes `/api/*` directly to the API** and everything else to the web
   server. It terminates TLS and is the only trusted forwarding hop; the API trusts
   forwarding headers from it alone. Next.js is not on the API path in production (its
   rewrite stays for local development).
5. **Two API instances** in the production-shaped local reference stack (and at least two
   in staging and production), so multi-process behaviour is exercised on every drill.
   Caddy is the reference stack's ingress only.
6. **A temporary real staging deployment** of the same images under the same contract is
   required before Phase 11 closes (D-22). The provider is chosen by comparison
   (deployment.md Appendix A) and stays outside the application: nothing in the code knows
   where it runs.
7. **The exact schema-revision guard is retained** (D-23). Releases with a schema change use
   **controlled downtime**: stop API traffic, stop every API instance and the worker, prove
   no application connection remains, migrate, start the new build, wait for readiness,
   restore traffic. Releases without a schema change roll with no downtime.
8. **Images are published from one gated path** (D-27): pushes to `main` and `v*` tags,
   after the existing CI jobs pass in the same run, to GHCR; deployments reference digests.
9. **No new infrastructure category:** no Redis, Kubernetes, Kafka, Celery, RabbitMQ,
   Elasticsearch, connection pooler, secrets-management product, new observability
   platform or broker. Secrets come from the platform's secret store as environment
   variables; metrics stay in Prometheus format on internal listeners.

## Consequences

- One image to scan, sign and roll back for every backend process; the worker and the
  migration job can never run a different code version than the API by accident.
- A migrating release has a short, planned unavailable interval (target under two minutes,
  measured in P11.2). A code rollback across a migration is roll-forward by default;
  downgrade only where a migration declares itself reversible and data-safe; otherwise a
  point-in-time restore with its data-loss window.
- The shared limits add a few small PostgreSQL writes per Ask and per sign-in attempt, and
  one new deployment secret (`LATTICE_CLIENT_KEY_SECRET`) for the keyed hash of client
  addresses. The table holds no address.
- The ingress must be configured correctly (routes, prefix stripping, appended
  `X-Forwarded-For`, ≥ 150 s read timeout); the contract and the smoke test make that
  checkable.
- Staging costs a little money for a few weeks; the local stack carries the repeatable
  verification.
- Managed PostgreSQL usually has no superuser: the bootstrap gains a production variant
  (P11.2); the startup role checks stay the authority on role safety.

## Alternatives considered

| Alternative | Why not |
| ----------- | ------- |
| Keep the Next.js rewrite as the production API path (raise its timeout, trust it as a proxy) | Puts the web process in the API's trust chain, adds a hop and a second timeout to tune, and Next.js passes a client-supplied `X-Forwarded-For` through unchanged |
| Separate images for API, worker and migrations | Version skew between processes that share one schema; more to build and publish for no isolation gain (the database roles already separate their privileges) |
| Redis for the in-flight and pre-auth limits | A new stateful service, credential and failure mode for a few counters PostgreSQL already handles at higher write rates for the other limits (architecture §9's trigger is not met) |
| Keep the pre-auth limit per process and document the bound | Rejected by the product owner (D-24): Phase 11 validates a multi-process deployment and no security limit may stay process-local |
| Compatibility floor / expand-contract deployments | More moving parts in a guard whose purpose is to fail closed; the accepted cost is a short downtime per migrating release (D-23). Revisit if an availability target requires it |
| Kubernetes | One API deployment, one worker and one web server need no orchestrator; it would dominate the operational surface |
| A PgBouncer pooler | The connection budget fits the staging and expected production plans (deployment.md §8); revisit in Phase 14 |
| Choosing a production provider now | Not needed to prove the contract; the staging choice is temporary and replaceable |
| Local stack only, no real staging | Cannot prove managed-PostgreSQL privileges, managed PITR, real TLS on a public origin or real OIDC callbacks (D-22) |

## Implementation notes (P11.1)

Factual refinements found while building, none changing the decision:

- **The trusted hop is application configuration, not a server flag.** The API reads
  `LATTICE_TRUSTED_PROXIES` (IP addresses/CIDRs, validated, `*` refused) and installs
  uvicorn's `ProxyHeadersMiddleware` itself; the image runs uvicorn with
  `--no-proxy-headers`, because uvicorn's default trusts 127.0.0.1 — which, in the old
  topology, was the Next.js server passing a client's `X-Forwarded-For` through. The same
  flag now applies to `make api`.
- **Next.js leaves the API path completely in production.** The rewrite is generated only
  under `next dev`; the production build's route manifest has no rewrites, so a request
  that reaches the web server's `/api` gets the web 404, never a proxy.
- **One backend image is safe for every backend process**: the database roles, not the
  image, separate their privileges, and each process gets only its own credential. The
  image therefore has no `HEALTHCHECK`; health is declared per process (api readiness,
  worker heartbeat).
- **Worker liveness needed no new infrastructure or schema:** a heartbeat gauge plus a
  file in the container's tmpfs, renewed by the loop and by lease renewals.
- **Caddy** (reference ingress only) listens on unprivileged ports but keeps
  `NET_BIND_SERVICE`, which its binary's file capability requires to execute.

## Reconsider when

- an availability target cannot tolerate migrating-release downtime (expand/contract);
- connection counts approach the database limit at the planned instance counts (pooler);
- independent scaling or ownership boundaries become real (architecture §9);
- a production provider is chosen whose ingress cannot be the single trusted hop.
