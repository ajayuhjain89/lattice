"""Which provider may receive document content (ADR-009, ADR-010, ADR-011;
security-model §6), observed payload for payload.

* A **local** provider is governed by its own approval, not by the organization's
  external egress level: approved for ``restricted`` it embeds confidential and
  restricted content, which still never reaches an external provider.
* A provider that may **use submitted content** (an unpaid Gemini key) receives
  nothing from a ``customer`` organization -- only from a ``synthetic`` one.
* A provider whose declared deployment differs from its record receives nothing.
"""

import json
from pathlib import Path

import httpx
from pydantic import SecretStr
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.providers.embedding import FakeEmbeddingProvider, RecordingEmbeddingProvider
from lattice.providers.gemini_embedding import GeminiEmbeddingProvider
from lattice.providers.local_embedding import LocalEmbeddingProvider

from ..providers.tiny import TINY, install
from .conftest import Org, scalar, sync
from .test_egress import _embedded_inputs


async def _set(db: Database, org: Org, sql: str) -> None:
    async with db.tenant_transaction(org.id) as s:
        await s.execute(text(sql), {"o": org.id})


async def test_local_provider_is_bounded_by_its_own_approval(
    owner_db: Database, halden: Org, tmp_path: Path
) -> None:
    install(tmp_path / "models")
    local = RecordingEmbeddingProvider(LocalEmbeddingProvider.from_cache(TINY, tmp_path / "models"))
    await sync(owner_db, halden, "drive", local)
    internal = await _embedded_inputs(owner_db, halden, ("internal",))
    above = await _embedded_inputs(owner_db, halden, ("confidential", "restricted"))
    assert above
    # First use: recorded as local, approved for internal only (the default).
    assert set(local.texts) == internal
    assert (
        await scalar(
            owner_db,
            halden.id,
            "SELECT deployment || '/' || approved_for_level FROM model_providers "
            "WHERE provider = 'local'",
        )
        == "local/internal"
    )

    # The administrator approves the local model for restricted; the organization's
    # *external* level stays internal.
    await _set(
        owner_db,
        halden,
        "UPDATE model_providers SET approved_for_level = 'restricted' WHERE provider = 'local'",
    )
    local.calls.clear()
    await sync(owner_db, halden, "drive", local)
    assert set(local.texts) == above  # newly eligible chunks, embedded locally
    assert (
        await scalar(
            owner_db,
            halden.id,
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE d.source_id = (SELECT id FROM sources WHERE key = 'drive') "
            "AND c.embedding_status <> 'embedded'",
        )
        == 0
    )

    # An external provider in the same organization is still capped at internal.
    external = RecordingEmbeddingProvider(FakeEmbeddingProvider())
    await sync(owner_db, halden, "drive", external, mode="reindex", reembed=True)
    assert set(external.texts) == internal
    assert not set(external.texts) & above


def _gemini(seen: list[httpx.Request], **kwargs: object) -> GeminiEmbeddingProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        n = len(json.loads(request.content)["requests"])
        return httpx.Response(200, json={"embeddings": [{"values": [1.0] + [0.0] * 1023}] * n})

    return GeminiEmbeddingProvider(
        SecretStr("test-key"),
        transport=httpx.MockTransport(handler),
        **kwargs,  # type: ignore[arg-type]
    )


async def test_an_unpaid_key_receives_only_synthetic_content(
    owner_db: Database, halden: Org
) -> None:
    seen: list[httpx.Request] = []
    await _set(
        owner_db,
        halden,
        "INSERT INTO org_settings (organization_id, data_origin) VALUES (:o, 'customer') "
        "ON CONFLICT (organization_id) DO UPDATE SET data_origin = 'customer'",
    )
    result = await sync(owner_db, halden, "drive", _gemini(seen))
    assert seen == []  # a valid key, and still nothing was sent
    assert result.egress_block == "provider_data_use"
    assert result.chunks_embedded == 0
    assert (
        await scalar(
            owner_db,
            halden.id,
            "SELECT count(*) FROM chunks WHERE embedding IS NOT NULL",
        )
        == 0
    )

    # The seeded synthetic organization (data_origin from its manifest) may use it.
    await _set(owner_db, halden, "UPDATE org_settings SET data_origin = 'synthetic'")
    result = await sync(owner_db, halden, "drive", _gemini(seen))
    assert result.egress_block is None
    assert result.chunks_embedded > 0
    sent = [
        r["content"]["parts"][0]["text"] for q in seen for r in json.loads(q.content)["requests"]
    ]
    internal = await _embedded_inputs(owner_db, halden, ("internal",))
    assert {text.removeprefix("title: none | text: ") for text in sent} == internal


async def test_seeded_synthetic_organizations_are_marked(owner_db: Database, halden: Org) -> None:
    assert await scalar(owner_db, halden.id, "SELECT data_origin FROM org_settings") == "synthetic"


async def test_a_deployment_claim_that_differs_from_the_record_sends_nothing(
    owner_db: Database, halden: Org
) -> None:
    provider = RecordingEmbeddingProvider(FakeEmbeddingProvider())
    await sync(owner_db, halden, "drive", provider)  # recorded as external_api
    await _set(owner_db, halden, "UPDATE model_providers SET approved_for_level = 'restricted'")

    class ClaimsLocal(FakeEmbeddingProvider):
        deployment = "local"

    liar = RecordingEmbeddingProvider(ClaimsLocal())
    result = await sync(owner_db, halden, "drive", liar, mode="reindex", reembed=True)
    assert liar.calls == []
    assert result.egress_block == "provider_deployment_mismatch"


# --- D-15: trust classes on the document side --------------------------------------------


class _Declares(FakeEmbeddingProvider):
    """The fake provider, declaring whatever trust class a test gives it."""

    def __init__(self, deployment: str, provider: str = "fake") -> None:
        super().__init__()
        self.deployment = deployment  # type: ignore[assignment]
        self.provider = provider


async def test_an_unknown_deployment_sends_no_document_text(
    owner_db: Database, halden: Org
) -> None:
    for label in ("private", "self_hosted", ""):
        provider = RecordingEmbeddingProvider(_Declares(label, provider=f"p-{label or 'blank'}"))
        result = await sync(owner_db, halden, "drive", provider, mode="reindex", reembed=True)
        assert provider.calls == []
        assert result.egress_block == "deployment_unknown"
        assert result.chunks_embedded == 0
        # Never recorded either: an unknown class is not a configuration.
        assert (
            await scalar(
                owner_db,
                halden.id,
                "SELECT count(*) FROM model_providers WHERE provider = :p",
                p=provider.provider,
            )
            == 0
        )
    assert (
        await scalar(owner_db, halden.id, "SELECT count(*) FROM chunks WHERE embedding IS NOT NULL")
        == 0
    )


async def test_organization_controlled_sends_documents_only_when_explicitly_allowed(
    owner_db: Database, halden: Org
) -> None:
    provider = RecordingEmbeddingProvider(_Declares("organization_controlled", "acme-inference"))
    await _set(
        owner_db,
        halden,
        "INSERT INTO org_settings (organization_id, external_egress_level) "
        "VALUES (:o, 'restricted') "
        "ON CONFLICT (organization_id) DO UPDATE SET external_egress_level = 'restricted'",
    )
    # First use records it with the default approval, which is not an approval here.
    result = await sync(owner_db, halden, "drive", provider)
    assert provider.calls == []
    assert result.egress_block == "provider_unapproved"
    assert (
        await scalar(
            owner_db,
            halden.id,
            "SELECT deployment || '/' || approved_for_level || '/' || (approved_at IS NULL) "
            "FROM model_providers WHERE provider = 'acme-inference'",
        )
        == "organization_controlled/internal/true"
    )

    # An administrator approves it for restricted; the organization has not allowed the
    # class (organization_controlled_level unset): still nothing.
    await _set(
        owner_db,
        halden,
        "UPDATE model_providers SET approved_for_level = 'restricted', approved_at = now() "
        "WHERE provider = 'acme-inference'",
    )
    result = await sync(owner_db, halden, "drive", provider, mode="reindex", reembed=True)
    assert provider.calls == []
    assert result.egress_block == "deployment_not_allowed"

    # The organization allows the class up to confidential: exactly internal and
    # confidential content is sent, restricted never.
    await _set(
        owner_db, halden, "UPDATE org_settings SET organization_controlled_level = 'confidential'"
    )
    result = await sync(owner_db, halden, "drive", provider, mode="reindex", reembed=True)
    assert result.egress_block is None
    allowed = await _embedded_inputs(owner_db, halden, ("internal", "confidential"))
    restricted = await _embedded_inputs(owner_db, halden, ("restricted",))
    confidential = await _embedded_inputs(owner_db, halden, ("confidential",))
    assert confidential  # the drive export has both
    assert restricted
    assert set(provider.texts) == allowed
    assert not set(provider.texts) & restricted
