"""``lattice eval baseline`` / ``lattice eval compare`` (P13.1, phase-13-plan EC-6, §9.1).

Synthetic reports, no database: every verdict of the comparison, the refusal of runs
that are not comparable, the unconditional security blocker, the injected-regression
case, and the rule that a baseline is never overwritten silently. The golden suites
check the same tooling against the real corpus (``test_golden_retrieval.py``).
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from lattice.evals import baseline
from lattice.evals.stats import proportion, wilson

SECURITY = {"unauthorized_candidates": 0, "cross_org_candidates": 0, "canary_hits": 0}
QUESTION = "What did we promise the customer about the private roadmap?"


def _evidence(records: list[str]) -> list[dict[str, Any]]:
    return [
        {"rank": i, "record": r, "anchors": ["1"], "matched_by": ["lexical"], "account": None}
        for i, r in enumerate(records, start=1)
    ]


def _item(item_id: str, category: str, found: dict[str, int | None]) -> dict[str, Any]:
    ranked = ["doc:filler"] * 20
    for label, rank in found.items():
        if rank is not None:
            ranked[rank - 1] = label.split("#")[0]
    return {
        "id": item_id,
        "category": category,
        "persona": "maya",
        "question": QUESTION,
        "error": None,
        "found_at": dict(found),
        "documents_found_at": {k.split("#")[0]: v for k, v in found.items()},
        "misses": [
            {"target": k, "cause": "ranked_below_k"}
            for k, v in found.items()
            if v is None or v > 10
        ],
        "identifier_hit": None,
        "evidence": _evidence(ranked),
    }


def _report(**overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "kind": "retrieval",
        "embedding_provider": None,
        "embedding_model": None,
        "config_version": "retrieval-6",
        "rerank_version": "rerank_v1",
        "limit": 20,
        "corpus_fingerprint": {"sha256": "c" * 64, "chunks": {"halden": 3}},
        "golden_version": {"sha256": "g" * 64, "items": 3},
        "database_platform": "aarch64",
        "summary": {
            "security": dict(SECURITY),
            "errors": {},
            "overall": {},
            "by_category": {},
        },
        "items": [
            _item("lookup-a", "lookup", {"doc:a#1": 1, "doc:b#2": 2}),
            _item("lookup-b", "lookup", {"doc:c#1": 3}),
            _item("stale-c", "stale", {"doc:d#1": 12}),
        ],
    }
    report.update(overrides)
    return report


def _moved(report: dict[str, Any], item_id: str, label: str, rank: int | None) -> dict[str, Any]:
    changed = copy.deepcopy(report)
    for i, item in enumerate(changed["items"]):
        if item["id"] == item_id:
            found = dict(item["found_at"]) | {label: rank}
            changed["items"][i] = _item(item_id, item["category"], found)
    return changed


def test_an_unchanged_run_is_identical() -> None:
    result = baseline.compare(_report(), _report())
    assert result.verdict == "identical"
    assert result.exit_code == 0
    assert result.findings == []


def test_an_injected_regression_under_the_same_configuration_is_unexplained_drift() -> None:
    run = _moved(_report(), "lookup-b", "doc:c#1", 14)
    result = baseline.compare(_report(), run)
    assert result.verdict == "drift"
    assert result.exit_code == 1
    regressions = {(f.item, f.kind) for f in result.regressions}
    assert ("lookup-b", "target_lost@5") in regressions
    assert ("lookup-b", "item_lost_all@10") in regressions
    assert ("category:lookup", "category_regressed") in regressions
    lookup = next(row for row in result.categories if row["slice"] == "lookup")
    assert lookup["baseline"]["n"] == lookup["run"]["n"] == 2
    assert lookup["small_slice"] is True
    assert "REGRESSION target_lost@5: doc:c#1: 3 -> 14" in baseline.render(result)


def test_an_improvement_without_a_declared_change_is_also_drift() -> None:
    run = _moved(_report(), "stale-c", "doc:d#1", 4)
    result = baseline.compare(_report(), run)
    assert result.verdict == "drift"
    assert result.regressions == []
    assert ("stale-c", "target_gained@5") in {(f.item, f.kind) for f in result.findings}


def test_a_declared_change_reports_gains_and_fails_only_on_regressions() -> None:
    declared = {"config_version": "retrieval-7"}
    better = baseline.compare(_report(), _moved(_report(**declared), "stale-c", "doc:d#1", 4))
    assert better.verdict == "changed"
    assert better.exit_code == 0
    assert better.intended == ["config_version: retrieval-6 -> retrieval-7"]
    worse = baseline.compare(_report(), _moved(_report(**declared), "lookup-a", "doc:a#1", 2))
    assert worse.verdict == "regressed"
    assert worse.exit_code == 1
    assert {(f.item, f.kind) for f in worse.regressions} == {
        ("lookup-a", "target_lost@1"),
        ("lookup-a", "document_lost@1"),
    }


def test_a_rank_change_inside_one_bucket_is_visible_but_not_a_regression() -> None:
    run = _moved(_report(**{"config_version": "retrieval-7"}), "lookup-b", "doc:c#1", 4)
    result = baseline.compare(_report(), run)
    assert result.verdict == "changed"
    assert ("lookup-b", "target_rank_changed") in {(f.item, f.kind) for f in result.findings}


def test_any_security_violation_blocks_whatever_else_is_true() -> None:
    leaky = _report()
    leaky["summary"]["security"]["unauthorized_candidates"] = 1
    result = baseline.compare(_report(), leaky)
    assert result.verdict == "security"
    assert result.exit_code == 1
    incomparable = _report(corpus_fingerprint={"sha256": "other"})
    incomparable["summary"]["security"]["canary_hits"] = 2
    assert baseline.compare(_report(), incomparable).verdict == "security"


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"corpus_fingerprint": {"sha256": "d" * 64, "chunks": {"halden": 4}}}, "corpus"),
        ({"golden_version": {"sha256": "h" * 64, "items": 4}}, "golden set"),
        ({"embedding_provider": "fake", "embedding_model": "fake-hashed-bow-v1"}, "configuration"),
    ],
)
def test_runs_that_are_not_comparable_are_refused(override: dict[str, Any], reason: str) -> None:
    result = baseline.compare(_report(), _report(**override))
    assert result.verdict == "incomparable"
    assert result.exit_code == 2
    assert any(r.startswith(reason) for r in result.incomparable), result.incomparable
    assert result.findings == []


def test_a_retrieval_run_is_never_compared_with_an_answer_run() -> None:
    result = baseline.compare(_report(), _answer_report(_answer_run("nps")))
    assert result.verdict == "incomparable"
    assert result.incomparable[0].startswith("kind")


def test_a_golden_change_can_compare_the_common_items() -> None:
    grown = _report(golden_version={"sha256": "h" * 64, "items": 4})
    grown["items"].append(_item("new-item", "lookup", {"doc:e#1": 1}))
    result = baseline.compare(_report(), grown, allow_golden_change=True)
    assert result.verdict == "changed"
    assert result.added == ["new-item"]
    assert result.removed == []
    # ...but an item whose labels did not change must still rank the same.
    moved = _moved(grown, "lookup-b", "doc:c#1", 14)
    assert baseline.compare(_report(), moved, allow_golden_change=True).verdict == "drift"


def test_semantic_runs_are_compared_on_their_own_database_architecture() -> None:
    semantic = {"embedding_provider": "fake", "embedding_model": "fake-hashed-bow-v1"}
    arm = _report(**semantic)
    x86 = _moved(_report(**semantic, database_platform="x86_64"), "lookup-b", "doc:c#1", 2)
    refused = baseline.compare(arm, x86)
    assert refused.verdict == "incomparable"
    assert "database platform" in refused.incomparable[0]
    listed = baseline.compare(arm, x86, cross_platform=True)
    assert listed.verdict == "platform_variant"
    assert listed.exit_code == 0
    assert {f.item for f in listed.findings} == {"lookup-b"}
    # Exact and lexical runs do not depend on the platform.
    assert baseline.compare(_report(), _report(database_platform="x86_64")).verdict == "identical"


def test_selection_outcome_changes_are_reported() -> None:
    run = _report()
    run["items"][2]["misses"] = [{"target": "doc:d#1", "cause": "not_selected"}]
    result = baseline.compare(_report(), run)
    assert ("stale-c", "selection_changed") in {(f.item, f.kind) for f in result.findings}


def test_snapshots_and_comparisons_hold_no_question_text() -> None:
    snap = baseline.snapshot(_report(), label="A")
    result = baseline.compare(_report(), _moved(_report(), "lookup-b", "doc:c#1", 14))
    text = json.dumps(snap) + baseline.render(result) + json.dumps(result.to_json())
    assert QUESTION not in text


def test_a_baseline_is_never_overwritten_silently(tmp_path: Path) -> None:
    path = tmp_path / "retrieval-A.json"
    first = baseline.write_baseline(_report(), path, label="A", reason="P13.1 reference")
    assert baseline.load(path)["items"] == first["items"]
    original = path.read_bytes()
    with pytest.raises(baseline.BaselineError, match="exists"):
        baseline.write_baseline(_report(), path, label="A", reason="again")
    assert path.read_bytes() == original
    with pytest.raises(baseline.BaselineError, match="reason"):
        baseline.write_baseline(_report(), path, label="A", reason=" ", replace=True)
    replaced = baseline.write_baseline(
        _report(), path, label="A", reason="intended change", replace=True
    )
    assert len(replaced["created"]["supersedes"]) == 64
    assert baseline.compare(baseline.load(path), _report()).verdict == "identical"


def test_unsafe_runs_never_become_baselines(tmp_path: Path) -> None:
    leaky = _report()
    leaky["summary"]["security"]["cross_org_candidates"] = 1
    with pytest.raises(baseline.BaselineError, match="security"):
        baseline.write_baseline(leaky, tmp_path / "b.json", label="A", reason="no")
    broken = _report()
    broken["summary"]["errors"] = {"persona_not_seeded": 1}
    with pytest.raises(baseline.BaselineError, match="errors"):
        baseline.write_baseline(broken, tmp_path / "b.json", label="A", reason="no")
    assert not (tmp_path / "b.json").exists()


def _answer_run(item_id: str, **fields: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "id": item_id,
        "egress_config": "default",
        "category": "insufficient_evidence",
        "persona": "maya",
        "question": QUESTION,
        "expected_status": "insufficient_evidence",
        "status": "insufficient_evidence",
        "status_reason": "no_relevant_evidence",
        "error": None,
        "model_called": False,
        "relevant": [],
        "targets_in_prompt": [],
        "targets_cited": [],
    }
    return run | fields


def _answer_report(*runs: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "answer",
        "generation": {"provider": "fake", "model": "fake-extractive-v1"},
        "embedding_provider": None,
        "embedding_model": None,
        "judge": None,
        "corpus_fingerprint": {"sha256": "c" * 64},
        "golden_version": {"sha256": "g" * 64},
        "database_platform": "aarch64",
        "summary": {"security": {"canary_hits": 0}, "errors": {}, "ran": 2, "status_accuracy": 1},
        "items": list(runs),
    }


def test_answer_runs_are_compared_per_run() -> None:
    answerable = {
        "category": "lookup",
        "expected_status": "answered",
        "status": "answered",
        "status_reason": None,
        "model_called": True,
        "relevant": ["doc:a#1"],
        "targets_in_prompt": ["doc:a#1"],
    }
    base = _answer_report(_answer_run("nps"), _answer_run("lookup", **answerable))
    run = _answer_report(
        _answer_run("nps", status="answered", status_reason=None, model_called=True),
        _answer_run("lookup", **answerable | {"targets_in_prompt": []}),
    )
    result = baseline.compare(base, run)
    assert result.verdict == "drift"
    kinds = {(f.item, f.kind) for f in result.regressions}
    assert ("nps|default", "status_changed") in kinds
    assert ("nps|default", "model_call_changed") in kinds  # the abstention is no longer caught
    assert ("lookup|default", "target_left_prompt") in kinds
    row = next(r for r in result.categories if r["slice"] == "insufficient_evidence")
    assert (row["baseline"]["status_correct"], row["run"]["status_correct"]) == (1, 0)


def test_rates_carry_their_denominator_and_interval() -> None:
    assert proportion(0, 13) == {"count": 0, "n": 13, "rate": 0.0, "ci95": [0.0, 0.2281]}
    assert proportion(0, 0)["rate"] is None
    low, high = wilson(12, 13) or (0.0, 0.0)
    assert low < 12 / 13 < high <= 1.0


def _with_splits(report: dict[str, Any], splits: dict[str, str]) -> dict[str, Any]:
    out = copy.deepcopy(report)
    for item in out["items"]:
        item["split"] = splits[item["id"]]
    return out


SPLITS = {"lookup-a": "dev", "lookup-b": "held_out", "stale-c": "dev"}


def test_regressions_are_also_reported_per_split() -> None:
    """P13.2 (EC-7): the held-out split is a slice of its own, with denominators; a held-out
    drop is a finding even when the categories hide it."""
    base = _with_splits(_report(), SPLITS)
    run = _with_splits(_moved(_report(), "lookup-b", "doc:c#1", 14), SPLITS)
    result = baseline.compare(base, run)
    rows = {row["slice"]: row for row in result.categories}
    assert rows["split:held_out"]["baseline"]["n"] == 1
    assert rows["split:dev"]["run"]["n"] == 2
    assert ("split:held_out", "split_regressed") in {(f.item, f.kind) for f in result.regressions}
    assert not rows["split:dev"].get("regression")
    # Runs without splits (P13.1 baselines) compare as before: no split rows.
    plain = baseline.compare(_report(), _moved(_report(), "lookup-b", "doc:c#1", 14))
    assert not [row for row in plain.categories if row["slice"].startswith("split:")]


def test_a_new_relevance_gate_is_a_declared_change_even_against_older_baselines() -> None:
    # Baselines written before P13.3 have no gate version: the gate's arrival is declared,
    # so its differences are reported as a change and only its regressions fail.
    base = _answer_report(_answer_run("nps", status="answered", model_called=True))
    run = _answer_report(_answer_run("nps")) | {"gate_version": "gate_v1"}
    result = baseline.compare(base, run)
    assert result.intended == ["gate_version: None -> gate_v1"]
    assert result.verdict == "changed"
    assert baseline.compare(run, run).verdict == "identical"


def test_the_retrieval_configuration_of_an_answer_run_is_declared() -> None:
    # P13.5: an answer run records the retrieval configuration its Asks used, so a
    # `--set` experiment or a new retrieval default is a declared change, never drift.
    base = _answer_report(_answer_run("nps")) | {"retrieval_config_version": "retrieval-6"}
    run = base | {"retrieval_config_version": "retrieval-6+max_chunks_per_document=2"}
    result = baseline.compare(base, run)
    assert result.intended == [
        "retrieval_config_version: retrieval-6 -> retrieval-6+max_chunks_per_document=2"
    ]
    assert result.verdict == "identical"  # declared, and nothing moved


def test_only_a_prompt_other_than_the_products_is_a_declared_change() -> None:
    # phase-13-followups §12 (E3): reports record their prompt configuration; the
    # product's prompt still compares identically with baselines from before the field.
    base = _answer_report(_answer_run("nps"))
    default = base | {"prompt_config": {"version": "answer-prompt-2", "max_evidence": 12}}
    assert baseline.compare(base, default).verdict == "identical"
    assert baseline.compare(base, default).intended == []
    wider = base | {"prompt_config": {"version": "answer-prompt-2", "max_evidence": 16}}
    result = baseline.compare(base, wider)
    assert result.intended == [
        "prompt_config: None -> {'version': 'answer-prompt-2', 'max_evidence': 16}"
    ]


def test_only_one_question_per_call_is_a_declared_query_embedding_change() -> None:
    # phase-13-followups O13 (F5.0): batched runs compare with baselines from before the
    # field; a local model embedding one question per call is declared.
    local = {"embedding_provider": "local", "embedding_model": "mxbai@x"}
    base = _report(**local)
    batched = _report(**local, query_embedding="batched")
    assert baseline.compare(base, batched).verdict == "identical"
    assert baseline.compare(base, batched).intended == []
    single = _moved(_report(**local, query_embedding="per_question"), "lookup-b", "doc:c#1", 4)
    result = baseline.compare(base, single)
    assert result.intended == ["query_embedding: None -> per_question"]
    assert result.verdict == "changed"


def test_a_new_action_request_detector_is_a_declared_change() -> None:
    # O12: baselines from before the field have no detector version; its arrival is
    # declared, so the refusal runs it changes are reported, never drift.
    base = _answer_report(_answer_run("nps"))
    run = base | {"request_kind_version": "request-kind-2"}
    result = baseline.compare(base, run)
    assert result.intended == ["request_kind_version: None -> request-kind-2"]
    assert baseline.compare(run, run).verdict == "identical"
