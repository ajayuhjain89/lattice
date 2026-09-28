"""D-15: deployment trust classes on the query side (security-model §6, ADR-011).

Observed, not assumed: every provider here is wrapped in a recorder (or a mock
transport), and "nothing is sent" means the recorder saw no call. The document side is
in ``tests/ingestion/test_provider_eligibility.py``; the pure decision in
``tests/providers/test_trust_classes.py``.

* ``local`` still needs the reader's authorization, the organization's question policy
  (D-13) and an explicit approval for anything above ``internal``;
* ``organization_controlled`` sends nothing until the organization sets a ceiling for
  the class *and* an administrator approves the record explicitly -- never
  ``restricted`` by implication, never through another organization's configuration;
* ``external_api`` stays under the external level, whatever credential is present;
* ``unknown`` (anything else) fails closed.
"""

import json
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from lattice.db.engine import Database
from lattice.providers.embedding import RecordingEmbeddingProvider
from lattice.providers.gemini_embedding import GeminiEmbeddingProvider
from lattice.providers.openai_embedding import OpenAIEmbeddingProvider

from .test_semantic_degradation import Scripted, assert_exact_and_lexical_still_work
from .world import FAKE, Org, documents_in, new_org, search

QUESTION = "status of HAL-4821"


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")  # fake provider registered, approved internal
    await org.member("priya")
    await org.document(
        "HAL-4821", "SCIM timeline", ["Expected in October."], kind="ticket", display_ref="HAL-4821"
    )
    await org.document("refunds", "Refund policy", ["Refund window is 14 days."])
    return org


def scripted(provider: str, model: str, deployment: str) -> RecordingEmbeddingProvider:
    return RecordingEmbeddingProvider(
        Scripted(provider=provider, model=model, deployment=cast(Any, deployment))
    )


async def outcome(
    app_db: Database, org: Org, provider: RecordingEmbeddingProvider, **query: Any
) -> tuple[bool, str | None]:
    """(was the question sent, the refusal reason if not)."""
    provider.calls.clear()
    result = await search(app_db, org, "priya", QUESTION, provider=provider, **query)
    sent = [c.purpose for c in provider.calls]
    assert sent in ([], ["query"])
    if not sent:
        assert result.semantic.status == "policy_excluded"
        assert_exact_and_lexical_still_work(org, result)
        return False, result.semantic.reason
    return True, None


async def test_local_is_bound_by_approval_and_question_policy(app_db: Database, org: Org) -> None:
    local = scripted("local", "local-model", "local")
    # Not recorded for this organization: never approved for anything.
    assert await outcome(app_db, org, local) == (False, "provider_unapproved")

    await org.register_provider("local", "local-model", deployment="local")  # default approval
    assert await outcome(app_db, org, local) == (True, None)
    assert await outcome(app_db, org, local, classification="confidential") == (
        False,
        "query_classification",
    )
    # The organization's question policy (D-13) applies to local inference too.
    await org.settings(default_query_sensitivity="confidential")
    assert await outcome(app_db, org, local) == (False, "query_classification")

    # Only an explicit approval raises it, and only as far as approved.
    await org.register_provider(
        "local", "local-model", "confidential", deployment="local", explicit=True
    )
    assert await outcome(app_db, org, local) == (True, None)
    assert await outcome(app_db, org, local, classification="restricted") == (
        False,
        "query_classification",
    )


async def test_local_inference_still_requires_authorization(app_db: Database, org: Org) -> None:
    question = "scim provisioning date"
    (vector,) = await Scripted().embed([question], "query")
    # Restricted, grant-only: priya may not read it, and its vector equals the question's.
    await org.document(
        "board-memo",
        "Scim plan",
        [question],
        sensitivity="restricted",
        access_mode="grants_only",
        model="local-model",
        vector=vector,
    )
    await org.document("readable", "Scim note", [question], model="local-model", vector=vector)
    await org.register_provider(
        "local", "local-model", "restricted", deployment="local", explicit=True
    )
    local = scripted("local", "local-model", "local")
    result = await search(app_db, org, "priya", question, provider=local)
    assert result.semantic.status == "matched"
    assert "readable" in documents_in(org, result)
    assert "board-memo" not in documents_in(org, result)
    assert all(
        org.document_of(c.chunk_id) != "board-memo"
        for stage in result.trace.stages.values()
        for c in stage
    )


async def test_organization_controlled_needs_a_class_ceiling_and_explicit_approval(
    app_db: Database, org: Org
) -> None:
    oc = scripted("acme-inference", "acme-embed", "organization_controlled")
    # Recorded (as a first sync would), even "approved" restricted -- but not explicitly.
    await org.register_provider(
        "acme-inference", "acme-embed", "restricted", deployment="organization_controlled"
    )
    await org.settings(
        external_egress_level="restricted", organization_controlled_level="restricted"
    )
    assert await outcome(app_db, org, oc) == (False, "provider_unapproved")

    # Explicitly approved, but the organization has not allowed the class.
    await org.execute(
        "UPDATE org_settings SET organization_controlled_level = NULL WHERE organization_id = :o",
        o=org.id,
    )
    await org.register_provider(
        "acme-inference",
        "acme-embed",
        "restricted",
        deployment="organization_controlled",
        explicit=True,
    )
    assert await outcome(app_db, org, oc) == (False, "deployment_not_allowed")

    # The organization allows it up to confidential: that ceiling holds.
    await org.settings(organization_controlled_level="confidential")
    assert await outcome(app_db, org, oc) == (True, None)
    assert await outcome(app_db, org, oc, classification="confidential") == (True, None)
    assert await outcome(app_db, org, oc, classification="restricted") == (
        False,
        "query_classification",
    )

    # Explicitly approved for internal only: the class ceiling does not raise it.
    await org.register_provider(
        "acme-inference", "acme-embed", deployment="organization_controlled", explicit=True
    )
    assert await outcome(app_db, org, oc, classification="confidential") == (
        False,
        "query_classification",
    )


async def test_another_organizations_configuration_is_never_used(
    app_db: Database, owner_db: Database, org: Org
) -> None:
    other = await new_org(owner_db, "veridian")
    await other.settings(organization_controlled_level="restricted")
    for name, model, deployment in (
        ("acme-inference", "acme-embed", "organization_controlled"),
        ("local", "local-model", "local"),
    ):
        await other.register_provider(
            name, model, "restricted", deployment=deployment, explicit=True
        )
    oc = scripted("acme-inference", "acme-embed", "organization_controlled")
    local = scripted("local", "local-model", "local")
    # Halden has no record of its own: Veridian's approvals and ceiling do not apply.
    assert await outcome(app_db, org, oc) == (False, "provider_unapproved")
    assert await outcome(app_db, org, local) == (False, "provider_unapproved")
    # With its own unapproved record, Halden is still refused: nothing is borrowed.
    await org.register_provider(
        "acme-inference", "acme-embed", deployment="organization_controlled"
    )
    assert await outcome(app_db, org, oc) == (False, "provider_unapproved")


async def test_relabelling_a_record_cannot_bypass_policy(app_db: Database, org: Org) -> None:
    # The fake provider: external_api, approved restricted, external level internal.
    await org.register_provider(level="restricted", explicit=True)
    fake = RecordingEmbeddingProvider(Scripted())
    assert await outcome(app_db, org, fake, classification="confidential") == (
        False,
        "query_classification",
    )
    # Someone relabels the row. The implementation still runs as external_api.
    await org.execute(
        "UPDATE model_providers SET deployment = 'organization_controlled' "
        "WHERE organization_id = :o AND provider = 'fake'",
        o=org.id,
    )
    assert await outcome(app_db, org, fake) == (False, "provider_deployment_mismatch")
    # And an implementation made to claim the new label gains nothing either: the
    # organization never allowed the class.
    claims = scripted("fake", FAKE.model, "organization_controlled")
    assert await outcome(app_db, org, claims) == (False, "deployment_not_allowed")
    # Relabelled to local while the implementation is external: refused.
    await org.execute(
        "UPDATE model_providers SET deployment = 'local' "
        "WHERE organization_id = :o AND provider = 'fake'",
        o=org.id,
    )
    assert await outcome(app_db, org, fake) == (False, "provider_deployment_mismatch")


async def test_an_external_credential_does_not_raise_the_ceiling(
    app_db: Database, org: Org
) -> None:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        n = len(json.loads(request.content).get("input", [None]))
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": [1.0] + [0.0] * 1023} for i in range(n)]}
        )

    await org.register_provider("openai", "text-embedding-3-small", "restricted", explicit=True)
    openai = RecordingEmbeddingProvider(
        OpenAIEmbeddingProvider(SecretStr("sk-valid"), transport=httpx.MockTransport(record))
    )
    # External level internal: approval restricted plus a valid key still stops at internal.
    assert await outcome(app_db, org, openai, classification="confidential") == (
        False,
        "query_classification",
    )
    assert requests == []

    # A provider that may use submitted content, in a customer organization: nothing,
    # whatever the approval and the key.
    await org.register_provider(
        "gemini", "gemini-embedding-2", "restricted", training_opt_out=False, explicit=True
    )
    await org.settings(external_egress_level="restricted", data_origin="customer")
    gemini = RecordingEmbeddingProvider(
        GeminiEmbeddingProvider(SecretStr("valid"), transport=httpx.MockTransport(record))
    )
    assert await outcome(app_db, org, gemini) == (False, "provider_data_use")
    assert requests == []


@pytest.mark.parametrize("label", ["private", "self_hosted", "", "LOCAL", "external"])
async def test_an_unknown_deployment_sends_no_query(app_db: Database, org: Org, label: str) -> None:
    await org.register_provider(level="restricted", explicit=True)
    await org.settings(
        external_egress_level="restricted", organization_controlled_level="restricted"
    )
    unknown = scripted("fake", FAKE.model, label)
    assert await outcome(app_db, org, unknown) == (False, "deployment_unknown")


@pytest.mark.parametrize("label", ["private", "unknown", "", "self_hosted"])
async def test_the_schema_stores_only_known_classes(owner_db: Database, label: str) -> None:
    org = await new_org(owner_db, "halden")
    with pytest.raises(IntegrityError, match="ck_model_providers_deployment"):
        await org.register_provider("other", "other-model", deployment=label)
    with pytest.raises(IntegrityError, match="ck_org_settings_organization_controlled_level"):
        await org.settings(organization_controlled_level="unlimited")
