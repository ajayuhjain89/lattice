"""Customer-operable sources (P12.6): Halden and Veridian seeded from the synthetic
manifests (with writable copies of their exports), an import area in ``tmp_path``, and
an administrator's client for each organization."""

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.imports.store import FilesystemImportStore
from lattice.jobs.worker import JobOutcome, Worker
from lattice.providers.embedding import FakeEmbeddingProvider

from ..conftest import CSRF, AppFactory
from ..factories import login
from ..imports.archives import zip_tree
from ..ingestion.conftest import Org, copy_org, seed

FAKE = FakeEmbeddingProvider()
HALDEN_ADMIN = "lena.brandt@halden.example"
REASON = "Connecting the customer's exports"


@pytest.fixture
async def halden(owner_db: Database, tmp_path: Path) -> Org:
    """Halden seeded, with a writable copy of its exports; Veridian seeded too."""
    await seed(owner_db, "veridian")
    return await seed(owner_db, "halden", copy_org(tmp_path, "halden"))


@pytest.fixture
def imports_root(tmp_path: Path) -> Path:
    root = tmp_path / "imports"
    root.mkdir()
    return root


@pytest.fixture
def store(imports_root: Path) -> FilesystemImportStore:
    return FilesystemImportStore(root=imports_root)


@pytest.fixture
async def admin(
    app_client: AppFactory, halden: Org, imports_root: Path
) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(imports_root=imports_root) as c:
        assert (await login(c, HALDEN_ADMIN)).status_code == 200
        yield c


async def veridian_admin(owner_db: Database) -> str:
    """Make a Veridian member an administrator; their e-mail."""
    email = "ines.carvalho@veridian.example"
    async with owner_db.unscoped_transaction() as s:
        org: uuid.UUID = (
            await s.execute(text("SELECT id FROM organizations WHERE slug = 'veridian'"))
        ).scalar_one()
    async with owner_db.tenant_transaction(org) as s:
        await s.execute(
            text(
                "UPDATE memberships SET is_admin = true WHERE user_id = "
                "(SELECT id FROM users WHERE email = :e)"
            ),
            {"e": email},
        )
    return email


def new_source(kind: str, key: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "connector_type": kind,
        "key": key,
        "name": f"{key.title()} export",
        "default_sensitivity": "internal",
        "authority": {"document_repository": "working", "ticketing": "working"}.get(
            kind, "conversational"
        ),
        "account_scoped": kind == "ticketing",
        "reason": REASON,
    }
    body.update(extra)
    return body


async def create(client: httpx.AsyncClient, kind: str, key: str, **extra: Any) -> dict[str, Any]:
    response = await client.post(
        "/v1/admin/sources", json=new_source(kind, key, **extra), headers=CSRF
    )
    assert response.status_code == 201, response.text
    found: dict[str, Any] = response.json()
    return found


async def upload(
    client: httpx.AsyncClient, source_id: str, data: bytes, **headers: str
) -> httpx.Response:
    return await client.post(
        f"/v1/admin/sources/{source_id}/imports",
        content=data,
        headers={**CSRF, "Content-Type": "application/zip", **headers},
    )


async def detail(client: httpx.AsyncClient, source_id: str) -> dict[str, Any]:
    response = await client.get(f"/v1/admin/sources/{source_id}")
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


async def work(
    worker_db: Database, store: FilesystemImportStore, *, embedding: Any = FAKE
) -> JobOutcome:
    outcome = await Worker(worker_db, embedding=embedding, store=store).run_once()
    assert outcome is not None, "no job was due"
    return outcome


async def sync_now(
    client: httpx.AsyncClient, worker_db: Database, store: FilesystemImportStore, source_id: str
) -> JobOutcome:
    response = await client.post(f"/v1/admin/sources/{source_id}/sync", headers=CSRF)
    assert response.status_code == 202, response.text
    return await work(worker_db, store)


def export(org: Org, folder: str, *, wrapper: str | None = None) -> bytes:
    """One of the organization's synthetic exports as an uploadable zip."""
    return zip_tree(org.root / folder, wrapper=wrapper)


async def rows(db: Database, org: uuid.UUID, sql: str, **params: Any) -> list[Any]:
    async with db.tenant_transaction(org) as s:
        return list((await s.execute(text(sql), params)).all())


async def events(db: Database, org: uuid.UUID, action_like: str = "source.%") -> list[Any]:
    return await rows(
        db,
        org,
        "SELECT actor, action, target, before, after, reason FROM audit_events "
        "WHERE action LIKE :a ORDER BY created_at, id",
        a=action_like,
    )
