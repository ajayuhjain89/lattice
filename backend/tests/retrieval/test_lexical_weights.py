"""Weighted lexical ranking (P13.4, G5): evaluation tunables, off by default.

The weights come from the question's statistics over the reader's *permitted* chunks, so
an unreadable document can change neither a weight nor an order (S4), and the weighted
statement keeps every security condition of the lexical channel (S1). The product
default (``retrieval-6``) does not use them: P13.4 found no setting that improved the
held-out split (evaluation-plan §18).
"""

import pytest

from lattice.authz import resolve_authorization_context
from lattice.db.engine import Database
from lattice.evals.retrieval import config_overrides
from lattice.retrieval import DEFAULT_CONFIG, RetrievalConfig
from lattice.retrieval.lexical import QUESTION_FRAME_WORDS
from lattice.retrieval.terms import QuestionStatistics, lexical_weights, question_statistics

from .world import Org, documents_in, new_org, search, stage_documents

WEIGHTED = RetrievalConfig(
    lexical_term_weights="idf",
    lexical_counted_terms="content",
    lexical_title_factor=0.5,
    lexical_account_factor=0.5,
)


@pytest.fixture
async def halden(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.department("leadership")
    await org.account("acme-corp", "Acme Corp", ["Acme"])
    await org.member("maya", scope="assigned", accounts=["acme-corp"])
    await org.member("grace", clearance="confidential", departments=["leadership"])
    return org


async def _statistics(app: Database, org: Org, member: str, question: str) -> QuestionStatistics:
    async with app.tenant_snapshot(org.id) as session:
        ctx = await resolve_authorization_context(
            session, organization_id=org.id, membership_id=org.members[member]
        )
        return await question_statistics(
            session, ctx, question, QUESTION_FRAME_WORDS, "english", []
        )


def test_the_product_default_does_not_weigh() -> None:
    assert not DEFAULT_CONFIG.lexical_weighted
    assert WEIGHTED.lexical_weighted
    assert not RetrievalConfig(lexical_mode="any_term", lexical_term_weights="idf").lexical_weighted
    with pytest.raises(ValueError, match="lexical_title_factor"):
        RetrievalConfig(lexical_title_factor=1.5)
    with pytest.raises(ValueError, match="lexical_account_factor"):
        RetrievalConfig(lexical_account_factor=-0.5)


async def test_weighted_lexical_never_returns_the_stronger_unauthorized_match(
    app_db: Database, halden: Org
) -> None:
    await halden.document(
        "acme-plan", "Acme Corp Q3 success plan", ["SCIM is planned for Q3."], account="acme-corp"
    )
    await halden.document(
        "exec-notes",
        "Acme executive escalation notes: SCIM commitments",
        ["SCIM commitments to Acme: GA 8 December, a credit if GA slips. SCIM commitments."],
        sensitivity="confidential",
        account="acme-corp",
        departments=["leadership"],
    )
    question = "What SCIM commitments did we make to Acme?"
    result = await search(app_db, halden, "maya", question, config=WEIGHTED)
    for stage in ("exact", "lexical", "semantic", "fused"):
        assert "exec-notes" not in stage_documents(halden, result, stage), stage
    assert documents_in(halden, result) == ["acme-plan"]
    grace = await search(app_db, halden, "grace", question, config=WEIGHTED)
    assert documents_in(halden, grace)[0] == "exec-notes"


async def test_statistics_count_permitted_chunks_only(app_db: Database, halden: Org) -> None:
    await halden.document("plan", "Plan", ["SCIM is planned for Q3."], account="acme-corp")
    question = "When is the SCIM offsite?"
    before = await _statistics(app_db, halden, "maya", question)
    await halden.document(
        "exec-offsite",
        "Offsite",
        ["SCIM offsite offsite offsite."],
        sensitivity="confidential",
        departments=["leadership"],
    )
    assert await _statistics(app_db, halden, "maya", question) == before
    assert before.document_frequency["offsit"] == 0
    grace = await _statistics(app_db, halden, "grace", question)
    assert grace.document_frequency["offsit"] == 1
    assert grace.chunks == before.chunks + 1


async def test_content_lexemes_leave_out_framing_words(app_db: Database, halden: Org) -> None:
    await halden.document("plan", "Plan", ["SCIM is planned for Q3."])
    stats = await _statistics(app_db, halden, "grace", "Tell me the latest on SCIM")
    assert "scim" in stats.content
    assert {"tell", "latest"} <= set(stats.document_frequency) - stats.content
    weights = lexical_weights(stats, RetrievalConfig(lexical_counted_terms="content"))
    assert set(weights.lexemes) == {"scim"}
    every = lexical_weights(stats, RetrievalConfig(lexical_title_factor=0.5))
    assert set(every.lexemes) == set(stats.document_frequency)
    assert set(every.lexemes.values()) == {1.0}


async def test_a_rare_word_outranks_a_common_one(app_db: Database, halden: Org) -> None:
    for i in range(4):
        await halden.document(f"renewal-{i}", f"Renewal {i}", [f"The renewal of account {i}."])
    await halden.document("freeze", "Freeze", ["The code freeze starts in December."])
    question = "When does the renewal freeze start?"
    counted = await search(app_db, halden, "grace", question, provider=None)
    weighted = await search(
        app_db,
        halden,
        "grace",
        question,
        provider=None,
        config=RetrievalConfig(lexical_term_weights="idf"),
    )
    assert documents_in(halden, weighted)[0] == "freeze"
    # Both "renewal" chunks and "freeze" match two lexemes when every lexeme counts one.
    assert "freeze" in documents_in(halden, counted)


async def test_a_title_only_match_counts_less_than_the_text(app_db: Database, halden: Org) -> None:
    await halden.document("titled", "Refund window", ["Customers may ask within a period."])
    await halden.document("text", "Policy", ["The refund window is 14 days."])
    question = "What is the refund window?"
    full = await search(app_db, halden, "grace", question, provider=None, config=RetrievalConfig())
    assert set(documents_in(halden, full)) == {"titled", "text"}
    half = await search(
        app_db,
        halden,
        "grace",
        question,
        provider=None,
        config=RetrievalConfig(lexical_title_factor=0.5),
    )
    assert documents_in(halden, half) == ["text", "titled"]


def test_config_overrides_name_the_run() -> None:
    config = config_overrides(["lexical_account_factor=0.5", "lexical_counted_terms=content"])
    assert config.lexical_account_factor == 0.5
    assert config.lexical_counted_terms == "content"
    assert config.version == (
        f"{DEFAULT_CONFIG.version}+lexical_account_factor=0.5,lexical_counted_terms=content"
    )
    assert config_overrides([]) is DEFAULT_CONFIG
    for bad in ("version=x", "nonsense=1", "lexical_title_factor"):
        with pytest.raises(ValueError, match="not a retrieval tunable"):
            config_overrides([bad])
    with pytest.raises(ValueError, match="lexical_title_factor"):
        config_overrides(["lexical_title_factor=2"])
