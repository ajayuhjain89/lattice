"""Ingestion test harness (Phase 4).

Every sync runs against real PostgreSQL through row-level security, as the operator
command does (lattice_owner, organization bound per transaction). Tests that change a
source's export work on a copy of the dataset in ``tmp_path``; the dataset itself is
never modified.
"""

import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.ingestion.sync import SyncOptions, SyncResult, sync_source
from lattice.providers.embedding import EmbeddingProvider
from lattice.seed import load_manifest, seed_manifest
from lattice.sources.config import SourcesFile, load_sources_file

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "synthetic"
GOLDEN = REPO / "evals" / "golden"
ORGS = ("halden", "veridian")

# connector_type -> the dataset's record-key prefix (doc:<id>, ticket:<id>, chat:<id>).
PREFIX = {"document_repository": "doc", "ticketing": "ticket", "chat_export": "chat"}


@dataclass
class Org:
    slug: str
    id: uuid.UUID
    root: Path  # the organization's dataset folder (a copy when the test mutates it)
    sources: SourcesFile

    def path(self, source: str) -> Path:
        config = self.sources.get(source)
        assert config is not None, source
        return self.root / config.path

    def reload(self) -> None:
        """Re-read sources.yaml after a test edited it."""
        self.sources = load_sources_file(self.root / "sources.yaml")


async def seed(owner_db: Database, slug: str, root: Path | None = None) -> Org:
    folder = root or DATA / slug
    await seed_manifest(owner_db, load_manifest(folder / "org.yaml"))
    async with owner_db.unscoped_transaction() as s:
        org_id: uuid.UUID = (
            await s.execute(text("SELECT id FROM organizations WHERE slug = :s"), {"s": slug})
        ).scalar_one()
    return Org(slug, org_id, folder, load_sources_file(folder / "sources.yaml"))


async def sync(
    db: Database,
    org: Org,
    source: str,
    provider: EmbeddingProvider | None = None,
    **options: object,
) -> SyncResult:
    config = org.sources.get(source)
    assert config is not None, source
    return await sync_source(
        db,
        org.id,
        config,
        org.path(source),
        provider,
        SyncOptions(**options),  # type: ignore[arg-type]
    )


async def sync_all(
    db: Database, org: Org, provider: EmbeddingProvider | None = None, **options: object
) -> dict[str, SyncResult]:
    return {s.key: await sync(db, org, s.key, provider, **options) for s in org.sources.sources}


def copy_org(tmp_path: Path, slug: str) -> Path:
    target = tmp_path / slug
    shutil.copytree(DATA / slug, target)
    return target


@pytest.fixture
async def halden(owner_db: Database, tmp_path: Path) -> Org:
    """Halden seeded, with a writable copy of its exports. Veridian is seeded too, so
    every test runs with a second tenant present."""
    await seed(owner_db, "veridian")
    return await seed(owner_db, "halden", copy_org(tmp_path, "halden"))


# --- observing writes -------------------------------------------------------------------

WATCHED = (
    "sources",
    "documents",
    "chunks",
    "document_grants",
    "access_groups",
    "access_group_members",
    "memberships",
    "model_providers",
)


async def row_versions(db: Database, org: uuid.UUID) -> dict[str, set[tuple[str, str]]]:
    """(ctid, xmin) of every row of the tables a sync may write. A row that is
    inserted, updated or deleted changes this set; an untouched row does not."""
    result: dict[str, set[tuple[str, str]]] = {}
    async with db.tenant_transaction(org) as s:
        for table in WATCHED:
            rows = await s.execute(text(f"SELECT ctid::text, xmin::text FROM {table}"))  # noqa: S608
            result[table] = {(r[0], r[1]) for r in rows.all()}
    return result


def changed_tables(
    before: Mapping[str, set[tuple[str, str]]], after: Mapping[str, set[tuple[str, str]]]
) -> set[str]:
    return {table for table in before if before[table] != after[table]}


async def scalar(db: Database, org: uuid.UUID, sql: str, **params: object) -> Any:
    async with db.tenant_transaction(org) as s:
        return (await s.execute(text(sql), params)).scalar_one()


async def rows(db: Database, org: uuid.UUID, sql: str, **params: object) -> list[tuple[Any, ...]]:
    async with db.tenant_transaction(org) as s:
        return [tuple(r) for r in (await s.execute(text(sql), params)).all()]
