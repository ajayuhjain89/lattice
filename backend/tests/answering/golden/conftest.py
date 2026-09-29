"""The real synthetic corpus, ingested once for this package (the same fixtures as the
retrieval golden suite: both organizations seeded and synced with the fake embedding
provider, admin grants applied)."""

from ...retrieval.golden.conftest import (  # noqa: F401 - package-scoped fixtures
    clean_tables,
    corpus,
    corpus_orgs,
    dataset,
    identities,
)
