"""Calls into the 0021 fenced functions and their refusals (ADR-017, P12.5).

Each function refuses with SQLSTATE ``LA001`` and a fixed reason as the message; this
module maps the reason to the API's error (a status, the field, the issue). Nothing from
the database other than the fixed reason reaches a caller."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.errors import ErrorCode, ErrorDetail, LatticeError

FENCED_SQLSTATE: Final = "LA001"

_REFUSALS: Final[dict[str, tuple[ErrorCode, str, str]]] = {
    # reason: (error code, field, issue)
    "policy_changed": (ErrorCode.CONFLICT, "policy_version", "changed"),
    "credential_changed": (ErrorCode.CONFLICT, "expected_credential_id", "changed"),
    "provider_exists": (ErrorCode.CONFLICT, "model", "provider_exists"),
    "grant_consumed": (ErrorCode.CONFLICT, "step_up", "grant_consumed"),
    "grant_expired": (ErrorCode.CONFLICT, "step_up", "grant_expired"),
    "grant_mismatch": (ErrorCode.CONFLICT, "step_up", "grant_mismatch"),
    "grant_invalid": (ErrorCode.CONFLICT, "step_up", "grant_invalid"),
    "session_ended": (ErrorCode.CONFLICT, "step_up", "session_ended"),
    "step_up_required": (ErrorCode.VALIDATION_FAILED, "change", "step_up_required"),
    "change_invalid": (ErrorCode.VALIDATION_FAILED, "change", "invalid"),
    "no_change": (ErrorCode.VALIDATION_FAILED, "change", "no_change"),
    "deployment_managed": (ErrorCode.VALIDATION_FAILED, "change", "deployment_managed"),
    "data_terms_fixed": (ErrorCode.VALIDATION_FAILED, "change", "data_terms_fixed"),
    "model_invalid": (ErrorCode.VALIDATION_FAILED, "model", "invalid"),
    "provider_unsupported": (ErrorCode.VALIDATION_FAILED, "provider", "unsupported"),
    "target_not_registered": (ErrorCode.VALIDATION_FAILED, "target_id", "not_registered"),
    "endpoint_not_https": (ErrorCode.VALIDATION_FAILED, "target_id", "not_https"),
    "endpoint_fixed": (ErrorCode.VALIDATION_FAILED, "target_id", "endpoint_fixed"),
    "trust_class_change": (ErrorCode.VALIDATION_FAILED, "target_id", "trust_class_change"),
    "credential_not_used": (ErrorCode.VALIDATION_FAILED, "provider", "credential_not_used"),
    "data_use_invalid": (ErrorCode.VALIDATION_FAILED, "data_use", "invalid"),
    "secret_invalid": (ErrorCode.VALIDATION_FAILED, "api_key", "invalid"),
}


def refusal(reason: str) -> LatticeError:
    if reason in ("not_administrator", "organization_inactive"):
        return LatticeError(ErrorCode.PERMISSION_DENIED)
    if reason == "provider_not_found":
        return LatticeError(ErrorCode.NOT_FOUND)
    code, field, issue = _REFUSALS.get(reason, (ErrorCode.VALIDATION_FAILED, "change", reason))
    return LatticeError(code, details=[ErrorDetail(field=field, issue=issue)])


def invalid(field: str, issue: str) -> LatticeError:
    return LatticeError(
        ErrorCode.VALIDATION_FAILED, details=[ErrorDetail(field=field, issue=issue)]
    )


def fenced_reason(exc: DBAPIError) -> str | None:
    """The fixed reason of a fenced function's refusal, or None for any other error."""
    orig = exc.orig
    for candidate in (orig, getattr(orig, "__cause__", None)):
        if candidate is not None and getattr(candidate, "sqlstate", None) == FENCED_SQLSTATE:
            message = getattr(candidate, "message", None) or str(candidate)
            return str(message).strip().splitlines()[0]
    return None


class Refused(Exception):
    """A fenced function refused; ``reason`` is its fixed code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@asynccontextmanager
async def fenced() -> AsyncIterator[None]:
    """Raise a fenced refusal as ``Refused`` (any other database error propagates)."""
    try:
        yield
    except DBAPIError as exc:
        reason = fenced_reason(exc)
        if reason is not None:
            raise Refused(reason) from None
        raise


async def call(session: AsyncSession, sql: str, params: dict[str, Any]) -> Any:
    """One fenced function call in a savepoint: its value, or ``Refused``."""
    async with fenced(), session.begin_nested():
        return (await session.execute(text(sql), params)).scalar_one()
