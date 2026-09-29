"""Sources and requested syncs (Phase 8). Thin: parse, call one service, map the result.

* ``GET /v1/sources`` -- any member: the sources they can read from, with counts inside
  their authorization predicate (security-model §10).
* ``GET /v1/admin/sources`` -- administrators: every source with aggregate totals, the
  last run and its failure codes, and the latest requested sync.
* ``GET /v1/admin/sources/{id}/failures`` -- administrators: the latest run's failed
  records, as templated codes with an identifier only for documents they can read.
* ``POST /v1/admin/sources/{id}/sync`` -- administrators: queue a sync (202); while one
  is queued or running, the same job is returned (200).
"""

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from lattice.api.deps import CurrentPrincipal
from lattice.authz import resolve_authorization_context
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorDetail, ErrorResponse, LatticeError
from lattice.identity.sessions import Principal
from lattice.jobs.queue import JobView, SourceNotActive, SourceNotFound, request_sync
from lattice.sources.status import admin_failures, admin_sources, member_sources

router = APIRouter(tags=["sources"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


class MemberSourceOut(BaseModel):
    id: uuid.UUID
    name: str
    connector_type: str
    readable_documents: int = Field(description="Active documents you can read here.")
    last_synced_at: datetime | None
    last_succeeded_at: datetime | None
    last_run_status: str | None


class MemberSourcesResponse(BaseModel):
    sources: list[MemberSourceOut]


class RunOut(BaseModel):
    id: uuid.UUID
    mode: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    counts: dict[str, int]
    deletion_guard_tripped: bool
    error_code: str | None


class FailureCountOut(BaseModel):
    stage: str
    code: str
    count: int


class JobOut(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    mode: str
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    sync_run_id: uuid.UUID | None


class AdminSourceOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    connector_type: str
    status: str
    account_scoped: bool
    default_sensitivity: str
    authority: str
    documents: int = Field(description="Active documents in the source (aggregate).")
    last_synced_at: datetime | None
    last_succeeded_at: datetime | None
    acl_synced_at: datetime | None
    last_run: RunOut | None
    failure_codes: list[FailureCountOut]
    job: JobOut | None


class AdminSourcesResponse(BaseModel):
    sources: list[AdminSourceOut]


class FailureOut(BaseModel):
    stage: str
    code: str
    params: dict[str, str | int]
    record_kind: Literal["identifier", "opaque"] = Field(
        description="identifier: the record's ID (you can read its document); "
        "opaque: a keyed reference only."
    )
    record: str
    created_at: datetime


class FailuresResponse(BaseModel):
    failures: list[FailureOut]


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["full", "acl_only"] = "full"


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _require_admin(principal: Principal) -> None:
    if not principal.is_admin:
        raise LatticeError(ErrorCode.PERMISSION_DENIED)


def _job(job: JobView) -> JobOut:
    return JobOut.model_validate(asdict(job))


@router.get(
    "/v1/sources",
    response_model=MemberSourcesResponse,
    responses=_ERRORS,
    summary="The sources you can read from, with counts of what you can read",
)
async def get_sources(request: Request, principal: CurrentPrincipal) -> MemberSourcesResponse:
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        ctx = await resolve_authorization_context(
            session,
            organization_id=principal.organization_id,
            membership_id=principal.membership_id,
        )
        found = await member_sources(session, ctx)
    return MemberSourcesResponse.model_validate({"sources": [asdict(s) for s in found]})


@router.get(
    "/v1/admin/sources",
    response_model=AdminSourcesResponse,
    responses=_ERRORS,
    summary="Every source's sync status and aggregate totals (administrators)",
)
async def get_admin_sources(request: Request, principal: CurrentPrincipal) -> AdminSourcesResponse:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        found = await admin_sources(session, principal.organization_id)
    return AdminSourcesResponse.model_validate({"sources": [asdict(s) for s in found]})


@router.get(
    "/v1/admin/sources/{source_id}/failures",
    response_model=FailuresResponse,
    responses=_ERRORS,
    summary="The latest sync's failed records: codes, and identifiers only for documents "
    "you can read (administrators)",
)
async def get_source_failures(
    source_id: uuid.UUID, request: Request, principal: CurrentPrincipal
) -> FailuresResponse:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        ctx = await resolve_authorization_context(
            session,
            organization_id=principal.organization_id,
            membership_id=principal.membership_id,
        )
        found = await admin_failures(session, ctx, source_id)
    if found is None:
        raise LatticeError(ErrorCode.NOT_FOUND)
    return FailuresResponse.model_validate({"failures": [asdict(f) for f in found]})


@router.post(
    "/v1/admin/sources/{source_id}/sync",
    response_model=JobOut,
    status_code=202,
    responses={**_ERRORS, 200: {"model": JobOut, "description": "A sync is already queued"}},
    summary="Request a sync of a source (administrators); the worker runs it",
    description="Idempotent while a sync of the source is queued or running: that job is "
    "returned with 200. Requires the X-Lattice-Request: 1 header.",
)
async def post_source_sync(
    source_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: CurrentPrincipal,
    body: SyncRequest | None = None,
) -> JobOut:
    _require_admin(principal)
    try:
        job, created = await request_sync(
            _db(request),
            organization_id=principal.organization_id,
            membership_id=principal.membership_id,
            source_id=source_id,
            mode=(body or SyncRequest()).mode,
        )
    except SourceNotFound:
        raise LatticeError(ErrorCode.NOT_FOUND) from None
    except SourceNotActive:
        raise LatticeError(
            ErrorCode.VALIDATION_FAILED,
            details=[ErrorDetail(field="source", issue="source_not_active")],
        ) from None
    if not created:
        response.status_code = 200
    request.state.log_fields = {
        **getattr(request.state, "log_fields", {}),
        "job_id": str(job.id),
    }
    return _job(job)
