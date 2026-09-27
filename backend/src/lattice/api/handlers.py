"""Exception handlers mapping every failure to the error envelope."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from lattice.api.responses import error_json, error_response
from lattice.config import Settings
from lattice.errors import DEFAULT_MESSAGE, ErrorCode, ErrorDetail, LatticeError

_HTTP_STATUS_CODES = {404: ErrorCode.NOT_FOUND, 405: ErrorCode.METHOD_NOT_ALLOWED}


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def register_exception_handlers(app: FastAPI, settings: Settings) -> None:
    async def lattice_error(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, LatticeError)  # noqa: S101 - registered for this type only
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

    app.add_exception_handler(LatticeError, lattice_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(StarletteHTTPException, http_error)
