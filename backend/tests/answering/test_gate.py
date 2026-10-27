"""The relevance gate (P13.3): deterministic abstention from permitted evidence only.

Weighted coverage stops a question whose specific words no permitted evidence contains
(G1) without a model call; a decisive item the egress policy holds back makes the answer
evidence-only instead of a draft from the weak remainder (G9); a calibrated semantic
floor lets paraphrase-only evidence through (G3). Nothing the asker cannot read changes
any of it (SEC-5, S4), and the trace holds numbers and codes, never the asker's words.
"""

import json
from typing import Any

import pytest

from lattice.answering.gate import GateConfig
from lattice.answering.models import AskRequest
from lattice.answering.service import AskOutcome, answer_question
from lattice.db.engine import Database
from lattice.retrieval import RetrievalQuery, retrieve

from ..retrieval.world import FAKE, NOW, Org, new_org
from .helpers import Harness, configure_generation, deps, run

PLAN = "Acme Corp renewed for three years and expanded to the EU region."
CALL = "The Acme Corp team asked about SSO and the onboarding checklist."
REFUND = "Enterprise customers may request a full refund within 14 days of purchase."
NPS = "What is Acme Corp's NPS score?"


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.account("acme", "Acme Corp", aliases=["Acme"])
    await org.group("exec")
    await org.member("maya", scope="all")
    await org.member("grace", clearance="confidential", scope="all")
    await org.member("tom", scope="assigned")  # sees no account
    await org.document("acme-plan", "Acme account plan", [PLAN], account="acme")
    await org.document("acme-call", "Acme kickoff call", [CALL], account="acme")
    await org.document("refund", "Refund policy", [REFUND], authority="system_of_record")
    await configure_generation(org)
    return org


def _gate(outcome: AskOutcome) -> dict[str, Any]:
    gate: dict[str, Any] = outcome.trace["retrieval"]["relevance_gate"]
    return gate


async def test_the_account_alone_does_not_answer_a_question_about_something_else(
    app_db: Database, org: Org
) -> None:
    harness = Harness()
    outcome = await run(app_db, org, "maya", NPS, harness)
    assert outcome.answer.status == "insufficient_evidence"
    assert outcome.answer.status_reason == "no_relevant_evidence"
    assert harness.builds == 0
    assert harness.requests == []
    gate = _gate(outcome)
    assert gate["decision"] == "no_decisive_evidence"
    assert {i["code"] for i in gate["items"]} <= {"account_only", "no_signal"}
    # Below the gate the evidence is still the asker's to read.
    assert outcome.answer.evidence
    assert {e.not_used_reason for e in outcome.answer.evidence} == {"relevance_floor"}


async def test_evidence_that_contains_the_specific_words_passes(app_db: Database, org: Org) -> None:
    await org.document(
        "acme-survey", "Acme survey", ["Acme Corp's NPS score in September was 41."], account="acme"
    )
    harness = Harness()
    outcome = await run(app_db, org, "maya", NPS, harness)
    assert outcome.answer.status == "answered"
    assert _gate(outcome)["decision"] == "pass"
    survey = org.documents["acme-survey"]
    used = {e.document_id for e in outcome.answer.evidence if e.used_for_answer}
    assert survey in used
    # Account-only items still join the prompt once the gate passes (context recall).
    assert org.documents["acme-plan"] in used


async def test_an_unreadable_strong_match_changes_nothing(app_db: Database, org: Org) -> None:
    before = await run(app_db, org, "maya", NPS, Harness())
    await org.document(
        "acme-board",
        "Acme board pack",
        ["Acme Corp NPS score 72, board only."],
        account="acme",
        sensitivity="restricted",
        access_mode="grants_only",
        grants=[("group", "exec")],
    )
    after = await run(app_db, org, "maya", NPS, Harness())
    assert after.answer.status == before.answer.status == "insufficient_evidence"
    # Same items, codes, coverage and weights: the statistics count readable chunks only.
    assert _gate(after) == _gate(before)


async def test_decisive_evidence_held_back_by_egress_is_evidence_only(
    app_db: Database, org: Org
) -> None:
    await org.document(
        "acme-survey",
        "Acme survey",
        ["Acme Corp's NPS score in September was 41."],
        account="acme",
        sensitivity="confidential",
    )
    harness = Harness()
    outcome = await run(app_db, org, "grace", NPS, harness)
    assert outcome.answer.status == "evidence_only"
    assert outcome.answer.status_reason == "egress_policy"
    assert harness.requests == []  # the weak internal remainder is not drafted from
    assert _gate(outcome)["decision"] == "decisive_held_back"
    reasons = {e.document_id: e.not_used_reason for e in outcome.answer.evidence}
    assert reasons[org.documents["acme-survey"]] == "egress_policy"
    assert reasons[org.documents["acme-plan"]] == "relevance_floor"
    assert any(n.code == "egress_excluded" for n in outcome.answer.notices)


async def test_a_paraphrase_needs_a_calibrated_semantic_floor(app_db: Database, org: Org) -> None:
    question = "How long do big customers have to get their money back?"
    without = await run(app_db, org, "maya", question, Harness())
    assert without.answer.status == "insufficient_evidence"  # no shared specific word
    floor = GateConfig(semantic_floor={FAKE.model: 0.05})
    harness = Harness()
    with_floor = await answer_question(
        app_db,
        organization_id=org.id,
        membership_id=org.members["maya"],
        request=AskRequest(question=question),
        deps=deps(harness),
        gate_config=floor,
        now=NOW,
    )
    assert with_floor.answer.status == "answered"
    refund = next(e for e in with_floor.answer.evidence if e.document_id == org.documents["refund"])
    codes = {i["chunk_id"]: i["code"] for i in _gate(with_floor)["items"]}
    assert codes[str(refund.evidence_id)] == "semantic"
    assert refund.used_for_answer


async def test_the_trace_holds_numbers_and_codes_not_the_askers_words(
    app_db: Database, org: Org
) -> None:
    outcome = await run(
        app_db, org, "maya", "What is Acme Corp's quinquennial NPS score?", Harness()
    )
    stored = json.dumps(_gate(outcome))
    for word in ("quinquenni", "nps", "score", "acm"):
        assert word not in stored.lower()
    assert _gate(outcome)["question_terms"] == 3


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        # Visible account named, question framing and a hyphenated compound dropped.
        ("maya", ("high", "prioriti", "ticket")),
        # Tom may not see Acme: its name is plain text, a content word like any other.
        ("tom", ("acm", "corp", "high", "prioriti", "ticket")),
    ],
)
async def test_content_terms_exclude_visible_accounts_framing_and_compounds(
    app_db: Database, org: Org, member: str, expected: tuple[str, ...]
) -> None:
    await org.document("triage", "Ticket triage", ["High-priority tickets get an owner in 4h."])
    result = await retrieve(
        app_db,
        organization_id=org.id,
        membership_id=org.members[member],
        query=RetrievalQuery(text="Summarize Acme Corp's latest high-priority tickets"),
        provider=None,
        now=NOW,
    )
    assert result.question_terms == expected
