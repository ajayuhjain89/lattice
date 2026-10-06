"""``lattice verify-restore``: is a restored database complete and safe? (Phase 11, P11.5)

Run as the schema owner (the migration credential) against a restored copy, read-only.
It passes only when every check of ``lattice check-deployment`` passes (the schema is at
this build's revision and matches the models; role attributes are safe; every table has
forced row-level security; database access is right; the privilege snapshot -- policies,
grants, functions, **triggers** -- is identical to the committed one) and:

* **audit integrity** -- ``audit_events`` still refuses UPDATE (its trigger is present
  and enabled) and every access-audit constraint trigger is present and enabled;
* **row counts** -- with a manifest (``--manifest``), each organization's row count in
  every tenant table, and the totals of the global tables, equal the manifest taken from
  the source database at the recovery point (``--write-manifest``, at backup time).

The manifest holds counts and organization slugs only: no row content, no identifiers
beyond slugs, no secret. Counting binds each organization in turn (row-level security
stays forced for the owner), so a tenant table's count is that organization's only.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from lattice.db.deployment import Check, Plan, run_checks
from lattice.db.migrate import APPLICATION_NAME
from lattice.db.schema import SCHEMA_REVISION

MANIFEST_VERSION: Final = 1
GLOBAL_TABLES: Final = ("organizations", "users", "external_identities")
# Tables whose writes by lattice_app must carry an audit event (0013) and the trigger name.
AUDIT_TRIGGERS: Final = (
    ("audit_events", "trg_audit_events_immutable"),
    ("memberships", "trg_memberships_access_audit"),
    ("account_assignments", "trg_account_assignments_access_audit"),
    ("membership_departments", "trg_membership_departments_access_audit"),
    ("access_group_members", "trg_access_group_members_access_audit"),
    ("document_grants", "trg_document_grants_access_audit"),
    ("directory_identities", "trg_directory_identities_access_audit"),
)

_TENANT_TABLES_SQL = text(
    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'organization_id' "
    "AND NOT a.attisdropped WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
    "ORDER BY 1"
)
_ORGANIZATIONS_SQL = text("SELECT id, slug FROM organizations ORDER BY slug")
_BIND_SQL = text("SELECT set_config('app.org_id', :o, true)")
_TRIGGERS_SQL = text(
    "SELECT c.relname, t.tgname, t.tgenabled::text AS tgenabled FROM pg_trigger t "
    "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND NOT t.tgisinternal"
)


@dataclass(frozen=True)
class Manifest:
    revision: str
    taken_at: str
    globals: dict[str, int]
    organizations: dict[str, dict[str, int]]

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": MANIFEST_VERSION,
                "revision": self.revision,
                "taken_at": self.taken_at,
                "globals": self.globals,
                "organizations": self.organizations,
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> "Manifest":
        data: dict[str, Any] = json.loads(raw)
        if data.get("version") != MANIFEST_VERSION:
            raise ValueError("unsupported manifest version")
        return cls(
            revision=str(data["revision"]),
            taken_at=str(data["taken_at"]),
            globals={str(k): int(v) for k, v in data["globals"].items()},
            organizations={
                str(org): {str(t): int(n) for t, n in tables.items()}
                for org, tables in data["organizations"].items()
            },
        )


async def take_manifest(conn: AsyncConnection) -> Manifest:
    """Row counts per organization and table, in one REPEATABLE READ snapshot."""
    await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    taken_at: datetime = (await conn.execute(text("SELECT now()"))).scalar_one()
    names: list[str] = list((await conn.execute(_TENANT_TABLES_SQL)).scalars())
    tenant_tables = [str(name) for name in names]
    globals_: dict[str, int] = {}
    for table in GLOBAL_TABLES:
        globals_[table] = (
            await conn.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608 - catalog names
        ).scalar_one()
    organizations: dict[str, dict[str, int]] = {}
    for org_id, slug in (await conn.execute(_ORGANIZATIONS_SQL)).all():
        await conn.execute(_BIND_SQL, {"o": str(org_id)})
        organizations[str(slug)] = {
            table: (
                await conn.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
            ).scalar_one()
            for table in tenant_tables
        }
    await conn.execute(_BIND_SQL, {"o": ""})
    await conn.rollback()
    return Manifest(
        revision=SCHEMA_REVISION,
        taken_at=taken_at.astimezone(UTC).isoformat(),
        globals=globals_,
        organizations=organizations,
    )


def manifest_differences(expected: Manifest, actual: Manifest) -> list[str]:
    problems: list[str] = []
    for table, count in sorted(expected.globals.items()):
        if actual.globals.get(table) != count:
            problems.append(f"{table}: {actual.globals.get(table)} rows, expected {count}")
    for slug in sorted(set(expected.organizations) | set(actual.organizations)):
        want = expected.organizations.get(slug)
        got = actual.organizations.get(slug)
        if want is None or got is None:
            problems.append(f"organization {slug}: {'unexpected' if want is None else 'missing'}")
            continue
        for table in sorted(set(want) | set(got)):
            if want.get(table) != got.get(table):
                problems.append(
                    f"{slug}.{table}: {got.get(table)} rows, expected {want.get(table)}"
                )
    return problems


async def _audit_problems(conn: AsyncConnection) -> list[str]:
    found = {
        (str(r.relname), str(r.tgname)): str(r.tgenabled) for r in await conn.execute(_TRIGGERS_SQL)
    }
    problems = []
    for table, trigger in AUDIT_TRIGGERS:
        state = found.get((table, trigger))
        if state is None:
            problems.append(f"{table}: trigger {trigger} missing")
        elif state == "D":
            problems.append(f"{table}: trigger {trigger} disabled")
    return problems


async def verify_restore(url: str, manifest: Manifest | None, plan: Plan) -> list[Check]:
    engine = create_async_engine(
        url,
        poolclass=pool.NullPool,
        connect_args={"server_settings": {"application_name": APPLICATION_NAME + "-restore"}},
    )
    try:
        async with engine.connect() as conn:
            checks = await run_checks(conn, plan)
            audit = await _audit_problems(conn)
            await conn.rollback()
            checks.append(
                Check(
                    "audit integrity",
                    not audit,
                    "; ".join(audit) or "append-only and access-audit triggers enabled",
                )
            )
            if manifest is not None:
                actual = await take_manifest(conn)
                differences = manifest_differences(manifest, actual)
                orgs = len(actual.organizations)
                rows = sum(sum(t.values()) for t in actual.organizations.values())
                summary = (
                    f"{len(differences)} difference(s), first: {differences[0]}"
                    if differences
                    else f"{orgs} organization(s), {rows} tenant rows and the global tables "
                    f"equal the manifest of {manifest.taken_at}"
                )
                checks.append(Check("row counts", not differences, summary))
            return checks
    finally:
        await engine.dispose()


async def write_manifest(url: str) -> Manifest:
    engine = create_async_engine(
        url,
        poolclass=pool.NullPool,
        connect_args={"server_settings": {"application_name": APPLICATION_NAME + "-manifest"}},
    )
    try:
        async with engine.connect() as conn:
            return await take_manifest(conn)
    finally:
        await engine.dispose()
