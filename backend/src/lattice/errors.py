"""Error taxonomy and the single API error envelope (docs/architecture.md §7).

Every API failure is rendered as::

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}

``message`` is always a fixed, user-safe sentence chosen by the code that raises the
error. Exception text from libraries (database drivers, SQL, file paths) never reaches
a response.
"""

from enum import StrEnum
from http import HTTPStatus

from pydantic import BaseModel


class ErrorCode(StrEnum):
    AUTHENTICATION_REQUIRED = "authentication_required"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    CSRF_REJECTED = "csrf_rejected"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    VALIDATION_FAILED = "validation_failed"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INTERNAL_ERROR = "internal_error"
    # Documented for later phases; defined now so the contract is stable.
    SOURCE_UNAVAILABLE = "source_unavailable"
    RETRIEVAL_FAILED = "retrieval_failed"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    GENERATION_INVALID = "generation_invalid"
    SECURITY_INVARIANT_VIOLATED = "security_invariant_violated"


HTTP_STATUS: dict[ErrorCode, HTTPStatus] = {
    ErrorCode.AUTHENTICATION_REQUIRED: HTTPStatus.UNAUTHORIZED,
    ErrorCode.AUTHENTICATION_FAILED: HTTPStatus.UNAUTHORIZED,
    ErrorCode.PERMISSION_DENIED: HTTPStatus.FORBIDDEN,
    ErrorCode.CSRF_REJECTED: HTTPStatus.FORBIDDEN,
    ErrorCode.NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.METHOD_NOT_ALLOWED: HTTPStatus.METHOD_NOT_ALLOWED,
    ErrorCode.VALIDATION_FAILED: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.SERVICE_UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    ErrorCode.INTERNAL_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,
    ErrorCode.SOURCE_UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    ErrorCode.RETRIEVAL_FAILED: HTTPStatus.INTERNAL_SERVER_ERROR,
    ErrorCode.PROVIDER_UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    ErrorCode.GENERATION_INVALID: HTTPStatus.BAD_GATEWAY,
    ErrorCode.SECURITY_INVARIANT_VIOLATED: HTTPStatus.INTERNAL_SERVER_ERROR,
}

DEFAULT_MESSAGE: dict[ErrorCode, str] = {
    ErrorCode.AUTHENTICATION_REQUIRED: "Sign in to continue.",
    ErrorCode.AUTHENTICATION_FAILED: "Sign-in failed.",
    ErrorCode.PERMISSION_DENIED: "You don't have permission to do this.",
    ErrorCode.CSRF_REJECTED: "The request was rejected because it did not come from Lattice.",
    ErrorCode.NOT_FOUND: "Not found.",
    ErrorCode.METHOD_NOT_ALLOWED: "This method is not allowed here.",
    ErrorCode.VALIDATION_FAILED: "The request is invalid.",
    ErrorCode.SERVICE_UNAVAILABLE: "Lattice is temporarily unavailable.",
    ErrorCode.INTERNAL_ERROR: (
        "Something went wrong. Try again, and contact support if it persists."
    ),
    ErrorCode.SOURCE_UNAVAILABLE: "A source could not be read.",
    ErrorCode.RETRIEVAL_FAILED: "Search failed.",
    ErrorCode.PROVIDER_UNAVAILABLE: "The AI provider is unavailable.",
    ErrorCode.GENERATION_INVALID: "The AI provider returned an invalid response.",
    ErrorCode.SECURITY_INVARIANT_VIOLATED: "The request was stopped by a security check.",
}


class ErrorDetail(BaseModel):
    """One problem with the request. Never contains the submitted value."""

    field: str
    issue: str


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    request_id: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class LatticeError(Exception):
    """An expected, classified failure. Raise it; the API layer renders the envelope."""

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        self.code = code
        self.message = message or DEFAULT_MESSAGE[code]
        self.details = details
        super().__init__(code.value)

    @property
    def status(self) -> HTTPStatus:
        return HTTP_STATUS[self.code]
