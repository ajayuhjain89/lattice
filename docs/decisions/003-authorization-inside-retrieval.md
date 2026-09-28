# ADR-003: Authorization compiled into retrieval SQL; RLS for tenant isolation; pre-generation invariant

Status: Accepted (proposed in Phase 0; RLS implemented in Phase 1, the SQL predicate and
tripwire in Phase 2, authorization inside every retrieval statement in Phase 5; the
pre-generation check is applied by answering in Phase 6)

## Context
The model must never receive information the user cannot see (SEC-1). Filtering after
retrieval would put forbidden content into application memory and risks mistakes;
filtering after generation is too late.

## Decision
1. Postgres RLS enforces **organization** isolation on every tenant table.
2. A single `authz` module compiles the user's `AuthorizationContext` into a SQL
   predicate that is part of **every** retrieval query (lexical, semantic, identifier,
   direct lookups).
3. A Python reference implementation of the same policy is proven equivalent by
   property-based tests, and is used as a pre-generation **tripwire**: if any selected
   evidence fails it, the request aborts and alerts (it never silently filters).
4. Access metadata lives on documents; chunks inherit via join. Permission changes
   are row updates; nothing is re-embedded.

## Alternatives considered
- **Post-retrieval filtering:** rejected (SEC-3).
- **Per-role/per-group embedding indexes:** multiplies storage, breaks immediate
  revocation.
- **Fine-grained policy entirely in RLS:** structurally attractive (can't forget the
  filter), but harder to explain in traces and access UI, harder to test for
  equivalence, and policy functions inside RLS can defeat index use for vector
  search. Kept as a reconsideration option.
- **External policy engine (OPA/Cedar/SpiceDB):** a second system and network hop for
  a policy that fits in ~20 lines; ReBAC-style engines also need a "list objects"
  answer turned back into SQL.

## Why this option
Unauthorized rows never leave Postgres; the policy is one small, reviewable function
with a proven SQL twin; revocation is immediate.

## Consequences
- Only `authz` may build access SQL. Enforced since Phase 2 by an AST test
  (`backend/tests/test_authz_boundary.py`) rather than an import-linter contract: the
  failure to prevent is access conditions written outside `authz`, which import rules
  cannot see.
- New retrieval paths must use the predicate; the mutation test catches omissions.
- The join chunks→documents is on the hot path; measured.

## Reconsider when
Policy complexity grows beyond what one reviewable function expresses (e.g. deny
rules, relationship chains, delegated sharing), or a forgotten-predicate incident
occurs — then move fine-grained policy into RLS or adopt a policy engine.
