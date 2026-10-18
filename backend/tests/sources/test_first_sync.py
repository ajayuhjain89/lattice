"""First sync of customer-created sources, through the real API and the real worker
(P12.6; phase-12-plan C7, C8, C10, C12), over the synthetic Halden exports with Veridian
present.

Each supported format: an administrator creates the source, uploads the export, the
worker checks it (the dry run), the administrator syncs it, the worker consumes the
import by its ID. Proved with the authorization predicate itself: every Halden member can
read exactly the documents they can read when the operator syncs the same export from a
sources file (same ACLs, sensitivities, accounts, private channels); account references
the product has not mapped fail closed (quarantine / not indexed), are reported safely,
mapped through the existing P12.3 API, and indexed by the next sync; a repeated sync
changes nothing; an updated export follows the versioning and deletion rules; a failed
worker attempt is retried without duplicating anything; the administrator gains no
content access; no private channel name appears in any response."""

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from lattice.authz import Document, readable_documents_predicate, resolve_authorization_context
from lattice.db.engine import Database
from lattice.imports.store import FilesystemImportStore

from ..conftest import CSRF
from ..imports.archives import zip_tree
from ..ingestion.conftest import Org, changed_tables, row_versions, sync
from .conftest import FAKE, create, detail, events, export, rows, sync_now, upload, work

TICKET_REFS = {
    "360001001": "acme-corp",
    "360001002": "northpeak-logistics",
    "360001003": "brightwater-health",
    "360001004": "kestrel-bank",
    "360001005": "acme-health",
    "360001006": "larkspur-retail",
    "360001007": "tidewell-energy",
    "360001008": "orbital-freight",
}
CHANNEL_REFS = {
    "G0ACCTACME1": "acme-corp",
    "C0EXTACME01": "acme-corp",
    "C0ACCTKES01": "kestrel-bank",
    "C0ACCTNPK01": "northpeak-logistics",
    "C0ACCTBRW01": "brightwater-health",
}
PRIVATE_CHANNEL_NAMES = ("acct-acme", "prod-identity", "exec-escalations")


async def _members(db: Database, org: Org) -> dict[str, uuid.UUID]:
    found = await rows(
        db, org.id, "SELECT u.email, m.id FROM memberships m JOIN users u ON u.id = m.user_id"
    )
    return {str(e): m for e, m in found}


async def _readable_by(db: Database, org: Org, source_key: str) -> dict[str, set[str]]:
    """member e-mail -> external IDs of the source's documents they can read now, by the
    authorization predicate (the API role, a read-only snapshot)."""
    [(source_id,)] = await rows(db, org.id, "SELECT id FROM sources WHERE key = :k", k=source_key)
    table = Document.__table__
    result: dict[str, set[str]] = {}
    for email, membership in (await _members(db, org)).items():
        async with db.tenant_snapshot(org.id) as s:
            ctx = await resolve_authorization_context(
                s, organization_id=org.id, membership_id=membership
            )
            found = await s.execute(
                select(table.c.external_id).where(
                    table.c.source_id == source_id, readable_documents_predicate(ctx, table)
                )
            )
            result[email] = {str(r[0]) for r in found}
    return result


async def _map(
    client: httpx.AsyncClient, source_id: str, kind: str, ref: str, account: str
) -> None:
    accounts = (await client.get("/v1/admin/access/accounts", params={"q": account})).json()
    account_id = next(a["id"] for a in accounts["items"] if a["key"] == account)
    response = await client.post(
        "/v1/admin/access/account-references",
        json={
            "source_id": source_id,
            "ref_kind": kind,
            "external_ref": ref,
            "account_id": account_id,
            "reason": "Customer record",
        },
        headers=CSRF,
    )
    assert response.status_code in (200, 201), response.text


def _no_private_names(payload: Any) -> None:
    text = json.dumps(payload)
    for name in PRIVATE_CHANNEL_NAMES:
        assert name not in text, name


async def test_a_documents_export_syncs_exactly_like_the_operators(
    owner_db: Database,
    app_db: Database,
    worker_db: Database,
    halden: Org,
    admin: httpx.AsyncClient,
    store: FilesystemImportStore,
) -> None:
    source = await create(admin, "document_repository", "drive-upload")
    assert source["next_action"] == "upload_import"
    assert source["can_sync"] is False
    assert source["sync_blocked_by"] == "import_required"
    refused = await admin.post(f"/v1/admin/sources/{source['id']}/sync", headers=CSRF)
    assert refused.status_code == 422
    assert refused.json()["error"]["details"][0]["issue"] == "import_required"

    response = await upload(admin, source["id"], export(halden, "documents"))
    assert response.status_code == 201, response.text
    uploaded = response.json()
    assert uploaded["check_queued"] is True
    assert uploaded["import"]["status"] == "ready"
    assert uploaded["import"]["current"] is True
    assert uploaded["import"]["entries"] == 48  # every Markdown file of the export

    # The check: the dry run, by the worker, writes no document.
    before = await row_versions(owner_db, halden.id)
    checked = await work(worker_db, store)
    assert checked.outcome == "succeeded", checked
    assert "documents" not in changed_tables(before, await row_versions(owner_db, halden.id))
    record = await detail(admin, source["id"])
    check = record["current_import"]["check"]
    assert check["error_code"] is None
    assert check["would_create"] == record["current_import"]["entries"] - check["invalid_records"]
    assert record["next_action"] == "start_sync"

    outcome = await sync_now(admin, worker_db, store, source["id"])
    assert outcome.outcome == "succeeded", outcome
    synced = await detail(admin, source["id"])
    assert synced["documents"] == check["would_create"]
    assert synced["next_action"] == "none"

    # The operator's sync of the same export, as a second source: identical readership.
    await sync(owner_db, halden, "drive", FAKE)
    product = await _readable_by(app_db, halden, "drive-upload")
    operator = await _readable_by(app_db, halden, "drive")
    assert product == operator
    assert any(product.values())
    # The administrator reads nothing the operator's sync did not already let them read.
    assert product["lena.brandt@halden.example"] == operator["lena.brandt@halden.example"]

    # A repeated sync of the same import writes no document, chunk, grant or group.
    before = await row_versions(owner_db, halden.id)
    again = await sync_now(admin, worker_db, store, source["id"])
    assert again.outcome == "succeeded"
    assert not (
        {"documents", "chunks", "document_grants", "access_groups"}
        & changed_tables(before, await row_versions(owner_db, halden.id))
    )
    assert {e.action for e in await events(owner_db, halden.id)} >= {
        "source.created",
        "source.import_accepted",
        "source.sync_requested",
    }


async def test_tickets_fail_closed_until_their_references_are_mapped(
    owner_db: Database,
    app_db: Database,
    worker_db: Database,
    halden: Org,
    admin: httpx.AsyncClient,
    store: FilesystemImportStore,
) -> None:
    source = await create(admin, "ticketing", "support-upload")
    assert (await upload(admin, source["id"], export(halden, "tickets"))).status_code == 201
    assert (await work(worker_db, store)).outcome == "succeeded"

    # No mapping yet: every ticket names an account reference the product has not mapped,
    # so nothing would be indexed -- reported as counts and exact references only.
    record = await detail(admin, source["id"])
    check = record["current_import"]["check"]
    assert check["would_create"] == 0
    assert check["invalid_records"] == check["records_seen"] > 0
    assert {c["code"] for c in check["failure_codes"]} == {"access_unmappable:account"}
    refs = {r["external_ref"]: r for r in check["unresolved_references"]}
    assert set(refs) == {*TICKET_REFS, "360009913"}
    assert {r["state"] for r in refs.values()} == {"unmapped"}
    assert record["next_action"] == "map_references"
    # The existing Unmapped references view lists them (P12.3's representation).
    unmapped = (await admin.get("/v1/admin/access/account-references/unmapped")).json()
    listed = {i["external_ref"] for i in unmapped["items"] if i["source"]["id"] == source["id"]}
    assert listed == set(refs)

    # A sync now indexes nothing: unmapped tickets fail closed (never account-less).
    first = await sync_now(admin, worker_db, store, source["id"])
    assert first.outcome == "succeeded"
    assert (await detail(admin, source["id"]))["documents"] == 0

    for ref, account in TICKET_REFS.items():
        await _map(admin, source["id"], "ticketing_organization", ref, account)
    # Mapped since the check: shown as mapped (it applies at the next check or sync).
    stale = (await detail(admin, source["id"]))["current_import"]["check"]
    states = {r["external_ref"]: r["state"] for r in stale["unresolved_references"]}
    assert states == {**dict.fromkeys(TICKET_REFS, "mapped"), "360009913": "unmapped"}
    check_again = await admin.post(f"/v1/admin/sources/{source['id']}/check", headers=CSRF)
    assert check_again.status_code == 202, check_again.text
    assert (await work(worker_db, store)).outcome == "succeeded"
    rechecked = (await detail(admin, source["id"]))["current_import"]["check"]
    assert [r["external_ref"] for r in rechecked["unresolved_references"]] == ["360009913"]
    assert rechecked["would_create"] > 0

    assert (await sync_now(admin, worker_db, store, source["id"])).outcome == "succeeded"
    await sync(owner_db, halden, "support", FAKE)
    product = await _readable_by(app_db, halden, "support-upload")
    assert product == await _readable_by(app_db, halden, "support")
    assert any(product.values())
    # Fernhill (still unmapped) is indexed for nobody, on both paths.
    fernhill = await rows(
        owner_db,
        halden.id,
        "SELECT count(*) FROM documents d JOIN sources s ON s.id = d.source_id "
        "WHERE s.key = 'support-upload' AND d.status = 'active' AND d.account_id IS NULL",
    )
    assert fernhill == [(0,)]


async def test_a_slack_export_keeps_private_channels_private_and_unnamed(
    owner_db: Database,
    app_db: Database,
    worker_db: Database,
    halden: Org,
    admin: httpx.AsyncClient,
    store: FilesystemImportStore,
) -> None:
    source = await create(admin, "chat_export", "slack-upload")
    changed = await admin.patch(
        f"/v1/admin/sources/{source['id']}",
        json={
            "version": source["version"],
            "reason": "Executive escalations are confidential",
            "channel_sensitivities": {"G0EXECESC01": "confidential"},
        },
        headers=CSRF,
    )
    assert changed.status_code == 200, changed.text
    for ref, account in CHANNEL_REFS.items():
        await _map(admin, source["id"], "chat_channel", ref, account)
    response = await upload(admin, source["id"], export(halden, "slack", wrapper="halden-export"))
    assert response.status_code == 201, response.text
    assert (await work(worker_db, store)).outcome == "succeeded"
    record = await detail(admin, source["id"])
    assert record["current_import"]["check"]["unresolved_total"] == 0
    assert (await sync_now(admin, worker_db, store, source["id"])).outcome == "succeeded"

    await sync(owner_db, halden, "slack", FAKE)
    product = await _readable_by(app_db, halden, "slack-upload")
    assert product == await _readable_by(app_db, halden, "slack")
    private = {
        r[0]
        for r in await rows(
            owner_db,
            halden.id,
            "SELECT d.external_id FROM documents d JOIN sources s ON s.id = d.source_id "
            "WHERE s.key = 'slack-upload' AND d.access_mode = 'grants_only' "
            "AND d.status = 'active'",
        )
    }
    assert private
    assert not product["lena.brandt@halden.example"] & private  # admin != channel member

    # No administrator surface names a private channel.
    for payload in (
        await detail(admin, source["id"]),
        (await admin.get("/v1/admin/sources")).json(),
        (await admin.get(f"/v1/admin/sources/{source['id']}/failures")).json(),
        (await admin.get("/v1/admin/access/account-references/unmapped")).json(),
        (await admin.get("/v1/admin/audit-events")).json(),
        (await admin.get(f"/v1/admin/sources/{source['id']}/disconnect-preview")).json(),
    ):
        _no_private_names(payload)


async def test_an_updated_export_follows_the_versioning_and_deletion_rules(
    owner_db: Database,
    worker_db: Database,
    halden: Org,
    admin: httpx.AsyncClient,
    store: FilesystemImportStore,
    tmp_path: Path,
) -> None:
    source = await create(admin, "document_repository", "drive-upload")
    first = (await upload(admin, source["id"], export(halden, "documents"))).json()["import"]
    await work(worker_db, store)
    await sync_now(admin, worker_db, store, source["id"])
    before = {
        r[0]: (r[1], r[2], r[3])
        for r in await rows(
            owner_db,
            halden.id,
            "SELECT d.external_id, d.version, d.status, d.sensitivity FROM documents d "
            "JOIN sources s ON s.id = d.source_id WHERE s.key = 'drive-upload'",
        )
    }

    # The next export: one document edited, one removed, one made confidential.
    changed = tmp_path / "next"
    shutil.copytree(halden.root / "documents", changed)
    paths = sorted(changed.rglob("*.md"))
    edited, removed, tightened = paths[0], paths[1], paths[2]
    edited.write_text(edited.read_text() + "\nA new closing paragraph.\n")
    removed.unlink()
    text = tightened.read_text()
    if "sensitivity:" in text.split("---")[1]:
        text = text.replace("sensitivity: internal", "sensitivity: confidential", 1)
    else:
        text = text.replace("\n---\n", "\nsensitivity: confidential\n---\n", 1)
    tightened.write_text(text)

    second = (await upload(admin, source["id"], zip_tree(changed))).json()["import"]
    assert second["id"] != first["id"]
    await work(worker_db, store)
    check = (await detail(admin, source["id"]))["current_import"]["check"]
    assert (check["would_update"], check["would_delete"]) == (1, 1)
    assert check["would_update_access"] >= 0
    await sync_now(admin, worker_db, store, source["id"])

    after = {
        r[0]: (r[1], r[2], r[3])
        for r in await rows(
            owner_db,
            halden.id,
            "SELECT d.external_id, d.version, d.status, d.sensitivity FROM documents d "
            "JOIN sources s ON s.id = d.source_id WHERE s.key = 'drive-upload'",
        )
    }
    # A record gone from a full snapshot is deleted (its row, chunks and grants; one of 48
    # is under the deletion guard's ratio); every other identity is kept, none duplicated.
    assert len(set(before) - set(after)) == 1
    assert set(after) < set(before)
    changed_ids = {k for k in after if after[k] != before[k]}
    assert len(changed_ids) == 2
    # The edited record is a new version; the tightened one an access update (same version).
    assert sorted((after[k][0] - before[k][0]) for k in changed_ids) == [0, 1]
    assert any(
        after[k][2] == "confidential" and before[k][2] != "confidential" for k in changed_ids
    )
    # The source identity did not change across imports.
    assert (
        len(await rows(owner_db, halden.id, "SELECT 1 FROM sources WHERE key = 'drive-upload'"))
        == 1
    )

    # The first import is superseded, and its files go once no job reads it.
    imports = dict(await rows(owner_db, halden.id, "SELECT id::text, status FROM source_imports"))
    assert imports == {first["id"]: "superseded", second["id"]: "ready"}
    from lattice.jobs.worker import Worker

    assert await Worker(worker_db, embedding=None, store=store).import_retention() == 1
    assert not (store.root / str(halden.id) / first["id"]).exists()
    assert store.tree(halden.id, uuid.UUID(second["id"])).is_dir()


async def test_an_interrupted_sync_is_retried_without_duplicates(
    owner_db: Database,
    worker_db: Database,
    halden: Org,
    admin: httpx.AsyncClient,
    store: FilesystemImportStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = await create(admin, "document_repository", "drive-upload")
    await upload(admin, source["id"], export(halden, "documents"))
    await work(worker_db, store)

    from lattice.ingestion.sync import Syncer

    real = Syncer._sweep
    calls = {"n": 0}

    async def interrupted(self: Any, *args: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("worker lost mid-sync")
        await real(self, *args, **kwargs)

    monkeypatch.setattr(Syncer, "_sweep", interrupted)
    response = await admin.post(f"/v1/admin/sources/{source['id']}/sync", headers=CSRF)
    assert response.status_code == 202
    assert (await work(worker_db, store)).outcome == "retry"
    async with owner_db.tenant_transaction(halden.id) as s:
        from sqlalchemy import text

        await s.execute(text("UPDATE jobs SET run_after = now() WHERE status = 'queued'"))
    assert (await work(worker_db, store)).outcome == "succeeded"

    runs = await rows(
        owner_db,
        halden.id,
        "SELECT r.status, r.import_id::text FROM source_sync_runs r JOIN sources s "
        "ON s.id = r.source_id WHERE s.key = 'drive-upload' ORDER BY r.started_at",
    )
    current = (await detail(admin, source["id"]))["current_import"]["id"]
    assert runs == [("aborted", current), ("succeeded", current)]
    counted = await rows(
        owner_db,
        halden.id,
        "SELECT count(*), count(DISTINCT d.external_id) FROM documents d "
        "JOIN sources s ON s.id = d.source_id WHERE s.key = 'drive-upload'",
    )
    assert counted == [(48, 48)]
