"""The final privilege state of lattice_auth, the NOLOGIN owner of the SECURITY DEFINER
pre-authentication lookups (docs/data-model.md §8): the session and dev-identity lookups
(Phase 1) and the OIDC connection and SCIM token lookups (Phase 10, migration 0013).

Invariant: lattice_auth can own and run exactly those functions and read exactly
the rows they need. It cannot log in, cannot create objects anywhere, cannot write,
and cannot read any other tenant table. Only lattice_owner (and superusers) can act as
it, and lattice_owner does not inherit its privileges.
"""

import asyncio
import hashlib
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from lattice.db.deployment import LATTICE_ROLES
from lattice.db.engine import Database
from lattice.identity.sessions import SessionPolicy, SessionService

from .conftest import alembic_config
from .factories import create_member, create_org

LOOKUP_FUNCTIONS = {
    "lattice_resolve_session",
    "lattice_identity_memberships",
    "lattice_oidc_connection",
    "lattice_resolve_scim_token",
}
# P11.4: the shared pre-authentication counters (written, not read for a lookup).
COUNTER_FUNCTIONS = {
    "lattice_pre_auth_hit": "lattice_app",
    "lattice_pre_auth_retention": "lattice_worker",
}
COUNTER_TABLE = "pre_auth_rate_limits"
# Exactly what the lookup functions read, and nothing else.
READABLE_TABLES = {
    "organizations",
    "users",
    "external_identities",
    "memberships",
    "sessions",
    "oidc_connections",
    "scim_tokens",
}
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


async def _rows(engine: AsyncEngine, sql: str) -> list[tuple[object, ...]]:
    async with engine.connect() as conn:
        return [tuple(r) for r in (await conn.execute(text(sql))).all()]


async def test_role_attributes(superuser_engine: AsyncEngine) -> None:
    rows = await _rows(
        superuser_engine,
        "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, "
        "rolreplication FROM pg_roles WHERE rolname = 'lattice_auth'",
    )
    assert rows == [(False, False, False, False, False, False)]


async def test_no_create_privilege_on_any_schema_or_the_database(
    superuser_engine: AsyncEngine,
) -> None:
    schemas = await _rows(
        superuser_engine,
        "SELECT nspname, has_schema_privilege('lattice_auth', oid, 'CREATE') FROM pg_namespace "
        "WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema'",
    )
    assert ("public", False) in schemas
    assert [name for name, can_create in schemas if can_create] == []

    database = await _rows(
        superuser_engine,
        "SELECT has_database_privilege('lattice_auth', current_database(), 'CREATE'), "
        "has_database_privilege('lattice_auth', current_database(), 'TEMPORARY'), "
        "has_database_privilege('lattice_auth', current_database(), 'CONNECT')",
    )
    assert database == [(False, False, False)]
    # USAGE is still required: the definer functions resolve public.* as lattice_auth.
    usage = await _rows(
        superuser_engine, "SELECT has_schema_privilege('lattice_auth', 'public', 'USAGE')"
    )
    assert usage == [(True,)]


async def test_owns_exactly_the_lookup_functions(superuser_engine: AsyncEngine) -> None:
    owned = await _rows(
        superuser_engine,
        """
        SELECT 'function', proname FROM pg_proc WHERE proowner = 'lattice_auth'::regrole
        UNION ALL
        SELECT 'relation', relname FROM pg_class WHERE relowner = 'lattice_auth'::regrole
        UNION ALL
        SELECT 'type', typname FROM pg_type WHERE typowner = 'lattice_auth'::regrole
        UNION ALL
        SELECT 'schema', nspname FROM pg_namespace WHERE nspowner = 'lattice_auth'::regrole
        """,
    )
    assert {(kind, name) for kind, name in owned} == {
        ("function", f) for f in (*LOOKUP_FUNCTIONS, *COUNTER_FUNCTIONS)
    }


async def test_lookup_functions_are_narrow_definer_functions(superuser_engine: AsyncEngine) -> None:
    rows = await _rows(
        superuser_engine,
        """
        SELECT p.proname, p.prosecdef, p.proconfig,
               array(SELECT DISTINCT coalesce(r.rolname, 'PUBLIC')
                     FROM aclexplode(p.proacl) a LEFT JOIN pg_roles r ON r.oid = a.grantee
                     WHERE a.privilege_type = 'EXECUTE' ORDER BY 1)
        FROM pg_proc p
        WHERE p.proname = ANY(ARRAY['lattice_resolve_session', 'lattice_identity_memberships',
                                    'lattice_oidc_connection', 'lattice_resolve_scim_token'])
        """,
    )
    assert {r[0] for r in rows} == LOOKUP_FUNCTIONS
    for name, security_definer, config, executors in rows:
        assert security_definer, name
        assert config == ["search_path=pg_catalog, public, pg_temp"], name
        # Only the owner and the runtime role may execute; never PUBLIC.
        assert executors == ["lattice_app", "lattice_auth"], name
    can_execute = await _rows(
        superuser_engine,
        "SELECT rolname FROM pg_roles WHERE NOT rolsuper AND rolname <> 'lattice_auth' "
        "AND has_function_privilege(oid, 'lattice_resolve_session(bytea)', 'EXECUTE') "
        "ORDER BY 1",
    )
    assert can_execute == [("lattice_app",)]


async def test_table_privileges_are_exactly_select_on_the_lookup_tables(
    superuser_engine: AsyncEngine,
) -> None:
    tables = [
        str(r[0])
        for r in await _rows(
            superuser_engine,
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')",
        )
    ]
    assert set(tables) >= READABLE_TABLES
    async with superuser_engine.connect() as conn:
        for table in tables:
            for privilege in TABLE_PRIVILEGES:
                held: object = (
                    await conn.execute(
                        text("SELECT has_table_privilege('lattice_auth', :t, :p)"),
                        {"t": f"public.{table}", "p": privilege},
                    )
                ).scalar_one()
                if table == COUNTER_TABLE:
                    expected = privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
                else:
                    expected = privilege == "SELECT" and table in READABLE_TABLES
                assert held is expected, f"{table}: {privilege}"
            if table not in READABLE_TABLES and table != COUNTER_TABLE:
                any_column: object = (
                    await conn.execute(
                        text("SELECT has_any_column_privilege('lattice_auth', :t, 'SELECT')"),
                        {"t": f"public.{table}"},
                    )
                ).scalar_one()
                assert any_column is False, table


async def test_only_select_policies_target_lattice_auth(superuser_engine: AsyncEngine) -> None:
    policies = await _rows(
        superuser_engine,
        "SELECT tablename, policyname, cmd FROM pg_policies WHERE 'lattice_auth' = ANY(roles)",
    )
    assert {(t, p, c) for t, p, c in policies} == {
        (t, "auth_lookup", "SELECT") for t in READABLE_TABLES
    } | {(COUNTER_TABLE, "auth_counters", "ALL")}


async def test_counter_functions_are_narrow_definer_functions(
    superuser_engine: AsyncEngine,
) -> None:
    rows = await _rows(
        superuser_engine,
        """
        SELECT p.proname, p.prosecdef, p.proconfig,
               array(SELECT DISTINCT coalesce(r.rolname, 'PUBLIC')
                     FROM aclexplode(p.proacl) a LEFT JOIN pg_roles r ON r.oid = a.grantee
                     WHERE a.privilege_type = 'EXECUTE' ORDER BY 1)
        FROM pg_proc p
        WHERE p.proname IN ('lattice_pre_auth_hit', 'lattice_pre_auth_retention')
        """,
    )
    assert {r[0] for r in rows} == set(COUNTER_FUNCTIONS)
    for name, security_definer, config, executors in rows:
        assert security_definer, name
        assert config == ["search_path=pg_catalog, public, pg_temp"], name
        assert executors == sorted([COUNTER_FUNCTIONS[str(name)], "lattice_auth"]), name


async def test_only_lattice_owner_can_act_as_lattice_auth_and_it_does_not_inherit(
    superuser_engine: AsyncEngine,
) -> None:
    edges = await _rows(
        superuser_engine,
        """
        SELECT g.rolname AS granted_role, m.rolname AS member, am.inherit_option, am.set_option
        FROM pg_auth_members am
        JOIN pg_roles g ON g.oid = am.roleid JOIN pg_roles m ON m.oid = am.member
        WHERE g.rolname = 'lattice_auth' OR m.rolname = 'lattice_auth'
        """,
    )
    # lattice_auth is a member of nothing; among the Lattice roles its only member is the
    # migration role, which may SET ROLE to it (to transfer function ownership) but
    # inherits nothing from it. Compared as a set, like check-deployment: a role
    # bootstrapped by the superuser and again by a platform administrator carries the
    # same grant twice (two grantors).
    lattice = {("lattice_auth", "lattice_owner", False, True)}
    assert {e for e in edges if e[1] in LATTICE_ROLES} == lattice
    assert all(e[0] == "lattice_auth" for e in edges), "lattice_auth is a member of nothing"
    # On a managed platform the administrative role that ran bootstrap_production.sql
    # (CREATEROLE, not a superuser) holds PostgreSQL 16's automatic ADMIN-only membership
    # in the roles it created: it may neither inherit from lattice_auth nor become it.
    for granted, member, inherit, set_role in edges:
        if member not in LATTICE_ROLES:
            assert (inherit, set_role) == (False, False), (granted, member)
    can_become = await _rows(
        superuser_engine,
        "SELECT rolname FROM pg_roles WHERE rolcanlogin AND NOT rolsuper "
        "AND pg_has_role(oid, 'lattice_auth', 'SET') ORDER BY 1",
    )
    assert can_become == [("lattice_owner",)]
    uses_privileges = await _rows(
        superuser_engine,
        "SELECT rolname FROM pg_roles WHERE rolcanlogin AND NOT rolsuper "
        "AND pg_has_role(oid, 'lattice_auth', 'USAGE') ORDER BY 1",
    )
    assert uses_privileges == []


# --- Behaviour, exercised as lattice_auth itself -----------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE public.lattice_probe (id int)",
        "CREATE VIEW public.lattice_probe AS SELECT 1",
        "CREATE FUNCTION public.lattice_probe() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        "CREATE SCHEMA lattice_probe",
        "CREATE TEMP TABLE lattice_probe (id int)",
        "CREATE SEQUENCE public.lattice_probe",
    ],
)
async def test_lattice_auth_cannot_create_objects(
    superuser_engine: AsyncEngine, statement: str
) -> None:
    async with superuser_engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(text("SET LOCAL ROLE lattice_auth"))
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(text(statement))
        finally:
            await transaction.rollback()


@pytest.mark.parametrize(
    "statement",
    [
        # Tenant tables outside the lookups' needs.
        "SELECT 1 FROM departments",
        "SELECT 1 FROM membership_departments",
        "SELECT 1 FROM accounts",
        "SELECT 1 FROM account_assignments",
        "SELECT 1 FROM access_groups",
        "SELECT 1 FROM access_group_members",
        "SELECT 1 FROM documents",
        "SELECT 1 FROM document_grants",
        # No writes anywhere, including the tables it may read.
        "INSERT INTO organizations (slug, name) VALUES ('probe', 'Probe')",
        "UPDATE memberships SET is_admin = true",
        "UPDATE sessions SET revoked_at = NULL",
        "DELETE FROM sessions",
        "TRUNCATE sessions",
        # Cannot loosen row-level security or change the functions it owns.
        "ALTER TABLE memberships DISABLE ROW LEVEL SECURITY",
    ],
)
async def test_lattice_auth_has_no_broader_tenant_access(
    superuser_engine: AsyncEngine, statement: str
) -> None:
    async with superuser_engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(text("SET LOCAL ROLE lattice_auth"))
            with pytest.raises(DBAPIError, match=r"permission denied|must be owner"):
                await conn.execute(text(statement))
        finally:
            await transaction.rollback()


async def test_lattice_auth_cannot_redefine_its_functions(superuser_engine: AsyncEngine) -> None:
    # Replacing a function requires CREATE on its schema even for the owner, so the
    # definer body cannot be swapped for a broader one without a migration.
    async with superuser_engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(text("SET LOCAL ROLE lattice_auth"))
            with pytest.raises(DBAPIError, match="permission denied for schema public"):
                await conn.execute(
                    text(
                        "CREATE OR REPLACE FUNCTION lattice_resolve_session(p_token_hash bytea) "
                        "RETURNS TABLE (session_id uuid, user_id uuid, organization_id uuid, "
                        "created_at timestamptz, last_seen_at timestamptz, "
                        "expires_at timestamptz, revoked_at timestamptz) "
                        "LANGUAGE sql STABLE SECURITY DEFINER AS "
                        "'SELECT id, user_id, organization_id, created_at, last_seen_at, "
                        "expires_at, revoked_at FROM public.sessions'"
                    )
                )
        finally:
            await transaction.rollback()


# --- The lookups still work for the runtime role -----------------------------------------


async def test_lookup_functions_still_work_for_the_runtime_role(
    owner_db: Database, app_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    member = await create_member(owner_db, org, "maya@halden.example")
    service = SessionService(
        app_db, SessionPolicy(idle_timeout=timedelta(hours=1), absolute_timeout=timedelta(hours=1))
    )
    token, _ = await service.create(user_id=member.user_id, organization_id=org)

    async with app_db.unscoped_transaction() as s:
        session_row = (
            await s.execute(
                text("SELECT user_id, organization_id FROM lattice_resolve_session(:h)"),
                {"h": hashlib.sha256(token.encode()).digest()},
            )
        ).one()
        memberships = (
            await s.execute(
                text(
                    "SELECT user_id, organization_slug FROM lattice_identity_memberships('dev', :s)"
                ),
                {"s": member.email},
            )
        ).all()
        unknown: object = (
            await s.execute(
                text("SELECT count(*) FROM lattice_resolve_session(:h)"), {"h": b"\x00" * 32}
            )
        ).scalar_one()
    assert tuple(session_row) == (member.user_id, org)
    assert [tuple(r) for r in memberships] == [(member.user_id, "halden")]
    assert unknown == 0
    # And end to end: the session authenticates through the definer lookup.
    principal = await service.authenticate(token)
    assert principal.membership_id == member.membership_id


# --- Existing databases converge to the same state ----------------------------------------


async def test_migrations_repair_a_database_created_by_the_original_phase_1(
    superuser_engine: AsyncEngine, owner_db: Database, app_db: Database
) -> None:
    # Recreate the state the original bootstrap + 0001 left behind: lattice_auth with
    # CREATE on public, and the lookups executable by PUBLIC with no explicit grant.
    async with superuser_engine.begin() as conn:
        await conn.execute(text("GRANT CREATE ON SCHEMA public TO lattice_auth"))
        for function in (
            "lattice_resolve_session(bytea)",
            "lattice_identity_memberships(text, text)",
        ):
            await conn.execute(text(f"GRANT EXECUTE ON FUNCTION {function} TO PUBLIC"))
            await conn.execute(text(f"REVOKE EXECUTE ON FUNCTION {function} FROM lattice_app"))
    try:
        await asyncio.to_thread(command.downgrade, alembic_config(), "0001")
        await asyncio.to_thread(command.upgrade, alembic_config(), "head")
        rows = await _rows(
            superuser_engine,
            """
            SELECT has_schema_privilege('lattice_auth', 'public', 'CREATE'),
                   has_function_privilege('lattice_owner', 'lattice_resolve_session(bytea)',
                                          'EXECUTE'),
                   has_function_privilege('lattice_app', 'lattice_resolve_session(bytea)',
                                          'EXECUTE'),
                   has_function_privilege('lattice_app',
                                          'lattice_identity_memberships(text, text)', 'EXECUTE')
            """,
        )
        assert rows == [(False, False, True, True)]
    finally:
        async with superuser_engine.begin() as conn:
            await conn.execute(text("REVOKE CREATE ON SCHEMA public FROM lattice_auth"))
        # Pooled connections cache prepared statements for the old table OIDs.
        await owner_db.engine.dispose()
        await app_db.engine.dispose()
        await superuser_engine.dispose()
