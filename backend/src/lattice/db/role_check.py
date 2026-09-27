"""Startup safety checks for the runtime database role.

The API must never run with a role that can bypass row-level security. At startup we
refuse to serve if the connected role:

* is a superuser or has BYPASSRLS;
* is a member of (and so could SET ROLE to) any role that is a superuser or has
  BYPASSRLS;
* owns, or is a member of a role that owns, any table in the public schema (an owner
  can ALTER TABLE ... DISABLE ROW LEVEL SECURITY);

or if any application table lacks ENABLED + FORCED row-level security, or the schema
has not been migrated.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# alembic_version holds no tenant data; the runtime role has no privileges on it.
RLS_EXEMPT_TABLES = frozenset({"alembic_version"})

_ROLE_SQL = text(
    """
    SELECT
      r.rolname AS role_name,
      r.rolsuper AS is_superuser,
      r.rolbypassrls AS bypasses_rls,
      EXISTS (
        SELECT 1 FROM pg_roles p
        WHERE (p.rolsuper OR p.rolbypassrls)
          AND p.oid <> r.oid
          AND pg_has_role(r.oid, p.oid, 'MEMBER')
      ) AS member_of_privileged_role,
      EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
          AND pg_has_role(r.oid, c.relowner, 'MEMBER')
      ) AS can_act_as_table_owner
    FROM pg_roles r
    WHERE r.rolname = current_user
    """
)

_TABLES_SQL = text(
    """
    SELECT c.relname AS table_name, c.relrowsecurity AS enabled, c.relforcerowsecurity AS forced
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    ORDER BY c.relname
    """
)


class UnsafeDatabaseRoleError(Exception):
    """The runtime database role or schema would allow row-level security to be bypassed."""


@dataclass(frozen=True)
class RoleReport:
    role_name: str
    problems: tuple[str, ...]


async def inspect_runtime_role(engine: AsyncEngine) -> RoleReport:
    async with engine.connect() as conn:
        role = (await conn.execute(_ROLE_SQL)).mappings().one()
        tables = (await conn.execute(_TABLES_SQL)).mappings().all()

    problems: list[str] = []
    if role["is_superuser"]:
        problems.append("role is a superuser")
    if role["bypasses_rls"]:
        problems.append("role has BYPASSRLS")
    if role["member_of_privileged_role"]:
        problems.append("role is a member of a superuser or BYPASSRLS role")
    if role["can_act_as_table_owner"]:
        problems.append("role owns (or is a member of the owner of) application tables")

    checked = [t for t in tables if t["table_name"] not in RLS_EXEMPT_TABLES]
    if not any(t["table_name"] == "alembic_version" for t in tables):
        problems.append("database schema is not migrated (run `make migrate`)")
    for table in checked:
        if not (table["enabled"] and table["forced"]):
            problems.append(f"table {table['table_name']} does not have forced row-level security")
    return RoleReport(role_name=str(role["role_name"]), problems=tuple(problems))


async def verify_runtime_role(engine: AsyncEngine) -> None:
    report = await inspect_runtime_role(engine)
    if report.problems:
        raise UnsafeDatabaseRoleError(
            f"Refusing to start: database role {report.role_name!r} is unsafe for runtime use: "
            + "; ".join(report.problems)
        )
