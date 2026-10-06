"""Production migrations: ``lattice migrate`` (Phase 11, P11.2; D-23, deployment.md §9.2).

The one way a deployment changes the schema: a one-off job with the schema owner's
credential (``LATTICE_MIGRATION_DATABASE_URL``, ``lattice_owner``), never at api or worker
startup. It refuses rather than migrating around a problem:

1. **Role.** The connected role owns the ``public`` schema and is neither a superuser nor
   BYPASSRLS: objects created by any other role would break the privilege model, and a
   runtime credential cannot migrate at all.
2. **Lock.** A database-native session advisory lock, waited for at most
   ``lock_wait_seconds``: a second migration refuses instead of running concurrently.
3. **Fence.** ``CONNECT`` on the database is revoked from ``lattice_app`` and
   ``lattice_worker`` for the duration, so no application process can connect while the
   schema changes (an old build starting mid-migration would otherwise pass its startup
   check against the old revision).
4. **Pre-flight.** No ``lattice_app`` / ``lattice_worker`` session may remain on the
   database (stop-the-world, D-23). Their presence is reported by role and application
   name and the migration refuses.
5. **Upgrade.** Alembic to head on the same connection, with ``lock_timeout`` and
   ``statement_timeout`` set. Alembic runs the whole chain in one transaction, so a
   failure leaves the previous revision in place.
6. **Verify.** The revision equals this build's ``SCHEMA_REVISION`` and the migrated
   schema matches the models exactly (no autogenerate difference).

The fence is lifted and the lock released whatever happened. If the process is killed
between steps 3 and 6 the fence stays (application roles cannot connect: fail closed);
``lattice migrate --release-fence`` or a new ``lattice migrate`` lifts it, and
``lattice check-deployment`` reports it.

Only identifiers, revisions, counts and fixed reasons are printed -- never a URL or
credential.
"""

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from lattice.db.models_registry import metadata
from lattice.db.schema import SCHEMA_REVISION, current_revision

# The application roles fenced off and waited for (bootstrap.sql grants them CONNECT).
APPLICATION_ROLES: Final = ("lattice_app", "lattice_worker")
# A fixed advisory-lock key ("LATTMIGR"); advisory locks are per database.
MIGRATION_LOCK_KEY: Final = int.from_bytes(b"LATTMIGR", "big")
APPLICATION_NAME: Final = "lattice-migrate"

State = Literal["current", "pending", "ahead", "unmigrated"]


class MigrationRefused(Exception):
    """The migration did not run (or did not verify); the message is printable."""


@dataclass(frozen=True)
class Status:
    database_revision: str | None
    build_revision: str
    pending: tuple[str, ...]
    state: State


@dataclass(frozen=True)
class Session:
    role: str
    application_name: str
    count: int


@dataclass
class Outcome:
    before: str | None
    after: str | None
    applied: tuple[str, ...]
    seconds: float
    notes: list[str] = field(default_factory=list)


def alembic_ini() -> Path:
    """The working directory's ``alembic.ini`` (the image's WORKDIR), else the source
    tree's (``backend/alembic.ini``)."""
    here = Path.cwd() / "alembic.ini"
    return here if here.is_file() else Path(__file__).resolve().parents[3] / "alembic.ini"


def alembic_config() -> Config:
    config = Config(str(alembic_ini()))
    config.attributes["configure_logging"] = False
    return config


def migration_chain(config: Config) -> list[str]:
    """Revisions from base to head, in order (the chain is linear: one head)."""
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if heads != [SCHEMA_REVISION]:
        raise MigrationRefused(
            f"this build's migration chain has heads {heads}, expected [{SCHEMA_REVISION}]"
        )
    return [rev.revision for rev in reversed(list(script.walk_revisions("base", "heads")))]


@dataclass(frozen=True)
class Header:
    """The deployment header every migration states (tests/test_migration_safety.py)."""

    revision: str
    reversible: bool
    locks: str


_REVERSIBLE = re.compile(r"^Reversible: (yes|no) \((.+)\)$", re.MULTILINE)
_LOCKS = re.compile(r"^Locks: (.+)$", re.MULTILINE)


def migration_headers(config: Config | None = None) -> dict[str, Header]:
    """Each revision's declared reversibility and locks, from its module docstring. A
    downgrade is an allowed rollback only for a revision declared reversible (its
    downgrade loses no data); everything else rolls forward (deployment.md §9.2)."""
    script = ScriptDirectory.from_config(config or alembic_config())
    headers = {}
    for rev in reversed(list(script.walk_revisions("base", "heads"))):  # oldest first
        doc = rev.module.__doc__ or ""
        reversible = _REVERSIBLE.search(doc)
        locks = _LOCKS.search(doc)
        if reversible is None or locks is None:
            raise MigrationRefused(f"migration {rev.revision} lacks its Reversible/Locks header")
        headers[rev.revision] = Header(rev.revision, reversible.group(1) == "yes", locks.group(1))
    return headers


def status_for(revision: str | None, chain: list[str]) -> Status:
    if revision is None:
        return Status(None, SCHEMA_REVISION, tuple(chain), "unmigrated")
    if revision not in chain:
        return Status(revision, SCHEMA_REVISION, (), "ahead")
    pending = tuple(chain[chain.index(revision) + 1 :])
    return Status(revision, SCHEMA_REVISION, pending, "pending" if pending else "current")


_ROLE_SQL = text(
    """
    SELECT r.rolname, r.rolsuper, r.rolbypassrls,
           pg_has_role(r.oid, n.nspowner, 'USAGE') AS owns_schema
    FROM pg_roles r, pg_namespace n
    WHERE r.rolname = current_user AND n.nspname = 'public'
    """
)

_SESSIONS_SQL = text(
    """
    SELECT r.rolname AS role, coalesce(a.application_name, '') AS application_name,
           count(*) AS sessions
    FROM pg_stat_activity a JOIN pg_roles r ON r.oid = a.usesysid
    WHERE a.datname = current_database() AND r.rolname = ANY(:roles)
      AND a.pid <> pg_backend_pid()
    GROUP BY 1, 2 ORDER BY 1, 2
    """
)

_HOLDER_SQL = text(
    """
    SELECT count(*) FROM pg_locks
    WHERE locktype = 'advisory' AND granted AND database = (
      SELECT oid FROM pg_database WHERE datname = current_database())
      AND objsubid = 1 AND ((classid::bigint << 32) | objid::bigint) = :key
    """
)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


async def _database(conn: AsyncConnection) -> str:
    return str((await conn.execute(text("SELECT current_database()"))).scalar_one())


async def read_status(conn: AsyncConnection) -> Status:
    return status_for(await current_revision(conn), migration_chain(alembic_config()))


async def application_sessions(conn: AsyncConnection) -> list[Session]:
    rows = await conn.execute(_SESSIONS_SQL, {"roles": list(APPLICATION_ROLES)})
    return [Session(str(r.role), str(r.application_name), int(r.sessions)) for r in rows]


async def check_owner(conn: AsyncConnection) -> None:
    role = (await conn.execute(_ROLE_SQL)).one()
    if role.rolsuper or role.rolbypassrls:
        raise MigrationRefused(
            f"role {role.rolname!r} is a superuser or has BYPASSRLS: migrations run as the "
            "schema owner (lattice_owner), never as an administrative role"
        )
    if not role.owns_schema:
        raise MigrationRefused(
            f"role {role.rolname!r} does not own the public schema: use the migration "
            "credential (LATTICE_MIGRATION_DATABASE_URL, lattice_owner)"
        )


async def acquire_lock(
    conn: AsyncConnection,
    wait_seconds: float,
    *,
    poll_seconds: float = 0.5,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    deadline = clock() + wait_seconds
    while True:
        got: bool = (
            await conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
            )
        ).scalar_one()
        await conn.commit()
        if got:
            return
        if clock() >= deadline:
            raise MigrationRefused(
                f"another migration holds the migration lock (waited {wait_seconds:g} s); "
                "nothing was changed"
            )
        await asyncio.sleep(poll_seconds)


async def release_lock(conn: AsyncConnection) -> None:
    await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
    await conn.commit()


async def set_fence(conn: AsyncConnection, *, closed: bool) -> None:
    """Revoke (closed) or grant back CONNECT for the application roles. The database
    owner granted it (bootstrap), so the owner can take it back and return it."""
    database = _quote(await _database(conn))
    roles = ", ".join(APPLICATION_ROLES)
    statement = (
        f"REVOKE CONNECT ON DATABASE {database} FROM {roles}"
        if closed
        else f"GRANT CONNECT ON DATABASE {database} TO {roles}"
    )
    await conn.execute(text(statement))
    await conn.commit()


async def fence_open_for_all(conn: AsyncConnection) -> list[str]:
    """Application roles that may still connect (directly, through PUBLIC or a role)."""
    database = await _database(conn)
    open_roles = []
    for role in APPLICATION_ROLES:
        allowed: bool = (
            await conn.execute(
                text("SELECT has_database_privilege(:role, :db, 'CONNECT')"),
                {"role": role, "db": database},
            )
        ).scalar_one()
        if allowed:
            open_roles.append(role)
    return open_roles


def _upgrade(sync_conn: Connection) -> None:
    config = alembic_config()
    config.attributes["connection"] = sync_conn
    command.upgrade(config, "head")


def _downgrade_to(target: str) -> Callable[[Connection], None]:
    def run(sync_conn: Connection) -> None:
        config = alembic_config()
        config.attributes["connection"] = sync_conn
        command.downgrade(config, target)

    return run


def undo_plan(status: Status, target: str, headers: dict[str, Header]) -> tuple[str, ...]:
    """The revisions a downgrade to ``target`` would undo, newest first. Refused unless the
    database is at this build's head, ``target`` is an older revision of its chain, and
    every undone revision declares a data-safe downgrade (D-23: roll forward otherwise)."""
    chain = list(headers)
    if status.state != "current":
        raise MigrationRefused(
            f"a downgrade starts from this build's revision {SCHEMA_REVISION}; the database "
            f"is at {status.database_revision}"
        )
    if target not in chain or target == SCHEMA_REVISION:
        raise MigrationRefused(f"{target} is not an older revision of this build's chain")
    undone = tuple(reversed(chain[chain.index(target) + 1 :]))
    irreversible = [rev for rev in undone if not headers[rev].reversible]
    if irreversible:
        raise MigrationRefused(
            f"revision(s) {', '.join(irreversible)} declare no data-safe downgrade: roll "
            "forward with a fix (or restore a backup); nothing was changed"
        )
    return undone


def schema_drift(sync_conn: Connection) -> list[str]:
    """Differences between the models and the connected schema (empty = exact match)."""
    context = MigrationContext.configure(sync_conn, opts={"compare_server_default": False})
    return [repr(diff)[:200] for diff in compare_metadata(context, metadata())]


async def _migrate_fenced(
    conn: AsyncConnection, report: Callable[[str], None], target: str | None
) -> Outcome:
    """Steps 4-6, with the lock held and the fence closed."""
    notes = []
    if still_open := await fence_open_for_all(conn):
        notes.append(
            "fence ineffective for " + ", ".join(still_open) + " (CONNECT through PUBLIC or "
            "a role membership); relying on the pre-flight alone"
        )
    sessions = await application_sessions(conn)
    if sessions:
        listed = ", ".join(f"{s.role}/{s.application_name or '-'} x{s.count}" for s in sessions)
        raise MigrationRefused(
            f"application sessions are still connected ({listed}): stop every api and "
            "worker instance first (stop-the-world, D-23); nothing was changed"
        )
    report("pre-flight: no application session on the database")
    before = await read_status(conn)
    await conn.commit()
    if target is not None:
        undone = undo_plan(before, target, migration_headers())
        report("downgrade: undo " + ", ".join(undone))
        await conn.run_sync(_downgrade_to(target))
        await conn.commit()
        after = await read_status(conn)
        await conn.commit()
        if after.database_revision != target:
            raise MigrationRefused(
                f"after the downgrade the database is at {after.database_revision}, "
                f"expected {target}"
            )
        report(f"verify: revision {target}")
        return Outcome(before.database_revision, target, undone, 0.0, notes)
    if before.state == "ahead":
        raise MigrationRefused(
            f"the database is at revision {before.database_revision}, which this build "
            f"({SCHEMA_REVISION}) does not know: deploy a newer build (roll forward); "
            "nothing was changed"
        )
    if before.pending:
        report("upgrade: " + " -> ".join(before.pending))
        await conn.run_sync(_upgrade)
        await conn.commit()
    else:
        report("upgrade: nothing to apply")
    after = await read_status(conn)
    drift = await conn.run_sync(schema_drift)
    await conn.commit()
    if after.state != "current":
        raise MigrationRefused(
            f"after the upgrade the database is at {after.database_revision}, "
            f"expected {SCHEMA_REVISION}"
        )
    if drift:
        raise MigrationRefused(
            f"the migrated schema differs from this build's models ({len(drift)} "
            f"difference(s), first: {drift[0]})"
        )
    report(f"verify: revision {SCHEMA_REVISION}, schema matches the models")
    return Outcome(before.database_revision, after.database_revision, before.pending, 0.0, notes)


def _engine(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        poolclass=pool.NullPool,
        connect_args={"server_settings": {"application_name": APPLICATION_NAME}},
    )


async def migrate(
    url: str,
    *,
    lock_wait_seconds: float = 60.0,
    lock_timeout_ms: int = 5_000,
    statement_timeout_ms: int = 600_000,
    report: Callable[[str], None] = print,
    downgrade_to: str | None = None,
) -> Outcome:
    """Run steps 1-6 of the module docstring. Raises MigrationRefused with a printable
    reason; any other exception is a failed migration (its transaction rolled back).

    ``downgrade_to``: instead of upgrading, undo revisions down to that one -- only when
    every undone revision declares a data-safe downgrade (``undo_plan``), with the same
    owner check, lock, fence and pre-flight."""
    engine = _engine(url)
    started = time.monotonic()
    try:
        async with engine.connect() as conn:
            await check_owner(conn)
            await conn.execute(text(f"SET lock_timeout = {int(lock_timeout_ms)}"))
            await conn.execute(text(f"SET statement_timeout = {int(statement_timeout_ms)}"))
            await conn.commit()
            await acquire_lock(conn, lock_wait_seconds)
            report("lock: acquired")
            try:
                await set_fence(conn, closed=True)
                report("fence: application roles may not connect")
                try:
                    outcome = await _migrate_fenced(conn, report, downgrade_to)
                finally:
                    await conn.rollback()
                    await set_fence(conn, closed=False)
                    report("fence: lifted")
            finally:
                await release_lock(conn)
            outcome.seconds = time.monotonic() - started
            return outcome
    finally:
        await engine.dispose()


async def release_fence(url: str) -> list[str]:
    """Grant CONNECT back to the application roles (after a killed migration); refuses
    while another migration holds the lock. Returns the roles that can now connect."""
    engine = _engine(url)
    try:
        async with engine.connect() as conn:
            await check_owner(conn)
            await acquire_lock(conn, 0)
            try:
                await set_fence(conn, closed=False)
                return await fence_open_for_all(conn)
            finally:
                await release_lock(conn)
    finally:
        await engine.dispose()


async def database_status(url: str) -> tuple[Status, list[Session], int]:
    """Read-only: the revision state, the application sessions, and whether a migration
    currently holds the lock (1) or not (0)."""
    engine = _engine(url)
    try:
        async with engine.connect() as conn:
            status = await read_status(conn)
            sessions = await application_sessions(conn)
            held = int((await conn.execute(_HOLDER_SQL, {"key": MIGRATION_LOCK_KEY})).scalar_one())
            return status, sessions, held
    finally:
        await engine.dispose()
