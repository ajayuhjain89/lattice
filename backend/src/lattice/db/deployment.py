"""``lattice check-deployment``: is this database safe to serve? (Phase 11, P11.2)

A read-only check, run as the schema owner (the migration credential) before and after a
deployment, that compares the database with what this build requires. No tenant row is
read; only catalogs and the schema revision.

* **schema** -- the revision equals ``SCHEMA_REVISION`` (exact, D-23) and the schema
  matches the models (no Alembic autogenerate difference);
* **roles** -- ``lattice_owner``, ``lattice_app``, ``lattice_worker`` and ``lattice_auth``
  exist with safe attributes (no superuser, BYPASSRLS, CREATEROLE, CREATEDB or
  REPLICATION; ``lattice_auth`` cannot log in); the runtime roles are members of no
  role; the owner reaches ``lattice_auth`` only WITH INHERIT FALSE;
* **row-level security** -- every table in ``public`` (except ``alembic_version``) has
  RLS enabled and forced;
* **database access** -- PUBLIC has no CONNECT or TEMPORARY; the application roles have
  CONNECT (a migration fence left behind is reported); no migration holds the lock;
* **privilege snapshot** -- policies, table/column grants, functions (owner, SECURITY
  DEFINER, settings, EXECUTE), triggers, schema ACL and role memberships equal the
  snapshot generated from a freshly migrated database and committed with the code
  (``privilege_snapshot.json``): any drift -- a hand-made GRANT, a dropped policy, a
  disabled trigger -- fails;
* **connection budget** -- ``max_connections`` minus reserved slots covers the planned
  processes plus a headroom of 10 (deployment.md §8).
"""

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any, Final

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from lattice.db.migrate import (
    APPLICATION_NAME,
    APPLICATION_ROLES,
    MIGRATION_LOCK_KEY,
    MigrationRefused,
    alembic_config,
    fence_open_for_all,
    migration_chain,
    schema_drift,
    status_for,
)
from lattice.db.role_check import RLS_EXEMPT_TABLES
from lattice.db.schema import SCHEMA_REVISION, current_revision

LATTICE_ROLES: Final = ("lattice_owner", "lattice_app", "lattice_worker", "lattice_auth")
SNAPSHOT_RESOURCE: Final = "privilege_snapshot.json"
HEADROOM: Final = 10
# Per-process connection ceilings (lattice.db.engine: max_overflow = pool_size).
WORKER_CONNECTIONS: Final = 4
OPERATOR_CONNECTIONS: Final = 3


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Plan:
    """The processes the database must serve at once (deployment.md §8)."""

    api_instances: int = 2
    api_pool_size: int = 5
    worker_instances: int = 1

    @property
    def connections(self) -> int:
        return (
            self.api_instances * 2 * self.api_pool_size
            + self.worker_instances * WORKER_CONNECTIONS
            + OPERATOR_CONNECTIONS
        )


_SNAPSHOT_QUERIES: Final[dict[str, str]] = {
    "schema": """
        SELECT n.nspowner::regrole::text AS owner,
               ARRAY(SELECT coalesce(nullif(a.grantee, 0)::regrole::text, 'PUBLIC')
                            || ':' || a.privilege_type
                     FROM aclexplode(n.nspacl) a WHERE a.grantee <> n.nspowner
                     ORDER BY 1) AS grants
        FROM pg_namespace n WHERE n.nspname = 'public'
    """,
    "tables": """
        SELECT c.relname AS name, c.relkind::text AS kind, c.relowner::regrole::text AS owner,
               c.relrowsecurity AS rls, c.relforcerowsecurity AS forced
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
          AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
                          AND d.objid = c.oid AND d.deptype = 'e')
        ORDER BY 1
    """,
    "table_grants": """
        SELECT c.relname AS name,
               coalesce(nullif(a.grantee, 0)::regrole::text, 'PUBLIC') AS grantee,
               a.privilege_type AS privilege, a.is_grantable AS grantable
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace,
             aclexplode(c.relacl) a
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
          AND a.grantee <> c.relowner
        ORDER BY 1, 2, 3
    """,
    "column_grants": """
        SELECT c.relname AS name, att.attname AS column,
               coalesce(nullif(a.grantee, 0)::regrole::text, 'PUBLIC') AS grantee,
               a.privilege_type AS privilege
        FROM pg_attribute att JOIN pg_class c ON c.oid = att.attrelid
             JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(att.attacl) a
        WHERE n.nspname = 'public' AND att.attacl IS NOT NULL AND NOT att.attisdropped
        ORDER BY 1, 2, 3, 4
    """,
    "policies": """
        SELECT c.relname AS name, p.polname AS policy, p.polpermissive AS permissive,
               p.polcmd::text AS command,
               ARRAY(SELECT CASE WHEN r = 0 THEN 'PUBLIC' ELSE r::regrole::text END
                     FROM unnest(p.polroles) r ORDER BY 1) AS roles,
               pg_get_expr(p.polqual, p.polrelid) AS using,
               pg_get_expr(p.polwithcheck, p.polrelid) AS with_check
        FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
             JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
        ORDER BY 1, 2
    """,
    "functions": """
        SELECT p.oid::regprocedure::text AS signature, p.proowner::regrole::text AS owner,
               p.prosecdef AS security_definer, p.proconfig AS settings,
               ARRAY(SELECT coalesce(nullif(a.grantee, 0)::regrole::text, 'PUBLIC')
                            || ':' || a.privilege_type
                     FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                     ORDER BY 1) AS grants
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass
                          AND d.objid = p.oid AND d.deptype = 'e')
        ORDER BY 1
    """,
    "triggers": """
        SELECT c.relname AS name, t.tgname AS trigger, t.tgenabled::text AS enabled,
               t.tgfoid::regprocedure::text AS function, t.tgdeferrable AS deferrable,
               t.tginitdeferred AS initially_deferred
        FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
             JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND NOT t.tgisinternal
        ORDER BY 1, 2
    """,
    "memberships": """
        SELECT r.rolname AS role, m.rolname AS member, am.admin_option AS admin,
               am.inherit_option AS inherit, am.set_option AS set
        FROM pg_auth_members am JOIN pg_roles r ON r.oid = am.roleid
             JOIN pg_roles m ON m.oid = am.member
        WHERE r.rolname = ANY(:roles) AND m.rolname = ANY(:roles)
        ORDER BY 1, 2
    """,
}


def privilege_snapshot(conn: Connection) -> dict[str, list[dict[str, Any]]]:
    """A normalised, environment-independent description of every privilege-relevant
    object in ``public`` (no database name, no OIDs, no grantors)."""
    snapshot: dict[str, list[dict[str, Any]]] = {}
    for section, sql in _SNAPSHOT_QUERIES.items():
        rows = conn.execute(text(sql), {"roles": list(LATTICE_ROLES)}).mappings().all()
        snapshot[section] = [
            {key: list(v) if isinstance(v, list | tuple) else v for key, v in r.items()}
            for r in rows
        ]
    return snapshot


def committed_snapshot() -> dict[str, Any]:
    raw = resources.files("lattice.db").joinpath(SNAPSHOT_RESOURCE).read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(raw)
    return loaded


def render_snapshot(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, indent=1, sort_keys=True) + "\n"


def snapshot_differences(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Human-readable differences, one line per added or missing entry."""
    lines = []
    for section in sorted(set(expected) | set(actual)):
        want = {json.dumps(e, sort_keys=True) for e in expected.get(section, [])}
        have = {json.dumps(e, sort_keys=True) for e in actual.get(section, [])}
        lines += [f"{section}: missing {e}" for e in sorted(want - have)]
        lines += [f"{section}: unexpected {e}" for e in sorted(have - want)]
    return lines


_ROLES_SQL = text(
    """
    SELECT r.rolname, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb,
           r.rolreplication, r.rolcanlogin,
           ARRAY(SELECT g.rolname FROM pg_auth_members am JOIN pg_roles g ON g.oid = am.roleid
                 WHERE am.member = r.oid ORDER BY 1) AS member_of,
           EXISTS (SELECT 1 FROM pg_auth_members am JOIN pg_roles g ON g.oid = am.roleid
                   WHERE am.member = r.oid AND am.inherit_option
                     AND g.rolname = 'lattice_auth') AS inherits_auth
    FROM pg_roles r WHERE r.rolname = ANY(:roles) ORDER BY 1
    """
)

_DATABASE_SQL = text(
    """
    SELECT d.datdba::regrole::text AS owner,
           EXISTS (SELECT 1 FROM aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a
                   WHERE a.grantee = 0 AND a.privilege_type IN ('CONNECT', 'TEMPORARY'))
             AS public_access
    FROM pg_database d WHERE d.datname = current_database()
    """
)

_BUDGET_SQL = text(
    """
    SELECT current_setting('max_connections')::int AS max,
           current_setting('superuser_reserved_connections')::int
             + coalesce(current_setting('reserved_connections', true), '0')::int AS reserved
    """
)

_LOCK_SQL = text(
    """
    SELECT count(*) FROM pg_locks
    WHERE locktype = 'advisory' AND granted AND objsubid = 1
      AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
      AND ((classid::bigint << 32) | objid::bigint) = :key
    """
)

_RLS_SQL = text(
    """
    SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
      AND NOT (c.relrowsecurity AND c.relforcerowsecurity) AND c.relname <> ALL(:exempt)
    ORDER BY 1
    """
)


def _role_problems(rows: list[Any]) -> list[str]:
    found = {r.rolname: r for r in rows}
    problems = [f"role {name} is missing" for name in LATTICE_ROLES if name not in found]
    for name, role in sorted(found.items()):
        for attribute, flag in (
            ("SUPERUSER", role.rolsuper),
            ("BYPASSRLS", role.rolbypassrls),
            ("CREATEROLE", role.rolcreaterole),
            ("CREATEDB", role.rolcreatedb),
            ("REPLICATION", role.rolreplication),
        ):
            if flag:
                problems.append(f"{name} has {attribute}")
        if name == "lattice_auth" and role.rolcanlogin:
            problems.append("lattice_auth can log in (it must be NOLOGIN)")
        if name in APPLICATION_ROLES and role.member_of:
            problems.append(f"{name} is a member of {', '.join(role.member_of)}")
        if name == "lattice_owner" and role.inherits_auth:
            problems.append("lattice_owner inherits lattice_auth (must be WITH INHERIT FALSE)")
    return problems


async def run_checks(conn: AsyncConnection, plan: Plan) -> list[Check]:
    checks: list[Check] = []

    def add(name: str, problems: list[str], ok_detail: str) -> None:
        checks.append(Check(name, not problems, "; ".join(problems) or ok_detail))

    try:
        chain = migration_chain(alembic_config())
        revision = await current_revision(conn)
        status = status_for(revision, chain)
        add(
            "schema revision",
            []
            if status.state == "current"
            else [f"database at {status.database_revision}, build expects {SCHEMA_REVISION}"],
            f"{SCHEMA_REVISION} (exact)",
        )
        if status.state == "current":
            drift = await conn.run_sync(schema_drift)
            add(
                "schema matches the models",
                [f"{len(drift)} difference(s), first: {drift[0]}"] if drift else [],
                "no difference",
            )
    except MigrationRefused as exc:
        add("schema revision", [str(exc)], "")

    roles = list(await conn.execute(_ROLES_SQL, {"roles": list(LATTICE_ROLES)}))
    add("role attributes and memberships", _role_problems(roles), "four roles, safe attributes")

    unforced = [
        str(r[0]) for r in await conn.execute(_RLS_SQL, {"exempt": sorted(RLS_EXEMPT_TABLES)})
    ]
    add(
        "forced row-level security",
        [f"{t} lacks enabled+forced RLS" for t in unforced],
        "every table",
    )

    database = (await conn.execute(_DATABASE_SQL)).one()
    access_problems = []
    if database.owner != "lattice_owner":
        access_problems.append(f"database owned by {database.owner}, expected lattice_owner")
    if database.public_access:
        access_problems.append("PUBLIC has CONNECT or TEMPORARY on the database")
    blocked = sorted(set(APPLICATION_ROLES) - set(await fence_open_for_all(conn)))
    if blocked:
        access_problems.append(
            f"{', '.join(blocked)} cannot connect: a migration fence was left in place "
            "(run `lattice migrate --release-fence`)"
        )
    if (await conn.execute(_LOCK_SQL, {"key": MIGRATION_LOCK_KEY})).scalar_one():
        access_problems.append("a migration currently holds the migration lock")
    add("database access", access_problems, "owner lattice_owner; no PUBLIC access; no fence")

    actual = await conn.run_sync(privilege_snapshot)
    differences = snapshot_differences(committed_snapshot(), actual)
    add(
        "privilege snapshot",
        [f"{len(differences)} difference(s), first: {differences[0]}"] if differences else [],
        "matches the committed snapshot",
    )

    budget = (await conn.execute(_BUDGET_SQL)).one()
    usable = budget.max - budget.reserved
    need = plan.connections + HEADROOM
    add(
        "connection budget",
        []
        if usable >= need
        else [f"{usable} usable connections, the plan needs {plan.connections} + {HEADROOM}"],
        f"{usable} usable >= {plan.connections} planned + {HEADROOM} headroom",
    )
    await conn.rollback()
    return checks


async def check_deployment(url: str, plan: Plan) -> list[Check]:
    engine = create_async_engine(
        url,
        poolclass=pool.NullPool,
        connect_args={"server_settings": {"application_name": APPLICATION_NAME + "-check"}},
    )
    try:
        async with engine.connect() as conn:
            return await run_checks(conn, plan)
    finally:
        await engine.dispose()


async def snapshot_of(url: str) -> dict[str, Any]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(privilege_snapshot)
    finally:
        await engine.dispose()
