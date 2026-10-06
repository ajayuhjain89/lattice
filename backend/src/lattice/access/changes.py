"""Access changes made by administrators (Phase 9), each in one transaction with its
audit event (T3: detectable, with who, when, what and why).

Rules, checked in this order so that a refusal reveals nothing the caller may not know:

1. The administrator capability is checked by the route (E7) before anything is read.
2. The member must exist in the caller's organization (404 otherwise -- also for another
   organization's members, which row-level security hides).
3. Nobody changes their own access here (403): an administrator raising their own
   clearance or granting themselves a document needs a second administrator.
4. A removed membership is not changed (422 ``member_removed``); removal is
   deprovisioning, not an access change.
5. A change carries the ``version`` the caller reviewed; a membership changed since is
   refused (409), so a preview is never applied to a different starting point.
6. Referenced accounts, departments and administrator-defined groups must exist in the
   organization (422 ``unknown``). A source-synced group is refused the same way, as
   unknown: its members follow the source, and the answer must not tell a private
   group's ID from a missing one.
7. A grant names a document the *caller* can read (404 otherwise, identical to a missing
   document): administrators cannot grant -- or learn about -- what they cannot read.
8. A member provisioned by the identity provider (SCIM, Phase 10) is suspended,
   reactivated and removed only by the identity provider (422
   ``managed_by_identity_provider``, D-20); their access stays editable here.

The database enforces the same limits for the API role (migration 0012): no
``is_admin``, no ``removed``, administrator membership grants and
administrator-defined groups only.
"""

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice import metrics
from lattice.access.audit import GRANT_CREATED, GRANT_REVOKED, MEMBER_CHANGED, record_event
from lattice.access.directory import (
    DOCUMENTS,
    AccessSnapshot,
    access_snapshot,
    document_references,
    entity_names,
)
from lattice.access.models import (
    AccessPreview,
    GrantRequest,
    MemberChange,
    MemberChangeRequest,
    RevokeRequest,
)
from lattice.access.preview import apply_change, context_after, document_delta, effects
from lattice.authz import (
    AuthorizationContext,
    readable_documents_predicate,
    resolve_authorization_context,
    resolve_member_context,
)
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorDetail, LatticeError


def _invalid(field: str, issue: str) -> LatticeError:
    return LatticeError(
        ErrorCode.VALIDATION_FAILED, details=[ErrorDetail(field=field, issue=issue)]
    )


def _not_found() -> LatticeError:
    return LatticeError(ErrorCode.NOT_FOUND)


def _own_access() -> LatticeError:
    return LatticeError(
        ErrorCode.PERMISSION_DENIED,
        "You can't change your own access. Ask another administrator.",
        details=[ErrorDetail(field="membership", issue="own_access")],
    )


async def _member_status(
    session: AsyncSession, organization_id: uuid.UUID, membership_id: uuid.UUID
) -> str | None:
    found: str | None = (
        await session.execute(
            text("SELECT status FROM memberships WHERE organization_id = :o AND id = :m"),
            {"o": organization_id, "m": membership_id},
        )
    ).scalar_one_or_none()
    return found


async def _checked_member(
    session: AsyncSession, viewer: AuthorizationContext, membership_id: uuid.UUID
) -> None:
    status = await _member_status(session, viewer.organization_id, membership_id)
    if status is None:
        raise _not_found()
    if membership_id == viewer.membership_id:
        raise _own_access()
    if status == "removed":
        raise _invalid("membership", "member_removed")


async def _refuse_managed_lifecycle(
    session: AsyncSession,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    before: AccessSnapshot,
    after: AccessSnapshot,
) -> None:
    if after.status == before.status:
        return
    provisioned: bool = (
        await session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM directory_identities "
                "WHERE organization_id = :o AND membership_id = :m)"
            ),
            {"o": organization_id, "m": membership_id},
        )
    ).scalar_one()
    if provisioned:
        raise _invalid("status", "managed_by_identity_provider")


async def _validate_references(
    session: AsyncSession, organization_id: uuid.UUID, change: MemberChange
) -> None:
    checks = (
        ("accounts", change.accounts, "SELECT id FROM accounts"),
        ("departments", change.departments, "SELECT id FROM departments"),
    )
    for field, ids, select_sql in checks:
        if not ids:
            continue
        found: set[uuid.UUID] = set(
            (
                await session.execute(
                    text(f"{select_sql} WHERE organization_id = :o AND id = ANY(:ids)"),
                    {"o": organization_id, "ids": list(set(ids))},
                )
            ).scalars()
        )
        if found != set(ids):
            raise _invalid(field, "unknown")
    if change.groups:
        # Administrator-defined groups only. Any other ID -- including a private group
        # the caller may not know exists -- is refused identically, as unknown.
        found_groups: set[uuid.UUID] = set(
            (
                await session.execute(
                    text(
                        "SELECT id FROM access_groups WHERE organization_id = :o "
                        "AND origin = 'admin' AND id = ANY(:ids)"
                    ),
                    {"o": organization_id, "ids": list(set(change.groups))},
                )
            ).scalars()
        )
        if found_groups != set(change.groups):
            raise _invalid("groups", "unknown")


async def _viewer(
    session: AsyncSession, organization_id: uuid.UUID, membership_id: uuid.UUID
) -> AuthorizationContext:
    return await resolve_authorization_context(
        session, organization_id=organization_id, membership_id=membership_id
    )


# --- preview ---------------------------------------------------------------------------


async def preview_member_change(
    session: AsyncSession,
    viewer: AuthorizationContext,
    membership_id: uuid.UUID,
    change: MemberChange,
) -> AccessPreview:
    """Inside the caller's snapshot. Same checks as the change, except the version."""
    org = viewer.organization_id
    await _checked_member(session, viewer, membership_id)
    await _validate_references(session, org, change)
    found = await access_snapshot(session, org, membership_id)
    current = await resolve_member_context(
        session, organization_id=org, membership_id=membership_id
    )
    if found is None or current is None:
        raise _not_found()
    before, _ = found
    after = apply_change(before, change)
    await _refuse_managed_lifecycle(session, org, membership_id, before, after)
    names = await entity_names(
        session,
        org,
        before.accounts
        | after.accounts
        | before.departments
        | after.departments
        | before.groups
        | after.groups,
    )
    gained, lost = await document_delta(
        session, viewer, current, context_after(current, before, after)
    )
    return AccessPreview(
        effects=effects(before, after, names), documents_gained=gained, documents_lost=lost
    )


# --- member changes --------------------------------------------------------------------


async def _sync_links(
    session: AsyncSession,
    *,
    table: str,
    column: str,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    before: frozenset[uuid.UUID],
    after: frozenset[uuid.UUID],
) -> None:
    params: dict[str, Any] = {"o": organization_id, "m": membership_id}
    if removed := before - after:
        await session.execute(
            text(
                f"DELETE FROM {table} WHERE organization_id = :o AND membership_id = :m "  # noqa: S608 - fixed names
                f"AND {column} = ANY(:ids)"
            ),
            {**params, "ids": sorted(removed)},
        )
    for value in sorted(after - before):
        await session.execute(
            text(
                f"INSERT INTO {table} (organization_id, membership_id, {column}) "  # noqa: S608 - fixed names
                "VALUES (:o, :m, :v)"
            ),
            {**params, "v": value},
        )


async def write_snapshot(
    session: AsyncSession,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    before: AccessSnapshot,
    after: AccessSnapshot,
) -> None:
    """Make the member's administrable access ``after`` (from ``before``)."""
    await session.execute(
        text(
            "UPDATE memberships SET status = :s, clearance = :c, account_scope = :a, "
            "authz_updated_at = clock_timestamp(), updated_at = now() "
            "WHERE organization_id = :o AND id = :m"
        ),
        {
            "o": organization_id,
            "m": membership_id,
            "s": after.status,
            "c": after.clearance,
            "a": after.account_scope,
        },
    )
    for table, column, old, new in (
        ("account_assignments", "account_id", before.accounts, after.accounts),
        ("membership_departments", "department_id", before.departments, after.departments),
        ("access_group_members", "group_id", before.groups, after.groups),
    ):
        await _sync_links(
            session,
            table=table,
            column=column,
            organization_id=organization_id,
            membership_id=membership_id,
            before=old,
            after=new,
        )


async def change_member(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    membership_id: uuid.UUID,
    request: MemberChangeRequest,
) -> None:
    async with db.tenant_transaction(organization_id) as session:
        viewer = await _viewer(session, organization_id, actor_membership_id)
        await _checked_member(session, viewer, membership_id)
        # Lock the membership so that the version check and the write are one step.
        version = (
            await session.execute(
                text(
                    "SELECT authz_updated_at FROM memberships "
                    "WHERE organization_id = :o AND id = :m FOR UPDATE"
                ),
                {"o": organization_id, "m": membership_id},
            )
        ).scalar_one_or_none()
        if version is None:
            raise _not_found()
        if version != request.version:
            raise LatticeError(ErrorCode.CONFLICT)
        await _validate_references(session, organization_id, request)
        found = await access_snapshot(session, organization_id, membership_id)
        if found is None:
            raise _not_found()
        before, _ = found
        after = apply_change(before, request)
        if after == before:
            raise _invalid("change", "no_change")
        await _refuse_managed_lifecycle(session, organization_id, membership_id, before, after)
        await write_snapshot(session, organization_id, membership_id, before, after)
        metrics.member_changed(before.status, after.status, "administrator")
        await record_event(
            session,
            organization_id,
            actor=f"member:{actor_membership_id}",
            action=MEMBER_CHANGED,
            target={"membership_id": str(membership_id)},
            before=before.as_json(),
            after=after.as_json(),
            reason=request.reason,
        )


# --- grants ----------------------------------------------------------------------------


async def _bump(
    session: AsyncSession, organization_id: uuid.UUID, membership_id: uuid.UUID
) -> None:
    await session.execute(
        text(
            "UPDATE memberships SET authz_updated_at = clock_timestamp() "
            "WHERE organization_id = :o AND id = :m"
        ),
        {"o": organization_id, "m": membership_id},
    )


async def _readable_document(
    session: AsyncSession, viewer: AuthorizationContext, document_id: uuid.UUID
) -> tuple[uuid.UUID, str] | None:
    """(source ID, opaque reference) of a document the viewer can read; None for
    anything else, identically."""
    found = (
        await session.execute(
            select(DOCUMENTS.c.id).where(
                DOCUMENTS.c.organization_id == viewer.organization_id,
                DOCUMENTS.c.id == document_id,
                readable_documents_predicate(viewer, DOCUMENTS),
            )
        )
    ).scalar_one_or_none()
    if found is None:
        return None
    return (await document_references(session, viewer.organization_id, [document_id]))[document_id]


async def create_grant(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    membership_id: uuid.UUID,
    request: GrantRequest,
) -> tuple[uuid.UUID, bool]:
    """(grant ID, created). An existing administrator grant is returned unchanged (no
    event)."""
    async with db.tenant_transaction(organization_id) as session:
        viewer = await _viewer(session, organization_id, actor_membership_id)
        await _checked_member(session, viewer, membership_id)
        document = await _readable_document(session, viewer, request.document_id)
        if document is None:
            raise _not_found()
        params = {"o": organization_id, "m": membership_id, "d": request.document_id}
        existing = (
            await session.execute(
                text(
                    "SELECT id FROM document_grants WHERE organization_id = :o "
                    "AND document_id = :d AND origin = 'admin' "
                    "AND principal_type = 'membership' AND membership_id = :m"
                ),
                params,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False
        grant_id: uuid.UUID = (
            await session.execute(
                text(
                    "INSERT INTO document_grants (organization_id, document_id, principal_type, "
                    "membership_id, origin) VALUES (:o, :d, 'membership', :m, 'admin') RETURNING id"
                ),
                params,
            )
        ).scalar_one()
        await _bump(session, organization_id, membership_id)
        source_id, reference = document
        await record_event(
            session,
            organization_id,
            actor=f"member:{actor_membership_id}",
            action=GRANT_CREATED,
            target={
                "membership_id": str(membership_id),
                "document_id": str(request.document_id),
                "grant_id": str(grant_id),
                "source_id": str(source_id),
                "reference": reference,
            },
            before=None,
            after={"origin": "admin", "principal_type": "membership"},
            reason=request.reason,
        )
        return grant_id, True


async def revoke_grant(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    grant_id: uuid.UUID,
    request: RevokeRequest,
) -> None:
    async with db.tenant_transaction(organization_id) as session:
        viewer = await _viewer(session, organization_id, actor_membership_id)
        grant = (
            await session.execute(
                text(
                    "SELECT origin, principal_type, membership_id, document_id "
                    "FROM document_grants WHERE organization_id = :o AND id = :g"
                ),
                {"o": organization_id, "g": grant_id},
            )
        ).one_or_none()
        # Only membership grants are shown, and only on documents the caller can read.
        if grant is None or grant.principal_type != "membership":
            raise _not_found()
        document = await _readable_document(session, viewer, grant.document_id)
        if document is None:
            raise _not_found()
        if grant.membership_id == viewer.membership_id:
            raise _own_access()
        if grant.origin != "admin":
            raise _invalid("grant", "source_grant")
        await session.execute(
            text("DELETE FROM document_grants WHERE organization_id = :o AND id = :g"),
            {"o": organization_id, "g": grant_id},
        )
        await _bump(session, organization_id, grant.membership_id)
        source_id, reference = document
        await record_event(
            session,
            organization_id,
            actor=f"member:{actor_membership_id}",
            action=GRANT_REVOKED,
            target={
                "membership_id": str(grant.membership_id),
                "document_id": str(grant.document_id),
                "grant_id": str(grant_id),
                "source_id": str(source_id),
                "reference": reference,
            },
            before={"origin": "admin", "principal_type": "membership"},
            after=None,
            reason=request.reason,
        )
