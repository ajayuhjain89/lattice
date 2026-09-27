"""``access_groups.key`` (decision D-9, migration 0004): a stable, human-readable,
organization-scoped identity for access groups.

Constraint tests insert as the superuser, which bypasses row-level security, so the
constraints themselves -- not RLS -- are what is being tested.
"""

import asyncio
import uuid

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from lattice.db.engine import Database

from .conftest import alembic_config
from .factories import create_group, create_org


async def _insert_group(engine: AsyncEngine, org: uuid.UUID, key: str | None) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO access_groups (organization_id, key, origin, name) "
                "VALUES (:o, :k, 'admin', 'Some group')"
            ),
            {"o": org, "k": key},
        )


async def test_key_is_unique_within_an_organization(
    owner_db: Database, superuser_engine: AsyncEngine
) -> None:
    org = await create_org(owner_db, "org-a")
    await _insert_group(superuser_engine, org, "exec-staff")
    with pytest.raises(IntegrityError, match="uq_access_groups_organization_id_key"):
        await _insert_group(superuser_engine, org, "exec-staff")


async def test_the_same_key_may_exist_in_different_organizations(
    owner_db: Database, superuser_engine: AsyncEngine
) -> None:
    a = await create_org(owner_db, "org-a")
    b = await create_org(owner_db, "org-b")
    await _insert_group(superuser_engine, a, "exec-staff")
    await _insert_group(superuser_engine, b, "exec-staff")
    async with superuser_engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT organization_id FROM access_groups WHERE key = 'exec-staff'")
            )
        ).all()
    assert {r[0] for r in rows} == {a, b}


@pytest.mark.parametrize(
    ("key", "constraint"),
    [
        (None, "not-null"),
        ("", "ck_access_groups_key_format"),
        ("Exec-Staff", "ck_access_groups_key_format"),  # citext: checked on the text value
        ("exec staff", "ck_access_groups_key_format"),
        ("-exec", "ck_access_groups_key_format"),
        ("exec_staff", "ck_access_groups_key_format"),
        ("x" * 64, "ck_access_groups_key_format"),
    ],
)
async def test_key_is_required_and_human_readable(
    owner_db: Database, superuser_engine: AsyncEngine, key: str | None, constraint: str
) -> None:
    org = await create_org(owner_db, "org-a")
    with pytest.raises(IntegrityError, match=constraint):
        await _insert_group(superuser_engine, org, key)


async def test_a_63_character_key_is_accepted(
    owner_db: Database, superuser_engine: AsyncEngine
) -> None:
    org = await create_org(owner_db, "org-a")
    await _insert_group(superuser_engine, org, "x" * 63)


async def test_renaming_a_group_keeps_its_key_and_identity(owner_db: Database) -> None:
    org = await create_org(owner_db, "org-a")
    group = await create_group(owner_db, org, "Exec staff", key="exec-staff")
    async with owner_db.tenant_transaction(org) as s:
        await s.execute(
            text("UPDATE access_groups SET name = 'Executive staff' WHERE id = :g"), {"g": group}
        )
        row = (
            await s.execute(
                text("SELECT id, key, name FROM access_groups WHERE id = :g"), {"g": group}
            )
        ).one()
    assert tuple(row) == (group, "exec-staff", "Executive staff")


async def test_migration_backfills_existing_groups_with_a_deterministic_key(
    owner_db: Database, app_db: Database, superuser_engine: AsyncEngine
) -> None:
    """A database that already holds groups (created under 0003, before keys existed)
    upgrades cleanly: each group gets ``group-<id>``, in every organization."""
    try:
        await asyncio.to_thread(command.downgrade, alembic_config(), "0003")
        await owner_db.engine.dispose()
        groups: dict[uuid.UUID, uuid.UUID] = {}
        for slug in ("org-a", "org-b"):
            org = await create_org(owner_db, slug)
            async with owner_db.tenant_transaction(org) as s:
                groups[
                    (
                        await s.execute(
                            text(
                                "INSERT INTO access_groups (organization_id, origin, name) "
                                "VALUES (:o, 'admin', 'Exec staff') RETURNING id"
                            ),
                            {"o": org},
                        )
                    ).scalar_one()
                ] = org
        await owner_db.engine.dispose()
        await asyncio.to_thread(command.upgrade, alembic_config(), "head")
        async with superuser_engine.connect() as conn:
            rows = (await conn.execute(text("SELECT id, key FROM access_groups"))).all()
        keys = {row[0]: row[1] for row in rows}
        assert keys == {g: f"group-{g}" for g in groups}
    finally:
        await asyncio.to_thread(command.upgrade, alembic_config(), "head")
        # Pooled connections cache prepared statements for the old table OIDs.
        await owner_db.engine.dispose()
        await app_db.engine.dispose()
        await superuser_engine.dispose()
