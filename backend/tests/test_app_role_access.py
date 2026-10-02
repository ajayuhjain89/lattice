"""The API role's access-management privileges (migration 0012, Phase 9; 0013, Phase 10).

``lattice_app`` may write exactly what access management needs, and the database --
not only the code -- limits it: memberships' status/clearance/scope columns (never
``is_admin``; ``removed`` only for a provisioned member), assignments, departments,
members of administrator-defined groups, administrator membership grants, and
``access.*`` audit events attributed to a member or a SCIM token. Phase 10 adds
provisioning: users owned by the organization, memberships with least-access defaults
for them only, directory identities; and sign-in attempts and rate-limit counters.
Every access write must commit with an audit event of its own transaction
(``tests/test_access_audit_enforcement.py``). Everything else stays read-only or
invisible."""

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
    create_oidc_connection,
    create_org,
    create_provisioned_member,
)

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")

# Table-level privileges of lattice_app (column-level UPDATE on memberships below).
APP_TABLES = {
    "organizations": {"SELECT"},
    "users": {"SELECT", "INSERT"},
    "external_identities": {"SELECT"},
    "memberships": {"SELECT"},  # INSERT and UPDATE are column grants (below)
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
    # Phase 10
    "oidc_connections": set(),
    "oidc_login_attempts": {"SELECT", "INSERT", "DELETE"},
    "directory_identities": {"SELECT", "INSERT"},
    "scim_tokens": set(),
    "rate_limit_counters": {"SELECT", "INSERT"},
}
# Column-level privileges (table-level ones above).
COLUMN_PRIVILEGES = {
    ("memberships", "INSERT"): {"organization_id", "user_id", "status"},
    ("memberships", "UPDATE"): {
        "status",
        "clearance",
        "account_scope",
        "authz_updated_at",
        "updated_at",
    },
    ("users", "UPDATE"): {"email", "display_name", "updated_at"},
    ("directory_identities", "UPDATE"): {
        "user_name",
        "given_name",
        "family_name",
        "version",
        "updated_at",
    },
    ("oidc_login_attempts", "UPDATE"): {"consumed_at"},
    ("rate_limit_counters", "UPDATE"): {"window_start", "count"},
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


@pytest.mark.parametrize(("key", "expected"), sorted(COLUMN_PRIVILEGES.items()))
async def test_app_column_privileges_are_exact(
    superuser_engine: AsyncEngine, key: tuple[str, str], expected: set[str]
) -> None:
    table, privilege = key
    async with superuser_engine.connect() as conn:
        columns: set[str] = set(
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = :t "
                        "AND has_column_privilege('lattice_app', 'public.' || :t, "
                        "column_name, :p)"
                    ),
                    {"t": table, "p": privilege},
                )
            ).scalars()
        )
    assert columns == expected


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


async def _run(
    db: Database,
    org: uuid.UUID,
    sql: str,
    *,
    audit_for: uuid.UUID | None = None,
    **params: object,
) -> int:
    """Run ``sql`` in one transaction; ``audit_for`` also records an access event for
    that membership, as every access write by the API must (migration 0013)."""
    async with db.tenant_transaction(org) as s:
        result = await s.execute(text(sql), params)
        if audit_for is not None:
            await s.execute(
                text(
                    "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
                    "VALUES (:o, 'member:test', 'access.member_changed', "
                    "jsonb_build_object('membership_id', CAST(:m AS text)), 'test')"
                ),
                {"o": org, "m": str(audit_for)},
            )
        return int(getattr(result, "rowcount", 0) or 0)


async def test_app_changes_clearance_scope_and_suspension(app_db: Database, world: World) -> None:
    changed = await _run(
        app_db,
        world.org,
        "UPDATE memberships SET clearance = 'confidential', account_scope = 'all', "
        "status = 'suspended', authz_updated_at = now() WHERE id = :m",
        audit_for=world.member,
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
        await _run(
            app_db,
            world.org,
            insert,
            audit_for=world.member,
            o=world.org,
            g=world.admin_group,
            m=world.member,
        )
        == 1
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
        audit_for=world.member,
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
    [
        ("operator:seed", "access.member_changed"),
        ("member:x", "provider.configured"),
        ("scim:x", "sso.connection_created"),
        ("idp:x", "access.member_changed"),
    ],
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
    assert (
        await _run(
            app_db,
            world.org,
            insert,
            o=world.org,
            actor=f"scim:{uuid.uuid4()}",
            action="access.member_changed",
        )
        == 1
    )
    with pytest.raises(DBAPIError, match="permission denied"):
        await _run(app_db, world.org, "DELETE FROM audit_events")


# --- Provisioning (Phase 10, migration 0013) --------------------------------------------

_INSERT_OWNED_USER = (
    "INSERT INTO users (email, display_name, home_organization_id) VALUES (:e, 'P', :h) "
    "RETURNING id"
)


async def _provision(
    app_db: Database, org: uuid.UUID, email: str, status: str = "active"
) -> tuple[uuid.UUID, uuid.UUID]:
    """(user, membership) inserted as the API role, with the event 0013 requires."""
    async with app_db.tenant_transaction(org) as s:
        user: uuid.UUID = (
            await s.execute(text(_INSERT_OWNED_USER), {"e": email, "h": org})
        ).scalar_one()
        membership: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO memberships (organization_id, user_id, status) "
                    "VALUES (:o, :u, :st) RETURNING id"
                ),
                {"o": org, "u": user, "st": status},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
                "VALUES (:o, 'scim:test', 'access.member_changed', "
                "jsonb_build_object('membership_id', CAST(:m AS text)), 'provisioned')"
            ),
            {"o": org, "m": str(membership)},
        )
    return user, membership


async def test_app_provisions_an_owned_user_with_least_access(
    app_db: Database, owner_db: Database, world: World
) -> None:
    _, membership = await _provision(app_db, world.org, "new@halden.example")
    async with owner_db.tenant_transaction(world.org) as s:
        row = (
            await s.execute(
                text(
                    "SELECT is_admin, clearance, account_scope, role_label, status "
                    "FROM memberships WHERE id = :m"
                ),
                {"m": membership},
            )
        ).one()
    assert tuple(row) == (False, "internal", "assigned", None, "active")


@pytest.mark.parametrize("home", ["none", "other"])
async def test_app_cannot_create_a_global_user_or_another_organizations_user(
    app_db: Database, world: World, home: str
) -> None:
    with pytest.raises(DBAPIError, match=RLS_REFUSED):
        await _run(
            app_db,
            world.org,
            _INSERT_OWNED_USER,
            e="x@x.example",
            h=None if home == "none" else world.other_org,
        )


async def test_app_cannot_add_a_global_user_or_a_removed_status(
    app_db: Database, owner_db: Database, world: World
) -> None:
    # A global user (member of another organization only) cannot be attached here.
    outsider = await create_member(owner_db, world.other_org, "outsider@veridian.example")
    with pytest.raises(DBAPIError, match=RLS_REFUSED):
        await _run(
            app_db,
            world.org,
            "INSERT INTO memberships (organization_id, user_id) VALUES (:o, :u)",
            o=world.org,
            u=outsider.user_id,
        )
    with pytest.raises(DBAPIError, match=RLS_REFUSED):
        await _provision(app_db, world.org, "removed@halden.example", status="removed")


async def test_an_owned_user_belongs_to_no_other_organization(
    app_db: Database, owner_db: Database, world: World
) -> None:
    user, _ = await _provision(app_db, world.org, "own@halden.example")
    # Not even the owner role can give it a membership elsewhere.
    with pytest.raises(DBAPIError, match="another organization"):
        await _run(
            owner_db,
            world.other_org,
            "INSERT INTO memberships (organization_id, user_id) VALUES (:o, :u)",
            o=world.other_org,
            u=user,
        )


async def test_app_updates_only_users_its_organization_owns(
    app_db: Database, owner_db: Database, world: World
) -> None:
    user, _ = await _provision(app_db, world.org, "own@halden.example")
    owned = await _run(
        app_db, world.org, "UPDATE users SET display_name = 'Renamed' WHERE id = :u", u=user
    )
    # Maya is a global user with a membership here: her profile is not the org's to edit.
    global_ = await _run(
        app_db,
        world.org,
        "UPDATE users SET display_name = 'Renamed' WHERE email = 'maya@halden.example'",
    )
    assert (owned, global_) == (1, 0)
    # And another organization cannot see the owned user at all.
    async with app_db.tenant_transaction(world.other_org) as s:
        seen: int = (
            await s.execute(text("SELECT count(*) FROM users WHERE id = :u"), {"u": user})
        ).scalar_one()
    assert seen == 0


async def test_app_removes_only_provisioned_members(
    app_db: Database, owner_db: Database, world: World
) -> None:
    connection = await create_oidc_connection(owner_db, world.org)
    member = await create_provisioned_member(
        owner_db, world.org, connection, "p@halden.example", "ext-p"
    )
    removed = await _run(
        app_db,
        world.org,
        "UPDATE memberships SET status = 'removed' WHERE id = :m",
        audit_for=member.membership_id,
        m=member.membership_id,
    )
    assert removed == 1
    # Removal is terminal for the API.
    assert (
        await _run(
            app_db,
            world.org,
            "UPDATE memberships SET status = 'active' WHERE id = :m",
            m=member.membership_id,
        )
        == 0
    )


async def test_app_links_directory_identities_inside_its_organization_only(
    app_db: Database, owner_db: Database, world: World
) -> None:
    own = await create_oidc_connection(owner_db, world.org)
    foreign = await create_oidc_connection(owner_db, world.other_org)
    insert = (
        "INSERT INTO directory_identities (organization_id, connection_id, membership_id, "
        "external_id, user_name) VALUES (:o, :c, :m, 'x', 'x')"
    )
    with pytest.raises(DBAPIError, match=r"row-level security|foreign key"):
        await _run(
            app_db,
            world.org,
            insert,
            audit_for=world.member,
            o=world.org,
            c=foreign,
            m=world.member,
        )
    assert (
        await _run(
            app_db, world.org, insert, audit_for=world.member, o=world.org, c=own, m=world.member
        )
        == 1
    )


async def test_app_deletes_only_finished_sign_in_attempts(
    app_db: Database, owner_db: Database, world: World
) -> None:
    connection = await create_oidc_connection(owner_db, world.org)
    async with owner_db.tenant_transaction(world.org) as s:
        for key, expires, consumed in (
            ("live", "now() + interval '5 minutes'", "NULL"),
            ("expired", "now() - interval '1 second'", "NULL"),
            ("used", "now() + interval '5 minutes'", "now()"),
        ):
            await s.execute(
                text(
                    "INSERT INTO oidc_login_attempts (organization_id, connection_id, "  # noqa: S608 - fixed values
                    "state_hash, binding_hash, nonce, code_verifier, created_at, expires_at, "
                    "consumed_at) VALUES (:o, :c, sha256(convert_to(:k, 'UTF8')), "
                    f"sha256('b'::bytea), repeat('n', 22), repeat('v', 43), now() - interval "
                    f"'1 minute', {expires}, {consumed})"
                ),
                {"o": world.org, "c": connection, "k": key},
            )
    assert await _run(app_db, world.org, "DELETE FROM oidc_login_attempts") == 2
