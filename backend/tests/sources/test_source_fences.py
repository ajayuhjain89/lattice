"""The database fences of P12.6 (ADR-017, migration 0022), statement by statement as the
runtime roles: no direct write to sources or imports, fenced functions only for an active
administrator of the bound organization, audit coupling at commit, and a worker that can
only record a check's counts and expire imports."""

import json
import os
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice.db.engine import Database
from lattice.imports.store import FilesystemImportStore
from lattice.jobs.worker import Worker

from ..conftest import CSRF, AppFactory
from ..factories import login
from ..imports.archives import files
from ..ingestion.conftest import Org
from .conftest import HALDEN_ADMIN, create, detail, rows, upload

DOC = (
    b"---\nid: d1\ntitle: T\nauthor: A\n"
    b"created: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n---\nBody\n"
)


async def _membership(db: Database, org: Org, email: str) -> uuid.UUID:
    [(found,)] = await rows(
        db,
        org.id,
        "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id WHERE u.email = :e",
        e=email,
    )
    assert isinstance(found, uuid.UUID)
    return found


async def _refused(db: Database, org: uuid.UUID, sql: str, match: str, **params: object) -> None:
    with pytest.raises(DBAPIError, match=match):
        async with db.tenant_transaction(org) as s:
            await s.execute(text(sql), params)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO sources (organization_id, key, connector_type, name, default_sensitivity, "
        "authority) VALUES (:o, 'x', 'ticketing', 'X', 'internal', 'working')",
        'UPDATE sources SET config = \'{"path": "/etc"}\'::jsonb',
        "UPDATE sources SET status = 'paused'",
        "UPDATE sources SET import_id = NULL",
        "DELETE FROM sources",
        "INSERT INTO source_imports (organization_id, source_id, format, created_by) "
        "SELECT :o, id, 'zip', 'member:00000000-0000-0000-0000-000000000000' FROM sources",
        "UPDATE source_imports SET status = 'ready'",
        "DELETE FROM source_imports",
    ],
)
async def test_the_api_role_cannot_write_sources_or_imports_directly(
    app_db: Database, halden: Org, admin: httpx.AsyncClient, sql: str
) -> None:
    source = await create(admin, "document_repository", "docs")
    await upload(admin, source["id"], files({"a.md": DOC}))
    await _refused(app_db, halden.id, sql, "permission denied", o=halden.id)


async def test_fenced_functions_need_an_active_administrator_of_this_organization(
    owner_db: Database, app_db: Database, halden: Org, admin: httpx.AsyncClient
) -> None:
    source = await create(admin, "document_repository", "docs")
    member = await _membership(owner_db, halden, "maya.okafor@halden.example")
    [(veridian,)] = await rows(
        owner_db, halden.id, "SELECT id FROM organizations WHERE slug = 'veridian'"
    )
    foreign = await _membership(
        owner_db,
        Org("veridian", veridian, halden.root, halden.sources),
        "ines.carvalho@veridian.example",
    )
    for actor in (member, foreign, uuid.uuid4()):
        for sql in (
            "SELECT lattice_create_source(:a, 'x', 'ticketing', 'X', 'internal', '{}', "
            "'working', true)",
            'SELECT lattice_update_source(:a, :s, 1, \'{"name": "Y"}\'::jsonb)',
            "SELECT lattice_set_source_paused(:a, :s, 1, true)",
            "SELECT lattice_disconnect_source(:a, :s, 1)",
            "SELECT lattice_purge_source(:a, :s)",
            "SELECT lattice_begin_import(:a, :s, 'zip')",
        ):
            await _refused(
                app_db, halden.id, sql, "not_administrator", a=actor, s=uuid.UUID(source["id"])
            )


async def test_every_source_change_needs_its_audit_event_at_commit(
    owner_db: Database, app_db: Database, halden: Org, admin: httpx.AsyncClient
) -> None:
    lena = await _membership(owner_db, halden, HALDEN_ADMIN)
    source = await create(admin, "document_repository", "docs")
    sid = uuid.UUID(source["id"])
    for sql in (
        "SELECT lattice_create_source(:a, 'x', 'ticketing', 'X', 'internal', '{}', "
        "'working', true)",
        'SELECT lattice_update_source(:a, :s, 1, \'{"name": "Renamed"}\'::jsonb)',
        "SELECT lattice_set_source_paused(:a, :s, 1, true)",
        "SELECT lattice_disconnect_source(:a, :s, 1)",
        "INSERT INTO jobs (organization_id, kind, source_id, requested_by, import_id) "
        "VALUES (:o, 'source_sync', :s, :a, NULL)",
    ):
        with pytest.raises(DBAPIError, match=r"without an audit event|job_import_mismatch"):
            async with app_db.tenant_transaction(halden.id) as s:
                await s.execute(text(sql), {"a": lena, "s": sid, "o": halden.id})
    assert (await detail(admin, source["id"]))["version"] == 1


async def test_an_import_rejection_needs_its_audit_event(
    owner_db: Database, app_db: Database, halden: Org, admin: httpx.AsyncClient
) -> None:
    lena = await _membership(owner_db, halden, HALDEN_ADMIN)
    source = await create(admin, "document_repository", "docs")
    async with app_db.tenant_transaction(halden.id) as s:
        import_id: uuid.UUID = (
            await s.execute(
                text("SELECT lattice_begin_import(:a, :s, 'zip')"),
                {"a": lena, "s": uuid.UUID(source["id"])},
            )
        ).scalar_one()
    with pytest.raises(DBAPIError, match="without an audit event"):
        async with app_db.tenant_transaction(halden.id) as s:
            await s.execute(
                text("SELECT lattice_reject_import(:a, :i, 'unsafe_path', NULL, NULL)"),
                {"a": lena, "i": import_id},
            )
    # An import that has not been validated can never become current.
    with pytest.raises(DBAPIError, match="import_state"):
        async with app_db.tenant_transaction(halden.id) as s:
            await s.execute(
                text("SELECT lattice_finish_import(:a, :i, 1, 0, 10)"), {"a": lena, "i": import_id}
            )


async def test_the_worker_only_records_checks_and_expires_imports(
    owner_db: Database, worker_db: Database, app_db: Database, halden: Org, admin: httpx.AsyncClient
) -> None:
    lena = await _membership(owner_db, halden, HALDEN_ADMIN)
    source = await create(admin, "document_repository", "docs")
    import_id = uuid.UUID(
        (await upload(admin, source["id"], files({"a.md": DOC}))).json()["import"]["id"]
    )
    for sql in (
        "SELECT lattice_create_source(:a, 'x', 'ticketing', 'X', 'internal', '{}', "
        "'working', true)",
        "SELECT lattice_begin_import(:a, :s, 'zip')",
        "UPDATE source_imports SET check_result = '{}'::jsonb",
        "UPDATE sources SET import_id = NULL",
    ):
        await _refused(
            worker_db, halden.id, sql, "permission denied", a=lena, s=uuid.UUID(source["id"])
        )
    # A check carries counts and codes only: anything else is refused.
    for payload in (
        {"records_seen": 1, "title": "Board minutes"},
        {"failure_codes": {"Board minutes": 1}},
        {"unresolved_references": [{"ref": "360001001", "records": 1, "sample": "text"}]},
        {"records_seen": -1},
    ):
        await _refused(
            worker_db,
            halden.id,
            "SELECT lattice_record_import_check(:i, CAST(:r AS jsonb), NULL)",
            "check_invalid",
            i=import_id,
            r=json.dumps(payload),
        )
    # The API role cannot use the worker's functions.
    await _refused(
        app_db,
        halden.id,
        "SELECT lattice_record_import_check(:i, '{}'::jsonb, NULL)",
        "permission denied",
        i=import_id,
    )
    await _refused(
        app_db, halden.id, "SELECT * FROM lattice_expire_imports(10)", "permission denied"
    )
    # Another organization's import is invisible to a check recorded for it.
    [(veridian,)] = await rows(
        owner_db, halden.id, "SELECT id FROM organizations WHERE slug = 'veridian'"
    )
    await _refused(
        worker_db,
        veridian,
        "SELECT lattice_record_import_check(:i, '{\"records_seen\": 1}'::jsonb, NULL)",
        "import_state",
        i=import_id,
    )


async def test_import_retention_spares_what_a_job_reads_and_sweeps_orphans(
    owner_db: Database,
    worker_db: Database,
    halden: Org,
    admin: httpx.AsyncClient,
    store: FilesystemImportStore,
    imports_root: Path,
) -> None:
    source = await create(admin, "document_repository", "docs")
    first = (await upload(admin, source["id"], files({"a.md": DOC}))).json()["import"]["id"]
    # A sync is requested on the first import, then a newer import supersedes it.
    async with owner_db.tenant_transaction(halden.id) as s:
        await s.execute(text("DELETE FROM jobs"))
    assert (
        await admin.post(f"/v1/admin/sources/{source['id']}/sync", headers=CSRF)
    ).status_code == 202
    second = (await upload(admin, source["id"], files({"b.md": DOC}))).json()["import"]["id"]
    worker = Worker(worker_db, embedding=None, store=store)
    assert await worker.import_retention() == 0  # the queued sync still reads the first
    assert store.tree(halden.id, uuid.UUID(first)).is_dir()

    assert (await worker.run_once()) is not None  # the sync of the first import
    # An orphan directory (an API that died between registering and receiving) and a fresh one.
    old = imports_root / str(halden.id) / str(uuid.uuid4())
    young = imports_root / str(halden.id) / str(uuid.uuid4())
    old.mkdir()
    young.mkdir()
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    assert await worker.import_retention() == 2  # the superseded first import and the orphan
    assert not (imports_root / str(halden.id) / first).exists()
    assert not old.exists()
    assert young.exists()
    assert store.tree(halden.id, uuid.UUID(second)).is_dir()
    assert dict(await rows(owner_db, halden.id, "SELECT id::text, status FROM source_imports")) == {
        first: "deleted",
        second: "ready",
    }


async def _setup_sources(client: httpx.AsyncClient) -> dict[str, object]:
    categories = (await client.get("/v1/admin/setup")).json()["categories"]
    found: dict[str, object] = next(c for c in categories if c["key"] == "sources")
    return found


async def test_the_setup_record_follows_sources_and_imports(
    halden: Org, admin: httpx.AsyncClient, worker_db: Database, store: FilesystemImportStore
) -> None:
    first = await _setup_sources(admin)
    assert (first["state"], first["reason"]) == ("required", "no_sources")
    assert first["action"] == {"kind": "link", "href": "/sources", "label": "Open Sources"}
    source = await create(admin, "document_repository", "docs")
    assert ((s := await _setup_sources(admin))["state"], s["reason"]) == (
        "required",
        "awaiting_import",
    )
    await upload(admin, source["id"], files({"../x.md": DOC}))
    assert ((s := await _setup_sources(admin))["state"], s["reason"]) == (
        "attention",
        "import_rejected",
    )
    await upload(admin, source["id"], files({"a.md": DOC}))
    # A check is queued, which is not a sync.
    assert ((s := await _setup_sources(admin))["state"], s["reason"]) == ("required", "not_synced")
    await Worker(worker_db, embedding=None, store=store).run_once()
    assert (
        await admin.post(f"/v1/admin/sources/{source['id']}/sync", headers=CSRF)
    ).status_code == 202
    assert ((s := await _setup_sources(admin))["state"], s["reason"]) == ("in_progress", "syncing")
    await Worker(worker_db, embedding=None, store=store).run_once()
    assert ((s := await _setup_sources(admin))["state"], s["reason"]) == ("complete", "synced")


async def test_the_cli_records_source_events_as_the_operator(
    owner_db: Database, halden: Org, app_client: AppFactory
) -> None:
    from lattice.ingestion.lifecycle import disconnect_source

    from ..ingestion.conftest import sync

    await sync(owner_db, halden, "drive")
    await sync(owner_db, halden, "drive")  # unchanged: no second event
    await disconnect_source(owner_db, halden.id, "drive")
    found = await rows(
        owner_db,
        halden.id,
        "SELECT actor, action FROM audit_events WHERE action LIKE 'source.%' ORDER BY created_at",
    )
    assert found == [("operator:sync", "source.created"), ("operator:cli", "source.disconnected")]
    async with app_client() as c:
        await login(c, HALDEN_ADMIN)
        page = (await c.get("/v1/admin/audit-events")).json()["items"]
        created = next(e for e in page if e["action"] == "source.created")
        assert created["actor"]["kind"] == "operator"
        # The source is named at read time (gone once purged), never its path.
        assert {d["field"] for d in created["details"]} >= {"source", "name"}
        assert "/srv" not in json.dumps(page)
        assert str(halden.root) not in json.dumps(page)
