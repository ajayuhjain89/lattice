"""Engine and transaction helpers that bind the organization context.

Organization isolation (docs/security-model.md §3) relies on every tenant query
running inside a transaction where ``app.org_id`` has been set with
``set_config('app.org_id', <org>, true)`` -- the parameterizable equivalent of
``SET LOCAL``. Because the setting is transaction-local, it is discarded on COMMIT or
ROLLBACK and can never leak to the next user of a pooled connection. RLS policies read
it through ``lattice_current_org_id()``, which yields NULL when it is unset or empty,
so a query without a bound organization sees no tenant rows.

Rules for callers:

* Use :meth:`Database.tenant_transaction` for anything that touches tenant data. The
  organization ID must come from the server-side session, never from client input.
* :meth:`Database.tenant_snapshot` is the same, but REPEATABLE READ and READ ONLY:
  one consistent snapshot for a whole read path (authorization context, retrieval and
  the pre-generation tripwire must all see the same permissions).
* :meth:`Database.unscoped_transaction` exists only for health checks and the
  SECURITY DEFINER pre-authentication lookups; tenant tables are invisible inside it.
* Do not keep using a session after its transaction block ends: autobegin would open
  a new transaction *without* the organization bound (it fails closed, returning no
  rows, but it is a bug).
"""

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from lattice.db.schema import current_revision

_BIND_ORG_SQL = text("SELECT set_config('app.org_id', :org_id, true)")
_DATABASE_CLOCK_SQL = text("SELECT extract(epoch FROM clock_timestamp())")


def create_engine(
    url: str,
    *,
    application_name: str,
    pool_size: int = 5,
    statement_timeout_ms: int | None = None,
    connect_timeout_seconds: float | None = None,
) -> AsyncEngine:
    """Long-running processes (the API, the worker) pass the timeouts: a statement over
    ``statement_timeout_ms`` is cancelled by the server, a transaction left idle as long
    is terminated, and an unreachable server fails a checkout after
    ``connect_timeout_seconds`` (also the pool's wait for a free connection). Operator
    tools (migrations, seed, sync from the CLI) keep the server defaults."""
    server_settings = {"application_name": application_name}
    if statement_timeout_ms is not None:
        server_settings["statement_timeout"] = str(statement_timeout_ms)
        server_settings["idle_in_transaction_session_timeout"] = str(statement_timeout_ms)
    connect_args: dict[str, object] = {"server_settings": server_settings}
    extra: dict[str, object] = {}
    if connect_timeout_seconds is not None:
        connect_args["timeout"] = connect_timeout_seconds
        extra["pool_timeout"] = connect_timeout_seconds
    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=pool_size,
        pool_pre_ping=True,
        connect_args=connect_args,
        **extra,
    )


async def bind_organization(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Bind the tenant for the current transaction only."""
    if not isinstance(organization_id, uuid.UUID):  # defensive: never accept raw strings
        raise TypeError("organization_id must be a UUID taken from the server-side session")
    await session.execute(_BIND_ORG_SQL, {"org_id": str(organization_id)})


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        self._snapshot_sessions = async_sessionmaker(
            engine.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True),
            expire_on_commit=False,
            autoflush=False,
        )

    @asynccontextmanager
    async def tenant_transaction(self, organization_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session, session.begin():
            await bind_organization(session, organization_id)
            yield session

    @asynccontextmanager
    async def tenant_snapshot(self, organization_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with self._snapshot_sessions() as session, session.begin():
            await bind_organization(session, organization_id)
            yield session

    @asynccontextmanager
    async def unscoped_transaction(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session, session.begin():
            yield session

    async def ping(self) -> None:
        """Connectivity check for /v1/health. Touches no tenant data."""
        async with self.unscoped_transaction() as session:
            await session.execute(text("SELECT 1"))

    async def schema_revision(self) -> str | None:
        """The applied migration revision (readiness). Touches no tenant data."""
        async with self.engine.connect() as conn:
            return await current_revision(conn)

    async def clock_skew(self) -> float:
        """This process's clock minus the database's, in seconds, compared at the
        midpoint of the round trip. Touches no tenant data."""
        async with self.engine.connect() as conn:
            before = time.time()
            database: Any = (await conn.execute(_DATABASE_CLOCK_SQL)).scalar_one()
            after = time.time()
        return (before + after) / 2 - float(database)

    async def dispose(self) -> None:
        await self.engine.dispose()
