"""Operational metrics (Prometheus text format), served on a separate listener.

What they are for: diagnosing a running deployment -- request rates, latencies, answer
outcomes, provider failures, database outages, sync and job results -- without reading
content. They complement the structured logs (one event per request, Ask, sync and job)
and the per-Ask query traces; none of them replaces the other.

Rules (security-model §10, SEC-8, SEC-14):

* **Bounded labels only.** Every label value comes from a closed set in this module
  (``_bounded``): HTTP method, the *route template* (``/v1/traces/{trace_id}``, never
  the raw path), status codes, answer statuses and reasons, provider implementation
  names, fixed error and rejection codes, sync modes and statuses. Anything outside a
  set is recorded as ``other``.
* **No identifiers or content.** No organization, membership, user, trace, document or
  source IDs; no question, answer, prompt or document text; no account, source or model
  names chosen by customers; no secrets. Per-organization numbers exist only where they
  already are -- in each organization's own traces and sync runs.
* **Not on the application port.** ``LATTICE_METRICS_ADDRESS`` (``host:port``) starts
  a separate listener; unset, nothing listens. The web app proxies ``/api/*`` to the
  API port only, so metrics are never reachable through it. Bind the listener to an
  internal interface; it has no authentication.

One registry per process (the API and the worker each serve their own). Run one
process per container: a multi-process server would need Prometheus' multiprocess mode.
"""

import logging
from collections.abc import Iterable
from typing import Final

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector

from lattice.config import parse_host_port
from lattice.log import log_event

REGISTRY: Final = CollectorRegistry(auto_describe=True)
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

OTHER: Final = "other"
_log = logging.getLogger(__name__)

# --- closed label sets -----------------------------------------------------------------

METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"})
ANSWER_STATUSES: Final = frozenset(
    {
        "answered",
        "partial",
        "insufficient_evidence",
        "evidence_only",
        "ambiguous",
        "unsupported_request",
        "failed",
    }
)
ANSWER_REASONS: Final = frozenset(
    {
        "none",
        "action_request",
        "claims_removed",
        "claims_unverified",
        "citation_validation_failed",
        "egress_policy",
        "generation_invalid",
        "generation_unavailable",
        "incomplete",
        "missing_referent",
        "multiple_accounts",
        "no_relevant_evidence",
        "provider_unavailable",
    }
)
# Implementation names (lattice.providers.generation_factory.SUPPORTED + test doubles).
PROVIDERS: Final = frozenset({"none", "fake", "ollama", "gemini", "local", "openai"})
GENERATION_OUTCOMES: Final = frozenset(
    {
        "ok",
        "schema_invalid",
        "timeout",
        "deadline_exceeded",
        "network",
        "unauthorized",
        "rate_limited",
        "quota_exhausted",
        "not_found",
        "provider_error",
        "refused",
        "truncated",
        "malformed_response",
    }
)
# lattice.answering.policy reason codes, plus "invoked" and the operator kill switch.
GENERATION_DECISIONS: Final = frozenset(
    {
        "invoked",
        "not_invoked",
        "generation_disabled",
        "not_configured",
        "provider_disabled",
        "provider_unsupported",
        "endpoint_invalid",
        "refused_in_production",
        "credential_unavailable",
        "deployment_unknown",
        "provider_deployment_mismatch",
        "deployment_not_allowed",
        "provider_unapproved",
        "provider_data_use",
        "policy_unrecognised",
        "question_classification",
    }
)
REJECTION_CODES: Final = frozenset(
    {
        "claim_too_long",
        "conflict_invalid",
        "malformed_claim",
        "no_citations",
        "no_shared_terms",
        "quote_not_found",
        "quote_too_long",
        "quote_too_short",
        "too_many_citations",
        "unknown_handle",
        "unsafe_content",
        "unsupported_number",
    }
)
SEMANTIC_OUTCOMES: Final = frozenset(
    {
        "matched",
        "no_matches",
        "policy_excluded",
        "provider_failed",
        "not_configured",
        "incompatible_space",
    }
)
AUTH_REJECTIONS: Final = frozenset(
    {
        "missing",
        "malformed_token",
        "unknown_session",
        "revoked",
        "expired",
        "idle_timeout",
        "inactive_membership_user_or_org",
        "context_unavailable",
    }
)
# OIDC sign-in outcomes (lattice.identity.oidc); never which account or organization.
SSO_OUTCOMES: Final = frozenset(
    {
        "started",
        "succeeded",
        "unknown_connection",
        "invalid_state",
        "provider_error",
        "token_invalid",
        "not_provisioned",
        "inactive",
        "rate_limited",
    }
)
SCIM_OPERATIONS_SET: Final = frozenset(
    {"create", "read", "list", "replace", "patch", "delete", "metadata", "unauthenticated"}
)
SYNC_MODES: Final = frozenset({"full", "acl_only", "reindex"})
SYNC_STATUSES: Final = frozenset({"succeeded", "partial", "failed", "aborted"})
SYNC_STAGES: Final = frozenset(
    {"read", "parse", "validate", "normalize", "map_access", "chunk", "embed", "persist"}
)
JOB_KINDS: Final = frozenset({"source_sync"})
JOB_OUTCOMES: Final = frozenset({"succeeded", "failed", "retry", "abandoned"})


def bounded(value: str | None, allowed: Iterable[str], *, none: str = "none") -> str:
    """``value`` if it is in the closed set, ``none`` for None, else ``other``."""
    if value is None:
        return none
    return value if value in allowed else OTHER


def outcome_of(code: str | None) -> str:
    """``generation_failed:timeout`` -> ``timeout`` (bounded)."""
    if code is None:
        return "ok"
    return bounded(code.removeprefix("generation_failed:"), GENERATION_OUTCOMES)


# --- metrics -------------------------------------------------------------------------------

_LATENCY = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)
_LONG = (0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 900.0, 1800.0)

HTTP_REQUESTS = Counter(
    "lattice_http_requests_total",
    "HTTP requests by method, route template and status code.",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "lattice_http_request_duration_seconds",
    "HTTP request duration by method and route template.",
    ["method", "route"],
    buckets=_LATENCY,
    registry=REGISTRY,
)
HTTP_IN_FLIGHT = Gauge(
    "lattice_http_requests_in_flight", "HTTP requests being served.", registry=REGISTRY
)
REQUESTS_REJECTED = Counter(
    "lattice_http_requests_rejected_total",
    "Requests refused before reaching a route (csrf, too_large).",
    ["reason"],
    registry=REGISTRY,
)
AUTH_REJECTED = Counter(
    "lattice_auth_rejections_total",
    "Requests refused for want of a usable session, by fixed reason.",
    ["reason"],
    registry=REGISTRY,
)
DEPENDENCY_FAILURES = Counter(
    "lattice_dependency_failures_total",
    "A dependency (the database) was unavailable, by where it was noticed.",
    ["dependency", "check"],
    registry=REGISTRY,
)
RATE_LIMITED = Counter(
    "lattice_rate_limited_total",
    "Requests refused by a rate or concurrency limit, by bucket (a closed set).",
    ["bucket"],
    registry=REGISTRY,
)
SSO_SIGN_INS = Counter(
    "lattice_sso_sign_ins_total",
    "OIDC sign-in attempts by fixed outcome.",
    ["outcome"],
    registry=REGISTRY,
)
SCIM_OPERATIONS = Counter(
    "lattice_scim_operations_total",
    "SCIM requests by operation and HTTP status class.",
    ["operation", "status"],
    registry=REGISTRY,
)
SECURITY_INVARIANT_VIOLATIONS = Counter(
    "lattice_security_invariant_violations_total",
    "Authorization or response invariants that stopped a request. Must stay 0: alert.",
    ["check"],
    registry=REGISTRY,
)
ASKS = Counter(
    "lattice_asks_total",
    "Ask outcomes by answer status and reason.",
    ["status", "reason"],
    registry=REGISTRY,
)
ASK_DURATION = Histogram(
    "lattice_ask_duration_seconds",
    "End-to-end Ask pipeline duration by answer status.",
    ["status"],
    buckets=_LATENCY,
    registry=REGISTRY,
)
RETRIEVAL_DURATION = Histogram(
    "lattice_retrieval_duration_seconds",
    "Retrieval duration inside Ask.",
    buckets=_LATENCY,
    registry=REGISTRY,
)
SEMANTIC_OUTCOMES_TOTAL = Counter(
    "lattice_retrieval_semantic_total",
    "Semantic channel outcome per Ask (policy_excluded and provider_failed degrade to "
    "exact + lexical).",
    ["outcome"],
    registry=REGISTRY,
)
GENERATION_DECISIONS_TOTAL = Counter(
    "lattice_generation_decisions_total",
    "Whether generation was invoked, or the fixed reason it was not.",
    ["decision"],
    registry=REGISTRY,
)
GENERATION_CALLS = Counter(
    "lattice_generation_calls_total",
    "Generation provider calls by implementation and outcome.",
    ["provider", "outcome"],
    registry=REGISTRY,
)
GENERATION_DURATION = Histogram(
    "lattice_generation_call_duration_seconds",
    "Generation call duration by implementation.",
    ["provider"],
    buckets=_LONG,
    registry=REGISTRY,
)
CLAIMS_REJECTED = Counter(
    "lattice_claims_rejected_total",
    "Model claims rejected by citation validation, by rejection code.",
    ["code"],
    registry=REGISTRY,
)
SYNC_RUNS = Counter(
    "lattice_sync_runs_total",
    "Source sync runs by mode and final status.",
    ["mode", "status"],
    registry=REGISTRY,
)
SYNC_RECORD_FAILURES = Counter(
    "lattice_sync_record_failures_total",
    "Records that failed a sync, by pipeline stage.",
    ["stage"],
    registry=REGISTRY,
)
SYNC_DURATION = Histogram(
    "lattice_sync_duration_seconds",
    "Source sync duration by mode.",
    ["mode"],
    buckets=_LONG,
    registry=REGISTRY,
)
JOBS = Counter(
    "lattice_jobs_total",
    "Background jobs finished by kind and outcome (retry: will run again).",
    ["kind", "outcome"],
    registry=REGISTRY,
)
JOB_DURATION = Histogram(
    "lattice_job_duration_seconds",
    "Background job duration by kind.",
    ["kind"],
    buckets=_LONG,
    registry=REGISTRY,
)
JOBS_WAITING = Gauge(
    "lattice_jobs_waiting",
    "Jobs queued and due, across organizations (worker view).",
    registry=REGISTRY,
)
WORKER_POLLS = Counter(
    "lattice_worker_polls_total",
    "Worker queue polls by result (claimed, empty, error).",
    ["result"],
    registry=REGISTRY,
)
MAINTENANCE_ROWS = Counter(
    "lattice_maintenance_rows_total",
    "Rows removed by maintenance tasks.",
    ["task"],
    registry=REGISTRY,
)
WORKER_HEARTBEAT = Gauge(
    "lattice_worker_heartbeat_timestamp_seconds",
    "Unix time of the worker's last heartbeat (each loop iteration and lease renewal).",
    registry=REGISTRY,
)
CLOCK_SKEW = Gauge(
    "lattice_clock_skew_seconds",
    "This process's clock minus the database's clock (API readiness checks, worker polls).",
    registry=REGISTRY,
)
BUILD_INFO = Gauge(
    "lattice_build_info",
    "Always 1; the labels identify the running build (internal listener only).",
    ["revision", "version", "schema_revision"],
    registry=REGISTRY,
)


def serve(address: str) -> tuple[object, object]:
    """Start the metrics listener on ``host:port``; returns (server, thread)."""
    host, port = parse_host_port(address)
    server, thread = start_http_server(port, addr=host, registry=REGISTRY)
    log_event(_log, logging.INFO, "metrics.listening", port=server.server_port)
    return server, thread
