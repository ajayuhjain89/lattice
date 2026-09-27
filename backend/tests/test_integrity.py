"""Cross-organization references are rejected by the database itself (composite
foreign keys on (organization_id, id)), independent of application code and of RLS."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from lattice.db.engine import Database

from .factories import Member, create_member, create_org


async def _department(db: Database, org: uuid.UUID) -> uuid.UUID:
    async with db.tenant_transaction(org) as s:
        value: object = (
            await s.execute(
                text(
                    "INSERT INTO departments (organization_id, key, name) "
                    "VALUES (:o, 'sales', 'Sales') RETURNING id"
                ),
                {"o": org},
            )
        ).scalar_one()
    assert isinstance(value, uuid.UUID)
    return value


async def _account(db: Database, org: uuid.UUID) -> uuid.UUID:
    async with db.tenant_transaction(org) as s:
        value: object = (
            await s.execute(
                text(
                    "INSERT INTO accounts (organization_id, key, name) "
                    "VALUES (:o, 'acme-corp', 'Acme Corp') RETURNING id"
                ),
                {"o": org},
            )
        ).scalar_one()
    assert isinstance(value, uuid.UUID)
    return value


@pytest.fixture
async def members(owner_db: Database) -> tuple[Member, Member]:
    a = await create_member(owner_db, await create_org(owner_db, "org-a"), "a@a.example")
    b = await create_member(owner_db, await create_org(owner_db, "org-b"), "b@b.example")
    return a, b


async def test_membership_cannot_join_another_orgs_department(
    superuser_engine: AsyncEngine, owner_db: Database, members: tuple[Member, Member]
) -> None:
    a, b = members
    b_department = await _department(owner_db, b.organization_id)
    # The superuser bypasses RLS entirely, so only the constraint can stop this.
    for organization in (a.organization_id, b.organization_id):
        with pytest.raises(IntegrityError, match="fk_membership_departments"):
            async with superuser_engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO membership_departments "
                        "(organization_id, membership_id, department_id) VALUES (:o, :m, :d)"
                    ),
                    {"o": organization, "m": a.membership_id, "d": b_department},
                )


async def test_membership_cannot_be_assigned_another_orgs_account(
    superuser_engine: AsyncEngine, owner_db: Database, members: tuple[Member, Member]
) -> None:
    a, b = members
    b_account = await _account(owner_db, b.organization_id)
    for organization in (a.organization_id, b.organization_id):
        with pytest.raises(IntegrityError, match="fk_account_assignments"):
            async with superuser_engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO account_assignments "
                        "(organization_id, membership_id, account_id) VALUES (:o, :m, :a)"
                    ),
                    {"o": organization, "m": a.membership_id, "a": b_account},
                )


async def test_session_requires_a_membership_in_its_organization(
    superuser_engine: AsyncEngine, members: tuple[Member, Member]
) -> None:
    a, b = members
    with pytest.raises(IntegrityError, match="fk_sessions_organization_id_user_id_memberships"):
        async with superuser_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO sessions (token_hash, user_id, organization_id, expires_at) "
                    "VALUES (:h, :u, :o, now() + interval '1 hour')"
                ),
                {"h": b"\x02" * 32, "u": a.user_id, "o": b.organization_id},
            )


async def test_same_org_links_are_accepted(
    owner_db: Database, members: tuple[Member, Member]
) -> None:
    a, _ = members
    department = await _department(owner_db, a.organization_id)
    account = await _account(owner_db, a.organization_id)
    async with owner_db.tenant_transaction(a.organization_id) as s:
        await s.execute(
            text(
                "INSERT INTO membership_departments (organization_id, membership_id, department_id)"
                " VALUES (:o, :m, :d)"
            ),
            {"o": a.organization_id, "m": a.membership_id, "d": department},
        )
        await s.execute(
            text(
                "INSERT INTO account_assignments (organization_id, membership_id, account_id) "
                "VALUES (:o, :m, :a)"
            ),
            {"o": a.organization_id, "m": a.membership_id, "a": account},
        )


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("organizations", "status", "archived"),
        ("memberships", "clearance", "restricted"),
        ("memberships", "account_scope", "some"),
        ("memberships", "status", "unknown"),
    ],
)
async def test_enumerated_columns_reject_unknown_values(
    superuser_engine: AsyncEngine,
    members: tuple[Member, Member],
    table: str,
    column: str,
    value: str,
) -> None:
    with pytest.raises(IntegrityError, match="ck_"):
        async with superuser_engine.begin() as conn:
            await conn.execute(text(f"UPDATE {table} SET {column} = :v"), {"v": value})  # noqa: S608


async def test_session_token_hash_must_be_sha256_length(
    superuser_engine: AsyncEngine, members: tuple[Member, Member]
) -> None:
    a, _ = members
    with pytest.raises(IntegrityError, match="ck_sessions_token_hash_sha256"):
        async with superuser_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO sessions (token_hash, user_id, organization_id, expires_at) "
                    "VALUES (:h, :u, :o, now() + interval '1 hour')"
                ),
                {"h": b"short", "u": a.user_id, "o": a.organization_id},
            )
