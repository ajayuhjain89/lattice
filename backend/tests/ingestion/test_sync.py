"""The sync pipeline end to end, against PostgreSQL (docs/ingestion-design.md §1-7).

* First sync of the Halden dataset; a second sync writes nothing (row versions of every
  table a sync may touch are unchanged, except the source's ``last_synced_at``).
* ``--dry-run`` reports exactly what the real run then does, and writes nothing.
* A content update bumps the version, keeps unchanged chunks (IDs and vectors) and embeds
  only new passages; a failure mid-write leaves the previous version whole.
* An access-only change never re-chunks or re-embeds; ``acl_only`` mode ignores content.
* Reclassification above the egress level removes vectors at once; lowering it restores
  them. Provider failures leave chunks lexical-only until a later sync fills them.
* A record whose access can no longer be mapped quarantines its document.
* Private-channel groups follow the export; supersession links are resolved.
"""

import json
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice.db.engine import Database
from lattice.ingestion import store
from lattice.ingestion.sync import SyncError
from lattice.providers.embedding import RecordingEmbeddingProvider

from .conftest import Org, changed_tables, row_versions, rows, scalar, sync, sync_all
from .helpers import chunks, document, edit_front_matter, edit_ticket

HALDEN_COUNTS = {
    # source: (records seen, created, rejected)
    "drive": (48, 48, 0),
    "support": (148, 147, 1),
    "slack": (148, 148, 0),
}


async def test_first_sync_then_a_second_sync_writes_nothing(
    owner_db: Database, halden: Org
) -> None:
    provider = RecordingEmbeddingProvider()
    first = await sync_all(owner_db, halden, provider)
    for key, (seen, created, rejected) in HALDEN_COUNTS.items():
        r = first[key]
        assert (r.records_seen, r.created, r.failed) == (seen, created, rejected), key
        assert r.status == ("partial" if rejected else "succeeded"), key
    assert first["support"].failure_codes == {"access_unmappable:account": 1}
    assert await scalar(owner_db, halden.id, "SELECT count(*) FROM documents") == 343
    assert (
        await scalar(
            owner_db, halden.id, "SELECT count(*) FROM documents WHERE external_id = 'HAL-4951'"
        )
        == 0
    )

    before = await row_versions(owner_db, halden.id)
    again = RecordingEmbeddingProvider()
    second = await sync_all(owner_db, halden, again)
    after = await row_versions(owner_db, halden.id)
    for key, (_, created, _) in HALDEN_COUNTS.items():
        r = second[key]
        assert (r.unchanged, r.created, r.updated, r.access_updated, r.deleted) == (
            created,
            0,
            0,
            0,
            0,
        ), key
        assert (r.chunks_embedded, r.embeddings_reused, r.quarantined) == (0, 0, 0), key
    assert again.calls == []
    # Only the operational record changes: the run rows and the source's timestamps.
    assert changed_tables(before, after) == {"sources"}


async def test_dry_run_predicts_the_real_run_and_writes_nothing(
    owner_db: Database, halden: Org
) -> None:
    provider = RecordingEmbeddingProvider()
    before = await row_versions(owner_db, halden.id)
    dry = await sync_all(owner_db, halden, provider, dry_run=True)
    assert changed_tables(before, await row_versions(owner_db, halden.id)) == set()
    assert await scalar(owner_db, halden.id, "SELECT count(*) FROM source_sync_runs") == 0
    assert provider.calls == []

    real = await sync_all(owner_db, halden, provider)
    for key in HALDEN_COUNTS:
        d, r = dry[key], real[key]
        assert d.status == "dry_run"
        assert (d.records_seen, d.created, d.updated, d.failed, d.failure_codes) == (
            r.records_seen,
            r.created,
            r.updated,
            r.failed,
            r.failure_codes,
        ), key

    edit_ticket(halden, "HAL-4821", lambda t: t.update(priority="urgent"))
    (halden.path("drive") / "policies" / "enterprise-refund-policy-v1.md").unlink(missing_ok=False)
    before = await row_versions(owner_db, halden.id)
    dry_again = await sync_all(owner_db, halden, provider, dry_run=True)
    assert changed_tables(before, await row_versions(owner_db, halden.id)) == set()
    assert dry_again["support"].updated == 1
    assert dry_again["drive"].would_delete == 1
    assert dry_again["drive"].deleted == 0


def _add_comment(ticket: dict[str, Any]) -> None:
    ticket["comments"].append(
        {
            "id": 999001,
            "author": {"name": "Priya Natarajan", "email": "priya@halden.example"},
            "author_role": "agent",
            "public": True,
            "created_at": "2026-09-27T10:00:00Z",
            "body": "Update: the redirect fix shipped in 2026.10.1 for every tenant.",
        }
    )
    ticket["updated_at"] = "2026-09-27T10:00:00Z"


async def test_a_content_update_keeps_unchanged_chunks_and_embeds_only_new_text(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "support", RecordingEmbeddingProvider())
    old_doc = await document(owner_db, halden, "HAL-4821")
    old_chunks = await chunks(owner_db, halden, old_doc["id"])
    assert all(c["status"] == "embedded" for c in old_chunks)

    edit_ticket(halden, "HAL-4821", _add_comment)
    provider = RecordingEmbeddingProvider()
    result = await sync(owner_db, halden, "support", provider)
    assert (result.updated, result.unchanged, result.created) == (1, 146, 0)
    assert result.embeddings_reused == len(old_chunks)
    assert result.chunks_embedded == 1
    (payload,) = provider.texts
    assert payload.endswith("Update: the redirect fix shipped in 2026.10.1 for every tenant.")

    new_doc = await document(owner_db, halden, "HAL-4821")
    new_chunks = await chunks(owner_db, halden, new_doc["id"])
    assert new_doc["id"] == old_doc["id"]
    assert new_doc["version"] == 2
    assert new_doc["content_hash"] != old_doc["content_hash"]
    assert new_doc["ingested_at"] > old_doc["ingested_at"]
    assert new_doc["acl_updated_at"] == old_doc["acl_updated_at"]
    assert [c["id"] for c in new_chunks[:-1]] == [c["id"] for c in old_chunks]
    assert all(c["status"] == "embedded" for c in new_chunks)


async def test_a_failed_write_leaves_the_previous_version_whole(
    owner_db: Database, halden: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await sync(owner_db, halden, "support", RecordingEmbeddingProvider())
    old_doc = await document(owner_db, halden, "HAL-4821")
    old_chunks = await chunks(owner_db, halden, old_doc["id"])

    async def boom(*args: object, **kwargs: object) -> None:
        raise DBAPIError("INSERT INTO document_grants", {}, Exception("simulated"))

    monkeypatch.setattr(store, "replace_source_grants", boom)
    edit_ticket(halden, "HAL-4821", _add_comment)
    result = await sync(owner_db, halden, "support", RecordingEmbeddingProvider())
    assert result.failure_codes["persist_failed"] == 1
    assert result.status == "partial"
    # The document row and chunk deletions ran before the failure; all rolled back.
    assert await document(owner_db, halden, "HAL-4821") == old_doc
    assert await chunks(owner_db, halden, old_doc["id"]) == old_chunks

    monkeypatch.undo()
    retried = await sync(owner_db, halden, "support", RecordingEmbeddingProvider())
    assert retried.updated == 1
    assert (await document(owner_db, halden, "HAL-4821"))["version"] == 2


async def test_an_access_change_never_rechunks_or_reembeds(owner_db: Database, halden: Org) -> None:
    await sync(owner_db, halden, "drive", RecordingEmbeddingProvider())
    old = await document(owner_db, halden, "refund-policy-v2")
    old_chunks = await chunks(owner_db, halden, old["id"])

    edit_front_matter(halden, "refund-policy-v2", departments=["support", "customer-success"])
    provider = RecordingEmbeddingProvider()
    before = await row_versions(owner_db, halden.id)
    result = await sync(owner_db, halden, "drive", provider)
    after = await row_versions(owner_db, halden.id)
    assert (result.access_updated, result.updated, result.unchanged) == (1, 0, 47)
    assert provider.calls == []
    assert "chunks" not in changed_tables(before, after)
    new = await document(owner_db, halden, "refund-policy-v2")
    assert new["content_hash"] == old["content_hash"]
    assert new["version"] == old["version"]
    assert new["access_hash"] != old["access_hash"]
    assert new["acl_updated_at"] > old["acl_updated_at"]
    assert len(new["department_ids"]) == 2
    assert await chunks(owner_db, halden, new["id"]) == old_chunks


async def test_acl_only_mode_applies_access_and_ignores_content(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "drive", RecordingEmbeddingProvider())
    old = await document(owner_db, halden, "refund-policy-v2")
    edit_front_matter(
        halden,
        "refund-policy-v2",
        body=lambda b: b + "\nA new paragraph the ACL sync must not ingest.\n",
        departments=["support"],
    )
    (halden.path("drive") / "policies" / "enterprise-refund-policy-v1.md").unlink()
    new_file = halden.path("drive") / "brand-new.md"
    new_file.write_text(
        "---\nid: brand-new\ntitle: Brand new\nauthor: Ana Lima\n"
        "created: 2026-09-20T09:00:00Z\nupdated: 2026-09-20T09:00:00Z\n---\nNew.\n"
    )
    provider = RecordingEmbeddingProvider()
    result = await sync(owner_db, halden, "drive", provider, mode="acl_only")
    assert (result.access_updated, result.updated, result.created, result.deleted) == (1, 0, 0, 0)
    assert result.skipped == 1  # the new record waits for a full sync
    assert provider.calls == []
    new = await document(owner_db, halden, "refund-policy-v2")
    assert new["content_hash"] == old["content_hash"]
    assert new["body"] == old["body"]
    assert new["access_hash"] != old["access_hash"]
    assert await scalar(owner_db, halden.id, "SELECT count(*) FROM documents") == 48

    full = await sync(owner_db, halden, "drive", provider)
    assert (full.updated, full.created, full.deleted) == (1, 1, 1)


async def test_raising_a_classification_removes_vectors_and_lowering_restores_them(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "drive", RecordingEmbeddingProvider())
    doc = await document(owner_db, halden, "refund-policy-v2")
    before = await chunks(owner_db, halden, doc["id"])
    assert all(c["has_vector"] for c in before)

    edit_front_matter(halden, "refund-policy-v2", sensitivity="confidential")
    provider = RecordingEmbeddingProvider()
    raised = await sync(owner_db, halden, "drive", provider)
    assert raised.access_updated == 1
    assert raised.chunks_policy_excluded == len(before)
    assert provider.calls == []
    excluded = await chunks(owner_db, halden, doc["id"])
    assert [c["id"] for c in excluded] == [c["id"] for c in before]
    assert all(
        (c["status"], c["has_vector"], c["model"]) == ("policy_excluded", False, None)
        for c in excluded
    )

    edit_front_matter(halden, "refund-policy-v2", sensitivity=None)
    lowered = await sync(owner_db, halden, "drive", provider)
    assert lowered.chunks_embedded == len(before)
    assert len(provider.texts) == len(before)
    restored = await chunks(owner_db, halden, doc["id"])
    assert all(c["status"] == "embedded" and c["has_vector"] for c in restored)


async def test_provider_failures_leave_chunks_lexical_until_a_later_sync(
    owner_db: Database, halden: Org
) -> None:
    failing = RecordingEmbeddingProvider(fail=True)
    first = await sync(owner_db, halden, "drive", failing)
    assert first.created == 48
    assert first.status == "partial"
    assert first.embedding_failures > 0
    counts = dict(
        await rows(owner_db, halden.id, "SELECT embedding_status, count(*) FROM chunks GROUP BY 1")
    )
    assert set(counts) <= {"failed", "policy_excluded"}
    assert await scalar(owner_db, halden.id, "SELECT count(*) FROM chunks WHERE tsv IS NULL") == 0

    second = await sync(owner_db, halden, "drive", RecordingEmbeddingProvider())
    assert second.chunks_embedded == counts["failed"]
    assert second.status == "succeeded"
    assert (
        await scalar(
            owner_db,
            halden.id,
            "SELECT count(*) FROM chunks WHERE embedding_status IN ('failed', 'pending')",
        )
        == 0
    )


async def test_without_a_provider_eligible_chunks_wait_as_pending(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "drive", None)
    statuses = dict(
        await rows(owner_db, halden.id, "SELECT embedding_status, count(*) FROM chunks GROUP BY 1")
    )
    assert set(statuses) == {"pending", "policy_excluded"}
    filled = await sync(owner_db, halden, "drive", RecordingEmbeddingProvider())
    assert filled.chunks_embedded == statuses["pending"]
    assert filled.unchanged == 48


async def test_unmappable_access_quarantines_an_existing_document(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "drive", None)
    edit_front_matter(halden, "refund-policy-v2", departments=["no-such-department"])
    result = await sync(owner_db, halden, "drive", None)
    assert result.failure_codes == {"access_unmappable:department": 1}
    assert result.quarantined == 1
    assert (await document(owner_db, halden, "refund-policy-v2"))["status"] == "quarantined"

    edit_front_matter(halden, "refund-policy-v2", departments=None)
    restored = await sync(owner_db, halden, "drive", None)
    assert restored.updated == 1
    assert (await document(owner_db, halden, "refund-policy-v2"))["status"] == "active"


async def test_an_unparseable_record_quarantines_its_existing_document(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "drive", None)
    edit_front_matter(halden, "refund-policy-v2", sensitivity="top-secret")
    result = await sync(owner_db, halden, "drive", None)
    assert result.failure_codes == {"invalid_record": 1}
    assert result.quarantined == 1
    assert result.deleted == 0
    assert (await document(owner_db, halden, "refund-policy-v2"))["status"] == "quarantined"


async def test_private_channel_membership_follows_the_export(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "slack", None)
    members_sql = (
        "SELECT u.email FROM access_group_members gm JOIN access_groups g ON g.id = gm.group_id "
        "JOIN memberships m ON m.id = gm.membership_id JOIN users u ON u.id = m.user_id "
        "WHERE g.external_ref = 'G0ACCTACME1'"
    )
    assert {r[0] for r in await rows(owner_db, halden.id, members_sql)} >= {
        "maya.okafor@halden.example",
        "grace.whitfield@halden.example",
    }
    group = await rows(
        owner_db,
        halden.id,
        "SELECT origin, key, name, source_id IS NOT NULL FROM access_groups "
        "WHERE external_ref = 'G0ACCTACME1'",
    )
    assert group == [("source", "src-slack-g0acctacme1", "#acct-acme", True)]
    stamps_sql = (
        "SELECT u.email, m.authz_updated_at FROM memberships m JOIN users u ON u.id = m.user_id"
    )
    before = dict(await rows(owner_db, halden.id, stamps_sql))

    groups_path = halden.path("slack") / "groups.json"
    groups = json.loads(groups_path.read_text())
    acme = next(g for g in groups if g["id"] == "G0ACCTACME1")
    acme["members"].remove("U01MAYAOKF")
    groups_path.write_text(json.dumps(groups))
    result = await sync(owner_db, halden, "slack", None)
    assert result.memberships_regrouped == 1
    emails = {r[0] for r in await rows(owner_db, halden.id, members_sql)}
    assert "maya.okafor@halden.example" not in emails
    after = dict(await rows(owner_db, halden.id, stamps_sql))
    assert {e for e in after if after[e] != before[e]} == {"maya.okafor@halden.example"}


async def test_supersession_links_are_resolved_and_cleared(owner_db: Database, halden: Org) -> None:
    await sync(owner_db, halden, "drive", None)
    link_sql = (
        "SELECT s.external_id, t.external_id FROM documents s "
        "JOIN documents t ON t.id = s.supersedes_document_id"
    )
    assert ("refund-policy-v2", "refund-policy-v1") in await rows(owner_db, halden.id, link_sql)
    (halden.path("drive") / "policies" / "enterprise-refund-policy-v1.md").unlink()
    result = await sync(owner_db, halden, "drive", None)
    assert result.deleted == 1
    assert ("refund-policy-v2", "refund-policy-v1") not in await rows(owner_db, halden.id, link_sql)


async def test_reindex_rechunks_and_reembeds_without_deleting(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "drive", RecordingEmbeddingProvider())
    total = await scalar(
        owner_db, halden.id, "SELECT count(*) FROM chunks WHERE embedding_status = 'embedded'"
    )
    (halden.path("drive") / "policies" / "enterprise-refund-policy-v1.md").unlink()

    provider = RecordingEmbeddingProvider()
    rechunked = await sync(owner_db, halden, "drive", provider, mode="reindex", rechunk=True)
    assert (rechunked.updated, rechunked.deleted) == (47, 0)
    assert provider.calls == []  # identical chunk inputs reuse their vectors
    assert await scalar(owner_db, halden.id, "SELECT count(*) FROM documents") == 48

    reembedded = await sync(owner_db, halden, "drive", provider, mode="reindex", reembed=True)
    assert reembedded.deleted == 0
    assert len(provider.texts) == reembedded.chunks_embedded
    assert reembedded.chunks_embedded == total  # every eligible chunk, again


async def test_one_sync_per_source_at_a_time(owner_db: Database, halden: Org) -> None:
    await sync(owner_db, halden, "drive", None, dry_run=True)
    name = f"lattice-sync:{halden.id}:drive"
    async with owner_db.engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(hashtextextended(:n, 0))"), {"n": name})
        await conn.commit()
        with pytest.raises(SyncError) as caught:
            await sync(owner_db, halden, "drive", None)
        assert caught.value.code == "source_busy"
        await conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:n, 0))"), {"n": name})
        await conn.commit()
    assert (await sync(owner_db, halden, "drive", None)).created == 48


async def test_an_unexpected_error_aborts_the_run_and_is_raised(
    owner_db: Database, halden: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: object, **kwargs: object) -> int:
        raise RuntimeError("bug")

    monkeypatch.setattr(store, "resolve_supersession", broken)
    with pytest.raises(RuntimeError):
        await sync(owner_db, halden, "drive", None)
    run = await rows(owner_db, halden.id, "SELECT status, error_code FROM source_sync_runs")
    assert run == [("aborted", "internal_error")]
    stamps = await rows(owner_db, halden.id, "SELECT last_succeeded_at, acl_synced_at FROM sources")
    assert stamps == [(None, None)]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("deleting", "source_config:source_deleting"),
        ("connector_type", "source_config:connector_type_changed"),
        ("unknown_account", "source_config:unknown_account"),
    ],
)
async def test_source_configuration_problems_refuse_the_sync(
    owner_db: Database, halden: Org, change: str, code: str
) -> None:
    key = "drive" if change == "connector_type" else "support"
    await sync(owner_db, halden, key, None)
    before = await row_versions(owner_db, halden.id)
    if change == "deleting":
        async with owner_db.tenant_transaction(halden.id) as s:
            await s.execute(text("UPDATE sources SET status = 'deleting' WHERE key = 'support'"))
        before = await row_versions(owner_db, halden.id)
    elif change == "connector_type":
        # The stored type is immutable (0022 refuses changing it); the file is what changes.
        async with owner_db.tenant_transaction(halden.id) as s:
            with pytest.raises(DBAPIError, match="source_identity_immutable"):
                await s.execute(
                    text("UPDATE sources SET connector_type = 'chat_export' WHERE key = 'drive'")
                )
        path = halden.root / "sources.yaml"
        configured = path.read_text()
        assert "  - key: drive\n    connector_type: document_repository\n" in configured
        path.write_text(
            configured.replace(
                "  - key: drive\n    connector_type: document_repository\n",
                "  - key: drive\n    connector_type: ticketing\n",
            )
        )
        halden.reload()
    else:
        path = halden.root / "sources.yaml"
        path.write_text(path.read_text().replace("360001001: acme-corp", "360001001: globex"))
        halden.reload()
    with pytest.raises(SyncError) as caught:
        await sync(owner_db, halden, key, None)
    assert caught.value.code == code
    assert changed_tables(before, await row_versions(owner_db, halden.id)) == set()
