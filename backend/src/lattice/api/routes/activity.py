"""Activity (Phase 8): your own questions; administrators' operational view of the
organization's Asks -- never their content (security-model §8, D-5)."""

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from lattice.api.deps import CurrentPrincipal
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorResponse, LatticeError
from lattice.tracing.activity import MAX_PAGE, organization_activity, own_activity

router = APIRouter(tags=["activity"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE)]


class OwnActivityOut(BaseModel):
    trace_id: uuid.UUID
    created_at: datetime
    status: str
    status_reason: str | None
    question: str


class OwnActivityResponse(BaseModel):
    items: list[OwnActivityOut]


class OperationalActivityOut(BaseModel):
    trace_id: uuid.UUID
    created_at: datetime
    membership_id: uuid.UUID
    member_name: str
    status: str
    status_reason: str | None
    error_code: str | None
    provider: str | None
    model: str | None
    duration_ms: float | None
    generation_calls: int
    input_tokens: int
    output_tokens: int


class OperationalActivityResponse(BaseModel):
    items: list[OperationalActivityOut]


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.get(
    "/v1/activity",
    response_model=OwnActivityResponse,
    responses=_ERRORS,
    summary="Your own recent questions, newest first (open one with /v1/traces/{id})",
)
async def get_own_activity(
    request: Request,
    principal: CurrentPrincipal,
    limit: Limit = 25,
    before: uuid.UUID | None = None,
) -> OwnActivityResponse:
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        items = await own_activity(
            session,
            organization_id=principal.organization_id,
            membership_id=principal.membership_id,
            limit=limit,
            before=before,
        )
    return OwnActivityResponse.model_validate({"items": [asdict(i) for i in items]})


@router.get(
    "/v1/admin/activity",
    response_model=OperationalActivityResponse,
    responses=_ERRORS,
    summary="The organization's Asks as operational rows: no questions, answers, "
    "evidence, counts or scope (administrators)",
)
async def get_organization_activity(
    request: Request,
    principal: CurrentPrincipal,
    limit: Limit = 50,
    before: uuid.UUID | None = None,
) -> OperationalActivityResponse:
    if not principal.is_admin:
        raise LatticeError(ErrorCode.PERMISSION_DENIED)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        items = await organization_activity(
            session, organization_id=principal.organization_id, limit=limit, before=before
        )
    return OperationalActivityResponse.model_validate({"items": [asdict(i) for i in items]})
