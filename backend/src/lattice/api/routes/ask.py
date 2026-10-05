"""Ask, evidence and traces (Phase 6). Thin: parse, call one service, map the result.
No SQL, no policy and no prompt text here (architecture §3)."""

import uuid
from typing import Any

from fastapi import APIRouter, Request

from lattice.answering.evidence import get_evidence
from lattice.answering.models import Answer, AskRequest, EvidenceDetail
from lattice.answering.service import AskDependencies, ask
from lattice.api.deps import CurrentPrincipal, Limits
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorResponse, LatticeError
from lattice.tracing.recorder import AdminTrace, AskerTrace, read_trace

router = APIRouter(tags=["ask"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
}
_ASK_ERRORS: dict[int | str, dict[str, Any]] = {**_ERRORS, 429: {"model": ErrorResponse}}


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.post(
    "/v1/ask",
    response_model=Answer,
    responses=_ASK_ERRORS,
    summary="Ask a question; get a grounded answer over the evidence you may read",
    description="Statuses (answered, partial, insufficient_evidence, evidence_only, "
    "ambiguous, unsupported_request, failed) are HTTP 200 outcomes. Every citation "
    "references an item of `evidence`. 429 with `Retry-After` when the member's Ask "
    "limits are exhausted. Requires the X-Lattice-Request: 1 header.",
)
async def post_ask(
    body: AskRequest, request: Request, principal: CurrentPrincipal, limits: Limits
) -> Answer:
    deps: AskDependencies = request.app.state.ask_dependencies
    # Per membership, before retrieval: the limit depends on nothing but the caller.
    async with limits.ask_concurrency.slot(principal.organization_id, principal.membership_id):
        await limits.limiter.hit(principal.organization_id, principal.membership_id, limits.ask)
        outcome = await ask(
            _db(request),
            organization_id=principal.organization_id,
            membership_id=principal.membership_id,
            request=body,
            deps=deps,
        )
    request.state.log_fields = {
        **getattr(request.state, "log_fields", {}),
        "trace_id": str(outcome.answer.trace_id),
    }
    return outcome.answer


@router.get(
    "/v1/evidence/{evidence_id}",
    response_model=EvidenceDetail,
    responses=_ERRORS,
    summary="One evidence passage you may read (404 for anything else)",
)
async def get_evidence_item(
    evidence_id: uuid.UUID, request: Request, principal: CurrentPrincipal
) -> EvidenceDetail:
    found = await get_evidence(
        _db(request),
        organization_id=principal.organization_id,
        membership_id=principal.membership_id,
        chunk_id=evidence_id,
    )
    if found is None:
        raise LatticeError(ErrorCode.NOT_FOUND)
    return found


@router.get(
    "/v1/traces/{trace_id}",
    response_model=AskerTrace | AdminTrace,
    responses=_ERRORS,
    summary="A query trace: your own in full (re-authorized); others' operational only "
    "(administrators)",
)
async def get_trace(
    trace_id: uuid.UUID, request: Request, principal: CurrentPrincipal
) -> AskerTrace | AdminTrace:
    found = await read_trace(
        _db(request),
        organization_id=principal.organization_id,
        viewer_membership_id=principal.membership_id,
        viewer_is_admin=principal.is_admin,
        trace_id=trace_id,
    )
    if found is None:
        raise LatticeError(ErrorCode.NOT_FOUND)
    return found
