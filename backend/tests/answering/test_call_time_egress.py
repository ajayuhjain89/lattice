"""Call-time egress (Phase 12, P12.5; phase-12-plan C6.1 fact 7; ADR-011): the provider
decision made when the request began is repeated immediately before every model call.

A configuration screen is not the enforcement boundary: a change committed between the
request's snapshot and the call -- a lowered ceiling, a denied external class, a disabled
or unapproved provider, a raised default question sensitivity, a document reclassified,
a revoked credential -- stops the call, and nothing of the prompt leaves Lattice. A
regeneration is checked again too. And the query side (D-13): with external APIs denied,
the question is not embedded externally while exact and lexical retrieval still answer."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from pydantic import SecretStr

from lattice.answering import service
from lattice.db.engine import Database
from lattice.providers import configuration

from ..retrieval.world import Org, new_org
from .helpers import ACTOR, Harness, configure_generation, run, set_credential

INTERNAL = "The onboarding checklist has five steps for new admins."
QUESTION = "What does the onboarding checklist say?"
KEY = "AIza-call-time-key-DO-NOT-LEAK-0001"
VALID = {
    "claims": [],
    "conflicts": [],
    "unknowns": [],
    "clarifying_question": "",
    "status": "insufficient_evidence",
}


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.member("tom")
    await org.document("internal", "Onboarding checklist", [INTERNAL])
    return org


def _between(
    monkeypatch: pytest.MonkeyPatch, change: Callable[[], Awaitable[None]], *, at_call: int = 1
) -> list[str | None]:
    """Commit ``change`` just before the ``at_call``-th re-check -- after the request's
    snapshot decided the provider may be called, before the call."""
    real = service.recheck_generation  # type: ignore[attr-defined]
    seen: list[str | None] = []

    async def recheck(*args: Any, **kwargs: Any) -> str | None:
        if len(seen) + 1 == at_call:
            await change()
        found = await real(*args, **kwargs)
        seen.append(found)
        return found

    monkeypatch.setattr(service, "recheck_generation", recheck)
    return seen


async def test_an_unchanged_policy_is_rechecked_and_the_call_made(
    app_db: Database, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await configure_generation(org)
    harness = Harness(outputs=[VALID])

    async def nothing() -> None:
        return None

    seen = _between(monkeypatch, nothing)
    outcome = await run(app_db, org, "tom", QUESTION, harness)
    assert seen == [None]
    assert len(harness.requests) == 1
    assert outcome.trace["egress"]["recheck_refused"] is None


def _changes(org: Org) -> dict[str, Callable[[], Awaitable[None]]]:
    async def deny_external() -> None:
        await org.execute(
            "INSERT INTO org_settings (organization_id, external_egress_level) VALUES (:o, NULL) "
            "ON CONFLICT (organization_id) DO UPDATE SET external_egress_level = NULL",
            o=org.id,
        )

    async def disable() -> None:
        await org.execute("UPDATE model_providers SET enabled = false WHERE purpose = 'generation'")

    async def withdraw_approval() -> None:
        await org.execute(
            "UPDATE model_providers SET approved_at = NULL WHERE purpose = 'generation'"
        )

    async def lower_approval_by_ceiling() -> None:
        await org.settings(default_query_sensitivity="confidential")

    async def reclassify() -> None:
        await org.execute("UPDATE documents SET sensitivity = 'confidential'")

    async def delete_evidence() -> None:
        await org.execute("UPDATE documents SET status = 'deleted'")

    return {
        "external denied": deny_external,
        "provider disabled": disable,
        "approval withdrawn": withdraw_approval,
        "question default raised": lower_approval_by_ceiling,
        "document reclassified": reclassify,
        "evidence deleted": delete_evidence,
    }


@pytest.mark.parametrize(
    "name",
    [
        "external denied",
        "provider disabled",
        "approval withdrawn",
        "question default raised",
        "document reclassified",
        "evidence deleted",
    ],
)
async def test_a_change_before_the_call_stops_it(
    app_db: Database, org: Org, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    await configure_generation(org)
    harness = Harness(outputs=[VALID])
    seen = _between(monkeypatch, _changes(org)[name])
    outcome = await run(app_db, org, "tom", QUESTION, harness)
    assert seen == ["policy_changed"]
    assert harness.builds == 1  # built when the request began ...
    assert harness.requests == []  # ... and never called: nothing left Lattice
    assert outcome.answer.status == "evidence_only"
    assert outcome.answer.status_reason == "egress_policy"
    assert outcome.trace["egress"]["decision_reason"] == "policy_changed"
    assert outcome.trace["prompt_chunk_ids"] == []
    assert not any(e.used_for_answer for e in outcome.answer.evidence)


async def test_a_revoked_credential_stops_the_call(
    app_db: Database, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await configure_generation(org, provider="gemini", model="gemini-3.5-flash-lite")
    await set_credential(org, "gemini", "memory:k", data_use="paid")

    async def revoke() -> None:
        async with org.owner.tenant_transaction(org.id) as s:
            await configuration.revoke_credential(
                s, org.id, provider="gemini", reason="leaked", actor=ACTOR
            )

    harness = Harness(outputs=[VALID])
    seen = _between(monkeypatch, revoke)
    outcome = await run(app_db, org, "tom", QUESTION, harness, memory={"k": SecretStr(KEY)})
    assert seen == ["credential_revoked"]
    assert harness.requests == []
    assert outcome.answer.status == "evidence_only"
    assert outcome.answer.status_reason == "generation_unavailable"
    assert KEY not in str(outcome.trace)


async def test_a_replaced_credential_stops_the_call_built_with_the_old_one(
    app_db: Database, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await configure_generation(org, provider="gemini", model="gemini-3.5-flash-lite")
    await set_credential(org, "gemini", "memory:k", data_use="paid")

    async def replace() -> None:
        await set_credential(org, "gemini", "memory:k2", data_use="paid")

    harness = Harness(outputs=[VALID])
    seen = _between(monkeypatch, replace)
    await run(
        app_db, org, "tom", QUESTION, harness, memory={"k": SecretStr(KEY), "k2": SecretStr(KEY)}
    )
    assert seen == ["credential_revoked"]
    assert harness.requests == []


async def test_a_regeneration_is_checked_again(
    app_db: Database, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await configure_generation(org)
    # The first draft is unusable, so Lattice would ask once more -- but the provider was
    # disabled meanwhile: the second call is not made.
    harness = Harness(outputs=["not json", VALID])

    async def disable() -> None:
        await org.execute("UPDATE model_providers SET enabled = false WHERE purpose = 'generation'")

    seen = _between(monkeypatch, disable, at_call=2)
    outcome = await run(app_db, org, "tom", QUESTION, harness)
    assert seen == [None, "policy_changed"]
    assert len(harness.requests) == 1
    assert outcome.trace["egress"]["recheck_refused"] == "policy_changed"
    assert outcome.trace["validation"]["regenerated"] is False


async def test_a_denied_external_class_skips_query_embedding_not_retrieval(
    app_db: Database, org: Org
) -> None:
    await org.settings(external_egress_level="restricted")
    harness = Harness()
    allowed = await run(app_db, org, "tom", QUESTION, harness)
    assert allowed.trace["retrieval"]["semantic_status"] != "policy_excluded"
    await org.execute(
        "UPDATE org_settings SET external_egress_level = NULL WHERE organization_id = :o", o=org.id
    )
    denied = await run(app_db, org, "tom", QUESTION, harness)
    assert denied.trace["retrieval"]["semantic_status"] == "policy_excluded"
    assert denied.trace["retrieval"]["semantic_reason"] == "deployment_not_allowed"
    # Exact and lexical retrieval still find the authorized evidence.
    assert {e.title for e in denied.answer.evidence} == {"Onboarding checklist"}


async def test_the_trace_records_the_decision_without_secrets(app_db: Database, org: Org) -> None:
    await configure_generation(org, provider="gemini", model="gemini-3.5-flash-lite")
    await set_credential(org, "gemini", "memory:k", data_use="paid")
    harness = Harness(outputs=[VALID])
    outcome = await run(app_db, org, "tom", QUESTION, harness, memory={"k": SecretStr(KEY)})
    egress = outcome.trace["egress"]
    assert egress["provider"] == "gemini"
    assert egress["deployment"] == "external_api"
    assert egress["effective_level"] == "internal"
    text_ = str(outcome.trace)
    assert KEY not in text_
    assert "memory:k" not in text_
    assert harness.secrets_seen  # the key reached the adapter, and nothing else
