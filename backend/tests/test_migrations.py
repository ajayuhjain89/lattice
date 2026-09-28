"""The migration chain applies cleanly and the models match the migrated schema."""

import asyncio

from alembic import command
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.providers import records
from lattice.providers.egress import ProviderDecision, provider_decision

from .conftest import alembic_config
from .factories import create_org


async def test_models_match_migrated_schema() -> None:
    # Raises if autogenerate would produce any operation (model/schema drift).
    await asyncio.to_thread(command.check, alembic_config())


async def test_downgrade_and_upgrade_round_trip(owner_db: Database, app_db: Database) -> None:
    await asyncio.to_thread(command.downgrade, alembic_config(), "base")
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    # Pooled connections cache prepared statements for the old table OIDs.
    await owner_db.engine.dispose()
    await app_db.engine.dispose()


async def test_0007_keeps_private_records_as_organization_controlled(
    owner_db: Database, app_db: Database
) -> None:
    """D-15: a ``private`` record survives the upgrade as ``organization_controlled``,
    without an explicit approval and with the class not allowed -- so it sends nothing
    until an administrator configures both -- and the downgrade relabels it back."""
    await asyncio.to_thread(command.downgrade, alembic_config(), "0006")
    await owner_db.engine.dispose()
    org = await create_org(owner_db, "halden")

    async def provider_rows() -> list[tuple[str, str]]:
        async with owner_db.tenant_transaction(org) as s:
            return [
                (str(p), str(d))
                for p, d in await s.execute(
                    text("SELECT provider, deployment FROM model_providers ORDER BY provider")
                )
            ]

    async with owner_db.tenant_transaction(org) as s:
        for provider, deployment in (
            ("acme", "private"),
            ("fake", "external_api"),
            ("mx", "local"),
        ):
            await s.execute(
                text(
                    "INSERT INTO model_providers (organization_id, purpose, provider, model, "
                    "deployment, approved_for_level, retention_mode, training_opt_out) "
                    "VALUES (:o, 'embedding', :p, 'm', :d, 'confidential', 'none', true)"
                ),
                {"o": org, "p": provider, "d": deployment},
            )
    await owner_db.engine.dispose()

    await asyncio.to_thread(command.upgrade, alembic_config(), "0007")
    await owner_db.engine.dispose()
    assert await provider_rows() == [
        ("acme", "organization_controlled"),
        ("fake", "external_api"),
        ("mx", "local"),
    ]
    async with owner_db.tenant_transaction(org) as s:
        assert (
            await s.execute(text("SELECT count(*) FROM model_providers WHERE approved_at IS NULL"))
        ).scalar_one() == 3
        record = await records.embedding_record(s, org, "acme", "m")
        settings = await records.org_egress(s, org)
    assert record is not None
    assert settings.organization_controlled_level is None
    decision = provider_decision(
        settings, record, deployment="organization_controlled", training_opt_out=True
    )
    assert decision == ProviderDecision(None, "provider_unapproved")

    await asyncio.to_thread(command.downgrade, alembic_config(), "0006")
    await owner_db.engine.dispose()
    assert await provider_rows() == [("acme", "private"), ("fake", "external_api"), ("mx", "local")]

    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    await owner_db.engine.dispose()
    await app_db.engine.dispose()
