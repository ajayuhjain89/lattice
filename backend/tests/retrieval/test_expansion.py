"""The second hop, ``expansion_v1`` (P13.6, D-40): an evaluation tunable, off in the
product (evaluation-plan §23 -- no variant improved dev).

Its security does not depend on whether it is adopted: every branch carries the
authorization predicate for the chunk it returns and for the seed it reads (S1); an
identifier planted in a readable chunk reaches only records the reader may read (S9), so
an unreadable record changes nothing (S4); the candidates pass E4 and the evaluation's
per-stage checks like every channel; the trace holds counts and IDs only (S10).
"""

import uuid

import pytest

from lattice.db.engine import Database
from lattice.retrieval import DEFAULT_CONFIG, RetrievalConfig
from lattice.retrieval.expansion import SEED_DOCUMENT, expansion_branches, expansion_statement
from lattice.retrieval.permitted import ResolvedScope

from .test_sql_structure import _ctx, predicate_sql, sql, where_clause
from .world import Org, new_org, search, stage_documents

EXPAND = RetrievalConfig(expansion=True, rrf_weight_expansion=0.75)


@pytest.fixture
async def halden(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.department("leadership")
    await org.member("maya")
    await org.member("grace", clearance="confidential", departments=["leadership"])
    await org.document(
        "HAL-100",
        "Refund dispute",
        [
            "The customer disputes the refund window; see HAL-200 and HAL-300.",
            "Agent note on the earlier escalation.",
            "Closing comment with no matching words at all.",
        ],
        kind="ticket",
        display_ref="HAL-100",
    )
    await org.document(
        "HAL-300",
        "Escalation",
        ["Escalation handled by support."],
        kind="ticket",
        display_ref="HAL-300",
    )
    return org


def test_the_product_default_does_not_expand() -> None:
    assert not DEFAULT_CONFIG.expansion
    with pytest.raises(ValueError, match="k_expand"):
        RetrievalConfig(k_expand=0)


@pytest.mark.parametrize("scope", ["assigned", "all"])
def test_every_branch_carries_the_predicate_for_the_chunk_and_the_seed(scope: str) -> None:
    ctx = _ctx(scope)
    branches = expansion_branches(
        ctx, ResolvedScope(), [uuid.uuid4()], "refund window", "x", ["HAL-1"], EXPAND
    )
    assert len(branches) == 3
    for branch in branches:
        where = where_clause(branch)
        compiled = sql(branch)
        assert predicate_sql(ctx) in compiled
        assert predicate_sql(ctx, SEED_DOCUMENT) in compiled
        for condition in (
            "chunks.organization_id = ?",
            "documents.organization_id = ?",
            "documents.status = ?",
            "seed_chunk.organization_id = ?",
            "seed_document.organization_id = ?",
            "seed_document.status = ?",
        ):
            assert condition in where, condition
    statement = expansion_statement(ctx, ResolvedScope(), [uuid.uuid4()], "q", "x", [], EXPAND)
    assert "LIMIT" in sql(statement)


async def test_a_planted_identifier_reaches_only_readable_records(
    app_db: Database, halden: Org
) -> None:
    # HAL-100's opening names HAL-200 (confidential) and HAL-300 (readable).
    await halden.document(
        "HAL-200",
        "Board escalation",
        ["Board-only escalation notes."],
        kind="ticket",
        display_ref="HAL-200",
        sensitivity="confidential",
        departments=["leadership"],
    )
    question = "Why does the customer dispute the refund window?"
    maya = await search(app_db, halden, "maya", question, provider=None, config=EXPAND)
    reached = stage_documents(halden, maya, "expansion")
    assert "HAL-300" in reached
    for stage in ("exact", "lexical", "expansion", "fused"):
        assert "HAL-200" not in stage_documents(halden, maya, stage), stage
    assert maya.trace.counts["expansion"] == len(maya.trace.stages["expansion"])
    grace = await search(app_db, halden, "grace", question, provider=None, config=EXPAND)
    assert {"HAL-200", "HAL-300"} <= stage_documents(halden, grace, "expansion")


async def test_an_unreadable_record_changes_nothing(app_db: Database, halden: Org) -> None:
    question = "Why does the customer dispute the refund window?"
    before = await search(app_db, halden, "maya", question, provider=None, config=EXPAND)
    await halden.document(
        "HAL-200",
        "Board escalation",
        ["Board-only refund window dispute notes, refund window, renewal."],
        kind="ticket",
        display_ref="HAL-200",
        sensitivity="confidential",
        departments=["leadership"],
    )
    after = await search(app_db, halden, "maya", question, provider=None, config=EXPAND)
    assert [e.chunk_id for e in after.evidence] == [e.chunk_id for e in before.evidence]
    assert after.trace.stages["expansion"] == before.trace.stages["expansion"]


async def test_the_hop_adds_only_what_the_first_pass_did_not_find(
    app_db: Database, halden: Org
) -> None:
    question = "Why does the customer dispute the refund window?"
    result = await search(app_db, halden, "grace", question, provider=None, config=EXPAND)
    first = {c.chunk_id for s in ("exact", "lexical") for c in result.trace.stages[s]}
    assert first.isdisjoint({c.chunk_id for c in result.trace.stages["expansion"]})
    plain = await search(app_db, halden, "grace", question, provider=None)
    assert "expansion" not in plain.trace.stages
