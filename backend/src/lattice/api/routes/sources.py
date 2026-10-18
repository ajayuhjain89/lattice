"""Sources and requested syncs (Phase 8). Thin: parse, call one service, map the result.

* ``GET /v1/sources`` -- any member: the sources they can read from, with counts inside
  their authorization predicate (security-model §10).
* ``GET /v1/admin/sources`` -- administrators: every source with aggregate totals, the
  last run and its failure codes, and the latest requested sync.
* ``GET /v1/admin/sources/{id}/failures`` -- administrators: the latest run's failed
  records, as templated codes with an identifier only for documents they can read.
* ``POST /v1/admin/sources/{id}/sync`` -- administrators: queue a sync (202); while one
  is queued or running, the same job is returned (200).

Phase 12 (P12.6): customer-operable export sources, administrators only, the capability
checked before anything is read (a source of another organization answers exactly as a
random ID):

* ``POST /v1/admin/sources`` -- create an export source (connector type, key, name,
  defaults, authority, account scoping, reason; audited).
* ``GET /v1/admin/sources/{id}`` -- the source record: settings, current import and its
  check, the latest upload, the latest job, the next action, the upload limits.
* ``POST …/{id}/changes/preview`` and ``PATCH …/{id}`` -- edit safe settings (version,
  reason; a widening change must be acknowledged; applies at the next sync).
* ``POST …/{id}/pause`` / ``…/resume`` -- version and reason; resuming starts nothing.
* ``GET …/{id}/disconnect-preview`` and ``POST …/{id}/disconnect`` -- the purge, with
  counts, the key typed and a reason.
* ``POST …/{id}/imports`` -- the streamed export upload (its own body limit and
  deadline; a zip, or for Tickets one JSON Lines page); ``POST …/{id}/check`` -- check the
  current import again (the dry run, as a job).
"""

import asyncio
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from lattice.api.deps import CurrentPrincipal, Limits
from lattice.authz import resolve_authorization_context
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorDetail, ErrorResponse, LatticeError
from lattice.identity.sessions import Principal
from lattice.imports.limits import ImportLimits
from lattice.imports.store import ImportStore
from lattice.jobs.queue import (
    ImportInvalid,
    ImportRequired,
    JobActive,
    JobView,
    SourceNotActive,
    SourceNotFound,
    request_check,
    request_sync,
)
from lattice.sources import admin as source_admin
from lattice.sources.admin import refusal
from lattice.sources.admin_models import (
    DisconnectPreview,
    DisconnectRequest,
    DisconnectResult,
    NewSourceRequest,
    NextAction,
    SourceChangePreview,
    SourceChangeRequest,
    SourceDetail,
    UploadResult,
    VersionedRequest,
)
from lattice.sources.status import admin_failures, admin_sources, member_sources
from lattice.sources.uploads import UploadContext, receive_upload

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
    kind: Literal["source_sync", "import_check"] = "source_sync"
    mode: str
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    sync_run_id: uuid.UUID | None
    import_id: uuid.UUID | None = None


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
    managed_by: Literal["operator", "organization"] = Field(
        description="operator: an operator's sources file configures it; organization: "
        "configured here and fed by uploaded exports."
    )
    version: int
    import_status: str | None = Field(description="The current import's status, if any.")
    rejected_upload_code: str | None = Field(
        description="The latest upload's refusal code, when it is newer than the current import."
    )
    next_action: NextAction


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
    except ImportRequired:
        raise refusal("import_required") from None
    except ImportInvalid:
        raise refusal("import_invalid") from None
    except JobActive:
        raise refusal("job_active") from None
    if not created:
        response.status_code = 200
    request.state.log_fields = {
        **getattr(request.state, "log_fields", {}),
        "job_id": str(job.id),
    }
    return _job(job)


# --- P12.6: customer-operable export sources -------------------------------------------------

_CHANGE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    409: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
}
_CSRF = "Requires the X-Lattice-Request: 1 header."


async def _admin_write(principal: Principal, limits: Limits) -> None:
    """Capability first (403 identical for any target), then the write limit."""
    _require_admin(principal)
    await limits.limiter.hit(
        principal.organization_id, principal.membership_id, limits.access_write
    )


def _store(request: Request) -> ImportStore | None:
    store: ImportStore | None = request.app.state.import_store
    return store


def _limits(request: Request) -> ImportLimits:
    limits: ImportLimits = request.app.state.import_limits
    return limits


async def _detail(request: Request, principal: Principal, source_id: uuid.UUID) -> SourceDetail:
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        found = await source_admin.source_detail(
            session,
            principal.organization_id,
            source_id,
            limits=_limits(request),
            store_available=_store(request) is not None,
        )
    if found is None:
        raise LatticeError(ErrorCode.NOT_FOUND)
    return found


@router.post(
    "/v1/admin/sources",
    response_model=SourceDetail,
    status_code=201,
    responses=_CHANGE_ERRORS,
    summary="Create an export source, with a reason (administrators; audited)",
    description="Documents (Markdown export), Tickets (JSON Lines export) or a Slack "
    "workspace export, configured here and fed by uploaded exports. It holds nothing until "
    "an import is synced and grants nobody anything. The key is immutable; 409 when another "
    f"source has it. {_CSRF}",
)
async def post_source(
    body: NewSourceRequest, request: Request, principal: CurrentPrincipal, limits: Limits
) -> SourceDetail:
    await _admin_write(principal, limits)
    created = await source_admin.create_source(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        request=body,
    )
    return await _detail(request, principal, created.id)


@router.get(
    "/v1/admin/sources/{source_id}",
    response_model=SourceDetail,
    responses=_ERRORS,
    summary="A source's record: settings, imports, latest job, next action (administrators)",
    description="Operational metadata only: no document, ticket or message content, no file "
    "names from imports, no private channel names.",
)
async def get_source(
    source_id: uuid.UUID, request: Request, principal: CurrentPrincipal
) -> SourceDetail:
    _require_admin(principal)
    return await _detail(request, principal, source_id)


@router.post(
    "/v1/admin/sources/{source_id}/changes/preview",
    response_model=SourceChangePreview,
    responses=_CHANGE_ERRORS,
    summary="Preview a settings change: what changes and whether it widens who can read "
    "records at the next sync (administrators; changes nothing)",
    description=_CSRF,
)
async def post_source_change_preview(
    source_id: uuid.UUID,
    body: SourceChangeRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> SourceChangePreview:
    await _admin_write(principal, limits)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        return await source_admin.preview_change(
            session, principal.organization_id, source_id, body
        )


@router.patch(
    "/v1/admin/sources/{source_id}",
    response_model=SourceDetail,
    responses=_CHANGE_ERRORS,
    summary="Change a source's safe settings, with the version and a reason (administrators; "
    "audited). Applies at the next sync",
    description="A change that makes records readable to more people needs "
    f"acknowledge_widening (preview first). 409 when the source changed. {_CSRF}",
)
async def patch_source(
    source_id: uuid.UUID,
    body: SourceChangeRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> SourceDetail:
    await _admin_write(principal, limits)
    await source_admin.update_source(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        source_id=source_id,
        request=body,
    )
    return await _detail(request, principal, source_id)


async def _pause(
    source_id: uuid.UUID,
    body: VersionedRequest,
    request: Request,
    principal: Principal,
    limits: Limits,
    *,
    paused: bool,
) -> SourceDetail:
    await _admin_write(principal, limits)
    await source_admin.set_paused(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        source_id=source_id,
        request=body,
        paused=paused,
    )
    return await _detail(request, principal, source_id)


@router.post(
    "/v1/admin/sources/{source_id}/pause",
    response_model=SourceDetail,
    responses=_CHANGE_ERRORS,
    summary="Pause a source: requested syncs are refused; its content stays searchable "
    "(administrators; audited)",
    description=_CSRF,
)
async def post_source_pause(
    source_id: uuid.UUID,
    body: VersionedRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> SourceDetail:
    return await _pause(source_id, body, request, principal, limits, paused=True)


@router.post(
    "/v1/admin/sources/{source_id}/resume",
    response_model=SourceDetail,
    responses=_CHANGE_ERRORS,
    summary="Resume a paused source; no sync starts by itself (administrators; audited)",
    description=_CSRF,
)
async def post_source_resume(
    source_id: uuid.UUID,
    body: VersionedRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> SourceDetail:
    return await _pause(source_id, body, request, principal, limits, paused=False)


@router.get(
    "/v1/admin/sources/{source_id}/disconnect-preview",
    response_model=DisconnectPreview,
    responses=_ERRORS,
    summary="What disconnecting would remove: counts only (administrators)",
)
async def get_disconnect_preview(
    source_id: uuid.UUID, request: Request, principal: CurrentPrincipal
) -> DisconnectPreview:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        return await source_admin.disconnect_preview(session, principal.organization_id, source_id)


@router.post(
    "/v1/admin/sources/{source_id}/disconnect",
    response_model=DisconnectResult,
    responses=_CHANGE_ERRORS,
    summary="Disconnect and purge a source, with its key typed and a reason (administrators; "
    "audited)",
    description="Its documents stop being readable at once, then are deleted with their "
    "chunks, grants, synced groups, runs, account mappings and imports. Accounts, other "
    "sources, traces (re-authorized on read) and the audit trail stay. Refused while a sync "
    f"runs (409). {_CSRF}",
)
async def post_source_disconnect(
    source_id: uuid.UUID,
    body: DisconnectRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> DisconnectResult:
    await _admin_write(principal, limits)
    return await source_admin.disconnect(
        _db(request),
        _store(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        source_id=source_id,
        request=body,
    )


@router.post(
    "/v1/admin/sources/{source_id}/imports",
    response_model=UploadResult,
    status_code=201,
    responses={
        **_CHANGE_ERRORS,
        413: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="Upload an export as the source's next import (administrators; streamed)",
    description="The body is the file itself (not a form): a zip archive, or for a Tickets "
    "source one JSON Lines page. Its format is decided by its content; no file name is "
    "read. It is validated entry by entry within the deployment's limits and, if valid, "
    "becomes the source's current import (the previous one is superseded) and is checked; "
    "nothing syncs until a sync is requested. A refused upload changes nothing (422 with "
    f"its code; 413 when too large). One upload per source at a time (409). {_CSRF}",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/zip": {"schema": {"type": "string", "format": "binary"}},
                "application/x-ndjson": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
async def post_source_import(
    source_id: uuid.UUID, request: Request, principal: CurrentPrincipal, limits: Limits
) -> UploadResult:
    await _admin_write(principal, limits)
    uploads: asyncio.Semaphore = request.app.state.import_uploads
    if uploads.locked():
        raise LatticeError(
            ErrorCode.RATE_LIMITED,
            "Too many uploads are being received right now. Try again shortly.",
            details=[ErrorDetail(field="import", issue="uploads_busy")],
        )
    declared = request.headers.get("content-length")
    settings = request.app.state.settings
    async with uploads:
        return await receive_upload(
            UploadContext(
                db=_db(request),
                store=_store(request),
                limits=_limits(request),
                timeout_seconds=settings.import_upload_timeout_seconds,
            ),
            organization_id=principal.organization_id,
            actor_membership_id=principal.membership_id,
            source_id=source_id,
            body=request.stream(),
            declared_length=int(declared) if declared and declared.isdigit() else None,
        )


@router.post(
    "/v1/admin/sources/{source_id}/check",
    response_model=JobOut,
    status_code=202,
    responses={**_CHANGE_ERRORS, 200: {"model": JobOut, "description": "A check is queued"}},
    summary="Check the current import again (the dry run of the sync pipeline, as a job; "
    "writes nothing, calls no provider)",
    description=f"Useful after mapping account references. {_CSRF}",
)
async def post_source_check(
    source_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: CurrentPrincipal,
    limits: Limits,
) -> JobOut:
    await _admin_write(principal, limits)
    try:
        job, created = await request_check(
            _db(request),
            organization_id=principal.organization_id,
            membership_id=principal.membership_id,
            source_id=source_id,
        )
    except SourceNotFound:
        raise LatticeError(ErrorCode.NOT_FOUND) from None
    except ImportRequired:
        raise refusal("import_required") from None
    except JobActive:
        raise refusal("job_active") from None
    if not created:
        response.status_code = 200
    return _job(job)
