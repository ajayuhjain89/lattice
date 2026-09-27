"""Unknown, malformed, incomplete or cross-tenant permission state is denied.

Two layers are tested for each case:

1. The schema refuses to store it (CHECK constraints, composite foreign keys, the
   department trigger) -- see also tests/test_authz_schema.py.
2. If such state existed anyway (the constraint removed inside a rolled-back superuser
   transaction), the SQL predicate, the reference policy and the context resolver
   still deny. The policy never infers a permissive default.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from lattice.authz import (
    POLICY_VERSION,
    AccessReason,
    AuthorizationContext,
    AuthorizationContextUnavailable,
    DocumentAccess,
    GrantRef,
    explain,
    readable,
    readable_documents_predicate,
    resolve_authorization_context,
)
from lattice.authz.sql import DOCUMENTS
from lattice.db.engine import Database

from .conftest import Halden, context_for, readable_names

ORG = uuid.UUID(int=1)
OTHER_ORG = uuid.UUID(int=2)
MEMBER = uuid.UUID(int=10)
SALES = uuid.UUID(int=20)


def _ctx(**overrides: object) -> AuthorizationContext:
    values: dict[str, object] = {
        "organization_id": ORG,
        "membership_id": MEMBER,
        "membership_status": "active",
        "clearance": "confidential",
        "account_scope": "all",
        "assigned_account_ids": frozenset(),
        "department_ids": frozenset({SALES}),
        "group_ids": frozenset(),
        "authz_updated_at": datetime(2026, 9, 27, tzinfo=UTC),
        "policy_version": POLICY_VERSION,
    }
    values.update(overrides)
    return AuthorizationContext(**values)  # type: ignore[arg-type]


def _doc(**overrides: object) -> DocumentAccess:
    values: dict[str, object] = {
        "id": uuid.UUID(int=100),
        "organization_id": ORG,
        "status": "active",
        "sensitivity": "internal",
        "access_mode": "policy",
        "account_id": None,
        "department_ids": frozenset(),
        "grants": (),
    }
    values.update(overrides)
    return DocumentAccess(**values)  # type: ignore[arg-type]


# --- Reference policy ------------------------------------------------------------------


def test_the_baseline_document_is_readable() -> None:
    assert readable(_ctx(), _doc())


@pytest.mark.parametrize(
    "doc",
    [
        _doc(access_mode="public"),
        _doc(access_mode=""),
        _doc(access_mode="POLICY"),
        _doc(sensitivity="secret"),
        _doc(sensitivity=""),
        _doc(sensitivity="Internal"),
        _doc(sensitivity="restricted", access_mode="policy"),  # schema forbids; denied anyway
        _doc(status="archived"),
        _doc(status="deleted"),
        _doc(organization_id=OTHER_ORG),
        _doc(department_ids=frozenset({uuid.uuid4()})),  # unknown department
    ],
    ids=lambda d: f"{d.access_mode}/{d.sensitivity}/{d.status}",
)
def test_reference_denies_unknown_or_malformed_document_state(doc: DocumentAccess) -> None:
    assert not readable(_ctx(), doc)
    assert not explain(_ctx(), doc).readable


@pytest.mark.parametrize(
    "grant",
    [
        GrantRef(OTHER_ORG, "membership", MEMBER),  # grant row from another tenant
        GrantRef(ORG, "user", MEMBER),  # unknown principal type
        GrantRef(ORG, "role", MEMBER),
        GrantRef(ORG, "everyone", ORG),
        GrantRef(ORG, "membership", uuid.uuid4()),  # someone else
        GrantRef(ORG, "department", uuid.uuid4()),
        GrantRef(ORG, "group", uuid.uuid4()),
    ],
    ids=lambda g: g.principal_type,
)
def test_reference_ignores_grants_that_do_not_apply(grant: GrantRef) -> None:
    restricted = _doc(sensitivity="restricted", access_mode="grants_only", grants=(grant,))
    assert not readable(_ctx(), restricted)


@pytest.mark.parametrize("status", ["suspended", "removed"])
def test_reference_denies_everything_to_an_inactive_membership(status: str) -> None:
    ctx = _ctx(membership_status=status)
    granted = _doc(grants=(GrantRef(ORG, "membership", MEMBER),))
    assert not readable(ctx, _doc())
    assert not readable(ctx, granted)
    assert explain(ctx, granted).reasons == (AccessReason.MEMBERSHIP_INACTIVE,)


def test_restricted_is_reachable_only_through_a_grant() -> None:
    top = _ctx(clearance="confidential", account_scope="all")
    restricted = _doc(sensitivity="restricted", access_mode="grants_only")
    assert not readable(top, restricted)
    assert readable(
        top,
        _doc(
            sensitivity="restricted",
            access_mode="grants_only",
            grants=(GrantRef(ORG, "membership", MEMBER),),
        ),
    )


def test_grants_only_is_never_satisfied_by_the_policy_path() -> None:
    everything = _ctx(clearance="confidential", account_scope="all")
    for sensitivity in ("internal", "confidential"):
        doc = _doc(sensitivity=sensitivity, access_mode="grants_only")
        assert not readable(everything, doc)
        assert explain(everything, doc).reasons == (AccessReason.GRANTS_ONLY_WITHOUT_GRANT,)


def test_assigned_scope_without_assignments_reads_no_account_documents() -> None:
    ctx = _ctx(account_scope="assigned", assigned_account_ids=frozenset())
    assert not readable(ctx, _doc(account_id=uuid.uuid4()))
    assert readable(ctx, _doc(account_id=None))


@pytest.mark.parametrize(
    "overrides",
    [
        {"clearance": "restricted"},  # clearance can never be restricted
        {"clearance": "top-secret"},
        {"account_scope": "everything"},
        {"membership_status": "pending"},
        {"department_ids": [SALES]},  # not a frozenset: strict model refuses coercion
        {"organization_id": str(ORG)},
        {"is_admin": True},  # no such field; extra fields are forbidden
    ],
)
def test_a_malformed_context_cannot_be_constructed(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _ctx(**overrides)


# --- SQL predicate and resolver with the schema's guards removed ------------------------


@pytest.fixture
async def unguarded(superuser_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A superuser transaction (RLS bypassed, always rolled back) in which the schema's
    guards can be removed to store state the schema normally refuses."""
    async with superuser_engine.connect() as conn:
        transaction = await conn.begin()
        try:
            yield conn
        finally:
            await transaction.rollback()


async def _predicate_by_document(
    conn: AsyncConnection, ctx: AuthorizationContext, ids: list[uuid.UUID]
) -> dict[uuid.UUID, object]:
    rows = await conn.execute(
        select(DOCUMENTS.c.id, readable_documents_predicate(ctx).label("ok")).where(
            DOCUMENTS.c.id.in_(ids)
        )
    )
    return {row[0]: row[1] for row in rows.all()}


@pytest.mark.parametrize(
    ("constraint", "column", "value"),
    [
        ("ck_documents_access_mode", "access_mode", "public"),
        ("ck_documents_access_mode", "access_mode", "POLICY"),
        ("ck_documents_sensitivity", "sensitivity", "secret"),
        ("ck_documents_sensitivity", "sensitivity", "Internal"),
        ("ck_documents_restricted_is_grants_only", "sensitivity", "restricted"),
        ("ck_documents_status", "status", "archived"),
    ],
)
async def test_predicate_denies_unknown_values_even_without_the_check(
    app_db: Database,
    halden: Halden,
    unguarded: AsyncConnection,
    constraint: str,
    column: str,
    value: str,
) -> None:
    # The highest-clearance reader (confidential, all accounts): nothing she lacks may
    # be what denies the document.
    grace = await context_for(app_db, halden.org, halden.members["grace"])
    document = halden.documents["company-handbook"]  # readable by everyone
    assert await _predicate_by_document(unguarded, grace, [document]) == {document: True}

    await unguarded.execute(text(f"ALTER TABLE documents DROP CONSTRAINT {constraint}"))
    await unguarded.execute(
        text(f"UPDATE documents SET {column} = :v WHERE id = :d"),  # noqa: S608 - fixed names
        {"v": value, "d": document},
    )
    assert await _predicate_by_document(unguarded, grace, [document]) == {document: False}


async def test_predicate_ignores_a_grant_row_belonging_to_another_tenant(
    app_db: Database, halden: Halden, unguarded: AsyncConnection
) -> None:
    """A grant whose organization differs from the reader's cannot open a document,
    even if the composite foreign keys that make it impossible were missing."""
    daniel = await context_for(app_db, halden.org, halden.members["daniel"])
    memo = halden.documents["board-mna-memo"]
    await unguarded.execute(
        text(
            "ALTER TABLE document_grants "
            "DROP CONSTRAINT fk_document_grants_organization_id_document_id_documents, "
            "DROP CONSTRAINT fk_document_grants_organization_id_membership_id_memberships"
        )
    )
    await unguarded.execute(
        text(
            "INSERT INTO document_grants (organization_id, document_id, principal_type, "
            "membership_id, origin) VALUES (:other, :d, 'membership', :m, 'admin')"
        ),
        {"other": halden.veridian, "d": memo, "m": halden.members["daniel"]},
    )
    assert await _predicate_by_document(unguarded, daniel, [memo]) == {memo: False}


async def test_predicate_ignores_a_grant_with_an_unknown_principal_type(
    app_db: Database, halden: Halden, unguarded: AsyncConnection
) -> None:
    daniel = await context_for(app_db, halden.org, halden.members["daniel"])
    memo = halden.documents["board-mna-memo"]
    await unguarded.execute(
        text("ALTER TABLE document_grants DROP CONSTRAINT ck_document_grants_one_principal")
    )
    await unguarded.execute(
        text(
            "INSERT INTO document_grants (organization_id, document_id, principal_type, origin) "
            "VALUES (:o, :d, 'everyone', 'admin')"
        ),
        {"o": halden.org, "d": memo},
    )
    assert await _predicate_by_document(unguarded, daniel, [memo]) == {memo: False}


@pytest.mark.parametrize(
    ("constraint", "column", "value"),
    [
        ("ck_memberships_clearance", "clearance", "restricted"),
        ("ck_memberships_clearance", "clearance", "top-secret"),
        ("ck_memberships_account_scope", "account_scope", "everything"),
    ],
)
async def test_resolver_refuses_malformed_membership_attributes(
    halden: Halden, unguarded: AsyncConnection, constraint: str, column: str, value: str
) -> None:
    grace = halden.members["grace"]
    await unguarded.execute(text(f"ALTER TABLE memberships DROP CONSTRAINT {constraint}"))
    await unguarded.execute(
        text(f"UPDATE memberships SET {column} = :v WHERE id = :m"),  # noqa: S608 - fixed names
        {"v": value, "m": grace},
    )
    await unguarded.execute(text("SET LOCAL ROLE lattice_app"))
    await unguarded.execute(
        text("SELECT set_config('app.org_id', :o, true)"), {"o": str(halden.org)}
    )
    session = AsyncSession(bind=unguarded)
    with pytest.raises(AuthorizationContextUnavailable):
        await resolve_authorization_context(
            session, organization_id=halden.org, membership_id=grace
        )
    await session.close()


async def test_resolver_refuses_a_membership_of_another_organization(
    app_db: Database, halden: Halden
) -> None:
    """The session binds Halden; a Veridian membership ID is simply not found."""
    async with app_db.tenant_transaction(halden.veridian) as s:
        noor: uuid.UUID = (await s.execute(text("SELECT id FROM memberships LIMIT 1"))).scalar_one()
    with pytest.raises(AuthorizationContextUnavailable):
        await context_for(app_db, halden.org, noor)
    with pytest.raises(AuthorizationContextUnavailable):
        await context_for(app_db, halden.org, uuid.uuid4())


async def test_unknown_department_makes_a_document_less_visible_not_more(
    app_db: Database, halden: Halden
) -> None:
    for person in ("maya", "priya", "tom", "grace", "lena"):
        assert "disbanded-team-notes" not in await readable_names(app_db, halden, person)


async def test_deleting_an_account_never_widens_its_documents(
    owner_db: Database, halden: Halden
) -> None:
    """If deleting an account cleared documents.account_id, account-specific documents
    would become readable by everyone. The foreign key refuses the deletion instead."""
    with pytest.raises(DBAPIError, match="fk_documents_organization_id_account_id_accounts"):
        async with owner_db.tenant_transaction(halden.org) as s:
            await s.execute(
                text("DELETE FROM accounts WHERE id = :a"), {"a": halden.accounts["kestrel-bank"]}
            )
