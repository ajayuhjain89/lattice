"""The real synthetic corpus for the operational-metadata suite (security-model §10).

Seeded, synced and admin-granted once for this package, as ``make sync`` does (fake
embeddings). Tests only read it."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from lattice.admin_grants import apply_admin_grants, load_admin_grants
from lattice.db.engine import Database
from lattice.providers.embedding import FakeEmbeddingProvider

from ...ingestion.conftest import ORGS, Org, seed, sync_all


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    """Overrides the root fixture: this package shares one ingested corpus."""


@pytest.fixture(scope="package")
async def corpus_orgs(owner_db: Database) -> AsyncIterator[dict[str, Org]]:
    async with owner_db.engine.begin() as conn:
        await conn.execute(text("TRUNCATE organizations, users CASCADE"))
    orgs = {slug: await seed(owner_db, slug) for slug in ORGS}
    for org in orgs.values():
        results = await sync_all(owner_db, org, FakeEmbeddingProvider())
        assert all(r.status in ("succeeded", "partial") for r in results.values())
        report = await apply_admin_grants(
            owner_db, org.slug, load_admin_grants(org.root / "admin-grants.yaml")
        )
        assert report.problems == []
    yield orgs
    async with owner_db.engine.begin() as conn:
        await conn.execute(text("TRUNCATE organizations, users CASCADE"))
