"""Permission-aware hybrid retrieval (docs/retrieval-design.md Part I).

A query becomes the best *permitted* evidence, in one consistent authorization
snapshot::

    session → AuthorizationContext → permitted scope
      → exact identifier | lexical (FTS) | semantic (pgvector)   -- each one SQL
        statement carrying the authz predicate, the tenant and the lifecycle conditions
      → E4 tripwire over every candidate → RRF fusion → rerank_v1 → selection
      → evidence (hydrated through the same predicate) → E4 tripwire → result + trace

Unauthorized rows never leave PostgreSQL: there is no retrieve-then-filter step.
Authorization comes only from ``lattice.authz``; this package never builds an access
condition of its own (``tests/test_authz_boundary.py``).

Public entry points: ``retrieve`` (resolves the context from the server-side
session's identifiers) and ``retrieve_with_context`` (inside an already-open snapshot).
"""

from lattice.retrieval.models import (
    DEFAULT_CONFIG,
    Evidence,
    RetrievalConfig,
    RetrievalQuery,
    RetrievalResult,
    RetrievalScope,
    SemanticOutcome,
    VisibleAccount,
)
from lattice.retrieval.service import RetrievalScopeUnavailable, retrieve, retrieve_with_context

__all__ = [
    "DEFAULT_CONFIG",
    "Evidence",
    "RetrievalConfig",
    "RetrievalQuery",
    "RetrievalResult",
    "RetrievalScope",
    "RetrievalScopeUnavailable",
    "SemanticOutcome",
    "VisibleAccount",
    "retrieve",
    "retrieve_with_context",
]
