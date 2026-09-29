"""Rendering of the error envelope, shared by middleware and exception handlers."""

from starlette.responses import JSONResponse

from lattice.errors import ErrorBody, ErrorCode, ErrorDetail, ErrorResponse, LatticeError


def error_response(
    error: LatticeError, request_id: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return error_json(error.code, error.message, request_id, error.details, headers)


def error_json(
    code: ErrorCode,
    message: str,
    request_id: str,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, request_id=request_id, details=details)
    )
    status = LatticeError(code).status
    if status == 503:
        # A transient condition: tell well-behaved clients when to try again.
        headers = {"Retry-After": "5", **(headers or {})}
    return JSONResponse(
        body.model_dump(mode="json", exclude_none=True), status_code=status, headers=headers
    )
