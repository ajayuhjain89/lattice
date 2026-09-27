import logging

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError

from lattice.api.schemas import HealthResponse
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorResponse, LatticeError
from lattice.log import log_event

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get(
    "/v1/health",
    response_model=HealthResponse,
    responses={503: {"model": ErrorResponse}},
    summary="Liveness and database connectivity",
)
async def health(request: Request) -> HealthResponse:
    db: Database = request.app.state.db
    try:
        await db.ping()
    except (SQLAlchemyError, OSError) as exc:
        log_event(
            logger, logging.WARNING, "health.database_unavailable", error_type=type(exc).__name__
        )
        raise LatticeError(ErrorCode.SERVICE_UNAVAILABLE) from None
    return HealthResponse(status="ok", database="ok")
