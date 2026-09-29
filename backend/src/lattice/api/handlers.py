"""Exception handlers mapping every failure to the error envelope.

Beyond ``LatticeError`` and validation errors:

* **The database is unreachable or too slow** (connection refused or lost, the pool's
  wait for a connection, a statement cancelled by ``statement_timeout``): 503
  ``service_unavailable`` with ``Retry-After``, logged as ``db.unavailable`` with the
  exception type only. A SQL *bug* (a missing table, a constraint) stays a 500
  ``internal_error``: it is not transient, and retrying would not help.
* **An authorization invariant tripped** (E4, or the classification re-check of the
  egress decision): 500 ``security_invariant_violated``. The ``authz`` module has
  already logged ``security.invariant_violated`` with the details; the response says
  nothing more than the fixed message, and the counter
  ``lattice_security_invariant_violations_total`` is the alerting signal.
"""

import logging

from asyncpg.exceptions import QueryCanceledError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from lattice import metrics
from lattice.api.responses import error_json, error_response
from lattice.authz import AuthorizationContextUnavailable, AuthorizationInvariantViolation
from lattice.config import Settings
from lattice.errors import DEFAULT_MESSAGE, ErrorCode, ErrorDetail, LatticeError
from lattice.log import log_event

logger = logging.getLogger("lattice.http")

# 400: FastAPI could not read the body (for example the client went away mid-upload).
_HTTP_STATUS_CODES = {
    400: ErrorCode.VALIDATION_FAILED,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
}


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def database_unavailable(exc: BaseException) -> bool:
    """Connectivity, pool exhaustion or a cancelled (timed-out) statement."""
    if isinstance(exc, (OperationalError, InterfaceError, PoolTimeoutError)):
        return True
    if isinstance(exc, DBAPIError):
        # 57014 query_canceled: statement_timeout (the driver error may be wrapped).
        orig = exc.orig
        return isinstance(orig, QueryCanceledError) or "57014" in (
            getattr(orig, "sqlstate", None),
            getattr(orig, "pgcode", None),
            getattr(getattr(orig, "__cause__", None), "sqlstate", None),
        )
    return isinstance(exc, (ConnectionError, TimeoutError))


def register_exception_handlers(app: FastAPI, settings: Settings) -> None:
    async def lattice_error(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, LatticeError)  # noqa: S101 - registered for this type only
        if isinstance(exc, AuthorizationContextUnavailable):
            metrics.AUTH_REJECTED.labels(reason="context_unavailable").inc()
        response = error_response(exc, _request_id(request))
        if exc.code is ErrorCode.AUTHENTICATION_REQUIRED and (
            settings.session_cookie_name in request.cookies
        ):
            # Stop the browser from re-sending a dead session token.
            response.delete_cookie(
                settings.session_cookie_name,
                path="/",
                secure=settings.session_cookie_secure,
                httponly=True,
                samesite="lax",
            )
        return response

    async def validation_error(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, RequestValidationError)  # noqa: S101
        # Report where and what kind of problem -- never the submitted value.
        details = [
            ErrorDetail(
                field=".".join(str(part) for part in err.get("loc", ())[1:]) or "body",
                issue=str(err.get("type", "invalid")),
            )
            for err in exc.errors()
        ]
        return error_json(
            ErrorCode.VALIDATION_FAILED,
            DEFAULT_MESSAGE[ErrorCode.VALIDATION_FAILED],
            _request_id(request),
            details,
        )

    async def http_error(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, StarletteHTTPException)  # noqa: S101
        code = _HTTP_STATUS_CODES.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        return error_json(
            code,
            DEFAULT_MESSAGE[code],
            _request_id(request),
            headers=dict(exc.headers) if exc.headers else None,
        )

    async def database_error(request: Request, exc: Exception) -> Response:
        if not database_unavailable(exc):
            raise exc  # a bug: the outer middleware renders internal_error
        metrics.DEPENDENCY_FAILURES.labels(dependency="database", check="request").inc()
        log_event(logger, logging.WARNING, "db.unavailable", error_type=type(exc).__name__)
        return error_json(
            ErrorCode.SERVICE_UNAVAILABLE,
            DEFAULT_MESSAGE[ErrorCode.SERVICE_UNAVAILABLE],
            _request_id(request),
        )

    async def invariant_violation(request: Request, exc: Exception) -> Response:
        metrics.SECURITY_INVARIANT_VIOLATIONS.labels(check="authorization").inc()
        return error_json(
            ErrorCode.SECURITY_INVARIANT_VIOLATED,
            DEFAULT_MESSAGE[ErrorCode.SECURITY_INVARIANT_VIOLATED],
            _request_id(request),
        )

    app.add_exception_handler(LatticeError, lattice_error)
    app.add_exception_handler(DBAPIError, database_error)
    app.add_exception_handler(PoolTimeoutError, database_error)
    app.add_exception_handler(ConnectionError, database_error)
    app.add_exception_handler(TimeoutError, database_error)
    app.add_exception_handler(AuthorizationInvariantViolation, invariant_violation)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(StarletteHTTPException, http_error)
