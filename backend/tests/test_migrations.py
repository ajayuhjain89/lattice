"""The migration chain applies cleanly and the models match the migrated schema."""

import asyncio

from alembic import command

from lattice.db.engine import Database

from .conftest import alembic_config


async def test_models_match_migrated_schema() -> None:
    # Raises if autogenerate would produce any operation (model/schema drift).
    await asyncio.to_thread(command.check, alembic_config())


async def test_downgrade_and_upgrade_round_trip(owner_db: Database, app_db: Database) -> None:
    await asyncio.to_thread(command.downgrade, alembic_config(), "base")
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    # Pooled connections cache prepared statements for the old table OIDs.
    await owner_db.engine.dispose()
    await app_db.engine.dispose()
