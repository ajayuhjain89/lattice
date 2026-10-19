"""Read models for the Access page (security-model §10; design-system B12 "Access
explanation"). Every function reads through row-level security bound to the viewer's
organization; the caller checks the administrator capability (E7).

What an administrator may see here, and nothing more:

* the member directory, accounts, departments and administrator-defined groups;
* a source-synced (private) group only when the viewer is a member of it; otherwise
  nothing about it -- not its name, members, member count or any identifier -- only
  that the member belongs to groups the viewer can't see;
* a document only when the viewer can read it now (``authz.readable_documents_predicate``
  in the same statement); a member's grants on other documents are a yes/no, never a
  list or a count.
"""

import uuid
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import Table, and_, any_, func, literal, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.access.models import (
    Directory,
    DirectoryAccount,
    DirectoryDepartment,
    DirectoryGroup,
    DocumentRef,
    GrantOut,
    GroupAccess,
    LifecycleAction,
    LifecycleBlock,
    MemberAccess,
    MemberLifecycle,
    MembersPage,
    MemberSummary,
    Named,
)
from lattice.authz import (
    AuthorizationContext,
    Document,
    readable_document_classifications,
    readable_documents_predicate,
)
from lattice.identity.models import DirectoryIdentity, User
from lattice.ingestion.store import opaque_ref
from lattice.orgs.models import Account, AccountAssignment, Department, Membership
from lattice.sources.models import Source

MAX_MEMBERS_PAGE: Final = 100
MAX_DOCUMENTS: Final = 20
_UUIDS = ARRAY(UUID(as_uuid=True))


def _table(model: Any) -> Table:
    table = model.__table__
    assert isinstance(table, Table)  # noqa: S101 - declarative models map to Tables
    return table


DOCUMENTS = _table(Document)
SOURCES = _table(Source)
MEMBERSHIPS = _table(Membership)
USERS = _table(User)
ACCOUNTS = _table(Account)
DEPARTMENTS = _table(Department)
ASSIGNMENTS = _table(AccountAssignment)
DIRECTORY = _table(DirectoryIdentity)


def _ids(values: Collection[uuid.UUID]) -> Any:
    return literal(sorted(values), _UUIDS)


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --- members ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccessSnapshot:
    """A member's administrable access state, as audited (``access.member_changed``)."""

    status: str
    clearance: str
    account_scope: str
    accounts: frozenset[uuid.UUID]
    departments: frozenset[uuid.UUID]
    # Administrator-defined groups only: source-group membership follows the source.
    groups: frozenset[uuid.UUID]
    # The administrator capability: no access to content, but audited with access
    # (threat model T3). Only operator tools change it; the API cannot (0012).
    is_admin: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "is_admin": self.is_admin,
            "clearance": self.clearance,
            "account_scope": self.account_scope,
            "accounts": sorted(str(i) for i in self.accounts),
            "departments": sorted(str(i) for i in self.departments),
            "groups": sorted(str(i) for i in self.groups),
        }


_SNAPSHOT_SQL = text(
    """
    SELECT m.status, m.clearance, m.account_scope, m.is_admin, m.authz_updated_at,
           ARRAY(SELECT aa.account_id FROM account_assignments aa
                 WHERE aa.organization_id = m.organization_id AND aa.membership_id = m.id)
             AS accounts,
           ARRAY(SELECT md.department_id FROM membership_departments md
                 WHERE md.organization_id = m.organization_id AND md.membership_id = m.id)
             AS departments,
           ARRAY(SELECT gm.group_id FROM access_group_members gm
                 JOIN access_groups g ON g.organization_id = gm.organization_id
                   AND g.id = gm.group_id AND g.origin = 'admin'
                 WHERE gm.organization_id = m.organization_id AND gm.membership_id = m.id)
             AS groups
    FROM memberships m
    WHERE m.organization_id = :o AND m.id = :m
    """
)


async def access_snapshot(
    session: AsyncSession, organization_id: uuid.UUID, membership_id: uuid.UUID
) -> tuple[AccessSnapshot, Any] | None:
    """The member's snapshot and its version (``authz_updated_at``); None if absent."""
    row = (
        await session.execute(_SNAPSHOT_SQL, {"o": organization_id, "m": membership_id})
    ).one_or_none()
    if row is None:
        return None
    return (
        AccessSnapshot(
            status=row.status,
            clearance=row.clearance,
            account_scope=row.account_scope,
            accounts=frozenset(row.accounts),
            departments=frozenset(row.departments),
            groups=frozenset(row.groups),
            is_admin=row.is_admin,
        ),
        row.authz_updated_at,
    )


_SOURCE_GROUP_MEMBERS_SQL = text(
    """
    SELECT gm.membership_id, count(*) AS groups
    FROM access_group_members gm
    JOIN access_groups g ON g.organization_id = gm.organization_id AND g.id = gm.group_id
    WHERE g.organization_id = :o AND g.source_id = :s
    GROUP BY gm.membership_id
    """
)


async def source_group_members(
    session: AsyncSession, organization_id: uuid.UUID, source_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    """Members of a source's synced groups (a chat export's private channels) and how
    many of them each is in: who loses access when the source is purged (P12.7)."""
    rows = await session.execute(_SOURCE_GROUP_MEMBERS_SQL, {"o": organization_id, "s": source_id})
    return {row.membership_id: int(row.groups) for row in rows}


def _member_columns() -> list[Any]:
    assigned = (
        select(func.count())
        .where(
            ASSIGNMENTS.c.organization_id == MEMBERSHIPS.c.organization_id,
            ASSIGNMENTS.c.membership_id == MEMBERSHIPS.c.id,
        )
        .scalar_subquery()
    )
    return [
        MEMBERSHIPS.c.id,
        USERS.c.display_name,
        USERS.c.email,
        MEMBERSHIPS.c.role_label,
        MEMBERSHIPS.c.status,
        MEMBERSHIPS.c.is_admin,
        MEMBERSHIPS.c.clearance,
        MEMBERSHIPS.c.account_scope,
        assigned.label("assigned"),
        select(1)
        .where(
            DIRECTORY.c.organization_id == MEMBERSHIPS.c.organization_id,
            DIRECTORY.c.membership_id == MEMBERSHIPS.c.id,
            DIRECTORY.c.managed_by == "identity_provider",
        )
        .exists()
        .label("provisioned"),
    ]


async def _departments_of(
    session: AsyncSession, organization_id: uuid.UUID, membership_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[Named]]:
    found: dict[uuid.UUID, list[Named]] = {}
    if not membership_ids:
        return found
    rows = await session.execute(
        text(
            "SELECT md.membership_id, d.id, d.name FROM membership_departments md "
            "JOIN departments d ON d.organization_id = md.organization_id "
            "AND d.id = md.department_id "
            "WHERE md.organization_id = :o AND md.membership_id = ANY(:ids) ORDER BY d.name, d.id"
        ),
        {"o": organization_id, "ids": list(membership_ids)},
    )
    for membership_id, department_id, name in rows:
        found.setdefault(membership_id, []).append(Named(id=department_id, name=name))
    return found


def _summary(row: Any, departments: list[Named]) -> MemberSummary:
    return MemberSummary(
        id=row.id,
        name=row.display_name,
        email=str(row.email),
        role_label=row.role_label,
        status=row.status,
        is_admin=row.is_admin,
        clearance=row.clearance,
        account_scope=row.account_scope,
        assigned_accounts=int(row.assigned),
        departments=departments,
        managed_by="identity_provider" if row.provisioned else None,
    )


async def members_page(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    q: str | None,
    limit: int,
    after: uuid.UUID | None,
) -> MembersPage:
    """Members by name (case-insensitive), then ID. ``after``: the last ID of the
    previous page; an ID that is not a member here starts from the top."""
    order = (func.lower(USERS.c.display_name), MEMBERSHIPS.c.id)
    query = (
        select(*_member_columns())
        .select_from(MEMBERSHIPS.join(USERS, USERS.c.id == MEMBERSHIPS.c.user_id))
        .where(MEMBERSHIPS.c.organization_id == organization_id)
    )
    if q:
        pattern = _like(q)
        query = query.where(or_(USERS.c.display_name.ilike(pattern), USERS.c.email.ilike(pattern)))
    if after is not None:
        anchor = (
            await session.execute(
                select(*order)
                .select_from(MEMBERSHIPS.join(USERS, USERS.c.id == MEMBERSHIPS.c.user_id))
                .where(MEMBERSHIPS.c.organization_id == organization_id, MEMBERSHIPS.c.id == after)
            )
        ).one_or_none()
        if anchor is not None:
            query = query.where(tuple_(*order) > tuple_(*anchor))
    size = min(limit, MAX_MEMBERS_PAGE)
    rows = (await session.execute(query.order_by(*order).limit(size + 1))).all()
    more = len(rows) > size
    rows = rows[:size]
    departments = await _departments_of(session, organization_id, [r.id for r in rows])
    return MembersPage(
        items=[_summary(r, departments.get(r.id, [])) for r in rows],
        next_after=rows[-1].id if more and rows else None,
    )


async def directory(session: AsyncSession, organization_id: uuid.UUID) -> Directory:
    accounts = await session.execute(
        select(
            ACCOUNTS.c.id,
            ACCOUNTS.c.key,
            ACCOUNTS.c.name,
            ACCOUNTS.c.status,
            ACCOUNTS.c.archived_at,
        )
        .where(ACCOUNTS.c.organization_id == organization_id)
        .order_by(ACCOUNTS.c.name, ACCOUNTS.c.id)
    )
    departments = await session.execute(
        select(DEPARTMENTS.c.id, DEPARTMENTS.c.key, DEPARTMENTS.c.name)
        .where(DEPARTMENTS.c.organization_id == organization_id)
        .order_by(DEPARTMENTS.c.name, DEPARTMENTS.c.id)
    )
    groups = await session.execute(
        text(
            "SELECT g.id, g.key, g.name, (SELECT count(*) FROM access_group_members gm "
            "WHERE gm.organization_id = g.organization_id AND gm.group_id = g.id) AS members "
            "FROM access_groups g WHERE g.organization_id = :o AND g.origin = 'admin' "
            "ORDER BY g.name, g.id"
        ),
        {"o": organization_id},
    )
    return Directory(
        accounts=[
            DirectoryAccount(
                id=r.id,
                key=str(r.key),
                name=r.name,
                status=r.status,
                archived=r.archived_at is not None,
            )
            for r in accounts
        ],
        departments=[
            DirectoryDepartment(id=r.id, key=str(r.key), name=r.name) for r in departments
        ],
        groups=[
            DirectoryGroup(id=r.id, key=str(r.key), name=r.name, members=int(r.members))
            for r in groups
        ],
    )


async def entity_names(
    session: AsyncSession, organization_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Names of accounts, departments and administrator-defined groups (the directory)."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    rows = await session.execute(
        text(
            "SELECT id, name FROM accounts WHERE organization_id = :o AND id = ANY(:ids) "
            "UNION ALL SELECT id, name FROM departments WHERE organization_id = :o "
            "AND id = ANY(:ids) UNION ALL SELECT id, name FROM access_groups "
            "WHERE organization_id = :o AND origin = 'admin' AND id = ANY(:ids)"
        ),
        {"o": organization_id, "ids": wanted},
    )
    return {r.id: r.name for r in rows}


# --- groups ----------------------------------------------------------------------------


async def group_views(
    session: AsyncSession, viewer: AuthorizationContext, wanted: Iterable[uuid.UUID]
) -> dict[uuid.UUID, GroupAccess]:
    """The groups of ``wanted`` that ``viewer`` may know exist (security-model §10 "Access
    groups"): administrator-defined groups, and source-synced groups the viewer is a
    member of. Others are absent -- filtered in SQL, so nothing of them is read."""
    ids = sorted(set(wanted))
    if not ids:
        return {}
    org = viewer.organization_id
    groups = (
        await session.execute(
            text(
                "SELECT g.id, g.origin, g.name, s.connector_type FROM access_groups g "
                "LEFT JOIN sources s ON s.organization_id = g.organization_id "
                "AND s.id = g.source_id WHERE g.organization_id = :o AND g.id = ANY(:ids) "
                "AND (g.origin = 'admin' OR EXISTS (SELECT 1 FROM access_group_members v "
                "WHERE v.organization_id = g.organization_id AND v.group_id = g.id "
                "AND v.membership_id = :viewer))"
            ),
            {"o": org, "ids": ids, "viewer": viewer.membership_id},
        )
    ).all()
    visible = [g.id for g in groups]
    members: dict[uuid.UUID, list[Named]] = {}
    if visible:
        for group_id, membership_id, name in await session.execute(
            text(
                "SELECT gm.group_id, m.id, u.display_name FROM access_group_members gm "
                "JOIN memberships m ON m.organization_id = gm.organization_id "
                "AND m.id = gm.membership_id JOIN users u ON u.id = m.user_id "
                "WHERE gm.organization_id = :o AND gm.group_id = ANY(:ids) "
                "ORDER BY lower(u.display_name), m.id"
            ),
            {"o": org, "ids": visible},
        ):
            members.setdefault(group_id, []).append(Named(id=membership_id, name=name))
    return {
        g.id: GroupAccess(
            id=g.id,
            origin=g.origin,
            name=g.name,
            connector_type=g.connector_type if g.origin == "source" else None,
            members=members.get(g.id, []),
            editable=g.origin == "admin",
        )
        for g in groups
    }


# --- documents -------------------------------------------------------------------------


async def readable_document_refs(
    session: AsyncSession, viewer: AuthorizationContext, document_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, DocumentRef]:
    """References for those documents the viewer can read now (others are absent)."""
    ids = sorted(set(document_ids))
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(
                DOCUMENTS.c.id,
                DOCUMENTS.c.title,
                DOCUMENTS.c.display_ref,
                SOURCES.c.name,
                SOURCES.c.connector_type,
            )
            .join(
                SOURCES,
                and_(
                    SOURCES.c.organization_id == DOCUMENTS.c.organization_id,
                    SOURCES.c.id == DOCUMENTS.c.source_id,
                ),
            )
            .where(
                DOCUMENTS.c.organization_id == viewer.organization_id,
                DOCUMENTS.c.id == any_(_ids(ids)),
                readable_documents_predicate(viewer, DOCUMENTS),
            )
        )
    ).all()
    return await _refs(session, viewer, rows)


async def _refs(
    session: AsyncSession, viewer: AuthorizationContext, rows: Sequence[Any]
) -> dict[uuid.UUID, DocumentRef]:
    classifications = await readable_document_classifications(session, viewer, [r.id for r in rows])
    return {
        r.id: DocumentRef(
            id=r.id,
            title=r.title,
            display_ref=r.display_ref,
            source_name=r.name,
            connector_type=r.connector_type,
            classification=classifications[r.id],
        )
        for r in rows
    }


async def search_documents(
    session: AsyncSession, viewer: AuthorizationContext, q: str, limit: int
) -> list[DocumentRef]:
    """Documents the viewer can read whose title or reference contains ``q``."""
    pattern = _like(q)
    rows = (
        await session.execute(
            select(
                DOCUMENTS.c.id,
                DOCUMENTS.c.title,
                DOCUMENTS.c.display_ref,
                SOURCES.c.name,
                SOURCES.c.connector_type,
            )
            .join(
                SOURCES,
                and_(
                    SOURCES.c.organization_id == DOCUMENTS.c.organization_id,
                    SOURCES.c.id == DOCUMENTS.c.source_id,
                ),
            )
            .where(
                DOCUMENTS.c.organization_id == viewer.organization_id,
                readable_documents_predicate(viewer, DOCUMENTS),
                or_(DOCUMENTS.c.title.ilike(pattern), DOCUMENTS.c.display_ref.ilike(pattern)),
            )
            .order_by(DOCUMENTS.c.source_updated_at.desc(), DOCUMENTS.c.id)
            .limit(min(limit, MAX_DOCUMENTS))
        )
    ).all()
    refs = await _refs(session, viewer, rows)
    return [refs[r.id] for r in rows]


async def document_references(
    session: AsyncSession, organization_id: uuid.UUID, document_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    """(source ID, opaque reference) of documents, for audit targets. The reference is
    the same keyed ``rec_…`` the Sources page shows for the record."""
    ids = sorted(set(document_ids))
    if not ids:
        return {}
    rows = await session.execute(
        select(
            DOCUMENTS.c.id, DOCUMENTS.c.source_id, DOCUMENTS.c.external_id, SOURCES.c.opaque_ref_key
        )
        .join(
            SOURCES,
            and_(
                SOURCES.c.organization_id == DOCUMENTS.c.organization_id,
                SOURCES.c.id == DOCUMENTS.c.source_id,
            ),
        )
        .where(DOCUMENTS.c.organization_id == organization_id, DOCUMENTS.c.id == any_(_ids(ids)))
    )
    return {r.id: (r.source_id, opaque_ref(bytes(r.opaque_ref_key), r.external_id)) for r in rows}


# --- a member's access -----------------------------------------------------------------

_GRANTS_SQL = text(
    """
    SELECT gr.id, gr.origin, gr.document_id, gr.created_at,
           ev.actor, ev.reason
    FROM document_grants gr
    LEFT JOIN LATERAL (
      SELECT e.actor, e.reason FROM audit_events e
      WHERE e.organization_id = gr.organization_id AND e.action = 'access.grant_created'
        AND e.target ->> 'grant_id' = gr.id::text
      ORDER BY e.created_at DESC LIMIT 1
    ) ev ON true
    WHERE gr.organization_id = :o AND gr.principal_type = 'membership'
      AND gr.membership_id = :m
    ORDER BY gr.created_at DESC, gr.id
    """
)


async def actor_names(
    session: AsyncSession, organization_id: uuid.UUID, actors: Iterable[str]
) -> dict[str, str]:
    """Display names of ``member:<membership id>`` actors that are still members."""
    ids: list[uuid.UUID] = []
    for actor in set(actors):
        kind, _, value = actor.partition(":")
        if kind == "member":
            try:
                ids.append(uuid.UUID(value))
            except ValueError:
                continue
    if not ids:
        return {}
    rows = await session.execute(
        select(MEMBERSHIPS.c.id, USERS.c.display_name)
        .join(USERS, USERS.c.id == MEMBERSHIPS.c.user_id)
        .where(MEMBERSHIPS.c.organization_id == organization_id, MEMBERSHIPS.c.id.in_(ids))
    )
    return {f"member:{membership_id}": name for membership_id, name in rows}


_LIFECYCLE_SQL = text(
    """
    SELECT u.status = 'active' AS user_active, d.external_id, d.managed_by,
           (c.status = 'active') AS identity_current,
           EXISTS (
             SELECT 1 FROM memberships o JOIN users ou ON ou.id = o.user_id
             WHERE o.organization_id = m.organization_id AND o.id <> m.id
               AND lower(ou.email) = lower(u.email)
           ) AS email_taken,
           (SELECT count(*) FROM memberships a JOIN users au ON au.id = a.user_id
             WHERE a.organization_id = m.organization_id AND a.is_admin
               AND a.status = 'active' AND au.status = 'active') AS active_administrators
    FROM memberships m JOIN users u ON u.id = m.user_id
    LEFT JOIN directory_identities d
      ON d.organization_id = m.organization_id AND d.membership_id = m.id
    LEFT JOIN oidc_connections c
      ON c.organization_id = d.organization_id AND c.id = d.connection_id
    WHERE m.organization_id = :o AND m.id = :m
    """
)


def _action(blocked_by: LifecycleBlock | None) -> LifecycleAction:
    return LifecycleAction(available=blocked_by is None, blocked_by=blocked_by)


async def _lifecycle(
    session: AsyncSession, viewer: AuthorizationContext, membership_id: uuid.UUID, member: Any
) -> MemberLifecycle:
    """The lifecycle actions the product offers for this member and why any is blocked
    (the same rules ``lattice.access.lifecycle`` and the fenced functions enforce)."""
    row = (
        await session.execute(_LIFECYCLE_SQL, {"o": viewer.organization_id, "m": membership_id})
    ).one()
    status: str = member.status
    is_self = membership_id == viewer.membership_id
    idp = row.managed_by == "identity_provider"
    sole_admin = (
        member.is_admin
        and status == "active"
        and row.user_active
        and int(row.active_administrators) <= 1
    )

    def status_change(applies: bool) -> LifecycleAction | None:
        if not applies:
            return None
        if is_self:
            return _action("own_membership")
        if idp:
            return _action("managed_by_identity_provider")
        return _action(None)

    suspend = status_change(status == "active")
    if suspend is not None and suspend.available and sole_admin:
        suspend = _action("last_administrator")
    reinstate: LifecycleAction | None = None
    if status == "removed":
        if row.external_id is None or not row.identity_current:
            reinstate = _action("identity_not_current")
        elif not row.user_active:
            reinstate = _action("user_inactive")
        elif row.email_taken:
            reinstate = _action("email_taken")
        else:
            reinstate = _action(None)
    grant = revoke = None
    if status != "removed":
        own = _action("own_membership") if is_self else None
        if member.is_admin:
            revoke = own or _action("last_administrator" if sole_admin else None)
        else:
            grant = own or _action(None)
    return MemberLifecycle(
        sign_in_identifier=row.external_id,
        identity_managed_by=row.managed_by,
        suspend=suspend,
        reactivate=status_change(status == "suspended"),
        reinstate=reinstate,
        grant_administrator=grant,
        revoke_administrator=revoke,
    )


async def member_access(
    session: AsyncSession, viewer: AuthorizationContext, membership_id: uuid.UUID
) -> MemberAccess | None:
    org = viewer.organization_id
    found = await access_snapshot(session, org, membership_id)
    if found is None:
        return None
    _, version = found
    row = (
        await session.execute(
            select(*_member_columns())
            .select_from(MEMBERSHIPS.join(USERS, USERS.c.id == MEMBERSHIPS.c.user_id))
            .where(MEMBERSHIPS.c.organization_id == org, MEMBERSHIPS.c.id == membership_id)
        )
    ).one()
    departments = (await _departments_of(session, org, [membership_id])).get(membership_id, [])
    accounts = [
        Named(id=r.id, name=r.name)
        for r in await session.execute(
            text(
                "SELECT a.id, a.name FROM account_assignments aa JOIN accounts a "
                "ON a.organization_id = aa.organization_id AND a.id = aa.account_id "
                "WHERE aa.organization_id = :o AND aa.membership_id = :m ORDER BY a.name, a.id"
            ),
            {"o": org, "m": membership_id},
        )
    ]
    member_groups: list[uuid.UUID] = list(
        (
            await session.execute(
                text(
                    "SELECT group_id FROM access_group_members "
                    "WHERE organization_id = :o AND membership_id = :m"
                ),
                {"o": org, "m": membership_id},
            )
        ).scalars()
    )
    views = await group_views(session, viewer, member_groups)
    groups = sorted(views.values(), key=lambda g: (g.origin != "admin", g.name.lower(), g.id))
    grant_rows = (await session.execute(_GRANTS_SQL, {"o": org, "m": membership_id})).all()
    refs = await readable_document_refs(session, viewer, [g.document_id for g in grant_rows])
    names = await actor_names(session, org, [g.actor for g in grant_rows if g.actor])
    grants = [
        GrantOut(
            id=g.id,
            origin=g.origin,
            document=refs[g.document_id],
            created_at=g.created_at,
            granted_by=names.get(g.actor) if g.actor else None,
            reason=g.reason,
            revocable=g.origin == "admin",
        )
        for g in grant_rows
        if g.document_id in refs
    ]
    return MemberAccess(
        member=_summary(row, departments),
        lifecycle=await _lifecycle(session, viewer, membership_id, row),
        version=version,
        is_self=membership_id == viewer.membership_id,
        accounts=accounts,
        departments=departments,
        groups=groups,
        has_hidden_groups=any(g not in views for g in member_groups),
        grants=grants,
        has_other_grants=any(g.document_id not in refs for g in grant_rows),
    )
