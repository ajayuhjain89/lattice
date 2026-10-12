"""Accounts and their external-reference mappings (Phase 12, P12.3). Thin: parse, check
the capability, call one service, map the result.

Under Access (``/v1/admin/access/...``), administrators only; the capability is checked
before anything is read, so a member gets the same 403 whether or not the target exists,
and an account or source of another organization answers exactly as a random ID.

* ``accounts`` -- list, create, read, edit (name, aliases, business status), archive and
  reactivate. Each change: a reason, the version (409), an audit event.
* ``account-references`` -- the references the latest syncs could not map, a Now → After
  preview, and map / remap / unmap, each with a reason and an audit event. A change
  applies at the source's next sync.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request

from lattice.accounts.models import (
    AccountChangeRequest,
    AccountDetail,
    AccountLifecycleRequest,
    AccountsPage,
    MappingPreview,
    MappingPreviewRequest,
    MappingResult,
    MapRequest,
    NewAccountRequest,
    RemapRequest,
    UnmappedReferences,
    UnmapRequest,
)
from lattice.accounts.service import (
    MAX_ACCOUNTS_PAGE,
    account_detail,
    accounts_page,
    create_account,
    map_reference,
    preview_mapping,
    remap_reference,
    set_archived,
    unmap_reference,
    unmapped_references,
    update_account,
)
from lattice.api.deps import CurrentPrincipal, Limits
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorResponse, LatticeError
from lattice.identity.sessions import Principal

router = APIRouter(tags=["accounts"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {**_ERRORS, 429: {"model": ErrorResponse}}
_CHANGE_ERRORS: dict[int | str, dict[str, Any]] = {**_WRITE_ERRORS, 409: {"model": ErrorResponse}}
_CSRF = "Requires the X-Lattice-Request: 1 header."


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _require_admin(principal: Principal) -> None:
    if not principal.is_admin:
        raise LatticeError(ErrorCode.PERMISSION_DENIED)


async def _admin_write(principal: Principal, limits: Limits) -> None:
    """Capability first (403 identical for any target), then the write limit."""
    _require_admin(principal)
    await limits.limiter.hit(
        principal.organization_id, principal.membership_id, limits.access_write
    )


async def _read(request: Request, principal: Principal, account_id: uuid.UUID) -> AccountDetail:
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        found = await account_detail(session, principal.organization_id, account_id)
    if found is None:
        raise LatticeError(ErrorCode.NOT_FOUND)
    return found


# --- accounts -----------------------------------------------------------------------------


@router.get(
    "/v1/admin/access/accounts",
    response_model=AccountsPage,
    responses=_ERRORS,
    summary="The organization's accounts by name (administrators)",
)
async def get_accounts(
    request: Request,
    principal: CurrentPrincipal,
    q: Annotated[str | None, Query(max_length=100)] = None,
    lifecycle: Literal["active", "archived"] | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_ACCOUNTS_PAGE)] = 50,
    after: uuid.UUID | None = None,
) -> AccountsPage:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        return await accounts_page(
            session,
            principal.organization_id,
            q=(q or "").strip() or None,
            lifecycle=lifecycle,
            limit=limit,
            after=after,
        )


@router.post(
    "/v1/admin/access/accounts",
    response_model=AccountDetail,
    status_code=201,
    responses=_CHANGE_ERRORS,
    summary="Create an account, with a reason (administrators; audited). Nobody is assigned it",
    description="The key is immutable (document front matter and Ask scopes name it); 409 "
    f"when another account has it. {_CSRF}",
)
async def post_account(
    body: NewAccountRequest, request: Request, principal: CurrentPrincipal, limits: Limits
) -> AccountDetail:
    await _admin_write(principal, limits)
    account_id = await create_account(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        request=body,
    )
    return await _read(request, principal, account_id)


@router.get(
    "/v1/admin/access/accounts/{account_id}",
    response_model=AccountDetail,
    responses=_ERRORS,
    summary="An account: its metadata, lifecycle and external-reference mappings (administrators)",
)
async def get_account(
    account_id: uuid.UUID, request: Request, principal: CurrentPrincipal
) -> AccountDetail:
    _require_admin(principal)
    return await _read(request, principal, account_id)


@router.patch(
    "/v1/admin/access/accounts/{account_id}",
    response_model=AccountDetail,
    responses=_CHANGE_ERRORS,
    summary="Change an account's name, aliases or business status, with a reason "
    "(administrators; audited)",
    description="`version` must be the account's current version (409 otherwise). An "
    f"archived account is read-only until reactivated (422 `archived`). {_CSRF}",
)
async def patch_account(
    account_id: uuid.UUID,
    body: AccountChangeRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> AccountDetail:
    await _admin_write(principal, limits)
    await update_account(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        account_id=account_id,
        request=body,
    )
    return await _read(request, principal, account_id)


@router.post(
    "/v1/admin/access/accounts/{account_id}/archive",
    response_model=AccountDetail,
    responses=_CHANGE_ERRORS,
    summary="Archive an account, with a reason (administrators; audited)",
    description="Nothing is deleted or hidden: documents, assignments, traces and "
    "existing mappings stay, and existing mappings keep resolving. The account accepts no "
    f"new mappings until reactivated. {_CSRF}",
)
async def post_archive(
    account_id: uuid.UUID,
    body: AccountLifecycleRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> AccountDetail:
    await _admin_write(principal, limits)
    await set_archived(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        account_id=account_id,
        archived=True,
        request=body,
    )
    return await _read(request, principal, account_id)


@router.post(
    "/v1/admin/access/accounts/{account_id}/reactivate",
    response_model=AccountDetail,
    responses=_CHANGE_ERRORS,
    summary="Reactivate an archived account, with a reason (administrators; audited)",
    description=f"The same account, with its history and mappings. {_CSRF}",
)
async def post_reactivate(
    account_id: uuid.UUID,
    body: AccountLifecycleRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> AccountDetail:
    await _admin_write(principal, limits)
    await set_archived(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        account_id=account_id,
        archived=False,
        request=body,
    )
    return await _read(request, principal, account_id)


# --- references ---------------------------------------------------------------------------


@router.get(
    "/v1/admin/access/account-references/unmapped",
    response_model=UnmappedReferences,
    responses=_ERRORS,
    summary="References the latest syncs could not map to an account, with the number of "
    "records waiting (administrators)",
    description="The exact reference only (a ticketing organization ID or a channel ID): "
    "never a channel name, a title or anything from a record.",
)
async def get_unmapped(request: Request, principal: CurrentPrincipal) -> UnmappedReferences:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        return await unmapped_references(session, principal.organization_id)


@router.post(
    "/v1/admin/access/account-references/preview",
    response_model=MappingPreview,
    responses=_WRITE_ERRORS,
    summary="Now → After for one reference: map, remap or unmap (administrators; nothing "
    "is changed)",
    description=f"Nothing indexed changes until the source's next sync. {_CSRF}",
)
async def post_mapping_preview(
    body: MappingPreviewRequest, request: Request, principal: CurrentPrincipal, limits: Limits
) -> MappingPreview:
    await _admin_write(principal, limits)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        return await preview_mapping(session, principal.organization_id, body)


@router.post(
    "/v1/admin/access/account-references",
    response_model=MappingResult,
    status_code=201,
    responses=_CHANGE_ERRORS,
    summary="Map an unmapped reference to an active account, with a reason "
    "(administrators; audited)",
    description="409 when the reference is already mapped (remap it instead); 422 "
    f"`account_archived` for an archived account. Applies at the next sync. {_CSRF}",
)
async def post_mapping(
    body: MapRequest, request: Request, principal: CurrentPrincipal, limits: Limits
) -> MappingResult:
    await _admin_write(principal, limits)
    return await map_reference(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        request=body,
    )


@router.post(
    "/v1/admin/access/account-references/remap",
    response_model=MappingResult,
    responses=_CHANGE_ERRORS,
    summary="Move a reference from its current account to another active account, with a "
    "reason (administrators; audited)",
    description="`current_account_id` must be the account it maps to now (409 otherwise). "
    f"Records move at the next sync. {_CSRF}",
)
async def post_remap(
    body: RemapRequest, request: Request, principal: CurrentPrincipal, limits: Limits
) -> MappingResult:
    await _admin_write(principal, limits)
    return await remap_reference(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        request=body,
    )


@router.post(
    "/v1/admin/access/account-references/unmap",
    response_model=MappingResult,
    responses=_CHANGE_ERRORS,
    summary="Remove a reference's mapping, with a reason (administrators; audited)",
    description="`current_account_id` must be the account it maps to now (409 otherwise). "
    "At the next sync its records are quarantined (account-scoped source) or lose the "
    f"account. {_CSRF}",
)
async def post_unmap(
    body: UnmapRequest, request: Request, principal: CurrentPrincipal, limits: Limits
) -> MappingResult:
    await _admin_write(principal, limits)
    return await unmap_reference(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        request=body,
    )
