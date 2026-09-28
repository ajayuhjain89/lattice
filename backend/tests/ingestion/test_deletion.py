"""Deletion: the snapshot sweep, its guard, source failures and source disconnect
(docs/ingestion-design.md §1, §9; docs/data-model.md §10).

* A record gone from a full snapshot deletes its document, chunks (vectors and lexical
  entries are chunk columns) and every grant on it.
* The guard stops the sweep when the snapshot looks broken: a mass deletion or an empty
  snapshot (overridable with ``allow_mass_deletion``), or a record that failed before
  it could be identified (never overridable; the unseen documents are quarantined).
* A source that cannot be read fails the run and deletes nothing.
* Disconnect makes the source's content unreadable at once and purges it completely.
"""

import json
import shutil

import pytest
from sqlalchemy import text

from lattice.admin_grants import apply_admin_grants, load_admin_grants
from lattice.db.engine import Database
from lattice.ingestion.lifecycle import SourceNotFound, disconnect_source
from lattice.ingestion.sync import SyncError

from .conftest import Org, changed_tables, row_versions, rows, scalar, sync, sync_all
from .helpers import document, ids_of, remove_tickets

BULK = "tickets-bulk.jsonl"


async def test_a_removed_record_is_deleted_with_its_chunks_and_grants(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "drive", None)
    doc = await document(owner_db, halden, "project-tern-memo")
    assert await scalar(
        owner_db, halden.id, "SELECT count(*) FROM chunks WHERE document_id = :d", d=doc["id"]
    )
    assert await scalar(
        owner_db,
        halden.id,
        "SELECT count(*) FROM document_grants WHERE document_id = :d",
        d=doc["id"],
    )
    path = next(halden.path("drive").rglob("project-tern-memo.md"))
    path.unlink()
    result = await sync(owner_db, halden, "drive", None)
    assert (result.deleted, result.unchanged, result.status) == (1, 47, "succeeded")
    for table in ("documents", "chunks", "document_grants"):
        column = "id" if table == "documents" else "document_id"
        assert (
            await scalar(
                owner_db,
                halden.id,
                f"SELECT count(*) FROM {table} WHERE {column} = :d",  # noqa: S608
                d=doc["id"],
            )
            == 0
        ), table


async def test_the_guard_stops_a_mass_deletion_until_overridden(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "support", None)
    (halden.path("support") / BULK).unlink()  # 120 of 147 tickets
    before = await row_versions(owner_db, halden.id)
    guarded = await sync(owner_db, halden, "support", None)
    assert guarded.deletion_guard_tripped
    assert guarded.error_code == "deletion_guard:mass_deletion"
    assert (guarded.deleted, guarded.would_delete, guarded.status) == (0, 120, "partial")
    assert "documents" not in changed_tables(before, await row_versions(owner_db, halden.id))
    run = await rows(
        owner_db,
        halden.id,
        "SELECT status, deletion_guard_tripped, error_code FROM source_sync_runs "
        "ORDER BY started_at DESC LIMIT 1",
    )
    assert run == [("partial", True, "deletion_guard:mass_deletion")]

    dry = await sync(owner_db, halden, "support", None, dry_run=True, allow_mass_deletion=True)
    assert (dry.would_delete, dry.deleted) == (120, 0)
    allowed = await sync(owner_db, halden, "support", None, allow_mass_deletion=True)
    assert allowed.deleted == 120
    assert await scalar(owner_db, halden.id, "SELECT count(*) FROM documents") == 27


async def test_a_small_deletion_passes_the_guard(owner_db: Database, halden: Org) -> None:
    await sync(owner_db, halden, "support", None)
    remove_tickets(halden, {"HAL-4821", "HAL-3187"})
    result = await sync(owner_db, halden, "support", None)
    assert (result.deleted, result.deletion_guard_tripped) == (2, False)


async def test_an_empty_snapshot_trips_the_guard(owner_db: Database, halden: Org) -> None:
    await sync(owner_db, halden, "drive", None)
    shutil.rmtree(halden.path("drive"))
    halden.path("drive").mkdir()
    result = await sync(owner_db, halden, "drive", None)
    assert result.error_code == "deletion_guard:empty_snapshot"
    assert result.deleted == 0
    assert await scalar(owner_db, halden.id, "SELECT count(*) FROM documents") == 48


async def test_an_unidentified_failure_blocks_deletion_and_quarantines_the_unseen(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "support", None)
    path = halden.path("support") / "tickets.jsonl"
    lines = path.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if json.loads(line)["id"] == "HAL-4821")
    lines[index] = lines[index][:40]  # truncated: the ticket cannot be identified
    path.write_text("\n".join(lines) + "\n")
    for override in (False, True):
        result = await sync(owner_db, halden, "support", None, allow_mass_deletion=override)
        assert result.error_code == "deletion_guard:unidentified_failures"
        assert result.failure_codes == {"malformed_json": 1, "access_unmappable:account": 1}
        assert result.deleted == 0
    # HAL-4821 is the only unseen document: it could be the broken line, so it is no
    # longer readable on the access the source last stated.
    assert (await document(owner_db, halden, "HAL-4821"))["status"] == "quarantined"
    statuses = await rows(
        owner_db, halden.id, "SELECT status, count(*) FROM documents GROUP BY 1 ORDER BY 1"
    )
    assert statuses == [("active", 146), ("quarantined", 1)]


async def test_an_unreadable_source_fails_the_run_and_deletes_nothing(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "support", None)
    shutil.rmtree(halden.path("support"))
    before = await row_versions(owner_db, halden.id)
    result = await sync(owner_db, halden, "support", None)
    assert (result.status, result.error_code) == ("failed", "source_unavailable:missing_export")
    assert changed_tables(before, await row_versions(owner_db, halden.id)) == {"sources"}
    succeeded = await rows(
        owner_db,
        halden.id,
        "SELECT last_succeeded_at IS NOT NULL FROM sources WHERE key = 'support'",
    )
    assert succeeded == [(False,)]  # the first sync was partial (HAL-4951)

    with pytest.raises(SyncError) as caught:
        await sync(owner_db, halden, "support", None, dry_run=True)
    assert caught.value.code == "source_unavailable:missing_export"


# --- disconnect -------------------------------------------------------------------------


async def test_disconnect_purges_everything_the_source_brought(
    owner_db: Database, halden: Org
) -> None:
    await sync_all(owner_db, halden, None)
    report = await apply_admin_grants(
        owner_db, "halden", load_admin_grants(halden.root / "admin-grants.yaml")
    )
    assert report.problems == []
    slack_id = (await rows(owner_db, halden.id, "SELECT id FROM sources WHERE key = 'slack'"))[0][0]
    drive_before = await ids_of(owner_db, halden, "documents", f"source_id <> '{slack_id}'")
    stamps_sql = "SELECT id, authz_updated_at FROM memberships"
    stamps = dict(await rows(owner_db, halden.id, stamps_sql))

    result = await disconnect_source(owner_db, halden.id, "slack")
    assert result.documents == 148
    assert result.chunks > 0
    assert result.vectors == 0  # synced without a provider
    assert result.source_grants > 0
    assert result.groups == len(json.loads((halden.path("slack") / "groups.json").read_text()))
    assert result.sync_runs == 1

    remaining = await rows(
        owner_db,
        halden.id,
        "SELECT (SELECT count(*) FROM sources WHERE key = 'slack'), "
        "(SELECT count(*) FROM documents WHERE source_id = :s), "
        "(SELECT count(*) FROM chunks c WHERE NOT EXISTS "
        " (SELECT 1 FROM documents d WHERE d.id = c.document_id)), "
        "(SELECT count(*) FROM access_groups WHERE origin = 'source'), "
        "(SELECT count(*) FROM source_sync_runs WHERE source_id = :s), "
        "(SELECT count(*) FROM document_grants g WHERE NOT EXISTS "
        " (SELECT 1 FROM documents d WHERE d.id = g.document_id))",
        s=slack_id,
    )
    assert remaining == [(0, 0, 0, 0, 0, 0)]
    assert await ids_of(owner_db, halden, "documents") == drive_before
    # Members of the purged private channels lost access: their context is stale.
    after = dict(await rows(owner_db, halden.id, stamps_sql))
    assert {m for m in after if after[m] != stamps[m]}

    with pytest.raises(SourceNotFound):
        await disconnect_source(owner_db, halden.id, "slack")


async def test_a_deleting_source_is_unreadable_and_disconnect_resumes(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "support", None)
    # Step 1 ran, the purge did not (e.g. the process died in between).
    async with owner_db.tenant_transaction(halden.id) as s:
        await s.execute(text("UPDATE sources SET status = 'deleting' WHERE key = 'support'"))
        await s.execute(text("UPDATE documents SET status = 'deleted'"))
    with pytest.raises(SyncError) as caught:
        await sync(owner_db, halden, "support", None)
    assert caught.value.code == "source_config:source_deleting"
    report = await disconnect_source(owner_db, halden.id, "support")
    assert report.documents == 147
    assert await scalar(owner_db, halden.id, "SELECT count(*) FROM documents") == 0
    # A new sync after the purge is a fresh source.
    again = await sync(owner_db, halden, "support", None)
    assert again.created == 147
