"""The real synthetic corpus, ingested once for this package.

Both organizations are seeded and synced through the Phase 4 pipeline with the
deterministic fake embedding provider, then ``admin-grants.yaml`` is applied (D-12) --
exactly what ``make sync`` does. Tests here only read it (the few that change access
restore it in ``finally``), so the per-test truncation of the root conftest is replaced
by one truncation before ingestion.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from lattice.admin_grants import apply_admin_grants, load_admin_grants
from lattice.dataset.lint import Dataset, load_dataset
from lattice.db.engine import Database
from lattice.evals.retrieval import Corpus, Identities, load_corpus, load_identities
from lattice.providers.embedding import FakeEmbeddingProvider

from ...ingestion.conftest import DATA, GOLDEN, ORGS, Org, seed, sync_all


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    """Overrides the root fixture: this package shares one ingested corpus."""


@pytest.fixture(scope="package")
async def corpus_orgs(owner_db: Database) -> AsyncIterator[dict[str, Org]]:
    async with owner_db.engine.begin() as conn:
        await conn.execute(text("TRUNCATE organizations, users CASCADE"))
    orgs = {slug: await seed(owner_db, slug) for slug in ORGS}
    provider = FakeEmbeddingProvider()
    for org in orgs.values():
        results = await sync_all(owner_db, org, provider)
        assert all(r.status in ("succeeded", "partial") for r in results.values())
        report = await apply_admin_grants(
            owner_db, org.slug, load_admin_grants(org.root / "admin-grants.yaml")
        )
        assert report.problems == []
    yield orgs
    async with owner_db.engine.begin() as conn:
        await conn.execute(text("TRUNCATE organizations, users CASCADE"))


@pytest.fixture(scope="package")
def dataset() -> Dataset:
    data = load_dataset(DATA, GOLDEN)
    assert data.problems == []
    return data


@pytest.fixture(scope="package")
async def identities(
    owner_db: Database, corpus_orgs: dict[str, Org], dataset: Dataset
) -> Identities:
    return await load_identities(owner_db, dataset)


@pytest.fixture(scope="package")
async def corpus(owner_db: Database, identities: Identities) -> Corpus:
    return await load_corpus(owner_db, identities.org_ids)
