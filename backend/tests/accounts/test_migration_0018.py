"""Migration 0018 over the synthetic organizations (phase-12-plan C10, §21 P12.3):
mappings move out of ``sources.config`` into ``account_external_refs`` and the same
documents keep the same accounts -- before and after, and after a re-sync."""

import asyncio
import uuid
from typing import Any

from alembic import command
from sqlalchemy import text

from lattice.db.engine import Database

from ..conftest import alembic_config
from ..ingestion.conftest import Org, seed, sync

SOURCES = ("support", "slack")


async def _q(db: Database, org: uuid.UUID, sql: str) -> list[Any]:
    async with db.tenant_transaction(org) as s:
        return list((await s.execute(text(sql))).all())


async def _documents(db: Database, org: Org) -> dict[tuple[str, str], tuple[Any, ...]]:
    rows = await _q(
        db,
        org.id,
        "SELECT s.key, d.external_id, a.key, d.status, d.access_hash FROM documents d "
        "JOIN sources s ON s.id = d.source_id LEFT JOIN accounts a ON a.id = d.account_id",
    )
    return {
        (str(r[0]), r[1]): (None if r[2] is None else str(r[2]), r[3], bytes(r[4])) for r in rows
    }


async def _mappings(db: Database, org: Org) -> set[tuple[str, str, str, str]]:
    rows = await _q(
        db,
        org.id,
        "SELECT s.key, r.ref_kind, r.external_ref, a.key FROM account_external_refs r "
        "JOIN sources s ON s.id = r.source_id JOIN accounts a ON a.id = r.account_id",
    )
    return {(str(r[0]), r[1], r[2], str(r[3])) for r in rows}


async def _configs(db: Database, org: Org) -> dict[str, dict[str, Any]]:
    rows = await _q(db, org.id, "SELECT key, config FROM sources")
    return {str(k): {x: v for x, v in c.items() if x != "path"} for k, c in rows}


async def _migrate(revision: str, *dbs: Database) -> None:
    if revision == "head":
        await asyncio.to_thread(command.upgrade, alembic_config(), revision)
    else:
        await asyncio.to_thread(command.downgrade, alembic_config(), revision)
    for db in dbs:  # pooled connections cache plans for the old table shapes
        await db.engine.dispose()


async def test_0018_moves_the_mappings_and_keeps_every_documents_account(
    owner_db: Database, app_db: Database, worker_db: Database
) -> None:
    orgs = [await seed(owner_db, "halden")]
    orgs.append(await seed(owner_db, "veridian"))
    for org in orgs:
        for source in SOURCES:
            await sync(owner_db, org, source, None)
    documents = {org.slug: await _documents(owner_db, org) for org in orgs}
    mappings = {org.slug: await _mappings(owner_db, org) for org in orgs}
    assert len(mappings["halden"]) == 13
    assert len(mappings["veridian"]) == 4

    try:
        await _migrate("0017", owner_db, app_db, worker_db)
        # 0017's shape: the mappings are in the configuration (as `lattice sync` wrote it).
        halden_config = await _configs(owner_db, orgs[0])
        assert halden_config["support"]["account_refs"]["360001001"] == "acme-corp"
        assert halden_config["slack"]["channels"]["G0ACCTACME1"] == {"account": "acme-corp"}
        assert halden_config["slack"]["channels"]["G0EXECESC01"] == {"sensitivity": "confidential"}
    finally:
        await _migrate("head", owner_db, app_db, worker_db)

    for org in orgs:
        assert await _mappings(owner_db, org) == mappings[org.slug]
        config = await _configs(owner_db, org)
        assert all("account_refs" not in c for c in config.values())
        assert all(
            "account" not in channel for channel in config["slack"].get("channels", {}).values()
        )
        assert await _documents(owner_db, org) == documents[org.slug]
        # Dated with the source: no sync is asked for (setup ``sync_required``).
        dated = await _q(
            owner_db,
            org.id,
            "SELECT count(*) FROM account_external_refs r JOIN sources s ON s.id = r.source_id "
            "WHERE r.created_by = 'operator:migration' AND r.updated_at = s.created_at",
        )
        assert dated[0][0] == len(mappings[org.slug])
        # A sync of the migrated sources -- as the worker runs it, from the rows -- finds
        # nothing to change.
        for source in SOURCES:
            result = await sync(owner_db, org, source, None, record_config=False)
            assert (result.created, result.updated, result.access_updated, result.deleted) == (
                0,
                0,
                0,
                0,
            ), (org.slug, source)
        assert await _documents(owner_db, org) == documents[org.slug]
