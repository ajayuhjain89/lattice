"""Alembic environment (async). Runs as lattice_owner.

The URL comes from ``config.attributes["database_url"]`` (used by tests) or else from
LATTICE_MIGRATION_DATABASE_URL. It is never written to alembic.ini or logged.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from lattice.authz import models as authz_models  # noqa: F401 - register tables
from lattice.config import load_migration_settings
from lattice.db.base import Base
from lattice.identity import models as identity_models  # noqa: F401 - register tables
from lattice.ingestion import models as ingestion_models  # noqa: F401 - register tables
from lattice.jobs import models as job_models  # noqa: F401 - register tables
from lattice.orgs import models as org_models  # noqa: F401 - register tables
from lattice.providers import models as provider_models  # noqa: F401 - register tables
from lattice.sources import models as source_models  # noqa: F401 - register tables
from lattice.tracing import models as tracing_models  # noqa: F401 - register tables

config = context.config
target_metadata = Base.metadata

# Show migration progress on the CLI. Programmatic callers (tests) set
# attributes["configure_logging"] = False so their own logging setup is untouched.
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _database_url() -> str:
    override = config.attributes.get("database_url")
    if isinstance(override, str):
        return override
    return load_migration_settings().migration_database_url.get_secret_value()


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_server_default=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) migrations are not supported for Lattice.")

asyncio.run(_run_async())
