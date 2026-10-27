"""RRF fusion, deduplication, rerank_v1 and evidence selection (no database)."""

import random
import uuid
from datetime import UTC, datetime, timedelta

from lattice.retrieval.fusion import fuse
from lattice.retrieval.models import Candidate, Channel, RetrievalConfig
from lattice.retrieval.rerank import AUTHORITY_WEIGHT, features, rerank, select_evidence

ORG = uuid.uuid4()
NOW = datetime(2026, 9, 27, tzinfo=UTC)
CONFIG = RetrievalConfig()


def candidate(
    chunk: uuid.UUID,
    channel: Channel,
    rank: int,
    *,
    org: uuid.UUID = ORG,
    document: uuid.UUID | None = None,
    authority: str = "working",
    age_days: float = 10,
    account: uuid.UUID | None = None,
    text: bytes | None = None,
    key: str = "doc",
) -> Candidate:
    return Candidate(
        organization_id=org,
        document_id=document or chunk,
        chunk_id=chunk,
        channel=channel,
        rank=rank,
        score=1.0 / rank,
        document_kind="doc",
        authority=authority,
        occurred_at=NOW - timedelta(days=age_days),
        content_hash=text or chunk.bytes,
        visible_account_id=account,
        sort_key=(key, 0),
    )


def test_channel_weights_scale_each_contribution() -> None:
    # P13.5 evaluation tunables: absent or 1.0, fusion is the plain RRF above.
    lexical, semantic = uuid.uuid4(), uuid.uuid4()
    lists = [[candidate(lexical, "lexical", 2)], [candidate(semantic, "semantic", 1)]]
    plain = fuse(lists, k=60, pool=40)
    assert [f.candidate.chunk_id for f in plain] == [semantic, lexical]
    assert fuse(lists, k=60, pool=40, weights=CONFIG.rrf_weights) == plain
    weighted = fuse(lists, k=60, pool=40, weights={"lexical": 1.5})
    assert [f.candidate.chunk_id for f in weighted] == [lexical, semantic]
    assert weighted[0].rrf_score == 1.5 / 62
    assert CONFIG.rrf_weights == {"exact": 1.0, "lexical": 1.0, "semantic": 1.0}


def test_rrf_uses_k_60_over_every_channel_that_found_a_chunk() -> None:
    a, b, c = (uuid.uuid4() for _ in range(3))
    fused = fuse(
        [
            [candidate(a, "exact", 1)],
            [candidate(b, "lexical", 1), candidate(a, "lexical", 2)],
            [candidate(c, "semantic", 1), candidate(a, "semantic", 3)],
        ],
        k=60,
        pool=40,
    )
    by_chunk = {f.candidate.chunk_id: f for f in fused}
    assert CONFIG.rrf_k == 60
    assert by_chunk[a].rrf_score == 1 / 61 + 1 / 62 + 1 / 63
    assert by_chunk[a].matched_by == ("exact", "lexical", "semantic")
    assert dict(by_chunk[a].ranks) == {"exact": 1, "lexical": 2, "semantic": 3}
    assert by_chunk[b].rrf_score == by_chunk[c].rrf_score == 1 / 61
    assert fused[0].candidate.chunk_id == a  # found by all three
    assert len(fused) == 3  # deduplicated: one entry per chunk


def test_fusion_pairs_exact_lexical_and_lexical_semantic() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    fused = fuse(
        [
            [candidate(a, "exact", 1)],
            [candidate(a, "lexical", 1), candidate(b, "lexical", 2)],
            [candidate(b, "semantic", 1)],
        ],
        k=60,
        pool=40,
    )
    assert {f.candidate.chunk_id: f.matched_by for f in fused} == {
        a: ("exact", "lexical"),
        b: ("lexical", "semantic"),
    }


def test_fusion_never_merges_two_organizations_rows() -> None:
    shared = uuid.uuid4()
    other = uuid.uuid4()
    fused = fuse(
        [[candidate(shared, "lexical", 1)], [candidate(shared, "semantic", 1, org=other)]],
        k=60,
        pool=40,
    )
    assert sorted((f.candidate.organization_id, f.matched_by) for f in fused) == sorted(
        [(ORG, ("lexical",)), (other, ("semantic",))]
    )


def test_fusion_order_is_deterministic_and_independent_of_input_order() -> None:
    chunks = [uuid.uuid4() for _ in range(30)]
    lexical = [candidate(c, "lexical", i + 1, key=f"k{i % 3}") for i, c in enumerate(chunks)]
    semantic = [candidate(c, "semantic", 30 - i, key=f"k{i % 3}") for i, c in enumerate(chunks)]
    expected = [f.candidate.chunk_id for f in fuse([lexical, semantic], k=60, pool=40)]
    rng = random.Random(7)  # noqa: S311 - deterministic shuffles, not security
    for _ in range(20):
        shuffled_l, shuffled_s = lexical[:], semantic[:]
        rng.shuffle(shuffled_l)
        rng.shuffle(shuffled_s)
        order = [f.candidate.chunk_id for f in fuse([shuffled_s, shuffled_l], k=60, pool=40)]
        assert order == expected


def test_fusion_keeps_the_top_pool_only() -> None:
    lexical = [candidate(uuid.uuid4(), "lexical", i + 1) for i in range(50)]
    assert len(fuse([lexical], k=60, pool=40)) == 40


def _ranked(cands: list[Candidate], **kw: object) -> list[uuid.UUID]:
    fused = fuse([cands], k=60, pool=40)
    ranked = rerank(
        fused,
        account_targets=kw.get("accounts", set()),  # type: ignore[arg-type]
        temporal_intent=bool(kw.get("temporal", False)),
        now=NOW,
        config=CONFIG,
    )
    return [r.fused.candidate.chunk_id for r in ranked]


def test_exact_identifier_match_outranks_equal_lexical_evidence() -> None:
    ticket, other = uuid.uuid4(), uuid.uuid4()
    fused = fuse([[candidate(ticket, "exact", 1)], [candidate(other, "lexical", 1)]], k=60, pool=40)
    ranked = rerank(fused, account_targets=set(), temporal_intent=False, now=NOW, config=CONFIG)
    assert ranked[0].fused.candidate.chunk_id == ticket
    assert ranked[0].features["identifier_match"] == 1.0


def test_recency_applies_only_with_temporal_intent_and_never_removes_older_evidence() -> None:
    old_decision, new_chat = uuid.uuid4(), uuid.uuid4()
    cands = [
        candidate(old_decision, "lexical", 1, age_days=200, authority="system_of_record"),
        candidate(new_chat, "lexical", 2, age_days=1, authority="conversational"),
    ]
    assert _ranked(cands) == [old_decision, new_chat]
    with_intent = _ranked(cands, temporal=True)
    assert with_intent == [new_chat, old_decision]  # the freshness signal works...
    assert set(with_intent) == {old_decision, new_chat}  # ...by reordering, never removing


def test_authority_never_removes_contradicting_evidence() -> None:
    record, rumour = uuid.uuid4(), uuid.uuid4()
    cands = [
        candidate(rumour, "lexical", 1, authority="conversational"),
        candidate(record, "lexical", 2, authority="system_of_record"),
    ]
    ranked = _ranked(cands)
    assert ranked == [record, rumour]  # the authority signal works, and keeps both
    assert AUTHORITY_WEIGHT == {"system_of_record": 1.0, "working": 0.5, "conversational": 0.0}


def test_every_factor_is_bounded() -> None:
    c = candidate(uuid.uuid4(), "exact", 1, authority="system_of_record", age_days=0)
    fused = fuse([[c]], k=60, pool=40)[0]
    f = features(fused, account_targets=set(), temporal_intent=True, now=NOW, config=CONFIG)
    f["account_match"] = 1.0
    from lattice.retrieval.rerank import final_score

    assert final_score(1.0, f, CONFIG) <= 1.15 * 1.10 * 1.20 * 1.50 + 1e-9


def test_account_match_uses_the_visible_account_only() -> None:
    acme = uuid.uuid4()
    visible, hidden = uuid.uuid4(), uuid.uuid4()
    fused = fuse(
        [[candidate(visible, "lexical", 2, account=acme), candidate(hidden, "lexical", 1)]],
        k=60,
        pool=40,
    )
    ranked = rerank(fused, account_targets={acme}, temporal_intent=False, now=NOW, config=CONFIG)
    by_chunk = {r.fused.candidate.chunk_id: r.features["account_match"] for r in ranked}
    assert by_chunk == {visible: 1.0, hidden: 0.0}


def test_rerank_is_deterministic_with_ties() -> None:
    cands = [candidate(uuid.uuid4(), "lexical", i + 1, key="same") for i in range(10)]
    first = _ranked(cands)
    assert all(_ranked(list(reversed(cands))) == first for _ in range(5))


def test_selection_drops_duplicate_text_and_defers_beyond_the_per_document_cap() -> None:
    doc = uuid.uuid4()
    same_doc = [candidate(uuid.uuid4(), "lexical", i + 1, document=doc) for i in range(5)]
    dup_text = candidate(uuid.uuid4(), "lexical", 6, text=same_doc[0].content_hash)
    other = candidate(uuid.uuid4(), "lexical", 7)
    fused = fuse([[*same_doc, dup_text, other]], k=60, pool=40)
    ranked = rerank(fused, account_targets=set(), temporal_intent=False, now=NOW, config=CONFIG)
    selected = select_evidence(ranked, limit=10, per_document=3)
    ids = [r.fused.candidate.chunk_id for r in selected]
    assert dup_text.chunk_id not in ids  # identical text kept once
    assert ids[:4] == [*(c.chunk_id for c in same_doc[:3]), other.chunk_id]  # cap first...
    assert ids[4:] == [c.chunk_id for c in same_doc[3:]]  # ...deferred chunks fill after
    assert [
        r.fused.candidate.chunk_id for r in select_evidence(ranked, limit=4, per_document=3)
    ] == ids[:4]
