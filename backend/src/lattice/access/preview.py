"""What a proposed access change would do (security-model §10 "Access-change previews").

Effects are described as rules derived from the change alone ("loses Kestrel Bank",
"clearance Confidential → Internal") -- never from documents. Document counts are
computed only over documents the *viewing administrator* can read, with the
administrator's own predicate in the same statement: the counts never include, and so
never hint at, documents the administrator cannot read.
"""

import uuid

from sqlalchemy import and_, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.access.directory import DOCUMENTS, AccessSnapshot
from lattice.access.models import AccessEffect, MemberChange, NamedRef
from lattice.authz import AuthorizationContext, proposed_context, readable_documents_predicate


def apply_change(snapshot: AccessSnapshot, change: MemberChange) -> AccessSnapshot:
    return AccessSnapshot(
        status=change.status or snapshot.status,
        clearance=change.clearance or snapshot.clearance,
        account_scope=change.account_scope or snapshot.account_scope,
        accounts=snapshot.accounts if change.accounts is None else frozenset(change.accounts),
        departments=snapshot.departments
        if change.departments is None
        else frozenset(change.departments),
        groups=snapshot.groups if change.groups is None else frozenset(change.groups),
        is_admin=snapshot.is_admin,
    )


def effects(
    before: AccessSnapshot | None, after: AccessSnapshot, names: dict[uuid.UUID, str]
) -> list[AccessEffect]:
    """``before`` is ``None`` for a membership that did not exist (provisioned)."""
    found: list[AccessEffect] = []
    if before is None:
        found.append(AccessEffect(kind="provisioned", after=after.status))
        found.append(AccessEffect(kind="clearance", after=after.clearance))
        found.append(AccessEffect(kind="account_scope", after=after.account_scope))
        before = AccessSnapshot(
            after.status,
            after.clearance,
            after.account_scope,
            frozenset(),
            frozenset(),
            frozenset(),
            is_admin=False,
        )
    if before.status != after.status:
        found.append(
            AccessEffect(kind="suspended" if after.status == "suspended" else "reactivated")
        )
    if before.is_admin != after.is_admin:
        found.append(
            AccessEffect(
                kind="administrator",
                before=str(before.is_admin).lower(),
                after=str(after.is_admin).lower(),
            )
        )
    if before.clearance != after.clearance:
        found.append(AccessEffect(kind="clearance", before=before.clearance, after=after.clearance))
    if before.account_scope != after.account_scope:
        found.append(
            AccessEffect(
                kind="account_scope", before=before.account_scope, after=after.account_scope
            )
        )

    def ref(i: uuid.UUID) -> NamedRef:
        return NamedRef(id=i, name=names.get(i))

    def sets(kind: str, old: frozenset[uuid.UUID], new: frozenset[uuid.UUID]) -> None:
        for i in sorted(new - old, key=lambda x: ((names.get(x) or "").lower(), x)):
            found.append(AccessEffect(kind=f"{kind}_added", subject=ref(i)))
        for i in sorted(old - new, key=lambda x: ((names.get(x) or "").lower(), x)):
            found.append(AccessEffect(kind=f"{kind}_removed", subject=ref(i)))

    sets("account", before.accounts, after.accounts)
    sets("department", before.departments, after.departments)
    sets("group", before.groups, after.groups)
    return found


def context_after(
    current: AuthorizationContext, before: AccessSnapshot, after: AccessSnapshot
) -> AuthorizationContext:
    """The member's context once ``after`` applies. Source-synced group membership is
    kept as it is; a member who cannot read because their user or organization is
    inactive stays unable to read whatever the change says."""
    status = after.status if current.membership_status == before.status else None
    return proposed_context(
        current,
        membership_status=status,
        clearance=after.clearance,
        account_scope=after.account_scope,
        accounts=after.accounts,
        departments=after.departments,
        groups=(current.group_ids - before.groups) | after.groups,
    )


async def document_delta(
    session: AsyncSession,
    viewer: AuthorizationContext,
    before: AuthorizationContext,
    after: AuthorizationContext,
) -> tuple[int, int]:
    """(gained, lost): documents the viewer can read that the member would newly be able
    to read, or would no longer be able to read."""
    can_before = readable_documents_predicate(before, DOCUMENTS)
    can_after = readable_documents_predicate(after, DOCUMENTS)
    row = (
        await session.execute(
            select(
                func.count().filter(and_(can_after, not_(can_before))),
                func.count().filter(and_(can_before, not_(can_after))),
            ).where(
                DOCUMENTS.c.organization_id == viewer.organization_id,
                readable_documents_predicate(viewer, DOCUMENTS),
            )
        )
    ).one()
    return int(row[0]), int(row[1])
