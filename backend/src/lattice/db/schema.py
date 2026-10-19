"""The database schema revision this build expects (fail closed on a mismatch).

A process started against a schema older or newer than its code would fail later, in
the middle of requests, with SQL errors. The API and the worker refuse to start
instead (``lattice.db.role_check``), and ``/v1/health/ready`` reports not ready.
``tests/test_migrations.py`` pins this constant to the head of the Alembic chain, so a
new migration cannot be merged without updating it.
"""

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

SCHEMA_REVISION: Final = "0023"

_READABLE_SQL = text(
    "SELECT to_regclass('public.alembic_version') IS NOT NULL "
    "AND has_table_privilege('public.alembic_version', 'SELECT')"
)


async def current_revision(conn: AsyncConnection) -> str | None:
    """The applied revision, or None when there is none the connected role may read.
    ``alembic_version`` holds no tenant data; the runtime roles may read it (0010)."""
    if not (await conn.execute(_READABLE_SQL)).scalar_one():
        return None
    return (await conn.execute(text("SELECT max(version_num) FROM alembic_version"))).scalar()


def revision_problem(revision: str | None) -> str | None:
    if revision == SCHEMA_REVISION:
        return None
    if revision is None:
        return (
            f"database schema is not migrated to {SCHEMA_REVISION} (run `lattice migrate`; "
            "`make migrate` in development)"
        )
    return (
        f"database schema is at revision {revision}, this build expects {SCHEMA_REVISION} "
        "(run `lattice migrate`, or deploy the build that matches the database)"
    )
