# ADR-001: FastAPI modular monolith with a rendering-only Next.js frontend

Status: Accepted (proposed in Phase 0; the FastAPI modular monolith is implemented since
Phase 1; the Next.js frontend arrives in Phase 7)

## Context
Lattice's core logic — authorization, retrieval, ingestion, generation, citation
validation — is security-critical and shares types and policy code. The team is small.
The frontend needs server rendering and good interaction, but no business rules.

## Decision
One Python package (`lattice`) served by FastAPI, with the ingestion worker as a
second process of the same codebase. Next.js renders UI only and proxies `/api/*` to
FastAPI on the same origin. OpenAPI is the contract; TypeScript types are generated.

## Alternatives considered
- **Next.js full-stack (route handlers + Node backend):** would split security logic
  across two languages or push Python-native ML/ingestion work into Node.
- **Separate microservices (ingestion, retrieval, API):** distributed-system costs
  (deploys, contracts, tracing, consistency) without independent scaling needs.
- **Django:** heavier ORM/admin conventions we don't need; weaker async story for
  provider-bound requests.

## Why this option
Single place for policy; shared Pydantic types; async I/O where the Ask path is
provider-bound; smallest operational surface.

## Consequences
- Module boundaries must be enforced by convention + import-linter, not by network.
- Next.js holds no secrets or rules; compromise of the web tier is bounded by the
  user's own session.
- Two toolchains (uv, pnpm) in one repo.

## Reconsider when
Ingestion compute needs differ by an order of magnitude from API needs, or separate
teams own ingestion and serving.
