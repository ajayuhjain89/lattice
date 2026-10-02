"""Accounts you may know exist, and access management (Phase 9). Thin: parse, check the
capability, call one service, map the result.

* ``GET /v1/accounts`` -- any member: the accounts an Ask scope may name
  (``authz.visible_accounts_predicate``; security-model §10 row 1).
* ``/v1/admin/access/...`` -- administrators: the member directory, a member's access,
  previews and changes (each audited), grants of documents they can read, and
  explanations over documents they can read. The capability is checked before anything
  is read, so a non-administrator gets the same 403 whether or not the target exists.
* ``GET /v1/admin/audit-events`` -- administrators: the audit trail, newest first.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from lattice.access.audit import MAX_AUDIT_PAGE, audit_page
from lattice.access.changes import change_member, create_grant, preview_member_change, revoke_grant
from lattice.access.directory import (
    ACCOUNTS,
    MAX_DOCUMENTS,
    MAX_MEMBERS_PAGE,
    directory,
    member_access,
    members_page,
    search_documents,
)
from lattice.access.explain import explain_member_document
from lattice.access.models import (
    AccessPreview,
    AuditPage,
    Directory,
    DocumentExplanation,
    DocumentsPage,
    GrantOut,
    GrantRequest,
    MemberAccess,
    MemberAccounts,
    MemberChange,
    MemberChangeRequest,
    MembersPage,
    RevokeRequest,
    VisibleAccount,
)
from lattice.api.deps import CurrentPrincipal, Limits
from lattice.authz import resolve_authorization_context, visible_accounts_predicate
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorResponse, LatticeError
from lattice.identity.sessions import Principal

router = APIRouter(tags=["access"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {**_ERRORS, 429: {"model": ErrorResponse}}
_CHANGE_ERRORS: dict[int | str, dict[str, Any]] = {**_WRITE_ERRORS, 409: {"model": ErrorResponse}}


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _require_admin(principal: Principal) -> None:
    if not principal.is_admin:
        raise LatticeError(ErrorCode.PERMISSION_DENIED)


async def _admin_write(principal: Principal, limits: Limits) -> None:
    """Capability first (403 identical for any target), then the administrator's write
    limit -- before the target is looked up, so a 429 says nothing about it."""
    _require_admin(principal)
    await limits.limiter.hit(
        principal.organization_id, principal.membership_id, limits.access_write
    )


async def _read_member(
    request: Request, principal: Principal, membership_id: uuid.UUID
) -> MemberAccess:
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        viewer = await resolve_authorization_context(
            session,
            organization_id=principal.organization_id,
            membership_id=principal.membership_id,
        )
        found = await member_access(session, viewer, membership_id)
    if found is None:
        raise LatticeError(ErrorCode.NOT_FOUND)
    return found


@router.get(
    "/v1/accounts",
    response_model=MemberAccounts,
    responses=_ERRORS,
    tags=["accounts"],
    summary="The accounts you may know exist: the only ones an Ask scope may name",
)
async def get_accounts(request: Request, principal: CurrentPrincipal) -> MemberAccounts:
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        ctx = await resolve_authorization_context(
            session,
            organization_id=principal.organization_id,
            membership_id=principal.membership_id,
        )
        rows = await session.execute(
            select(ACCOUNTS.c.key, ACCOUNTS.c.name)
            .where(visible_accounts_predicate(ctx, ACCOUNTS))
            .order_by(ACCOUNTS.c.name, ACCOUNTS.c.key)
        )
        accounts = [VisibleAccount(key=str(key), name=name) for key, name in rows]
    return MemberAccounts(account_scope=ctx.account_scope, accounts=accounts)


@router.get(
    "/v1/admin/access/members",
    response_model=MembersPage,
    responses=_ERRORS,
    summary="The organization's members by name (administrators)",
)
async def get_members(
    request: Request,
    principal: CurrentPrincipal,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_MEMBERS_PAGE)] = 50,
    after: uuid.UUID | None = None,
) -> MembersPage:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        return await members_page(
            session,
            principal.organization_id,
            q=(q or "").strip() or None,
            limit=limit,
            after=after,
        )


@router.get(
    "/v1/admin/access/directory",
    response_model=Directory,
    responses=_ERRORS,
    summary="Accounts, departments and administrator-defined groups that can be assigned "
    "(administrators)",
)
async def get_directory(request: Request, principal: CurrentPrincipal) -> Directory:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        return await directory(session, principal.organization_id)


@router.get(
    "/v1/admin/access/members/{membership_id}",
    response_model=MemberAccess,
    responses=_ERRORS,
    summary="A member's access: scope, groups and grants on documents you can read "
    "(administrators)",
)
async def get_member(
    membership_id: uuid.UUID, request: Request, principal: CurrentPrincipal
) -> MemberAccess:
    _require_admin(principal)
    return await _read_member(request, principal, membership_id)


@router.post(
    "/v1/admin/access/members/{membership_id}/preview",
    response_model=AccessPreview,
    responses=_WRITE_ERRORS,
    summary="What a change would do: effects as rules, counts only over documents you can "
    "read (administrators; nothing is changed)",
    description="Requires the X-Lattice-Request: 1 header.",
)
async def post_member_preview(
    membership_id: uuid.UUID,
    body: MemberChange,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> AccessPreview:
    await _admin_write(principal, limits)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        viewer = await resolve_authorization_context(
            session,
            organization_id=principal.organization_id,
            membership_id=principal.membership_id,
        )
        return await preview_member_change(session, viewer, membership_id, body)


@router.patch(
    "/v1/admin/access/members/{membership_id}",
    response_model=MemberAccess,
    responses=_CHANGE_ERRORS,
    summary="Change a member's access, with a reason (administrators; audited)",
    description="`version` must be the member's current version (409 otherwise). You "
    "cannot change your own access (403). Requires the X-Lattice-Request: 1 header.",
)
async def patch_member(
    membership_id: uuid.UUID,
    body: MemberChangeRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> MemberAccess:
    await _admin_write(principal, limits)
    await change_member(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        membership_id=membership_id,
        request=body,
    )
    return await _read_member(request, principal, membership_id)


@router.get(
    "/v1/admin/access/documents",
    response_model=DocumentsPage,
    responses=_ERRORS,
    summary="Documents you can read whose title or reference contains `q` (administrators)",
)
async def get_documents(
    request: Request,
    principal: CurrentPrincipal,
    q: Annotated[str, Query(min_length=2, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=MAX_DOCUMENTS)] = 10,
) -> DocumentsPage:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        viewer = await resolve_authorization_context(
            session,
            organization_id=principal.organization_id,
            membership_id=principal.membership_id,
        )
        return DocumentsPage(items=await search_documents(session, viewer, q.strip(), limit))


@router.get(
    "/v1/admin/access/members/{membership_id}/documents/{document_id}",
    response_model=DocumentExplanation,
    responses=_ERRORS,
    summary="Why a member can or cannot read a document you can read (administrators)",
)
async def get_member_document(
    membership_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal,
) -> DocumentExplanation:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        viewer = await resolve_authorization_context(
            session,
            organization_id=principal.organization_id,
            membership_id=principal.membership_id,
        )
        found = await explain_member_document(session, viewer, membership_id, document_id)
    if found is None:
        raise LatticeError(ErrorCode.NOT_FOUND)
    return found


@router.post(
    "/v1/admin/access/members/{membership_id}/grants",
    response_model=GrantOut,
    status_code=201,
    responses={
        **_WRITE_ERRORS,
        200: {"model": GrantOut, "description": "The grant already exists"},
    },
    summary="Grant a member one document you can read, with a reason (administrators; audited)",
    description="Idempotent: an existing administrator grant is returned with 200. "
    "Requires the X-Lattice-Request: 1 header.",
)
async def post_grant(
    membership_id: uuid.UUID,
    body: GrantRequest,
    request: Request,
    response: Response,
    principal: CurrentPrincipal,
    limits: Limits,
) -> GrantOut:
    await _admin_write(principal, limits)
    grant_id, created = await create_grant(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        membership_id=membership_id,
        request=body,
    )
    if not created:
        response.status_code = 200
    member = await _read_member(request, principal, membership_id)
    for grant in member.grants:
        if grant.id == grant_id:
            return grant
    raise LatticeError(ErrorCode.NOT_FOUND)  # revoked or made unreadable in between


@router.post(
    "/v1/admin/access/grants/{grant_id}/revoke",
    status_code=204,
    responses=_WRITE_ERRORS,
    summary="Revoke an administrator grant on a document you can read, with a reason "
    "(administrators; audited)",
    description="Requires the X-Lattice-Request: 1 header.",
)
async def post_revoke(
    grant_id: uuid.UUID,
    body: RevokeRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> Response:
    await _admin_write(principal, limits)
    await revoke_grant(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        grant_id=grant_id,
        request=body,
    )
    return Response(status_code=204)


@router.get(
    "/v1/admin/audit-events",
    response_model=AuditPage,
    responses=_ERRORS,
    summary="Access and provider changes, newest first (administrators)",
)
async def get_audit_events(
    request: Request,
    principal: CurrentPrincipal,
    limit: Annotated[int, Query(ge=1, le=MAX_AUDIT_PAGE)] = 50,
    before: uuid.UUID | None = None,
) -> AuditPage:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        viewer = await resolve_authorization_context(
            session,
            organization_id=principal.organization_id,
            membership_id=principal.membership_id,
        )
        return await audit_page(session, viewer, limit=limit, before=before)
