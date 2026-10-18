"""Source administration in the product (P12.6; phase-12-plan C7; ADR-017): create,
edit (with the widening guard), pause and resume, disconnect -- administrators only,
same organization, versioned, audited, and never a content or access shortcut."""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.imports.store import FilesystemImportStore
from lattice.ingestion.sync import SyncError

from ..conftest import CSRF, AppFactory
from ..factories import login
from ..ingestion.conftest import Org, sync
from .conftest import (
    FAKE,
    REASON,
    create,
    detail,
    events,
    export,
    new_source,
    rows,
    sync_now,
    upload,
    veridian_admin,
    work,
)


async def _patch(client: httpx.AsyncClient, source: dict[str, Any], **body: Any) -> httpx.Response:
    return await client.patch(
        f"/v1/admin/sources/{source['id']}",
        json={"version": source["version"], "reason": REASON, **body},
        headers=CSRF,
    )


async def _post(
    client: httpx.AsyncClient, source: dict[str, Any], action: str, **body: Any
) -> httpx.Response:
    return await client.post(
        f"/v1/admin/sources/{source['id']}/{action}",
        json={"version": source["version"], "reason": REASON, **body},
        headers=CSRF,
    )


def _issue(response: httpx.Response) -> str:
    issue: str = response.json()["error"]["details"][0]["issue"]
    return issue


# --- create --------------------------------------------------------------------------------


async def test_an_administrator_creates_an_export_source_that_grants_nothing(
    owner_db: Database, halden: Org, admin: httpx.AsyncClient
) -> None:
    grants_before = await rows(owner_db, halden.id, "SELECT count(*) FROM document_grants")
    source = await create(
        admin, "document_repository", "policies", default_departments=["support", "it"]
    )
    assert source["managed_by"] == "organization"
    assert source["status"] == "active"
    assert source["documents"] == 0
    assert [d["key"] for d in source["settings"]["default_departments"]] == ["it", "support"]
    assert source["upload"] == {
        "available": True,
        "formats": ["zip"],
        "max_upload_bytes": 100 * 1024 * 1024,
        "max_expanded_bytes": 512 * 1024 * 1024,
        "max_file_bytes": 32 * 1024 * 1024,
        "max_entries": 20_000,
    }
    assert await rows(owner_db, halden.id, "SELECT count(*) FROM document_grants") == grants_before
    [event] = await events(owner_db, halden.id)
    assert event.action == "source.created"
    assert event.actor.startswith("member:")
    assert event.reason == REASON
    assert event.after["managed_by"] == "organization"
    # The configuration holds no path, ever.
    [(config,)] = await rows(
        owner_db, halden.id, "SELECT config FROM sources WHERE key = 'policies'"
    )
    assert config == {"default_departments": ["it", "support"], "channels": {}}


@pytest.mark.parametrize(
    ("change", "status", "issue"),
    [
        ({"key": "Bad Key"}, 422, None),
        ({"connector_type": "github_issues"}, 422, None),
        ({"default_sensitivity": "restricted"}, 422, None),
        ({"default_departments": ["no-such-department"]}, 422, "unknown_department"),
        ({"reason": "   "}, 422, None),
        ({"path": "/srv/lattice/data"}, 422, None),
    ],
)
async def test_creating_validates_every_field(
    halden: Org, admin: httpx.AsyncClient, change: dict[str, Any], status: int, issue: str | None
) -> None:
    response = await admin.post(
        "/v1/admin/sources",
        json={**new_source("document_repository", "docs"), **change},
        headers=CSRF,
    )
    assert response.status_code == status, response.text
    if issue:
        assert _issue(response) == issue


async def test_a_key_is_unique_and_immutable(
    owner_db: Database, halden: Org, admin: httpx.AsyncClient
) -> None:
    await sync(owner_db, halden, "drive", FAKE)  # the operator's "drive"
    taken = await admin.post(
        "/v1/admin/sources", json=new_source("ticketing", "drive"), headers=CSRF
    )
    assert taken.status_code == 409
    assert _issue(taken) == "taken"
    source = await create(admin, "ticketing", "support-upload")
    renamed = await _patch(admin, source, key="other")
    assert renamed.status_code == 422  # not a field of the change


async def test_a_sources_file_never_takes_over_a_product_source(
    owner_db: Database, halden: Org, admin: httpx.AsyncClient
) -> None:
    await create(admin, "document_repository", "drive")
    with pytest.raises(SyncError, match="managed_in_product"):
        await sync(owner_db, halden, "drive", FAKE)
    with pytest.raises(SyncError, match="managed_in_product"):
        await sync(owner_db, halden, "drive", FAKE, dry_run=True)


# --- edit ----------------------------------------------------------------------------------


async def test_a_change_that_widens_readership_must_be_acknowledged(
    owner_db: Database, halden: Org, admin: httpx.AsyncClient
) -> None:
    source = await create(
        admin,
        "document_repository",
        "docs",
        default_sensitivity="confidential",
        default_departments=["support"],
    )
    preview = await admin.post(
        f"/v1/admin/sources/{source['id']}/changes/preview",
        json={
            "version": source["version"],
            "reason": REASON,
            "default_sensitivity": "internal",
            "default_departments": [],
            "name": "Docs",
        },
        headers=CSRF,
    )
    assert preview.status_code == 200, preview.text
    found = {c["field"]: c["effect"] for c in preview.json()["changes"]}
    assert found == {
        "name": "neutral",
        "default_sensitivity": "widens",
        "default_departments": "widens",
    }
    assert preview.json()["widens"] is True
    assert preview.json()["takes_effect"] == "next_sync"

    refused = await _patch(admin, source, default_sensitivity="internal")
    assert refused.status_code == 422
    assert _issue(refused) == "widening_not_acknowledged"
    narrowing = await _patch(admin, source, default_departments=["support", "it"])
    assert narrowing.status_code == 422  # adding a department lets more people read
    applied = await _patch(admin, source, default_sensitivity="internal", acknowledge_widening=True)
    assert applied.status_code == 200, applied.text
    assert applied.json()["version"] == source["version"] + 1
    assert applied.json()["settings"]["default_sensitivity"] == "internal"
    updated = [e for e in await events(owner_db, halden.id) if e.action == "source.updated"]
    assert len(updated) == 1
    assert updated[0].before["default_sensitivity"] == "confidential"
    assert updated[0].after["default_sensitivity"] == "internal"
    assert updated[0].after["widens"] is True


async def test_edits_are_versioned(halden: Org, admin: httpx.AsyncClient) -> None:
    source = await create(admin, "document_repository", "docs")
    assert (await _patch(admin, source, name="Docs one")).status_code == 200
    stale = await _patch(admin, source, name="Docs two")
    assert stale.status_code == 409
    assert _issue(stale) == "source_changed"
    current = await detail(admin, source["id"])
    same = await _patch(admin, current, name="Docs one")
    assert same.status_code == 422
    assert _issue(same) == "no_change"


async def test_channel_sensitivities_belong_to_chat_sources(
    halden: Org, admin: httpx.AsyncClient
) -> None:
    docs = await create(admin, "document_repository", "docs")
    refused = await _patch(admin, docs, channel_sensitivities={"C0GENERAL01": "confidential"})
    assert refused.status_code == 422
    assert _issue(refused) == "channels_invalid"
    chat = await create(admin, "chat_export", "chat")
    bad_id = await _patch(admin, chat, channel_sensitivities={"#general": "confidential"})
    assert bad_id.status_code == 422
    ok = await _patch(admin, chat, channel_sensitivities={"C0GENERAL01": "confidential"})
    assert ok.status_code == 200
    assert ok.json()["settings"]["channel_sensitivities"] == {"C0GENERAL01": "confidential"}
    lowered = await _patch(admin, ok.json(), channel_sensitivities={})
    assert _issue(lowered) == "widening_not_acknowledged"


async def test_an_operator_managed_source_is_read_only_here(
    owner_db: Database, halden: Org, admin: httpx.AsyncClient
) -> None:
    await sync(owner_db, halden, "drive", FAKE)
    [(source_id,)] = await rows(owner_db, halden.id, "SELECT id FROM sources WHERE key = 'drive'")
    record = await detail(admin, str(source_id))
    assert record["managed_by"] == "operator"
    assert record["current_import"] is None
    assert record["can_sync"] is True
    refused = await _patch(admin, record, name="Renamed")
    assert refused.status_code == 422
    assert _issue(refused) == "operator_managed"
    upload_refused = await upload(admin, str(source_id), b"PK\x05\x06" + bytes(18))
    assert _issue(upload_refused) == "operator_managed"


# --- pause and resume ------------------------------------------------------------------------


async def test_pausing_refuses_syncs_and_keeps_content_searchable(
    owner_db: Database,
    app_db: Database,
    worker_db: Database,
    halden: Org,
    admin: httpx.AsyncClient,
    app_client: AppFactory,
    store: FilesystemImportStore,
) -> None:
    await sync(owner_db, halden, "drive", FAKE)
    [(source_id,)] = await rows(owner_db, halden.id, "SELECT id FROM sources WHERE key = 'drive'")
    record = await detail(admin, str(source_id))
    # A sync queued before the pause is refused by the worker when it runs.
    assert (
        await admin.post(f"/v1/admin/sources/{source_id}/sync", headers=CSRF)
    ).status_code == 202
    paused = await _post(admin, record, "pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    assert paused.json()["next_action"] == "resume"
    outcome = await work(worker_db, store)
    assert (outcome.outcome, outcome.error_code) == ("failed", "source_config:source_paused")
    refused = await admin.post(f"/v1/admin/sources/{source_id}/sync", headers=CSRF)
    assert refused.status_code == 422
    # Members still find its content.
    async with app_client() as member:
        await login(member, "maya.okafor@halden.example")
        listed = (await member.get("/v1/sources")).json()["sources"]
        assert any(s["id"] == str(source_id) for s in listed)

    again = await _post(admin, paused.json(), "pause")
    assert again.status_code == 422
    resumed = await _post(admin, paused.json(), "resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"
    # Resuming starts nothing.
    queued = await rows(
        owner_db, halden.id, "SELECT count(*) FROM jobs WHERE status IN ('queued', 'running')"
    )
    assert queued == [(0,)]
    assert [e.action for e in await events(owner_db, halden.id, "source.%ed")][-2:] == [
        "source.paused",
        "source.resumed",
    ]


# --- disconnect --------------------------------------------------------------------------------


async def test_disconnecting_previews_counts_and_purges_only_this_source(
    owner_db: Database,
    worker_db: Database,
    halden: Org,
    admin: httpx.AsyncClient,
    store: FilesystemImportStore,
) -> None:
    await sync(owner_db, halden, "slack", FAKE)  # another source, with mappings
    source = await create(admin, "ticketing", "support-upload")
    first = (await upload(admin, source["id"], export(halden, "tickets"))).json()["import"]
    await work(worker_db, store)
    accounts = (await admin.get("/v1/admin/access/accounts", params={"q": "acme-corp"})).json()
    acme = next(a["id"] for a in accounts["items"] if a["key"] == "acme-corp")
    mapped = await admin.post(
        "/v1/admin/access/account-references",
        json={
            "source_id": source["id"],
            "ref_kind": "ticketing_organization",
            "external_ref": "360001001",
            "account_id": acme,
            "reason": "Customer record",
        },
        headers=CSRF,
    )
    assert mapped.status_code in (200, 201), mapped.text
    await sync_now(admin, worker_db, store, source["id"])
    record = await detail(admin, source["id"])
    assert record["documents"] > 0

    preview = (await admin.get(f"/v1/admin/sources/{source['id']}/disconnect-preview")).json()
    assert preview["documents"] == record["documents"]
    assert preview["account_mappings"] == 1
    assert preview["imports"] == 1
    assert preview["sync_running"] is False
    assert preview["operator_managed"] is False

    wrong = await _post(admin, record, "disconnect", confirm_key="support")
    assert _issue(wrong) == "confirmation_mismatch"
    done = await _post(admin, record, "disconnect", confirm_key="support-upload")
    assert done.status_code == 200, done.text
    assert done.json()["purged"] is True
    assert done.json()["account_mappings"] == 1
    assert (await admin.get(f"/v1/admin/sources/{source['id']}")).status_code == 404
    assert await rows(
        owner_db,
        halden.id,
        "SELECT count(*) FROM documents d JOIN sources s ON s.id = d.source_id "
        "WHERE s.key = 'support-upload'",
    ) == [(0,)]
    # The import is deleted (row kept, no source, files gone); accounts and the other
    # source's mappings stay.
    assert await rows(
        owner_db,
        halden.id,
        "SELECT status, source_id FROM source_imports WHERE id = :i",
        i=uuid.UUID(first["id"]),
    ) == [("deleted", None)]
    assert not (store.root / str(halden.id) / first["id"]).exists()
    assert (
        await rows(owner_db, halden.id, "SELECT count(*) FROM accounts WHERE key = 'acme-corp'")
    ) == [(1,)]
    slack_maps = await rows(
        owner_db,
        halden.id,
        "SELECT count(*) FROM account_external_refs r JOIN sources s "
        "ON s.id = r.source_id WHERE s.key = 'slack'",
    )
    assert slack_maps == [(5,)]
    [event] = [e for e in await events(owner_db, halden.id) if e.action == "source.disconnected"]
    assert event.after["documents"] == record["documents"]
    assert event.before["key"] == "support-upload"


async def test_disconnect_is_refused_while_a_sync_runs(
    owner_db: Database, halden: Org, admin: httpx.AsyncClient
) -> None:
    await sync(owner_db, halden, "drive", FAKE)
    [(source_id,)] = await rows(owner_db, halden.id, "SELECT id FROM sources WHERE key = 'drive'")
    source = await detail(admin, str(source_id))
    async with owner_db.tenant_transaction(halden.id) as s:
        await s.execute(
            text(
                "INSERT INTO jobs (organization_id, kind, source_id, requested_by, status, "
                "attempts, started_at, lease_expires_at) SELECT :o, 'source_sync', :s, m.id, "
                "'running', 1, now(), now() + interval '5 minutes' FROM memberships m LIMIT 1"
            ),
            {"o": halden.id, "s": uuid.UUID(source["id"])},
        )
    refused = await _post(admin, source, "disconnect", confirm_key="drive")
    assert refused.status_code == 409
    assert _issue(refused) == "sync_running"
    assert (await detail(admin, source["id"]))["status"] == "active"


# --- authorization ----------------------------------------------------------------------------


async def test_members_are_refused_before_anything_is_read(
    halden: Org, admin: httpx.AsyncClient, app_client: AppFactory
) -> None:
    source = await create(admin, "document_repository", "docs")
    async with app_client() as member:
        await login(member, "maya.okafor@halden.example")
        for target in (source["id"], str(uuid.uuid4())):
            for method, path, body in (
                ("GET", f"/v1/admin/sources/{target}", None),
                (
                    "PATCH",
                    f"/v1/admin/sources/{target}",
                    {"version": 1, "reason": "x", "name": "y"},
                ),
                ("POST", f"/v1/admin/sources/{target}/pause", {"version": 1, "reason": "x"}),
                ("POST", f"/v1/admin/sources/{target}/imports", None),
                ("POST", f"/v1/admin/sources/{target}/check", None),
                ("GET", f"/v1/admin/sources/{target}/disconnect-preview", None),
            ):
                response = await member.request(method, path, json=body, headers=CSRF)
                assert response.status_code == 403, (method, path)
        created = await member.post(
            "/v1/admin/sources", json=new_source("ticketing", "x"), headers=CSRF
        )
        assert created.status_code == 403


async def test_another_organizations_source_answers_as_a_random_id(
    owner_db: Database,
    halden: Org,
    admin: httpx.AsyncClient,
    app_client: AppFactory,
    imports_root: Any,
) -> None:
    source = await create(admin, "document_repository", "docs")
    email = await veridian_admin(owner_db)
    async with app_client(imports_root=imports_root) as other:
        await login(other, email)
        for target in (source["id"], str(uuid.uuid4())):
            answers = [
                await other.get(f"/v1/admin/sources/{target}"),
                await other.patch(
                    f"/v1/admin/sources/{target}",
                    json={"version": 1, "reason": "x", "name": "y"},
                    headers=CSRF,
                ),
                await other.post(
                    f"/v1/admin/sources/{target}/pause",
                    json={"version": 1, "reason": "x"},
                    headers=CSRF,
                ),
                await other.post(
                    f"/v1/admin/sources/{target}/imports",
                    content=b"PK\x05\x06" + bytes(18),
                    headers=CSRF,
                ),
                await other.post(f"/v1/admin/sources/{target}/sync", headers=CSRF),
                await other.post(f"/v1/admin/sources/{target}/check", headers=CSRF),
                await other.get(f"/v1/admin/sources/{target}/disconnect-preview"),
                await other.post(
                    f"/v1/admin/sources/{target}/disconnect",
                    json={"version": 1, "reason": "x", "confirm_key": "docs"},
                    headers=CSRF,
                ),
            ]
            assert [a.status_code for a in answers] == [404] * len(answers), [
                (a.status_code, a.text) for a in answers
            ]
    # Nothing changed for Halden.
    assert (await detail(admin, source["id"]))["version"] == source["version"]
    assert await rows(owner_db, halden.id, "SELECT count(*) FROM source_imports") == [(0,)]
