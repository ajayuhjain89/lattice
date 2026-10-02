"""Access audit enforced by the database (migration 0013; ADR-014's residual risk).

Every access write by the API role (``lattice_app``) must commit together with an
``access.*`` audit event of the *same transaction* naming the same membership. The
check is a deferred constraint trigger, so the event may come after the change, and a
transaction that does not record one cannot commit. Operator tools (the owner role) and
the worker's source writes are not affected: they are not access changes by the API.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.db.engine import Database

from .factories import (
    create_account,
    create_department,
    create_document,
    create_group,
    create_member,
    create_oidc_connection,
    create_org,
    create_provisioned_member,
)

REFUSED = "without an audit event"


@dataclass
class World:
    org: uuid.UUID
    member: uuid.UUID
    other: uuid.UUID
    provisioned: uuid.UUID
    group: uuid.UUID
    source_group: uuid.UUID
    document: uuid.UUID
    department: uuid.UUID
    account: uuid.UUID
    connection: uuid.UUID


@pytest.fixture
async def world(owner_db: Database) -> World:
    org = await create_org(owner_db, "halden")
    connection = await create_oidc_connection(owner_db, org)
    return World(
        org=org,
        member=(await create_member(owner_db, org, "maya@halden.example")).membership_id,
        other=(await create_member(owner_db, org, "omar@halden.example")).membership_id,
        provisioned=(
            await create_provisioned_member(owner_db, org, connection, "p@halden.example", "e")
        ).membership_id,
        group=await create_group(owner_db, org, "Exec staff"),
        source_group=await create_group(owner_db, org, "acct-acme", origin="source"),
        document=await create_document(owner_db, org),
        department=await create_department(owner_db, org, "sales"),
        account=await create_account(owner_db, org, "acme"),
        connection=connection,
    )


async def _event(s: AsyncSession, org: uuid.UUID, membership: uuid.UUID) -> None:
    await s.execute(
        text(
            "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
            "VALUES (:o, 'member:test', 'access.member_changed', "
            "jsonb_build_object('membership_id', CAST(:m AS text)), 'test')"
        ),
        {"o": org, "m": str(membership)},
    )


# Each access write the API role may make, as (SQL, parameters) for a world.
WRITES: dict[str, Callable[[World], tuple[str, dict[str, object]]]] = {
    "membership update": lambda w: (
        "UPDATE memberships SET clearance = 'confidential' WHERE id = :m",
        {"m": w.member},
    ),
    "assignment insert": lambda w: (
        "INSERT INTO account_assignments (organization_id, membership_id, account_id) "
        "VALUES (:o, :m, :a)",
        {"o": w.org, "m": w.member, "a": w.account},
    ),
    "department insert": lambda w: (
        "INSERT INTO membership_departments (organization_id, membership_id, department_id) "
        "VALUES (:o, :m, :d)",
        {"o": w.org, "m": w.member, "d": w.department},
    ),
    "group member insert": lambda w: (
        "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
        "VALUES (:o, :g, :m)",
        {"o": w.org, "g": w.group, "m": w.member},
    ),
    "grant insert": lambda w: (
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "membership_id, origin) VALUES (:o, :d, 'membership', :m, 'admin')",
        {"o": w.org, "d": w.document, "m": w.member},
    ),
    "directory identity update": lambda w: (
        "UPDATE directory_identities SET user_name = 'renamed', version = version + 1 "
        "WHERE membership_id = :m",
        {"m": w.provisioned},
    ),
    "removal": lambda w: (
        "UPDATE memberships SET status = 'removed' WHERE id = :m",
        {"m": w.provisioned},
    ),
}


def _target(name: str, world: World) -> uuid.UUID:
    return world.provisioned if name in {"directory identity update", "removal"} else world.member


@pytest.mark.parametrize("name", sorted(WRITES))
async def test_an_access_write_without_an_event_cannot_commit(
    app_db: Database, world: World, name: str
) -> None:
    sql, params = WRITES[name](world)
    with pytest.raises(DBAPIError, match=REFUSED):
        async with app_db.tenant_transaction(world.org) as s:
            await s.execute(text(sql), params)


@pytest.mark.parametrize("name", sorted(WRITES))
async def test_an_event_for_another_member_does_not_count(
    app_db: Database, world: World, name: str
) -> None:
    sql, params = WRITES[name](world)
    with pytest.raises(DBAPIError, match=REFUSED):
        async with app_db.tenant_transaction(world.org) as s:
            await s.execute(text(sql), params)
            await _event(s, world.org, world.other)


@pytest.mark.parametrize("name", sorted(WRITES))
async def test_an_event_of_the_same_transaction_lets_it_commit(
    app_db: Database, world: World, name: str
) -> None:
    sql, params = WRITES[name](world)
    async with app_db.tenant_transaction(world.org) as s:
        # Deferred: the event may come after the change, as the API writes it.
        result = await s.execute(text(sql), params)
        await _event(s, world.org, _target(name, world))
    assert getattr(result, "rowcount", 0) == 1


async def test_an_event_of_an_earlier_transaction_does_not_count(
    app_db: Database, world: World
) -> None:
    async with app_db.tenant_transaction(world.org) as s:
        await _event(s, world.org, world.member)
    with pytest.raises(DBAPIError, match=REFUSED):
        async with app_db.tenant_transaction(world.org) as s:
            await s.execute(
                text("UPDATE memberships SET clearance = 'confidential' WHERE id = :m"),
                {"m": world.member},
            )


async def test_deletes_are_audited_too(app_db: Database, owner_db: Database, world: World) -> None:
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(
            text(
                "INSERT INTO account_assignments (organization_id, membership_id, account_id) "
                "VALUES (:o, :m, :a)"
            ),
            {"o": world.org, "m": world.member, "a": world.account},
        )
    with pytest.raises(DBAPIError, match=REFUSED):
        async with app_db.tenant_transaction(world.org) as s:
            await s.execute(
                text("DELETE FROM account_assignments WHERE membership_id = :m"),
                {"m": world.member},
            )


async def test_a_new_membership_needs_its_event(app_db: Database, world: World) -> None:
    with pytest.raises(DBAPIError, match=REFUSED):
        async with app_db.tenant_transaction(world.org) as s:
            user: uuid.UUID = (
                await s.execute(
                    text(
                        "INSERT INTO users (email, display_name, home_organization_id) "
                        "VALUES ('n@halden.example', 'N', :o) RETURNING id"
                    ),
                    {"o": world.org},
                )
            ).scalar_one()
            await s.execute(
                text("INSERT INTO memberships (organization_id, user_id) VALUES (:o, :u)"),
                {"o": world.org, "u": user},
            )


async def test_operator_and_worker_writes_are_not_access_changes_by_the_api(
    owner_db: Database, worker_db: Database, world: World
) -> None:
    # The seed and the operator commands record their own events; the database does
    # not demand one from them.
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(
            text("UPDATE memberships SET clearance = 'confidential' WHERE id = :m"),
            {"m": world.member},
        )
    # A sync mirrors a source group's members.
    async with worker_db.tenant_transaction(world.org) as s:
        await s.execute(
            text(
                "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
                "VALUES (:o, :g, :m)"
            ),
            {"o": world.org, "g": world.source_group, "m": world.member},
        )
