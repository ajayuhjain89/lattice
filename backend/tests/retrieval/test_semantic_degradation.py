"""Query egress policy and semantic degradation (security-model §6; retrieval-design §5).

A recording provider captures every payload, so "the query was not sent" is observed,
not assumed. Whatever happens to semantic retrieval, exact and lexical retrieval still
return permitted evidence, and the outcome says why semantic did not contribute --
``policy_excluded``, ``provider_failed``, ``not_configured`` and ``incompatible_space``
are never collapsed into ``no_matches``.
"""

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
import pytest
from pydantic import SecretStr

from lattice.db.engine import Database
from lattice.providers.embedding import (
    Deployment,
    EmbeddingProvider,
    FakeEmbeddingProvider,
    Purpose,
    RecordingEmbeddingProvider,
)
from lattice.providers.openai_embedding import OpenAIEmbeddingProvider
from lattice.retrieval import RetrievalConfig

from .world import FAKE, Org, documents_in, new_org, search


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")  # the fake provider is registered, internal
    await org.member("priya")
    await org.document(
        "HAL-4821", "SCIM timeline", ["Expected in October."], kind="ticket", display_ref="HAL-4821"
    )
    await org.document("refunds", "Refund policy", ["Refund window is 14 days."])
    return org


def assert_exact_and_lexical_still_work(org: Org, result: object) -> None:
    from lattice.retrieval import RetrievalResult

    assert isinstance(result, RetrievalResult)
    assert documents_in(org, result) == ["HAL-4821"]
    assert result.trace.counts["exact"] >= 1
    assert result.trace.counts["semantic"] == 0
    assert result.trace.degraded is True


async def test_an_internal_query_is_embedded_with_purpose_query(app_db: Database, org: Org) -> None:
    provider = RecordingEmbeddingProvider()
    result = await search(app_db, org, "priya", "status of HAL-4821", provider=provider)
    assert result.semantic.status == "matched"
    assert [(c.texts, c.purpose) for c in provider.calls] == [(("status of HAL-4821",), "query")]


@pytest.mark.parametrize("classification", ["confidential", "restricted"])
async def test_sensitive_queries_are_not_sent_under_the_default_policy(
    app_db: Database, org: Org, classification: str
) -> None:
    provider = RecordingEmbeddingProvider()
    result = await search(
        app_db, org, "priya", "status of HAL-4821", provider=provider, classification=classification
    )
    assert provider.calls == []  # nothing left Lattice
    assert (result.semantic.status, result.semantic.reason) == (
        "policy_excluded",
        "query_classification",
    )
    assert_exact_and_lexical_still_work(org, result)


async def test_opt_in_admits_confidential_queries_but_never_beyond_the_provider_approval(
    app_db: Database, org: Org
) -> None:
    await org.set_egress_level("confidential")
    provider = RecordingEmbeddingProvider()
    # The organization opted in, but the provider is still approved for internal only.
    result = await search(
        app_db, org, "priya", "HAL-4821", provider=provider, classification="confidential"
    )
    assert provider.calls == []
    assert result.semantic.status == "policy_excluded"
    await org.register_provider(level="confidential")
    result = await search(
        app_db, org, "priya", "HAL-4821", provider=provider, classification="confidential"
    )
    assert len(provider.calls) == 1
    assert result.semantic.status == "matched"
    restricted = await search(
        app_db, org, "priya", "HAL-4821", provider=provider, classification="restricted"
    )
    assert len(provider.calls) == 1  # restricted still not sent
    assert restricted.semantic.status == "policy_excluded"


async def test_an_unregistered_provider_never_receives_the_query(
    app_db: Database, org: Org
) -> None:
    await org.execute("DELETE FROM model_providers")
    provider = RecordingEmbeddingProvider()
    result = await search(app_db, org, "priya", "status of HAL-4821", provider=provider)
    assert provider.calls == []
    assert (result.semantic.status, result.semantic.reason) == (
        "policy_excluded",
        "provider_unapproved",
    )
    assert_exact_and_lexical_still_work(org, result)


async def test_provider_failure_degrades_to_exact_and_lexical(app_db: Database, org: Org) -> None:
    provider = RecordingEmbeddingProvider(fail=True)
    result = await search(app_db, org, "priya", "status of HAL-4821", provider=provider)
    assert (result.semantic.status, result.semantic.reason) == (
        "provider_failed",
        "embedding_failed:provider_error",
    )
    assert_exact_and_lexical_still_work(org, result)


@dataclass
class Scripted:
    """A provider that answers with whatever a test scripts (or never answers)."""

    vectors: list[list[float]] | None = None
    delay: float = 0.0
    provider: str = "fake"
    model: str = FAKE.model
    dimensions: int = 1024
    deployment: Deployment = "external_api"
    retention_mode: str = "none"
    training_opt_out: bool = True
    max_batch: int = 8

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        await asyncio.sleep(self.delay)
        return self.vectors if self.vectors is not None else [FAKE.vector(t) for t in texts]


@pytest.mark.parametrize(
    ("provider", "reason"),
    [
        (Scripted(delay=5), "embedding_failed:timeout"),
        (Scripted(vectors=[[0.1] * 512]), "embedding_failed:wrong_dimensions"),
        (Scripted(vectors=[[math.nan] * 1024]), "embedding_failed:malformed_response"),
        (Scripted(vectors=[[0.0] * 1024]), "embedding_failed:malformed_response"),
        (Scripted(vectors=[]), "embedding_failed:malformed_response"),
        (Scripted(vectors=[[0.1] * 1024, [0.1] * 1024]), "embedding_failed:malformed_response"),
    ],
)
async def test_bad_provider_responses_degrade_safely(
    app_db: Database, org: Org, provider: Scripted, reason: str
) -> None:
    config = RetrievalConfig(query_embed_timeout_seconds=0.2)
    result = await search(
        app_db, org, "priya", "status of HAL-4821", provider=provider, config=config
    )
    assert (result.semantic.status, result.semantic.reason) == ("provider_failed", reason)
    assert_exact_and_lexical_still_work(org, result)


async def test_an_openai_outage_degrades_and_nothing_leaks_the_key(
    app_db: Database, org: Org, caplog: pytest.LogCaptureFixture
) -> None:
    await org.register_provider("openai", "text-embedding-3-small")
    key = "sk-test-DO-NOT-LEAK-0123456789"
    provider = OpenAIEmbeddingProvider(
        SecretStr(key),
        max_attempts=1,
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text="down")),
    )
    result = await search(app_db, org, "priya", "status of HAL-4821", provider=provider)
    assert (result.semantic.status, result.semantic.reason) == (
        "provider_failed",
        "embedding_failed:provider_error",
    )
    assert_exact_and_lexical_still_work(org, result)
    assert key not in result.model_dump_json()
    assert key not in caplog.text


async def test_incompatible_embedding_spaces_are_not_compared(app_db: Database, org: Org) -> None:
    small = RecordingEmbeddingProvider(FakeEmbeddingProvider(dimensions=512))
    result = await search(app_db, org, "priya", "status of HAL-4821", provider=small)
    assert small.calls == []
    assert (result.semantic.status, result.semantic.reason) == ("incompatible_space", "dimensions")
    # Two providers registered under one model name: vectors cannot be told apart.
    await org.register_provider("other-vendor", FAKE.model)
    ambiguous = RecordingEmbeddingProvider()
    result = await search(app_db, org, "priya", "status of HAL-4821", provider=ambiguous)
    assert ambiguous.calls == []
    assert (result.semantic.status, result.semantic.reason) == (
        "incompatible_space",
        "model_ambiguous",
    )
    assert_exact_and_lexical_still_work(org, result)


async def test_not_configured_and_no_matches_are_distinct(app_db: Database, org: Org) -> None:
    none = await search(app_db, org, "priya", "status of HAL-4821", provider=None)
    assert none.semantic.status == "not_configured"
    assert none.trace.degraded is True
    # A registered provider whose model has no vectors in the corpus: nothing to match.
    await org.register_provider("fake", "fresh-model")
    fresh: EmbeddingProvider = Scripted(model="fresh-model")
    empty = await search(app_db, org, "priya", "status of HAL-4821", provider=fresh)
    assert empty.semantic.status == "no_matches"
    assert empty.trace.degraded is False
    assert documents_in(org, empty) == ["HAL-4821"]
