"""The streamed upload route (P12.6; phase-12-plan C8.1-C8.4): its own body limit (every
other route keeps 64 KiB), streaming to disk, refusals by fixed code with nothing left
behind, interrupted and mismatched bodies, storage limits, one upload per source,
authorization at every step, and an import that can never cross sources or
organizations or be read once rejected or deleted."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice.api.app import create_app
from lattice.db.engine import Database
from lattice.imports.limits import MIB
from lattice.imports.store import FilesystemImportStore
from lattice.jobs.worker import Worker

from ..conftest import CSRF, AppFactory, make_settings
from ..factories import login
from ..imports.archives import files
from ..ingestion.conftest import Org
from .conftest import HALDEN_ADMIN, create, detail, events, export, rows, upload, work

DOC = (
    b"---\nid: d1\ntitle: T\nauthor: A\n"
    b"created: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n---\nBody\n"
)
VALID = files({"a.md": DOC})


def _issue(response: httpx.Response) -> str:
    issue: str = response.json()["error"]["details"][0]["issue"]
    return issue


async def _imports(db: Database, org: Org) -> list[tuple[str, str | None]]:
    return [
        (str(r[0]), r[1])
        for r in await rows(
            db, org.id, "SELECT status, rejected_code FROM source_imports ORDER BY created_at"
        )
    ]


def _left_on_disk(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# --- accepted --------------------------------------------------------------------------------


async def test_a_valid_upload_becomes_the_current_import_and_is_checked(
    owner_db: Database, halden: Org, admin: httpx.AsyncClient, imports_root: Path
) -> None:
    source = await create(admin, "document_repository", "docs")
    response = await upload(admin, source["id"], VALID, **{"Content-Type": "text/plain"})
    assert response.status_code == 201, response.text  # the content decides, not the header
    body = response.json()
    assert body["import"]["format"] == "zip"
    assert body["import"]["sha256"]
    assert len(body["import"]["sha256"]) == 64
    assert body["check_queued"] is True
    assert (await detail(admin, source["id"]))["job"]["kind"] == "import_check"
    # Stored under generated IDs only; no client name anywhere.
    [import_id] = [
        str(r[0]) for r in await rows(owner_db, halden.id, "SELECT id FROM source_imports")
    ]
    assert _left_on_disk(imports_root) == [f"{halden.id}/{import_id}/tree/a.md"]
    [accepted] = await events(owner_db, halden.id, "source.import_%")
    assert accepted.action == "source.import_accepted"
    assert accepted.target == {"source_id": source["id"], "import_id": import_id}
    assert set(accepted.after) == {
        "format",
        "size_bytes",
        "sha256",
        "entries",
        "ignored_entries",
        "expanded_bytes",
    }


async def test_a_tickets_source_accepts_one_json_lines_page(
    halden: Org, admin: httpx.AsyncClient
) -> None:
    source = await create(admin, "ticketing", "support-upload")
    page = (halden.root / "tickets" / "tickets.jsonl").read_bytes()
    response = await upload(admin, source["id"], page, **{"Content-Type": "application/x-ndjson"})
    assert response.status_code == 201, response.text
    assert response.json()["import"]["format"] == "jsonl"
    docs = await create(admin, "document_repository", "docs")
    refused = await upload(admin, docs["id"], page)
    assert (refused.status_code, _issue(refused)) == (422, "archive_invalid")


async def test_a_new_upload_supersedes_and_a_refused_one_changes_nothing(
    owner_db: Database, halden: Org, admin: httpx.AsyncClient, imports_root: Path
) -> None:
    source = await create(admin, "document_repository", "docs")
    first = (await upload(admin, source["id"], VALID)).json()["import"]
    hostile = await upload(admin, source["id"], files({"../../escape.md": DOC}))
    assert (hostile.status_code, _issue(hostile)) == (422, "unsafe_path")
    record = await detail(admin, source["id"])
    assert record["current_import"]["id"] == first["id"]
    assert record["latest_upload"]["status"] == "deleted"
    assert record["latest_upload"]["rejected_code"] == "unsafe_path"
    assert _left_on_disk(imports_root) == [f"{halden.id}/{first['id']}/tree/a.md"]
    assert not list(imports_root.parent.rglob("escape.md"))
    rejected = [
        e for e in await events(owner_db, halden.id) if e.action == "source.import_rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0].after["code"] == "unsafe_path"

    second = (await upload(admin, source["id"], files({"b.md": DOC}))).json()["import"]
    assert dict(await rows(owner_db, halden.id, "SELECT id::text, status FROM source_imports")) == {
        first["id"]: "superseded",
        second["id"]: "ready",
        record["latest_upload"]["id"]: "deleted",
    }


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "archive_invalid"),
        (b"not an archive at all", "archive_invalid"),
        (files({"a.md": DOC, "A.md": DOC}), "duplicate_name"),
        (files({"readme.txt": b"no markdown"}), "unsupported_layout"),
        (b"PK\x03\x04" + b"\x00" * 64, "archive_invalid"),
    ],
)
async def test_refused_uploads_leave_nothing_and_say_why(
    halden: Org, admin: httpx.AsyncClient, imports_root: Path, data: bytes, code: str
) -> None:
    source = await create(admin, "document_repository", "docs")
    response = await upload(admin, source["id"], data)
    assert response.status_code == 422
    assert _issue(response) == code
    assert "a.md" not in response.text
    assert "readme" not in response.text
    assert _left_on_disk(imports_root) == []
    assert (await detail(admin, source["id"]))["current_import"] is None


# --- size, streaming and interruption ---------------------------------------------------------


async def test_ordinary_routes_keep_the_64_kib_cap(halden: Org, admin: httpx.AsyncClient) -> None:
    source = await create(admin, "document_repository", "docs")
    big = {"version": source["version"], "reason": "x" * 70_000, "name": "n"}
    response = await admin.patch(f"/v1/admin/sources/{source['id']}", json=big, headers=CSRF)
    assert response.status_code == 413
    # The upload route's own limit is not reachable under another method or path.
    response = await admin.put(
        f"/v1/admin/sources/{source['id']}/imports", content=b"x" * 70_000, headers=CSRF
    )
    assert response.status_code == 413


async def test_an_oversized_upload_is_refused_by_its_declared_and_its_streamed_size(
    halden: Org, app_client: AppFactory, imports_root: Path
) -> None:
    async with app_client(imports_root=imports_root, import_max_upload_bytes=1 * MIB) as c:
        await login(c, HALDEN_ADMIN)
        source = await create(c, "document_repository", "docs")
        declared = await upload(c, source["id"], b"PK\x03\x04" + b"\x00" * (1 * MIB))
        assert declared.status_code == 413

        async def stream() -> AsyncIterator[bytes]:
            yield b"PK\x03\x04"
            for _ in range(5):
                yield b"\x00" * (300 * 1024)

        streamed = await c.post(
            f"/v1/admin/sources/{source['id']}/imports",
            content=stream(),
            headers={**CSRF, "Content-Type": "application/zip"},
        )
        assert streamed.status_code == 413
        assert (await detail(c, source["id"]))["current_import"] is None
    assert _left_on_disk(imports_root) == []


@asynccontextmanager
async def _raw_app(imports_root: Path, **overrides: Any) -> AsyncIterator[tuple[FastAPI, str]]:
    """The app and an administrator's cookie header, for hand-made ASGI requests."""
    app = create_app(make_settings(imports_root=imports_root, **overrides))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            assert (await login(c, HALDEN_ADMIN)).status_code == 200
            cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
            source = await create(c, "document_repository", "docs")
            app.state.test_source = source["id"]
            yield app, cookie


async def _send(
    app: FastAPI,
    cookie: str,
    path: str,
    chunks: list[bytes],
    *,
    length: str | None,
    disconnect: bool,
) -> tuple[int | None, bytes]:
    headers = [(b"cookie", cookie.encode()), (b"x-lattice-request", b"1"), (b"host", b"testserver")]
    if length is not None:
        headers.append((b"content-length", length.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 5000),
        "server": ("testserver", 80),
    }
    pending = [
        {"type": "http.request", "body": c, "more_body": disconnect or i < len(chunks) - 1}
        for i, c in enumerate(chunks)
    ]
    if disconnect:
        pending.append({"type": "http.disconnect"})
    status: int | None = None
    body = b""

    async def receive() -> dict[str, Any]:
        if pending:
            return pending.pop(0)
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        nonlocal status, body
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            body += message.get("body", b"")

    await asyncio.wait_for(app(scope, receive, send), timeout=30)
    return status, body


async def test_an_interrupted_upload_is_refused_and_cleaned_up(
    owner_db: Database, halden: Org, imports_root: Path
) -> None:
    async with _raw_app(imports_root) as (app, cookie):
        path = f"/v1/admin/sources/{app.state.test_source}/imports"
        status, body = await _send(
            app, cookie, path, [VALID[:40], VALID[40:80]], length=str(len(VALID)), disconnect=True
        )
    assert status in (422, None), body  # the client is gone; the refusal is recorded
    assert await _imports(owner_db, halden) == [("deleted", "upload_incomplete")]
    assert _left_on_disk(imports_root) == []
    [rejected] = await events(owner_db, halden.id, "source.import_rejected")
    assert rejected.after["code"] == "upload_incomplete"


@pytest.mark.parametrize(
    ("length", "sent"),
    [
        (str(len(VALID) + 500), VALID),  # the body ended before its declared length
        (str(len(VALID) - 10), VALID),  # more arrived than it claimed
    ],
)
async def test_a_body_that_differs_from_its_declared_length_is_refused(
    owner_db: Database, halden: Org, imports_root: Path, length: str, sent: bytes
) -> None:
    async with _raw_app(imports_root) as (app, cookie):
        path = f"/v1/admin/sources/{app.state.test_source}/imports"
        status, body = await _send(app, cookie, path, [sent], length=length, disconnect=False)
    assert status == 422, body
    assert json.loads(body)["error"]["details"][0]["issue"] == "upload_incomplete"
    assert _left_on_disk(imports_root) == []


async def test_an_invalid_content_length_is_refused_before_reading(
    owner_db: Database, halden: Org, imports_root: Path
) -> None:
    async with _raw_app(imports_root) as (app, cookie):
        path = f"/v1/admin/sources/{app.state.test_source}/imports"
        status, _ = await _send(app, cookie, path, [VALID], length="12abc", disconnect=False)
    assert status == 413
    assert await _imports(owner_db, halden) == []


# --- storage ----------------------------------------------------------------------------------


async def test_without_an_import_area_uploads_are_unavailable(
    halden: Org, app_client: AppFactory
) -> None:
    async with app_client() as c:
        await login(c, HALDEN_ADMIN)
        source = await create(c, "document_repository", "docs")
        assert source["upload"]["available"] is False
        response = await upload(c, source["id"], VALID)
        assert (response.status_code, _issue(response)) == (503, "storage_unavailable")


async def test_a_full_import_area_refuses_before_receiving(
    owner_db: Database,
    halden: Org,
    admin: httpx.AsyncClient,
    imports_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = await create(admin, "document_repository", "docs")
    monkeypatch.setattr(FilesystemImportStore, "free_bytes", lambda self: 10 * MIB)
    response = await upload(admin, source["id"], VALID)
    assert (response.status_code, _issue(response)) == (503, "storage_full")
    assert await _imports(owner_db, halden) == []


async def test_the_organization_quota_bounds_retained_imports(
    owner_db: Database, halden: Org, app_client: AppFactory, imports_root: Path
) -> None:
    limits = {
        "import_max_upload_bytes": 1 * MIB,
        "import_max_expanded_bytes": 1 * MIB,
        "import_max_file_bytes": 1 * MIB,
        "import_organization_quota_bytes": 2 * MIB + 100,
    }
    async with app_client(imports_root=imports_root, **limits) as c:
        await login(c, HALDEN_ADMIN)
        source = await create(c, "document_repository", "docs")
        assert (await upload(c, source["id"], VALID)).status_code == 201
        refused = await upload(c, source["id"], files({"b.md": DOC * 1000}))
        assert (refused.status_code, _issue(refused)) == (503, "storage_full")


# --- one at a time, authorization ---------------------------------------------------------------


async def test_one_upload_per_source_at_a_time_and_abandoned_ones_are_reclaimed(
    owner_db: Database, app_db: Database, halden: Org, admin: httpx.AsyncClient
) -> None:
    source = await create(admin, "document_repository", "docs")
    [(lena,)] = await rows(
        owner_db,
        halden.id,
        "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id WHERE u.email = :e",
        e=HALDEN_ADMIN,
    )
    async with app_db.tenant_transaction(halden.id) as s:
        await s.execute(
            text("SELECT lattice_begin_import(:a, :s, 'zip')"),
            {"a": lena, "s": uuid.UUID(source["id"])},
        )
    busy = await upload(admin, source["id"], VALID)
    assert (busy.status_code, _issue(busy)) == (409, "upload_in_progress")
    async with owner_db.tenant_transaction(halden.id) as s:
        await s.execute(text("UPDATE source_imports SET created_at = now() - interval '2 hours'"))
    assert (await upload(admin, source["id"], VALID)).status_code == 201
    assert ("rejected", "upload_abandoned") in await _imports(owner_db, halden)


async def test_uploads_need_the_csrf_header_and_a_slot(
    halden: Org, admin: httpx.AsyncClient, app_client: AppFactory, imports_root: Path
) -> None:
    source = await create(admin, "document_repository", "docs")
    no_csrf = await admin.post(f"/v1/admin/sources/{source['id']}/imports", content=VALID)
    assert no_csrf.status_code == 403
    cross_site = await upload(admin, source["id"], VALID, **{"Sec-Fetch-Site": "cross-site"})
    assert cross_site.status_code == 403

    def busy(app: FastAPI) -> None:
        app.state.import_uploads = asyncio.Semaphore(0)

    async with app_client(busy, imports_root=imports_root) as c:
        await login(c, HALDEN_ADMIN)
        response = await upload(c, source["id"], VALID)
        assert (response.status_code, _issue(response)) == (429, "uploads_busy")


# --- an import never crosses its source, its organization or its lifecycle -----------------------


async def test_an_import_attaches_only_to_its_own_source(
    owner_db: Database, app_db: Database, halden: Org, admin: httpx.AsyncClient
) -> None:
    one = await create(admin, "document_repository", "one")
    two = await create(admin, "document_repository", "two")
    imported = (await upload(admin, one["id"], VALID)).json()["import"]
    async with owner_db.tenant_transaction(halden.id) as s:
        with pytest.raises(DBAPIError, match="import_not_attachable"):
            async with s.begin_nested():
                await s.execute(
                    text("UPDATE sources SET import_id = :i WHERE id = :s"),
                    {"i": uuid.UUID(imported["id"]), "s": uuid.UUID(two["id"])},
                )
        with pytest.raises(DBAPIError, match="job_import_mismatch"):
            async with s.begin_nested():
                await s.execute(
                    text(
                        "INSERT INTO jobs (organization_id, kind, source_id, requested_by, "
                        "import_id) "
                        "SELECT :o, 'source_sync', :s, m.id, :i FROM memberships m LIMIT 1"
                    ),
                    {"o": halden.id, "s": uuid.UUID(two["id"]), "i": uuid.UUID(imported["id"])},
                )


async def test_a_deleted_import_is_never_consumed(
    owner_db: Database,
    worker_db: Database,
    halden: Org,
    admin: httpx.AsyncClient,
    store: FilesystemImportStore,
) -> None:
    source = await create(admin, "document_repository", "docs")
    await upload(admin, source["id"], export(halden, "documents"))
    await work(worker_db, store)  # the check
    assert (
        await admin.post(f"/v1/admin/sources/{source['id']}/sync", headers=CSRF)
    ).status_code == 202
    # The import's files disappear before the job runs (an operator, a disk failure).
    store.delete(halden.id, uuid.UUID((await detail(admin, source["id"]))["current_import"]["id"]))
    outcome = await Worker(worker_db, embedding=None, store=store).run_once()
    assert outcome is not None
    assert (outcome.outcome, outcome.error_code) == ("failed", "import_unavailable")
    assert (await detail(admin, source["id"]))["documents"] == 0
    # A worker without the import area cannot read any import either.
    assert (
        await admin.post(f"/v1/admin/sources/{source['id']}/sync", headers=CSRF)
    ).status_code == 202
    blind = await Worker(worker_db, embedding=None, store=None).run_once()
    assert blind is not None
    assert blind.error_code == "storage_unavailable"
