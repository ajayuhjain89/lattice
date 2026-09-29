"""Health endpoints. Unauthenticated, so they report states and nothing else: no
versions, revisions, hosts, counts, organizations or error text.

* ``/v1/health/live`` -- the process serves requests (no dependency is touched). For a
  liveness probe: failing it means restart the process.
* ``/v1/health/ready`` -- the database answers and its schema is at the revision this
  build expects. For a readiness probe / load balancer: failing it means send no
  traffic here; restarting will not help.
* ``/v1/health`` -- the Phase 1 check (database connectivity), kept for compatibility.
"""

import logging
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError

from lattice import metrics
from lattice.api.schemas import HealthResponse, LiveResponse, ReadyResponse
from lattice.db.engine import Database
from lattice.db.schema import revision_problem
from lattice.errors import ErrorCode, ErrorResponse, LatticeError
from lattice.log import log_event

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)
_UNAVAILABLE: dict[int | str, dict[str, Any]] = {503: {"model": ErrorResponse}}


def _database_unavailable(check: str, exc: BaseException) -> LatticeError:
    metrics.DEPENDENCY_FAILURES.labels(dependency="database", check=check).inc()
    log_event(logger, logging.WARNING, "health.database_unavailable", error_type=type(exc).__name__)
    return LatticeError(ErrorCode.SERVICE_UNAVAILABLE)


@router.get(
    "/v1/health",
    response_model=HealthResponse,
    responses=_UNAVAILABLE,
    summary="Liveness and database connectivity",
)
async def health(request: Request) -> HealthResponse:
    db: Database = request.app.state.db
    try:
        await db.ping()
    except (SQLAlchemyError, OSError) as exc:
        raise _database_unavailable("health", exc) from None
    return HealthResponse(status="ok", database="ok")


@router.get("/v1/health/live", response_model=LiveResponse, summary="Liveness (no dependencies)")
async def live() -> LiveResponse:
    return LiveResponse(status="ok")


@router.get(
    "/v1/health/ready",
    response_model=ReadyResponse,
    responses=_UNAVAILABLE,
    summary="Readiness: the database answers and its schema matches this build",
)
async def ready(request: Request) -> ReadyResponse:
    db: Database = request.app.state.db
    try:
        revision = await db.schema_revision()
    except (SQLAlchemyError, OSError) as exc:
        raise _database_unavailable("ready", exc) from None
    if revision_problem(revision) is not None:
        metrics.DEPENDENCY_FAILURES.labels(dependency="database", check="schema_revision").inc()
        log_event(logger, logging.WARNING, "health.schema_mismatch")
        raise LatticeError(ErrorCode.SERVICE_UNAVAILABLE)
    return ReadyResponse(status="ready", database="ok", schema_revision="ok")
