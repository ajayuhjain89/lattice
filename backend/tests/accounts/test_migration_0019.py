"""Migration 0019 (P12.3 correction): an unmapped reference stays known as unresolved.

Upgrading keeps every mapping; a reference can be unresolved only at 0019 (0018 refuses a
NULL account); downgrading deletes the unresolved references and restores 0018's rule."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice.db.engine import Database

from ..ingestion.conftest import Org, sync
from ..ingestion.conftest import halden as halden
from .test_migration_0018 import _migrate, _q


async def _rows(db: Database, org: Org) -> set[tuple[str, str | None]]:
    rows = await _q(
        db,
        org.id,
        "SELECT r.external_ref, a.key FROM account_external_refs r "
        "LEFT JOIN accounts a ON a.id = r.account_id",
    )
    return {(r[0], None if r[1] is None else str(r[1])) for r in rows}


async def _unresolve(db: Database, org: Org, ref: str) -> None:
    async with db.tenant_transaction(org.id) as s:
        done = await s.execute(
            text("UPDATE account_external_refs SET account_id = NULL WHERE external_ref = :r"),
            {"r": ref},
        )
        assert done.rowcount == 1  # type: ignore[attr-defined]


async def test_0019_round_trip(
    owner_db: Database, app_db: Database, worker_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "slack", None)
    mapped = await _rows(owner_db, halden)
    assert len(mapped) == 5
    await _unresolve(owner_db, halden, "C0ACCTKES01")
    unresolved = (mapped - {("C0ACCTKES01", "kestrel-bank")}) | {("C0ACCTKES01", None)}
    assert await _rows(owner_db, halden) == unresolved
    try:
        await _migrate("0018", owner_db, app_db, worker_db)
        # 0018 cannot represent it: deleted; every mapped reference stays.
        assert await _rows(owner_db, halden) == mapped - {("C0ACCTKES01", "kestrel-bank")}
        with pytest.raises(DBAPIError, match=r"not-null|account_not_found"):
            await _unresolve(owner_db, halden, "C0EXTACME01")
    finally:
        await _migrate("head", owner_db, app_db, worker_db)
    await _unresolve(owner_db, halden, "C0EXTACME01")
    assert ("C0EXTACME01", None) in await _rows(owner_db, halden)
