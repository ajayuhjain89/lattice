"""rerank_v2, record-aware ranking (phase-13-followups §17, F5.2): complementary coverage,
the soft lift, record-first interleaving and within-record order. No database; the
coverage statement is in the SQL-structure and capture suites."""

import uuid

import pytest

from lattice.evals.retrieval import config_overrides
from lattice.retrieval.models import FusedCandidate, RankedCandidate, RetrievalConfig
from lattice.retrieval.records import record_gains, rerank_records

from .test_fusion_rerank import ORG, candidate

QUESTION = ("refund", "window", "acme", "renewal")


def _pool(*members: tuple[str, str, float]) -> list[RankedCandidate]:
    """``(record, chunk, rerank_v1 score)`` in rerank_v1 order."""
    records: dict[str, uuid.UUID] = {}
    out = []
    for position, (record, chunk, score) in enumerate(members, start=1):
        c = candidate(
            uuid.uuid5(ORG, chunk),
            "lexical",
            position,
            document=records.setdefault(record, uuid.uuid5(ORG, record)),
        )
        fused = FusedCandidate(c, {"lexical": position}, {"lexical": score}, score, position)
        out.append(RankedCandidate(fused, {}, score, position))
    return out


def _ids(ranked: list[RankedCandidate]) -> list[str]:
    names = {uuid.uuid5(ORG, n): n for n in ["a1", "a2", "a3", "b1", "b2", "c1", "c2", "d1"]}
    return [names[r.fused.candidate.chunk_id] for r in ranked]


def _matched(**lexemes: str) -> dict[uuid.UUID, frozenset[str]]:
    return {uuid.uuid5(ORG, chunk): frozenset(words.split()) for chunk, words in lexemes.items()}


def _config(**kw: object) -> RetrievalConfig:
    return RetrievalConfig(rerank_version="rerank_v2", **kw)  # type: ignore[arg-type]


def test_complementary_coverage_is_what_a_records_other_chunks_add() -> None:
    pool = _pool(("a", "a1", 1.0), ("b", "b1", 0.9), ("a", "a2", 0.5), ("c", "c1", 0.4))
    matched = _matched(a1="refund", a2="window acme", b1="refund window acme", c1="renewal")
    gains = record_gains(pool, matched, QUESTION)
    by_record = {_ids([r])[0][0]: gains[(ORG, r.fused.candidate.document_id)] for r in pool}
    assert by_record == {"a": 0.5, "b": 0.0, "c": 0.0}  # one pooled chunk adds nothing
    # A question with fewer than two content lexemes gives no record any lift.
    assert set(record_gains(pool, matched, ("refund",)).values()) == {0.0}


def test_the_soft_lift_reorders_records_and_keeps_each_records_own_order() -> None:
    pool = _pool(("b", "b1", 1.0), ("a", "a1", 0.95), ("c", "c1", 0.9), ("a", "a2", 0.5))
    matched = _matched(b1="refund window", a1="refund", c1="refund", a2="window acme renewal")
    ranked = rerank_records(pool, matched, QUESTION, _config(record_mode="soft", record_weight=0.3))
    # a gains 3/4: 0.95 * 1.225 > 1.0; a2 rises with it but stays behind a1.
    assert _ids(ranked) == ["a1", "b1", "c1", "a2"]
    assert [r.final_rank for r in ranked] == [1, 2, 3, 4]
    assert ranked[0].features["record_gain"] == 0.75
    assert sorted(_ids(ranked)) == sorted(_ids(pool))  # reorders; adds and removes nothing


def test_the_lift_is_bounded() -> None:
    pool = _pool(("b", "b1", 1.0), ("a", "a1", 0.6), ("a", "a2", 0.1))
    matched = _matched(b1="refund", a1="refund", a2="window acme renewal")
    ranked = rerank_records(pool, matched, QUESTION, _config(record_mode="soft", record_weight=0.5))
    assert _ids(ranked)[0] == "b1"  # 0.6 * 1.375 < 1.0: a record is lifted at most 1.5 times
    with pytest.raises(ValueError, match="record_weight"):
        RetrievalConfig(record_weight=0.6)


def test_interleave_takes_each_records_chunks_in_turns() -> None:
    pool = _pool(
        ("a", "a1", 1.0), ("a", "a2", 0.9), ("a", "a3", 0.8), ("b", "b1", 0.7), ("c", "c1", 0.6)
    )
    ranked = rerank_records(pool, {}, QUESTION, _config(record_mode="interleave"))
    assert _ids(ranked) == ["a1", "b1", "c1", "a2", "a3"]


def test_within_record_order_moves_a_chunk_that_adds_coverage_only_inside_its_record() -> None:
    pool = _pool(
        ("a", "a1", 1.0), ("a", "a2", 0.9), ("b", "b1", 0.85), ("a", "a3", 0.8), ("c", "c1", 0.7)
    )
    matched = _matched(a1="refund window", a2="refund", b1="acme", a3="renewal", c1="refund")
    ranked = rerank_records(pool, matched, QUESTION, _config(record_within=True))
    # a3 adds "renewal", a2 adds nothing: they swap, in a's own positions (2 and 4).
    assert _ids(ranked) == ["a1", "a3", "b1", "a2", "c1"]


def test_the_best_chunk_stays_first_even_without_lexemes() -> None:
    pool = _pool(("a", "a1", 1.0), ("a", "a2", 0.9))
    ranked = rerank_records(
        pool, _matched(a2="refund window"), QUESTION, _config(record_within=True)
    )
    assert _ids(ranked) == ["a1", "a2"]


def test_record_tunables_name_rerank_v2_in_the_evaluation_only() -> None:
    assert RetrievalConfig().record_aware is False
    assert RetrievalConfig().rerank_version == "rerank_v1"
    soft = config_overrides(["record_mode=soft", "record_weight=0.15"])
    assert (soft.record_mode, soft.record_weight, soft.rerank_version) == (
        "soft",
        0.15,
        "rerank_v2",
    )
    assert soft.version == "retrieval-6+record_mode=soft,record_weight=0.15"
    assert config_overrides(["record_within=true"]).rerank_version == "rerank_v2"
    assert config_overrides(["record_weight=0.5"]).rerank_version == "rerank_v1"  # still off
    with pytest.raises(ValueError, match="record_mode"):
        RetrievalConfig(record_mode="strict")  # type: ignore[arg-type]
