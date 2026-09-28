"""Provider choice changes relevance only, never authorization (Phase 5 closure).

The same adversarial setup runs with each kind of provider -- the fake test double, a
real local ONNX model (the tiny fixture) and the Gemini adapter (mock transport,
synthetic organization): the question is embedded, and unreadable documents and
another tenant's documents carry vectors *identical* to the question's. None of them
may appear in any semantic candidate list or in the evidence.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from lattice.db.engine import Database
from lattice.providers.embedding import EmbeddingProvider
from lattice.providers.gemini_embedding import GeminiEmbeddingProvider
from lattice.providers.local_embedding import LocalEmbeddingProvider

from ..providers.tiny import TINY, install
from .world import FAKE, new_org, search, stage_documents

QUESTION = "scim provisioning date"


def _gemini() -> GeminiEmbeddingProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        texts = [r["content"]["parts"][0]["text"] for r in json.loads(request.content)["requests"]]
        # Same text after the prefix -> same vector: documents match the question exactly.
        return httpx.Response(
            200,
            json={"embeddings": [{"values": FAKE.vector(t.split(": ", 2)[-1])} for t in texts]},
        )

    return GeminiEmbeddingProvider(SecretStr("k"), transport=httpx.MockTransport(handler))


async def _provider(kind: str, tmp_path: Path) -> EmbeddingProvider:
    if kind == "local":
        install(tmp_path)
        return LocalEmbeddingProvider.from_cache(TINY, tmp_path)
    if kind == "gemini":
        return _gemini()
    return FAKE


@pytest.mark.parametrize("kind", ["fake", "local", "gemini"])
async def test_every_provider_returns_only_readable_same_tenant_evidence(
    app_db: Database, owner_db: Database, tmp_path: Path, kind: str
) -> None:
    provider = await _provider(kind, tmp_path)
    (vector,) = await provider.embed([QUESTION], "query")  # the question's own vector

    halden = await new_org(owner_db, "halden")
    other = await new_org(owner_db, "veridian")
    for org in (halden, other):
        await org.settings(data_origin="synthetic")  # the Gemini unpaid key may be used
        await org.register_provider(
            provider.provider,
            provider.model,
            deployment=provider.deployment,
            training_opt_out=provider.training_opt_out,
        )
    await halden.department("leadership")
    await halden.member("maya")
    await halden.member("grace", clearance="confidential", departments=["leadership"])
    await halden.document("public", "Scim note", ["scim provisioning date is October"])
    hidden: dict[str, dict[str, Any]] = {
        "leadership-only": {"sensitivity": "confidential", "departments": ["leadership"]},
        "restricted": {"sensitivity": "restricted", "access_mode": "grants_only"},
        "grants-only": {"access_mode": "grants_only"},
        "deleted": {"status": "deleted"},
    }
    for key, extra in hidden.items():
        await halden.document(
            key, "Scim plan", [QUESTION], model=provider.model, vector=vector, **extra
        )
    await other.document("foreign", "Scim plan", [QUESTION], model=provider.model, vector=vector)

    result = await search(app_db, halden, "maya", QUESTION, provider=provider)
    assert result.semantic.status in ("matched", "no_matches")
    assert result.semantic.deployment == provider.deployment
    semantic = stage_documents(halden, result, "semantic")
    evidence = {halden.document_of(e.chunk_id) for e in result.evidence}
    assert semantic <= {"public"}
    assert evidence == {"public"}
    foreign = {str(i) for ids in other.chunks.values() for i in ids}
    assert not foreign & {str(c.chunk_id) for c in result.trace.stages["semantic"]}
    # Control: the withheld documents really are the best semantic matches -- an
    # authorized reader gets the leadership one through the semantic channel.
    grace = await search(app_db, halden, "grace", QUESTION, provider=provider)
    assert "leadership-only" in stage_documents(halden, grace, "semantic")
