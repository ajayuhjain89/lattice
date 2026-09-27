"""Structural integrity of the Phase 2 authorization tables.

Cross-organization relationships are rejected by the database itself -- composite
foreign keys and the department trigger -- independent of application code and of
RLS: every insert here runs as the superuser, which bypasses RLS entirely.
"""

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from lattice.db.engine import Database

from .factories import (
    Member,
    create_account,
    create_department,
    create_document,
    create_group,
    create_member,
    create_org,
)


@dataclass
class Tenant:
    member: Member
    department: uuid.UUID
    account: uuid.UUID
    group: uuid.UUID
    document: uuid.UUID

    @property
    def org(self) -> uuid.UUID:
        return self.member.organization_id


async def _tenant(db: Database, slug: str) -> Tenant:
    org = await create_org(db, slug)
    member = await create_member(db, org, f"someone@{slug}.example")
    return Tenant(
        member=member,
        department=await create_department(db, org, "sales"),
        account=await create_account(db, org, "acme-corp"),
        group=await create_group(db, org, "Exec staff"),
        document=await create_document(db, org),
    )


@pytest.fixture
async def tenants(owner_db: Database) -> tuple[Tenant, Tenant]:
    return await _tenant(owner_db, "org-a"), await _tenant(owner_db, "org-b")


async def _superuser_insert(engine: AsyncEngine, sql: str, **params: object) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)


# --- Cross-organization references are impossible -------------------------------------

CROSS_ORG_CASES = {
    "group member from another org": (
        "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
        "VALUES (:org, :a_group, :b_member)",
        "fk_access_group_members",
    ),
    "grant on own document to another org's membership": (
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "membership_id, origin) VALUES (:org, :a_document, 'membership', :b_member, 'admin')",
        "fk_document_grants",
    ),
    "grant to another org's department": (
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "department_id, origin) VALUES (:org, :a_document, 'department', :b_department, 'admin')",
        "fk_document_grants",
    ),
    "grant to another org's group": (
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "group_id, origin) VALUES (:org, :a_document, 'group', :b_group, 'admin')",
        "fk_document_grants",
    ),
    "grant row on another org's document": (
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "membership_id, origin) VALUES (:org, :b_document, 'membership', :a_member, 'admin')",
        "fk_document_grants",
    ),
    "document on another org's account": (
        "INSERT INTO documents (organization_id, account_id, sensitivity, access_mode) "
        "VALUES (:org, :b_account, 'internal', 'policy')",
        "fk_documents_organization_id_account_id_accounts",
    ),
    "document restricted to another org's department": (
        "INSERT INTO documents (organization_id, sensitivity, access_mode, department_ids) "
        "VALUES (:org, 'internal', 'policy', ARRAY[CAST(:b_department AS uuid)])",
        "department_ids must reference departments",
    ),
    "document restricted to a nonexistent department": (
        "INSERT INTO documents (organization_id, sensitivity, access_mode, department_ids) "
        "VALUES (:org, 'internal', 'policy', ARRAY[gen_random_uuid()])",
        "department_ids must reference departments",
    ),
}


# Rows linking two tenants' objects must fail whichever organization the row claims.
# (A document row claiming org B that names B's own account is simply valid, so the
# document cases claim org A only.)
_CLAIMS = [
    (case, claim)
    for case in sorted(CROSS_ORG_CASES)
    for claim in (("a",) if case.startswith("document") else ("a", "b"))
]


@pytest.mark.parametrize(("case", "as_org"), _CLAIMS)
async def test_cross_organization_reference_is_rejected(
    superuser_engine: AsyncEngine, tenants: tuple[Tenant, Tenant], case: str, as_org: str
) -> None:
    a, b = tenants
    sql, constraint = CROSS_ORG_CASES[case]
    params = {
        "org": a.org if as_org == "a" else b.org,
        "a_group": a.group,
        "a_document": a.document,
        "a_member": a.member.membership_id,
        "b_member": b.member.membership_id,
        "b_department": b.department,
        "b_group": b.group,
        "b_document": b.document,
        "b_account": b.account,
    }
    used = {k: v for k, v in params.items() if f":{k}" in sql}
    with pytest.raises(IntegrityError, match=constraint):
        await _superuser_insert(superuser_engine, sql, **used)


async def test_moving_a_document_to_another_org_is_rejected(
    superuser_engine: AsyncEngine, owner_db: Database, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    restricted = await create_document(owner_db, a.org, department_ids=(a.department,))
    with pytest.raises(IntegrityError, match="department_ids must reference departments"):
        await _superuser_insert(
            superuser_engine,
            "UPDATE documents SET organization_id = :b WHERE id = :d",
            b=b.org,
            d=restricted,
        )


async def test_same_org_references_are_accepted(
    owner_db: Database, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    await create_document(
        owner_db,
        a.org,
        account_id=a.account,
        department_ids=(a.department, a.department),  # duplicates are harmless
        grants=(
            ("membership", a.member.membership_id),
            ("department", a.department),
            ("group", a.group),
        ),
    )


# --- CHECK constraints ---------------------------------------------------------------

INVALID_ROWS = {
    "restricted but policy": (
        "INSERT INTO documents (organization_id, sensitivity, access_mode) "
        "VALUES (:org, 'restricted', 'policy')",
        "ck_documents_restricted_is_grants_only",
    ),
    "unknown sensitivity": (
        "INSERT INTO documents (organization_id, sensitivity, access_mode) "
        "VALUES (:org, 'secret', 'policy')",
        "ck_documents_sensitivity",
    ),
    "unknown access mode": (
        "INSERT INTO documents (organization_id, sensitivity, access_mode) "
        "VALUES (:org, 'internal', 'public')",
        "ck_documents_access_mode",
    ),
    "missing sensitivity": (
        "INSERT INTO documents (organization_id, access_mode) VALUES (:org, 'policy')",
        "sensitivity",
    ),
    "null department element": (
        "INSERT INTO documents (organization_id, sensitivity, access_mode, department_ids) "
        "VALUES (:org, 'internal', 'policy', ARRAY[NULL]::uuid[])",
        "ck_documents_department_ids_no_nulls",
    ),
    "unknown document status": (
        "INSERT INTO documents (organization_id, sensitivity, access_mode, status) "
        "VALUES (:org, 'internal', 'policy', 'archived')",
        "ck_documents_status",
    ),
    "grant naming no principal": (
        "INSERT INTO document_grants (organization_id, document_id, principal_type, origin) "
        "VALUES (:org, :doc, 'membership', 'admin')",
        "ck_document_grants_one_principal",
    ),
    "grant type disagreeing with its column": (
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "department_id, origin) VALUES (:org, :doc, 'membership', :dept, 'admin')",
        "ck_document_grants_one_principal",
    ),
    "grant naming two principals": (
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "membership_id, department_id, origin) "
        "VALUES (:org, :doc, 'membership', :member, :dept, 'admin')",
        "ck_document_grants_one_principal",
    ),
    "unknown principal type": (
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "membership_id, origin) VALUES (:org, :doc, 'user', :member, 'admin')",
        "ck_document_grants_one_principal",
    ),
    "unknown grant origin": (
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "membership_id, origin) VALUES (:org, :doc, 'membership', :member, 'magic')",
        "ck_document_grants_origin",
    ),
    "source group without external ref": (
        "INSERT INTO access_groups (organization_id, origin, name) VALUES (:org, 'source', 'x')",
        "ck_access_groups_external_ref_iff_source",
    ),
    "admin group with external ref": (
        "INSERT INTO access_groups (organization_id, origin, external_ref, name) "
        "VALUES (:org, 'admin', 'C123', 'x')",
        "ck_access_groups_external_ref_iff_source",
    ),
}


@pytest.mark.parametrize("case", sorted(INVALID_ROWS))
async def test_malformed_access_state_is_rejected(
    superuser_engine: AsyncEngine, tenants: tuple[Tenant, Tenant], case: str
) -> None:
    a, _ = tenants
    sql, constraint = INVALID_ROWS[case]
    params = {
        "org": a.org,
        "doc": a.document,
        "dept": a.department,
        "member": a.member.membership_id,
    }
    used = {k: v for k, v in params.items() if f":{k}" in sql}
    with pytest.raises(IntegrityError, match=constraint):
        await _superuser_insert(superuser_engine, sql, **used)


# --- Lifecycle -------------------------------------------------------------------------


async def test_deleting_a_principal_deletes_its_grants(
    owner_db: Database, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    other = await create_member(owner_db, a.org, "other@org-a.example")
    await create_document(
        owner_db,
        a.org,
        sensitivity="restricted",
        access_mode="grants_only",
        grants=(
            ("membership", other.membership_id),
            ("department", a.department),
            ("group", a.group),
        ),
    )
    async with owner_db.tenant_transaction(a.org) as s:
        assert (await s.execute(text("SELECT count(*) FROM document_grants"))).scalar_one() == 3
        await s.execute(text("DELETE FROM memberships WHERE id = :m"), {"m": other.membership_id})
        await s.execute(text("DELETE FROM departments WHERE id = :d"), {"d": a.department})
        await s.execute(text("DELETE FROM access_groups WHERE id = :g"), {"g": a.group})
        assert (await s.execute(text("SELECT count(*) FROM document_grants"))).scalar_one() == 0


async def test_deleted_department_leaves_a_dangling_id_that_matches_no_one(
    owner_db: Database, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    document = await create_document(owner_db, a.org, department_ids=(a.department,))
    async with owner_db.tenant_transaction(a.org) as s:
        await s.execute(text("DELETE FROM departments WHERE id = :d"), {"d": a.department})
        stored: object = (
            await s.execute(
                text("SELECT department_ids FROM documents WHERE id = :d"), {"d": document}
            )
        ).scalar_one()
    assert stored == [a.department]  # kept: the document stays restricted (fail closed)


# --- Privileges -----------------------------------------------------------------------

PHASE_2_TABLES = ("access_groups", "access_group_members", "documents", "document_grants")


@pytest.mark.parametrize("table", PHASE_2_TABLES)
async def test_runtime_role_can_only_read_authorization_tables(
    superuser_engine: AsyncEngine, table: str
) -> None:
    async with superuser_engine.connect() as conn:
        held = {
            privilege
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES")
            if (
                await conn.execute(
                    text("SELECT has_table_privilege('lattice_app', :t, :p)"),
                    {"t": table, "p": privilege},
                )
            ).scalar_one()
        }
    assert held == {"SELECT"}


@pytest.mark.parametrize("table", PHASE_2_TABLES)
async def test_owner_role_is_subject_to_forced_rls_on_authorization_tables(
    owner_db: Database, tenants: tuple[Tenant, Tenant], table: str
) -> None:
    async with owner_db.unscoped_transaction() as s:
        assert (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0  # noqa: S608


async def test_runtime_role_cannot_write_through_rls_either(
    app_db: Database, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_db.tenant_transaction(a.org) as s:
            await s.execute(
                text("UPDATE documents SET sensitivity = 'internal' WHERE id = :d"),
                {"d": a.document},
            )
