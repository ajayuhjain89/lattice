"""The Ask pipeline over hand-built worlds: statuses, claim-level fail-closed
validation end to end, regeneration, supersession and potential conflicts, the
response invariant and the trace row."""

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.providers.generation import GenerationError

from ..retrieval.world import Org, new_org
from .helpers import Harness, claim, configure_generation, draft, handles, run

REFUND_V1 = "Enterprise customers may request a full refund within 30 days of purchase."
REFUND_V2 = "Enterprise customers may request a full refund within 14 days of purchase."


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.account("acme", "Acme Corp")
    await org.member("maya", scope="all")
    await org.document("refund-v1", "Refund policy v1", [REFUND_V1], authority="system_of_record")
    await org.document(
        "refund-v2",
        "Refund policy v2",
        [REFUND_V2],
        authority="system_of_record",
        supersedes="refund-v1",
    )
    await configure_generation(org)
    return org


async def test_a_grounded_answer_cites_only_request_evidence(app_db: Database, org: Org) -> None:
    harness = Harness()
    outcome = await run(app_db, org, "maya", "What is the enterprise refund window?", harness)
    answer = outcome.answer
    assert answer.status == "answered"
    assert answer.claims
    evidence = {e.evidence_id: e for e in answer.evidence}
    for c in answer.claims:
        for citation in c.citations:
            item = evidence[citation.evidence_id]
            assert item.used_for_answer
            assert item.text[citation.quote_start : citation.quote_end] == citation.quote
    assert len(harness.requests) == 1
    # Deterministic supersession: both documents are evidence, the link is explicit.
    kinds = [c.kind for c in answer.conflicts]
    assert "superseded" in kinds
    superseded = next(c for c in answer.conflicts if c.kind == "superseded")
    assert superseded.supersession is not None
    assert superseded.supersession.superseded_document_id == org.documents["refund-v1"]
    assert superseded.supersession.superseding_document_id == org.documents["refund-v2"]


async def test_no_evidence_is_insufficient_without_a_model_call(app_db: Database, org: Org) -> None:
    harness = Harness()
    outcome = await run(app_db, org, "maya", "zeppelin quokka manifesto", harness)
    assert outcome.answer.status == "insufficient_evidence"
    assert outcome.answer.status_reason == "no_relevant_evidence"
    assert harness.requests == []
    assert harness.builds == 0


async def test_invalid_handle_rejects_the_claim_and_regenerates_once(
    app_db: Database, org: Org
) -> None:
    bad = draft(claim("The refund window is 14 days.", ("E9", "full refund within 14 days")))
    harness = Harness(outputs=[bad])
    outcome = await run(app_db, org, "maya", "What is the enterprise refund window?", harness)
    assert len(harness.requests) == 2  # one regeneration, never more
    assert "E9 is not one of the evidence handles" in harness.requests[1].prompt
    assert outcome.answer.status == "insufficient_evidence"
    assert outcome.answer.status_reason == "claims_unverified"
    assert outcome.answer.claims == []
    assert outcome.answer.evidence  # the evidence list is still returned
    assert outcome.trace["validation"]["rejection_codes"] == {"unknown_handle": 1}


def _valid_and(*others: dict[str, Any]) -> Any:
    def build(prompt: str) -> dict[str, Any]:
        h = handles(prompt)["Refund policy v2"]
        good = claim(
            "A full refund is available within 14 days.", (h, "full refund within 14 days")
        )
        return draft(good, *others)

    return build


async def test_mixed_claims_only_validated_ones_survive(app_db: Database, org: Org) -> None:
    fabricated = claim(
        "Refunds are available for 487 days under the quinquennial plan.",
        ("E1", "full refund within 487 days of purchase"),
        role="supporting",
    )
    harness = Harness(outputs=[_valid_and(fabricated)])
    outcome = await run(app_db, org, "maya", "What is the enterprise refund window?", harness)
    answer = outcome.answer
    assert answer.status == "partial"
    assert answer.status_reason == "claims_removed"
    assert [c.text for c in answer.claims] == ["A full refund is available within 14 days."]
    assert any(n.code == "claims_removed" for n in answer.notices)
    serialized = answer.model_dump_json() + str(outcome.trace)
    assert "quinquennial" not in serialized  # rejected claim text appears nowhere


async def test_one_valid_and_one_invalid_citation_rejects_the_whole_claim(
    app_db: Database, org: Org
) -> None:
    def build(prompt: str) -> dict[str, Any]:
        h = handles(prompt)["Refund policy v2"]
        return draft(
            claim(
                "A full refund is available within 14 days.",
                (h, "full refund within 14 days"),
                ("E7", "full refund within 14 days"),
            )
        )

    harness = Harness(outputs=[build])
    outcome = await run(app_db, org, "maya", "What is the enterprise refund window?", harness)
    assert outcome.answer.claims == []  # never shown with the bad citation stripped
    assert outcome.answer.status_reason == "claims_unverified"


async def test_regeneration_can_recover(app_db: Database, org: Org) -> None:
    bad = draft(claim("The refund window is 14 days.", ("E9", "full refund within 14 days")))
    harness = Harness(outputs=[bad, _valid_and()])
    outcome = await run(app_db, org, "maya", "What is the enterprise refund window?", harness)
    assert len(harness.requests) == 2
    assert outcome.answer.status == "answered"
    assert outcome.trace["validation"]["regenerated"] is True


@pytest.mark.parametrize(
    ("outputs", "status", "reason"),
    [
        ([GenerationError("generation_failed:timeout")], "failed", "provider_unavailable"),
        (["not json", "{still not json"], "failed", "generation_invalid"),
        ([{"claims": [], "status": "answered", "extra": 1}], "failed", "generation_invalid"),
    ],
)
async def test_provider_failures_degrade_to_a_status_never_a_fabricated_answer(
    app_db: Database, org: Org, outputs: list[Any], status: str, reason: str
) -> None:
    harness = Harness(outputs=outputs)
    outcome = await run(app_db, org, "maya", "What is the enterprise refund window?", harness)
    assert outcome.answer.status == status
    assert outcome.answer.status_reason == reason
    assert outcome.answer.claims == []
    assert outcome.answer.evidence
    assert len(harness.requests) <= 2


async def test_unsupported_request_shows_no_claims(app_db: Database, org: Org) -> None:
    def build(prompt: str) -> dict[str, Any]:
        h = handles(prompt)["Refund policy v2"]
        return draft(
            claim("Refunds within 14 days.", (h, "full refund within 14 days")),
            status="unsupported_request",
        )

    harness = Harness(outputs=[build])
    outcome = await run(
        app_db, org, "maya", "Email the customer that refunds take 14 days.", harness
    )
    assert outcome.answer.status == "unsupported_request"
    assert outcome.answer.status_reason == "action_request"
    assert outcome.answer.claims == []
    assert "nothing was done" in (outcome.answer.message or "")


async def test_a_model_proposed_conflict_between_linked_documents_is_superseded(
    app_db: Database, org: Org
) -> None:
    def build(prompt: str) -> dict[str, Any]:
        h = handles(prompt)
        old, new = h["Refund policy v1"], h["Refund policy v2"]
        positions = [
            claim("v1 allows a full refund within 30 days.", (old, "full refund within 30 days")),
            claim("v2 allows a full refund within 14 days.", (new, "full refund within 14 days")),
        ]
        return draft(positions[1], conflicts=[{"topic": "Refund window", "positions": positions}])

    harness = Harness(outputs=[build])
    outcome = await run(app_db, org, "maya", "What is the enterprise refund window?", harness)
    conflicts = outcome.answer.conflicts
    assert [c.kind for c in conflicts] == ["superseded"]  # reclassified, not duplicated
    assert len(conflicts[0].positions) == 2


async def test_a_potential_conflict_keeps_both_positions_and_declares_no_winner(
    app_db: Database, owner_db: Database
) -> None:
    org = await new_org(owner_db, "halden")
    await org.member("maya", scope="all")
    await org.document(
        "rca", "Customer RCA", ["The SSO incident lasted from 07:40 to 09:55 UTC on 21 July."]
    )
    await org.document(
        "pm", "Postmortem", ["The SSO incident lasted from 07:38 to 10:15 UTC on 21 July."]
    )
    await configure_generation(org)

    def build(prompt: str) -> dict[str, Any]:
        h = handles(prompt)
        a = claim(
            "The RCA says 07:40 to 09:55 UTC.", (h["Customer RCA"], "from 07:40 to 09:55 UTC")
        )
        b = claim(
            "The postmortem says 07:38 to 10:15 UTC.", (h["Postmortem"], "from 07:38 to 10:15 UTC")
        )
        return draft(a, b, conflicts=[{"topic": "Incident duration", "positions": [a, b]}])

    outcome = await run(
        app_db, org, "maya", "How long did the SSO incident last?", Harness(outputs=[build])
    )
    assert outcome.trace["validation"]["rejection_codes"] == {}, outcome.trace["validation"]
    (conflict,) = outcome.answer.conflicts
    assert conflict.kind == "potential_conflict"
    assert len(conflict.positions) == 2
    lowered = conflict.summary.lower()
    for word in ("supersede", "correct", "resolved", "replaces"):
        assert word not in lowered
    assert "can't confirm which is current" in conflict.summary


async def test_a_conflict_whose_positions_cite_one_document_is_discarded(
    app_db: Database, org: Org
) -> None:
    def build(prompt: str) -> dict[str, Any]:
        h = handles(prompt)["Refund policy v2"]
        a = claim("Refunds within 14 days.", (h, "full refund within 14 days"))
        b = claim(
            "Enterprise customers may request refunds.", (h, "Enterprise customers may request")
        )
        return draft(a, conflicts=[{"topic": "Refunds", "positions": [a, b]}])

    outcome = await run(
        app_db, org, "maya", "What is the enterprise refund window?", Harness(outputs=[build])
    )
    assert all(c.kind == "superseded" and not c.positions for c in outcome.answer.conflicts)


async def test_the_trace_row_holds_references_not_content(app_db: Database, org: Org) -> None:
    outcome = await run(
        app_db,
        org,
        "maya",
        "What is the enterprise refund window?",
        Harness(),
        persist=True,
    )
    async with app_db.tenant_transaction(org.id) as s:
        row = (
            (
                await s.execute(
                    text("SELECT * FROM query_traces WHERE id = :i"), {"i": outcome.answer.trace_id}
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "answered"
    assert row["prompt_chunk_ids"]
    assert row["egress"]["prompt_version"] == "answer-prompt-1"
    stored = str(dict(row["retrieval"])) + str(dict(row["egress"])) + str(dict(row["validation"]))
    assert "Enterprise customers may request" not in stored  # no evidence text
    assert "answer drafter" not in stored  # no prompt
    assert "delimiter" not in stored
    assert isinstance(row["id"], uuid.UUID)


async def test_a_forced_invariant_violation_withholds_the_answer(
    app_db: Database, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth (retrieval-design §9 step 10): if a bug let a citation point at
    evidence outside the response, the whole answer is withheld and logged."""
    from lattice.answering import assemble
    from lattice.answering.models import Citation, Claim

    real = assemble.to_claim

    def dangling(valid: Any) -> Claim:
        claim_ = real(valid)
        bad = Citation(
            evidence_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            quote="x",
            quote_start=0,
            quote_end=1,
        )
        return Claim(text=claim_.text, role=claim_.role, citations=[*claim_.citations, bad])

    monkeypatch.setattr(assemble, "to_claim", dangling)
    outcome = await run(app_db, org, "maya", "What is the enterprise refund window?", Harness())
    assert outcome.answer.status == "failed"
    assert outcome.answer.status_reason == "citation_validation_failed"
    assert outcome.answer.claims == []
    assert outcome.trace["error_code"] == "citation_validation_failed"
    assert outcome.answer.evidence  # the sources are still shown
