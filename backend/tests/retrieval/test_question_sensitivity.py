"""D-13: question sensitivity and query egress (security-model §6).

The effective sensitivity of a question is the more restrictive of the organization's
trusted default and the caller's declaration. A declaration, a provider choice or a
provider credential can only make handling stricter. When the policy forbids sending
the question to an external provider nothing is sent -- observed with recording
providers and mock transports, not assumed -- exact and lexical retrieval still run,
and a local provider approved for that sensitivity may still embed it.
"""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from lattice.db.engine import Database
from lattice.providers.egress import more_restrictive, question_sensitivity
from lattice.providers.embedding import FakeEmbeddingProvider, RecordingEmbeddingProvider
from lattice.providers.gemini_embedding import GeminiEmbeddingProvider
from lattice.providers.local_embedding import LocalEmbeddingProvider
from lattice.providers.openai_embedding import OpenAIEmbeddingProvider

from ..providers.tiny import TINY, install
from .test_semantic_degradation import Scripted, assert_exact_and_lexical_still_work
from .world import FAKE, Org, new_org, search

LEVELS = ("internal", "confidential", "restricted")


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")  # fake provider registered, approved internal
    await org.member("priya")
    await org.document(
        "HAL-4821", "SCIM timeline", ["Expected in October."], kind="ticket", display_ref="HAL-4821"
    )
    await org.document("refunds", "Refund policy", ["Refund window is 14 days."])
    return org


# --- the rule itself ----------------------------------------------------------------------


@pytest.mark.parametrize("org_default", LEVELS)
@pytest.mark.parametrize("declared", [None, *LEVELS])
def test_a_declaration_can_only_raise_the_organization_default(
    org_default: str, declared: str | None
) -> None:
    effective = question_sensitivity(org_default, declared)
    assert LEVELS.index(effective) >= LEVELS.index(org_default)
    if declared is not None:
        assert LEVELS.index(effective) >= LEVELS.index(declared)
    assert effective == LEVELS[max(LEVELS.index(org_default), LEVELS.index(declared or "internal"))]


def test_unrecognised_levels_fail_closed() -> None:
    assert more_restrictive("internal", "secret") == "restricted"
    assert question_sensitivity("bogus", None) == "restricted"
    assert more_restrictive() == "restricted"


# --- retrieval ------------------------------------------------------------------------------


async def test_the_asker_may_raise_the_sensitivity(app_db: Database, org: Org) -> None:
    provider = RecordingEmbeddingProvider()
    result = await search(
        app_db, org, "priya", "status of HAL-4821", provider=provider, classification="confidential"
    )
    assert result.question_sensitivity == "confidential"
    assert result.trace.question_sensitivity == "confidential"
    assert provider.calls == []
    assert (result.semantic.status, result.semantic.reason) == (
        "policy_excluded",
        "query_classification",
    )
    assert_exact_and_lexical_still_work(org, result)


@pytest.mark.parametrize(
    ("org_default", "claimed"),
    [("confidential", "internal"), ("restricted", "internal"), ("restricted", "confidential")],
)
async def test_a_client_cannot_lower_the_organization_default(
    app_db: Database, org: Org, org_default: str, claimed: str
) -> None:
    await org.settings(default_query_sensitivity=org_default)
    provider = RecordingEmbeddingProvider()
    result = await search(
        app_db, org, "priya", "status of HAL-4821", provider=provider, classification=claimed
    )
    assert result.question_sensitivity == org_default
    assert provider.calls == []  # the question never left Lattice
    assert result.semantic.reason == "query_classification"
    assert_exact_and_lexical_still_work(org, result)
    # Without any declaration the default applies as well.
    silent = await search(app_db, org, "priya", "status of HAL-4821", provider=provider)
    assert silent.question_sensitivity == org_default
    assert provider.calls == []


async def test_a_provider_credential_cannot_lower_it(app_db: Database, org: Org) -> None:
    await org.settings(default_query_sensitivity="confidential")
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    await org.register_provider("openai", "text-embedding-3-small")
    await org.register_provider("gemini", "gemini-embedding-2", training_opt_out=False)
    await org.settings(data_origin="synthetic")
    for provider in (
        OpenAIEmbeddingProvider(SecretStr("sk-valid"), transport=httpx.MockTransport(record)),
        GeminiEmbeddingProvider(
            SecretStr("valid"), data_use="paid", transport=httpx.MockTransport(record)
        ),
    ):
        result = await search(app_db, org, "priya", "status of HAL-4821", provider=provider)
        assert result.question_sensitivity == "confidential"
        assert result.semantic.status == "policy_excluded"
        assert_exact_and_lexical_still_work(org, result)
    assert requests == []


async def test_provider_selection_cannot_lower_it(app_db: Database, org: Org) -> None:
    # A second external provider approved for restricted is still capped by the
    # organization's external level (internal).
    await org.register_provider("other", "other-model", level="restricted")
    other = RecordingEmbeddingProvider(Scripted(provider="other", model="other-model"))
    result = await search(
        app_db, org, "priya", "HAL-4821", provider=other, classification="confidential"
    )
    assert other.calls == []
    assert result.semantic.reason == "query_classification"
    # A provider that *claims* to be local while its record says external_api is
    # refused outright -- the claim cannot move it under the local rule.
    liar = RecordingEmbeddingProvider(Scripted(deployment="local"))
    result = await search(app_db, org, "priya", "HAL-4821", provider=liar)
    assert liar.calls == []
    assert (result.semantic.status, result.semantic.reason) == (
        "policy_excluded",
        "provider_deployment_mismatch",
    )
    # An unrecorded provider was never approved for anything.
    stranger = RecordingEmbeddingProvider(Scripted(provider="stranger", model="s-1"))
    result = await search(app_db, org, "priya", "HAL-4821", provider=stranger)
    assert stranger.calls == []
    assert result.semantic.reason == "provider_unapproved"


async def test_local_semantic_runs_when_external_is_forbidden(
    app_db: Database, org: Org, tmp_path: Path
) -> None:
    install(tmp_path)
    local = LocalEmbeddingProvider.from_cache(TINY, tmp_path)
    (vector,) = await local.embed(["Document: Scim review\n\nscim provisioning date"], "document")
    await org.document(
        "scim-review", "Scim review", ["scim provisioning date"], model=TINY.identity, vector=vector
    )
    await org.register_provider("local", TINY.identity, "confidential", deployment="local")
    # The organization forbids sending confidential content to external providers.
    await org.settings(external_egress_level="internal", default_query_sensitivity="confidential")

    external = RecordingEmbeddingProvider(FakeEmbeddingProvider())
    blocked = await search(app_db, org, "priya", "scim provisioning date", provider=external)
    assert external.calls == []
    assert blocked.semantic.reason == "query_classification"

    recording = RecordingEmbeddingProvider(local)
    allowed = await search(app_db, org, "priya", "scim provisioning date", provider=recording)
    assert [c.purpose for c in recording.calls] == ["query"]
    assert (allowed.semantic.status, allowed.semantic.deployment) == ("matched", "local")
    assert allowed.trace.embedding_deployment == "local"
    assert allowed.question_sensitivity == "confidential"
    # ...but never beyond the local provider's own approval.
    restricted = await search(
        app_db,
        org,
        "priya",
        "scim provisioning date",
        provider=recording,
        classification="restricted",
    )
    assert len(recording.calls) == 1
    assert restricted.semantic.reason == "query_classification"


async def test_a_data_using_provider_never_receives_a_customer_question(
    app_db: Database, org: Org
) -> None:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        n = len(json.loads(request.content)["requests"])
        return httpx.Response(200, json={"embeddings": [{"values": [1.0] + [0.0] * 1023}] * n})

    await org.register_provider("gemini", "gemini-embedding-2", training_opt_out=False)
    unpaid = GeminiEmbeddingProvider(SecretStr("k"), transport=httpx.MockTransport(record))
    # data_origin defaults to customer: nothing is sent, whatever the key.
    result = await search(app_db, org, "priya", "status of HAL-4821", provider=unpaid)
    assert requests == []
    assert (result.semantic.status, result.semantic.reason) == (
        "policy_excluded",
        "provider_data_use",
    )
    assert_exact_and_lexical_still_work(org, result)
    # A synthetic organization may use it (development and evaluation only).
    await org.settings(data_origin="synthetic")
    result = await search(app_db, org, "priya", "status of HAL-4821", provider=unpaid)
    assert len(requests) == 1
    assert result.semantic.status in ("matched", "no_matches")
    # A paid declaration on the provider cannot override the record's data terms.
    await org.settings(data_origin="customer")
    paid = GeminiEmbeddingProvider(
        SecretStr("k"), data_use="paid", transport=httpx.MockTransport(record)
    )
    result = await search(app_db, org, "priya", "status of HAL-4821", provider=paid)
    assert len(requests) == 1
    assert result.semantic.reason == "provider_data_use"


def test_fake_provider_is_declared_external() -> None:
    # The test double makes no network call but is governed like an external provider,
    # so every egress test above exercises the external rule.
    assert FAKE.deployment == "external_api"
