"""Operational controls of the Ask pipeline (Phase 8): the deployment-wide generation
kill switch, the generation deadline, and the metrics an Ask records.

The kill switch is a stop, never a policy input: with it off nothing is built, no
credential is resolved and nothing is sent, and the asker still gets the evidence they
may read. The deadline bounds one Ask's time in generation: a hung provider yields a
``failed`` answer with its evidence, never a hanging request or an invented answer."""

import asyncio
from typing import Any

import pytest
from pydantic import SecretStr

from lattice import metrics
from lattice.db.engine import Database
from lattice.providers.generation import GenerationRequest, GenerationResult

from ..retrieval.world import Org, new_org
from .helpers import Harness, configure_generation, run, set_credential

QUESTION = "What does the onboarding checklist say?"
INTERNAL = "The onboarding checklist has five steps for new admins."


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.member("tom")
    await org.document("internal", "Onboarding checklist", [INTERNAL])
    return org


def _sample(name: str, **labels: str) -> float:
    value = metrics.REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else value


async def test_the_kill_switch_sends_nothing_and_keeps_the_evidence(
    app_db: Database, org: Org
) -> None:
    await configure_generation(org)
    harness = Harness()
    before = _sample("lattice_generation_decisions_total", decision="generation_disabled")
    outcome = await run(
        app_db, org, "tom", QUESTION, harness, operations={"generation_enabled": False}
    )
    assert harness.builds == 0
    assert harness.requests == []
    assert outcome.answer.status == "evidence_only"
    assert outcome.answer.status_reason == "generation_unavailable"
    assert outcome.answer.claims == []
    assert [e.text for e in outcome.answer.evidence] == [INTERNAL]
    assert outcome.trace["egress"]["decision_reason"] == "generation_disabled"
    assert outcome.trace["validation"]["generation_calls"] == 0
    after = _sample("lattice_generation_decisions_total", decision="generation_disabled")
    assert after == before + 1


async def test_the_kill_switch_resolves_no_credential(app_db: Database, org: Org) -> None:
    await configure_generation(
        org, provider="gemini", model="gemini-3.5-flash-lite", training_opt_out=False
    )
    await set_credential(org, "gemini", "memory:halden-gemini", data_use="paid")
    harness = Harness()
    outcome = await run(
        app_db,
        org,
        "tom",
        QUESTION,
        harness,
        memory={"halden-gemini": SecretStr("never-used")},
        operations={"generation_enabled": False},
    )
    assert harness.secrets_seen == []
    assert harness.builds == 0
    assert outcome.answer.status == "evidence_only"


async def test_a_policy_refusal_keeps_its_own_reason_with_the_switch_off(
    app_db: Database, org: Org
) -> None:
    """Nothing configured: the trace says so, not that an operator stopped it."""
    harness = Harness()
    outcome = await run(
        app_db, org, "tom", QUESTION, harness, operations={"generation_enabled": False}
    )
    assert outcome.trace["egress"]["decision_reason"] == "not_configured"


async def test_a_hung_provider_fails_the_answer_at_the_deadline(
    app_db: Database, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await configure_generation(org)
    harness = Harness()
    calls = 0

    async def hang(self: Any, request: GenerationRequest) -> GenerationResult:
        nonlocal calls
        calls += 1
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    monkeypatch.setattr("lattice.providers.generation.FakeGenerationProvider.generate", hang)
    before = _sample("lattice_generation_calls_total", provider="fake", outcome="timeout")
    started = asyncio.get_running_loop().time()
    outcome = await run(
        app_db, org, "tom", QUESTION, harness, operations={"generation_deadline_seconds": 0.5}
    )
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 10
    assert calls == 1  # the deadline is for the whole Ask: no second attempt
    assert outcome.answer.status == "failed"
    assert outcome.answer.status_reason == "provider_unavailable"
    assert outcome.answer.claims == []
    assert [e.text for e in outcome.answer.evidence] == [INTERNAL]
    assert outcome.trace["error_code"] == "generation_failed:timeout"
    after = _sample("lattice_generation_calls_total", provider="fake", outcome="timeout")
    assert after == before + 1


async def test_a_spent_deadline_starts_no_call(
    app_db: Database, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call (first or regeneration) is never started once the budget is spent."""
    await configure_generation(org)
    harness = Harness()
    calls = 0

    async def count(self: Any, request: GenerationRequest) -> GenerationResult:
        nonlocal calls
        calls += 1
        raise AssertionError("no call may start after the deadline")

    monkeypatch.setattr("lattice.providers.generation.FakeGenerationProvider.generate", count)
    outcome = await run(
        app_db, org, "tom", QUESTION, harness, operations={"generation_deadline_seconds": 0.0}
    )
    assert calls == 0
    assert outcome.answer.status == "failed"
    assert outcome.answer.status_reason == "provider_unavailable"
    assert outcome.trace["error_code"] == "generation_failed:deadline_exceeded"


async def test_an_ask_is_counted_with_bounded_labels(app_db: Database, org: Org) -> None:
    await configure_generation(org)
    before = _sample("lattice_asks_total", status="answered", reason="none")
    outcome = await run(app_db, org, "tom", QUESTION, Harness())
    assert outcome.answer.status == "answered"
    assert _sample("lattice_asks_total", status="answered", reason="none") == before + 1
    exposition = _exposition()
    for forbidden in (str(org.id), str(org.members["tom"]), "onboarding", "halden"):
        assert forbidden not in exposition.lower()


def _exposition() -> str:
    from prometheus_client import generate_latest

    return generate_latest(metrics.REGISTRY).decode()
