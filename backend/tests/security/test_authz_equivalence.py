"""Property-based proof that the SQL predicate and the reference policy agree.

This is the central Phase 2 test (docs/authorization-model.md §6). Hypothesis
generates small two-organization worlds -- members with every combination of
membership status, clearance, account scope, assignments, departments, groups and the
administrator flag; documents with every sensitivity, access mode, status, account,
department restriction (including departments deleted after the fact) and grants to
members, departments and groups (applicable, unrelated, and of deleted principals).

Each world is written by a superuser inside one transaction that is always rolled
back. For every (member, document) pair, all of these must hold:

1. The resolved AuthorizationContext equals the one derived from the generated spec
   (and resolution fails closed for inactive memberships). ``is_admin`` never matters.
2. As ``lattice_app`` with the member's organization bound (RLS on), the SQL predicate
   evaluated per row equals the reference ``readable()`` exactly, and the WHERE form
   returns exactly the reference's readable set.
3. With RLS bypassed (superuser), the predicate still equals the reference for every
   document of *both* organizations -- the predicate's own organization clause is
   tested, not just RLS.
4. ``visible_accounts_predicate`` equals ``account_visible`` for every account.
5. ``load_document_access`` round-trips the generated access facts, and
   ``explain(...).readable`` equals ``readable``.

A mismatch is a security defect. The number of examples is LATTICE_AUTHZ_EXAMPLES
(default 500, the CI minimum from docs/implementation-plan.md); the test also asserts
that every policy path was actually exercised, so a degenerate generator cannot pass.
"""

import asyncio
import os
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from lattice.authz import (
    POLICY_VERSION,
    AccessReason,
    AccountRef,
    AuthorizationContext,
    AuthorizationContextUnavailable,
    DocumentAccess,
    GrantRef,
    account_visible,
    explain,
    load_document_access,
    readable,
    readable_documents_predicate,
    resolve_authorization_context,
    visible_accounts_predicate,
)
from lattice.authz.sql import ACCOUNTS, DOCUMENTS
from lattice.db.engine import create_engine

from ..conftest import SUPERUSER_URL

EXAMPLES = int(os.environ.get("LATTICE_AUTHZ_EXAMPLES", "500"))
MINIMUM_EXAMPLES = 500

N_DEPARTMENTS = 3  # plus one "ghost" per organization, deleted after documents name it
GHOST = N_DEPARTMENTS
N_ACCOUNTS = 3
N_GROUPS = 2
AUTHZ_UPDATED_AT = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

HOME, OTHER = 0, 1
org_index = st.sampled_from((HOME,) * 5 + (OTHER,))


@dataclass(frozen=True)
class MemberSpec:
    org: int
    status: str
    is_admin: bool
    clearance: str
    account_scope: str
    accounts: frozenset[int]
    departments: frozenset[int]  # may include GHOST
    groups: frozenset[int]


@dataclass(frozen=True)
class GrantSpec:
    principal_type: str
    target: int  # index into the document organization's members/departments/groups
    origin: str


@dataclass(frozen=True)
class DocumentSpec:
    org: int
    status: str
    sensitivity: str
    access_mode: str
    account: int | None
    departments: frozenset[int]  # may include GHOST
    grants: tuple[GrantSpec, ...]


@dataclass(frozen=True)
class WorldSpec:
    members: tuple[MemberSpec, ...]
    documents: tuple[DocumentSpec, ...]


members_st = st.builds(
    MemberSpec,
    org=org_index,
    status=st.sampled_from(("active",) * 8 + ("suspended", "removed")),
    is_admin=st.booleans(),
    clearance=st.sampled_from(("internal", "confidential")),
    account_scope=st.sampled_from(("all", "assigned")),
    accounts=st.frozensets(st.integers(0, N_ACCOUNTS - 1)),
    departments=st.frozensets(st.integers(0, GHOST)),
    groups=st.frozensets(st.integers(0, N_GROUPS - 1)),
)
grants_st = st.builds(
    GrantSpec,
    principal_type=st.sampled_from(("membership", "department", "group")),
    target=st.integers(0, 7),
    origin=st.sampled_from(("admin", "source")),
)


@st.composite
def documents_st(draw: st.DrawFn) -> DocumentSpec:
    sensitivity = draw(st.sampled_from(("internal", "confidential", "restricted")))
    access_mode = (
        "grants_only"
        if sensitivity == "restricted"  # the only combination the schema admits
        else draw(st.sampled_from(("policy", "policy", "grants_only")))
    )
    return DocumentSpec(
        org=draw(org_index),
        status=draw(st.sampled_from(("active", "active", "active", "deleted"))),
        sensitivity=sensitivity,
        access_mode=access_mode,
        account=draw(st.none() | st.integers(0, N_ACCOUNTS - 1)),
        departments=draw(st.frozensets(st.integers(0, GHOST), max_size=2)),
        grants=tuple(draw(st.lists(grants_st, max_size=3))),
    )


worlds_st = st.builds(
    WorldSpec,
    members=st.lists(members_st, min_size=1, max_size=4).map(tuple),
    documents=st.lists(documents_st(), min_size=1, max_size=10).map(tuple),
)


# --- Building a world in the database ------------------------------------------------


@dataclass
class Org:
    id: uuid.UUID
    departments: list[uuid.UUID]  # index GHOST is deleted after documents are written
    accounts: list[uuid.UUID]
    groups: list[uuid.UUID]
    members: list[uuid.UUID]


@dataclass
class World:
    orgs: list[Org]
    contexts: list[AuthorizationContext]  # expected, one per member spec
    documents: list[DocumentAccess]  # expected
    accounts: list[AccountRef]


async def _insert_one(conn: AsyncConnection, sql: str, params: dict[str, object]) -> uuid.UUID:
    value: object = (await conn.execute(text(sql), params)).scalar_one()
    assert isinstance(value, uuid.UUID)
    return value


async def _build(conn: AsyncConnection, spec: WorldSpec) -> World:
    orgs: list[Org] = []
    for i in (HOME, OTHER):
        org_id = await _insert_one(
            conn,
            "INSERT INTO organizations (slug, name) VALUES (:s, :n) RETURNING id",
            {"s": f"eq-{i}-{uuid.uuid4().hex[:12]}", "n": f"Org {i}"},
        )
        departments = [
            await _insert_one(
                conn,
                "INSERT INTO departments (organization_id, key, name) VALUES (:o, :k, :n) "
                "RETURNING id",
                {"o": org_id, "k": f"d{d}", "n": f"Department {d}"},
            )
            for d in range(N_DEPARTMENTS + 1)
        ]
        accounts = [
            await _insert_one(
                conn,
                "INSERT INTO accounts (organization_id, key, name) VALUES (:o, :k, :n) "
                "RETURNING id",
                {"o": org_id, "k": "acme-corp" if a == 0 else f"a{a}", "n": f"Account {a}"},
            )
            for a in range(N_ACCOUNTS)
        ]
        groups = [
            await _insert_one(
                conn,
                "INSERT INTO access_groups (organization_id, origin, external_ref, name) "
                "VALUES (:o, :origin, :ref, :n) RETURNING id",
                {
                    "o": org_id,
                    "origin": "source" if g % 2 else "admin",
                    "ref": f"C{g}" if g % 2 else None,
                    "n": f"g{g}",
                },
            )
            for g in range(N_GROUPS)
        ]
        orgs.append(Org(org_id, departments, accounts, groups, []))

    membership_ids: list[uuid.UUID] = []
    for m in spec.members:
        org = orgs[m.org]
        user_id = await _insert_one(
            conn,
            "INSERT INTO users (email, display_name) VALUES (:e, 'x') RETURNING id",
            {"e": f"{uuid.uuid4().hex}@eq.example"},
        )
        membership_id = await _insert_one(
            conn,
            "INSERT INTO memberships (organization_id, user_id, status, is_admin, clearance, "
            "account_scope, role_label, authz_updated_at) "
            "VALUES (:o, :u, :st, :adm, :c, :sc, 'Administrator', :t) RETURNING id",
            {
                "o": org.id,
                "u": user_id,
                "st": m.status,
                "adm": m.is_admin,
                "c": m.clearance,
                "sc": m.account_scope,
                "t": AUTHZ_UPDATED_AT,
            },
        )
        for a in m.accounts:
            await conn.execute(
                text(
                    "INSERT INTO account_assignments (organization_id, membership_id, "
                    "account_id) VALUES (:o, :m, :a)"
                ),
                {"o": org.id, "m": membership_id, "a": org.accounts[a]},
            )
        for d in m.departments:
            await conn.execute(
                text(
                    "INSERT INTO membership_departments (organization_id, membership_id, "
                    "department_id) VALUES (:o, :m, :d)"
                ),
                {"o": org.id, "m": membership_id, "d": org.departments[d]},
            )
        for g in m.groups:
            await conn.execute(
                text(
                    "INSERT INTO access_group_members (organization_id, group_id, "
                    "membership_id) VALUES (:o, :g, :m)"
                ),
                {"o": org.id, "g": org.groups[g], "m": membership_id},
            )
        org.members.append(membership_id)
        membership_ids.append(membership_id)

    document_rows: list[tuple[uuid.UUID, DocumentSpec, list[tuple[str, uuid.UUID]]]] = []
    for doc in spec.documents:
        org = orgs[doc.org]
        document_id = await _insert_one(
            conn,
            "INSERT INTO documents (organization_id, account_id, sensitivity, access_mode, "
            "department_ids, status) VALUES (:o, :a, :s, :m, CAST(:d AS uuid[]), :st) "
            "RETURNING id",
            {
                "o": org.id,
                "a": None if doc.account is None else org.accounts[doc.account],
                "s": doc.sensitivity,
                "m": doc.access_mode,
                "d": [org.departments[i] for i in sorted(doc.departments)],
                "st": doc.status,
            },
        )
        written: list[tuple[str, uuid.UUID]] = []
        for grant in doc.grants:
            pool = {
                "membership": org.members,
                "department": org.departments,
                "group": org.groups,
            }[grant.principal_type]
            if not pool:
                continue
            principal = pool[grant.target % len(pool)]
            if (grant.principal_type, principal) in written:
                continue
            column = {"membership": "membership_id", "department": "department_id"}.get(
                grant.principal_type, "group_id"
            )
            await conn.execute(
                text(
                    "INSERT INTO document_grants (organization_id, document_id, "  # noqa: S608
                    f"principal_type, {column}, origin) VALUES (:o, :d, :t, :p, :origin)"
                ),
                {
                    "o": org.id,
                    "d": document_id,
                    "t": grant.principal_type,
                    "p": principal,
                    "origin": grant.origin,
                },
            )
            written.append((grant.principal_type, principal))
        document_rows.append((document_id, doc, written))

    # Departments deleted after the fact: memberships and department grants cascade
    # away; documents keep naming the dead ID ("unknown department").
    ghosts = [org.departments[GHOST] for org in orgs]
    await conn.execute(text("DELETE FROM departments WHERE id = ANY(:g)"), {"g": ghosts})

    contexts = [
        AuthorizationContext(
            organization_id=orgs[m.org].id,
            membership_id=membership_ids[i],
            membership_status=m.status,  # type: ignore[arg-type]
            clearance=m.clearance,  # type: ignore[arg-type]
            account_scope=m.account_scope,  # type: ignore[arg-type]
            assigned_account_ids=frozenset(orgs[m.org].accounts[a] for a in m.accounts),
            department_ids=frozenset(
                orgs[m.org].departments[d] for d in m.departments if d != GHOST
            ),
            group_ids=frozenset(orgs[m.org].groups[g] for g in m.groups),
            authz_updated_at=AUTHZ_UPDATED_AT,
            policy_version=POLICY_VERSION,
        )
        for i, m in enumerate(spec.members)
    ]
    documents = [
        DocumentAccess(
            id=document_id,
            organization_id=orgs[d.org].id,
            status=d.status,
            sensitivity=d.sensitivity,
            access_mode=d.access_mode,
            account_id=None if d.account is None else orgs[d.org].accounts[d.account],
            department_ids=frozenset(orgs[d.org].departments[i] for i in d.departments),
            grants=tuple(
                sorted(
                    (
                        GrantRef(orgs[d.org].id, principal_type, principal)
                        for principal_type, principal in written
                        if principal not in ghosts
                    ),
                    key=lambda g: (g.principal_type, g.principal_id),
                )
            ),
        )
        for document_id, d, written in document_rows
    ]
    account_refs = [AccountRef(account_id, org.id) for org in orgs for account_id in org.accounts]
    return World(orgs, contexts, documents, account_refs)


# --- Checking a world ---------------------------------------------------------------


async def _bind_runtime_role(conn: AsyncConnection, organization_id: uuid.UUID) -> None:
    await conn.execute(text("SET LOCAL ROLE lattice_app"))
    await conn.execute(
        text("SELECT set_config('app.org_id', :o, true)"), {"o": str(organization_id)}
    )


async def _check_world(conn: AsyncConnection, spec: WorldSpec, stats: Counter[str]) -> None:
    transaction = await conn.begin()
    try:
        world = await _build(conn, spec)
        session = AsyncSession(bind=conn)
        documents_by_id = {d.id: d for d in world.documents}
        live_departments = {d for org in world.orgs for d in org.departments[:GHOST]}
        for member_spec, expected in zip(spec.members, world.contexts, strict=True):
            # 1. Context resolution, as the runtime role inside the member's org.
            await _bind_runtime_role(conn, expected.organization_id)
            if member_spec.status == "active":
                resolved = await resolve_authorization_context(
                    session,
                    organization_id=expected.organization_id,
                    membership_id=expected.membership_id,
                )
                assert resolved == expected
            else:
                with pytest.raises(AuthorizationContextUnavailable):
                    await resolve_authorization_context(
                        session,
                        organization_id=expected.organization_id,
                        membership_id=expected.membership_id,
                    )
                stats["contexts_refused_inactive"] += 1
            ctx = expected

            reference = {d.id: readable(ctx, d) for d in world.documents}

            # 2. SQL predicate under RLS, per row and as a WHERE clause.
            predicate = readable_documents_predicate(ctx)
            per_row: dict[uuid.UUID, object] = dict(
                (await conn.execute(select(DOCUMENTS.c.id, predicate.label("ok")))).all()
            )
            own_documents = {
                d.id for d in world.documents if d.organization_id == ctx.organization_id
            }
            assert set(per_row) == own_documents, "RLS must expose exactly the bound org"
            for document_id, ok in per_row.items():
                assert ok is reference[document_id], (ctx, documents_by_id[document_id])
            where: set[uuid.UUID] = set(
                (await conn.execute(select(DOCUMENTS.c.id).where(predicate))).scalars()
            )
            assert where == {i for i, ok in reference.items() if ok}

            # 5a. The loader round-trips the access facts (under RLS).
            loaded = await load_document_access(session, ctx.organization_id, list(reference))
            assert loaded == {i: d for i, d in documents_by_id.items() if i in own_documents}

            # 3 + 4. Without RLS: the predicates alone, over both organizations.
            await conn.execute(text("RESET ROLE"))
            unfiltered: dict[uuid.UUID, object] = dict(
                (await conn.execute(select(DOCUMENTS.c.id, predicate.label("ok")))).all()
            )
            world_ids = set(documents_by_id)
            assert {i: unfiltered[i] for i in world_ids} == reference
            visible: dict[uuid.UUID, object] = dict(
                (
                    await conn.execute(
                        select(ACCOUNTS.c.id, visible_accounts_predicate(ctx).label("ok"))
                    )
                ).all()
            )
            for account in world.accounts:
                assert visible[account.id] is account_visible(ctx, account), (ctx, account)
                stats["account_pairs"] += 1
                stats["accounts_visible"] += visible[account.id] is True

            # 5b. Explanations agree with the decision; tally which paths were exercised.
            for document in world.documents:
                explanation = explain(ctx, document)
                assert explanation.readable is reference[document.id]
                stats["pairs"] += 1
                stats[f"readable_via_{explanation.path}"] += 1
                stats.update(r.value for r in explanation.reasons)
                if document.department_ids - live_departments:
                    stats["pairs_with_unknown_department"] += 1
        await session.close()
        stats["examples"] += 1
    finally:
        await transaction.rollback()


# Every policy path the generator must reach for the run to count as meaningful.
# Unknown access modes and sensitivities cannot be stored (CHECK constraints); they are
# proven fail-closed in test_authz_fail_closed.py with the constraints removed.
REQUIRED_PATHS = (
    "readable_via_grant",
    "readable_via_policy",
    "readable_via_none",
    "contexts_refused_inactive",
    "pairs_with_unknown_department",
    *(
        r.value
        for r in AccessReason
        if r not in (AccessReason.UNKNOWN_ACCESS_MODE, AccessReason.UNKNOWN_SENSITIVITY)
    ),
)


def test_sql_predicate_is_equivalent_to_the_reference_policy(migrated_schema: None) -> None:
    assert EXAMPLES >= MINIMUM_EXAMPLES or os.environ.get("LATTICE_AUTHZ_EXAMPLES")
    stats: Counter[str] = Counter()
    with asyncio.Runner() as runner:
        engine = create_engine(SUPERUSER_URL, application_name="lattice-tests-authz-eq")
        conn = runner.run(engine.connect().start())

        @settings(
            max_examples=EXAMPLES,
            deadline=None,
            suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
        )
        @given(spec=worlds_st)
        def check(spec: WorldSpec) -> None:
            runner.run(_check_world(conn, spec, stats))

        try:
            check()
        finally:
            runner.run(conn.close())
            runner.run(engine.dispose())

    summary = ", ".join(f"{k}={v}" for k, v in sorted(stats.items()))
    print(f"\nauthz equivalence: {summary}")  # shown with pytest -s
    assert stats["examples"] >= min(EXAMPLES, MINIMUM_EXAMPLES)
    missing = [path for path in REQUIRED_PATHS if stats[path] < 10]
    assert not missing, f"generator did not exercise: {missing}"
