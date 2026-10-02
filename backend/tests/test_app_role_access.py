"""The API role's access-management privileges (migration 0012, Phase 9).

``lattice_app`` may write exactly what access management needs, and the database --
not only the code -- limits it: memberships' status/clearance/scope columns (never
``is_admin``, never ``removed``), assignments, departments, members of
administrator-defined groups, administrator membership grants, and ``access.*`` audit
events attributed to a member. Everything else stays read-only or invisible."""

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from lattice.db.engine import Database

from .factories import (
    add_grant,
    create_account,
    create_department,
    create_document,
    create_group,
    create_member,
    create_org,
)

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")

# Table-level privileges of lattice_app (column-level UPDATE on memberships below).
APP_TABLES = {
    "organizations": {"SELECT"},
    "users": {"SELECT"},
    "external_identities": {"SELECT"},
    "memberships": {"SELECT"},
    "sessions": {"SELECT", "INSERT"},
    "departments": {"SELECT"},
    "membership_departments": {"SELECT", "INSERT", "DELETE"},
    "accounts": {"SELECT"},
    "account_assignments": {"SELECT", "INSERT", "DELETE"},
    "access_groups": {"SELECT"},
    "access_group_members": {"SELECT", "INSERT", "DELETE"},
    "documents": {"SELECT"},
    "document_grants": {"SELECT", "INSERT", "DELETE"},
    "chunks": {"SELECT"},
    "sources": {"SELECT"},
    "source_sync_runs": {"SELECT"},
    "sync_record_failures": {"SELECT"},
    "org_settings": {"SELECT"},
    "model_providers": {"SELECT"},
    "provider_credentials": {"SELECT"},
    "query_traces": {"SELECT", "INSERT"},
    "audit_events": {"SELECT", "INSERT"},
    "jobs": {"SELECT"},
}
MEMBERSHIP_UPDATE_COLUMNS = {
    "status",
    "clearance",
    "account_scope",
    "authz_updated_at",
    "updated_at",
}
RLS_REFUSED = r"row-level security"


async def _held(engine: AsyncEngine, table: str) -> set[str]:
    async with engine.connect() as conn:
        return {
            p
            for p in PRIVILEGES
            if (
                await conn.execute(
                    text("SELECT has_table_privilege('lattice_app', :t, :p)"), {"t": table, "p": p}
                )
            ).scalar_one()
        }


@pytest.mark.parametrize(("table", "expected"), sorted(APP_TABLES.items()))
async def test_app_table_privileges_are_exact(
    superuser_engine: AsyncEngine, table: str, expected: set[str]
) -> None:
    assert await _held(superuser_engine, table) == expected


async def test_every_table_is_in_the_app_inventory(superuser_engine: AsyncEngine) -> None:
    async with superuser_engine.connect() as conn:
        tables: set[str] = set(
            (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
                        "AND c.relkind IN ('r', 'p') AND c.relname <> 'alembic_version'"
                    )
                )
            ).scalars()
        )
    assert tables == set(APP_TABLES)


async def test_app_updates_only_the_access_columns_of_memberships(
    superuser_engine: AsyncEngine,
) -> None:
    async with superuser_engine.connect() as conn:
        columns: set[str] = set(
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'memberships' "
                        "AND has_column_privilege('lattice_app', 'public.memberships', "
                        "column_name, 'UPDATE')"
                    )
                )
            ).scalars()
        )
    assert columns == MEMBERSHIP_UPDATE_COLUMNS


@dataclass
class World:
    org: uuid.UUID
    other_org: uuid.UUID
    member: uuid.UUID
    removed: uuid.UUID
    admin_group: uuid.UUID
    source_group: uuid.UUID
    document: uuid.UUID
    department: uuid.UUID
    account: uuid.UUID


@pytest.fixture
async def world(owner_db: Database) -> World:
    org = await create_org(owner_db, "halden")
    other = await create_org(owner_db, "veridian")
    member = await create_member(owner_db, org, "maya@halden.example")
    removed = await create_member(owner_db, org, "gone@halden.example", status="removed")
    return World(
        org=org,
        other_org=other,
        member=member.membership_id,
        removed=removed.membership_id,
        admin_group=await create_group(owner_db, org, "Exec staff"),
        source_group=await create_group(owner_db, org, "acct-acme", origin="source"),
        document=await create_document(owner_db, org),
        department=await create_department(owner_db, org, "sales"),
        account=await create_account(owner_db, org, "acme"),
    )


async def _run(db: Database, org: uuid.UUID, sql: str, **params: object) -> int:
    async with db.tenant_transaction(org) as s:
        result = await s.execute(text(sql), params)
        return int(getattr(result, "rowcount", 0) or 0)


async def test_app_changes_clearance_scope_and_suspension(app_db: Database, world: World) -> None:
    changed = await _run(
        app_db,
        world.org,
        "UPDATE memberships SET clearance = 'confidential', account_scope = 'all', "
        "status = 'suspended', authz_updated_at = now() WHERE id = :m",
        m=world.member,
    )
    assert changed == 1


async def test_app_cannot_remove_a_member_or_touch_a_removed_one(
    app_db: Database, world: World
) -> None:
    with pytest.raises(DBAPIError, match=RLS_REFUSED):
        await _run(
            app_db,
            world.org,
            "UPDATE memberships SET status = 'removed' WHERE id = :m",
            m=world.member,
        )
    assert (
        await _run(
            app_db,
            world.org,
            "UPDATE memberships SET status = 'active' WHERE id = :m",
            m=world.removed,
        )
        == 0
    )


async def test_app_cannot_make_anyone_an_administrator(app_db: Database, world: World) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await _run(
            app_db,
            world.org,
            "UPDATE memberships SET is_admin = true WHERE id = :m",
            m=world.member,
        )


async def test_app_edits_members_of_administrator_groups_only(
    app_db: Database, world: World
) -> None:
    insert = (
        "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
        "VALUES (:o, :g, :m)"
    )
    assert (
        await _run(app_db, world.org, insert, o=world.org, g=world.admin_group, m=world.member) == 1
    )
    with pytest.raises(DBAPIError, match=RLS_REFUSED):
        await _run(app_db, world.org, insert, o=world.org, g=world.source_group, m=world.member)


async def test_app_cannot_remove_members_of_a_source_group(
    owner_db: Database, app_db: Database, world: World
) -> None:
    await _run(
        owner_db,
        world.org,
        "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
        "VALUES (:o, :g, :m)",
        o=world.org,
        g=world.source_group,
        m=world.member,
    )
    removed = await _run(
        app_db,
        world.org,
        "DELETE FROM access_group_members WHERE group_id = :g",
        g=world.source_group,
    )
    assert removed == 0


@pytest.mark.parametrize(
    ("principal_type", "column", "origin"),
    [("membership", "membership_id", "source"), ("group", "group_id", "admin")],
)
async def test_app_inserts_administrator_membership_grants_only(
    app_db: Database, world: World, principal_type: str, column: str, origin: str
) -> None:
    principal = world.member if principal_type == "membership" else world.admin_group
    with pytest.raises(DBAPIError, match=RLS_REFUSED):
        await _run(
            app_db,
            world.org,
            "INSERT INTO document_grants (organization_id, document_id, principal_type, "  # noqa: S608 - fixed column
            f"{column}, origin) VALUES (:o, :d, :t, :p, :origin)",
            o=world.org,
            d=world.document,
            t=principal_type,
            p=principal,
            origin=origin,
        )
    added = await _run(
        app_db,
        world.org,
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "membership_id, origin) VALUES (:o, :d, 'membership', :m, 'admin')",
        o=world.org,
        d=world.document,
        m=world.member,
    )
    assert added == 1


async def test_app_cannot_delete_source_grants(
    owner_db: Database, app_db: Database, world: World
) -> None:
    await add_grant(
        owner_db, world.org, world.document, "membership", world.member, origin="source"
    )
    await add_grant(owner_db, world.org, world.document, "group", world.admin_group)
    assert await _run(app_db, world.org, "DELETE FROM document_grants") == 0


async def test_app_cannot_write_another_organizations_access(
    app_db: Database, world: World
) -> None:
    with pytest.raises(DBAPIError, match=RLS_REFUSED):
        await _run(
            app_db,
            world.org,
            "INSERT INTO account_assignments (organization_id, membership_id, account_id) "
            "VALUES (:o, :m, :a)",
            o=world.other_org,
            m=world.member,
            a=world.account,
        )


@pytest.mark.parametrize(
    ("actor", "action"),
    [("operator:seed", "access.member_changed"), ("member:x", "provider.configured")],
)
async def test_app_records_only_access_events_by_a_member(
    app_db: Database, world: World, actor: str, action: str
) -> None:
    insert = (
        "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
        "VALUES (:o, :actor, :action, '{}', 'because')"
    )
    with pytest.raises(DBAPIError, match=RLS_REFUSED):
        await _run(app_db, world.org, insert, o=world.org, actor=actor, action=action)
    assert (
        await _run(
            app_db,
            world.org,
            insert,
            o=world.org,
            actor=f"member:{world.member}",
            action="access.member_changed",
        )
        == 1
    )
    with pytest.raises(DBAPIError, match="permission denied"):
        await _run(app_db, world.org, "DELETE FROM audit_events")
