"""The sealed split and the follow-up authoring flow (phase-13-followups §7.4, F2a): sealed
and staged items stay out of every default load, the lint keeps each item in the
directory of its split, the proposer divides new items in the given shares, ``--write``
moves them, the freeze and the review packet cover sealed items, and a sealed run is
logged and never a baseline. Files only."""

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from lattice.cli import main
from lattice.dataset import provenance, splits
from lattice.dataset.lint import lint, load_all, load_dataset, sealed_dir_for, staging_dir_for
from lattice.dataset.manifests import GoldenItem, OwnerReview
from lattice.dataset.review import render_packet
from lattice.evals import baseline, sealed
from lattice.evals.retrieval import golden_version, held_out_cohorts

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "synthetic"
GOLDEN = REPO / "evals" / "golden"


@pytest.fixture(scope="module")
def items() -> list[GoldenItem]:
    return [item for _, item in load_dataset(DATA, GOLDEN).goldens]


def _bases(items: list[GoldenItem], n: int) -> list[GoldenItem]:
    """Standalone dev items whose labels a new item can borrow without touching a group,
    a trap phrase or a declared class."""
    bases = [
        i
        for i in items
        if i.split == "dev"
        and not (i.same_question_group or i.paraphrase_group or i.trap_phrases or i.classes)
        and not i.exhaustive
        and i.expected is not None
        and i.expected.status == "answered"
    ]
    assert len(bases) >= n
    return bases[:n]


def _new(base: GoldenItem, n: int, **update: Any) -> GoldenItem:
    fields: dict[str, Any] = {
        "id": f"f2-probe-{n:02d}",
        "question": f"Probe {n} for the follow-up tooling: what changed in record {base.id}?",
        "label_source": "authored_f2",
        "cohort": "f2",
        "split": None,
        "owner_review": None,
    }
    return base.model_copy(update=fields | update)


def _yaml(items: list[GoldenItem]) -> str:
    rows = [i.model_dump(mode="json", exclude_none=True, exclude_defaults=True) for i in items]
    for row, item in zip(rows, items, strict=True):
        row["label_source"] = item.label_source  # a default-free dump keeps it anyway
    return "# Follow-up probes (test).\n\n" + yaml.safe_dump(rows, sort_keys=False, width=88)


@pytest.fixture
def golden(tmp_path: Path) -> Path:
    target = tmp_path / "golden"
    shutil.copytree(GOLDEN, target)
    return target


def _stage(golden: Path, items: list[GoldenItem], n: int) -> list[GoldenItem]:
    new = [_new(base, k) for k, base in enumerate(_bases(items, n), start=1)]
    staging = staging_dir_for(golden)
    staging.mkdir()
    (staging / "probes.yaml").write_text(_yaml(new), encoding="utf-8")
    return new


def _problems(golden: Path) -> list[str]:
    return [str(p) for p in lint(load_all(DATA, golden))]


def test_default_loads_never_see_sealed_or_staged_items(
    golden: Path, items: list[GoldenItem]
) -> None:
    new = _stage(golden, items, 2)
    sealed_item = new[1].model_copy(
        update={"split": "sealed", "owner_review": OwnerReview(batch="S1")}
    )
    sealed_dir_for(golden).mkdir()
    (sealed_dir_for(golden) / "probes.yaml").write_text(_yaml([sealed_item]), encoding="utf-8")
    (staging_dir_for(golden) / "probes.yaml").write_text(_yaml(new[:1]), encoding="utf-8")
    default = {i.id for _, i in load_dataset(DATA, golden).goldens}
    everything = {i.id for _, i in load_all(DATA, golden).goldens}
    assert not default & {i.id for i in new}
    assert everything - default == {i.id for i in new}
    # The eval runners load sealed items only for --sealed, and only sealed ones then.
    plain = [i for _, i in sealed.load(DATA, golden, sealed=False).goldens]
    assert sealed_item.id not in {i.id for i in plain}
    chosen, problem = sealed.restrict(
        [i for _, i in sealed.load(DATA, golden, sealed=True).goldens], sealed=True
    )
    assert problem is None
    assert [i.id for i in chosen] == [sealed_item.id]


def test_the_lint_keeps_items_in_the_directory_of_their_split(
    golden: Path, items: list[GoldenItem]
) -> None:
    new = _stage(golden, items, 3)
    # Staged items without a split lint cleanly while they are being authored.
    assert _problems(golden) == []
    staged_with_split = new[0].model_copy(update={"split": "dev"})
    in_golden = new[1].model_copy(
        update={"split": "sealed", "owner_review": OwnerReview(batch="S1")}
    )
    dev_in_sealed = new[2].model_copy(update={"split": "dev"})
    (staging_dir_for(golden) / "probes.yaml").write_text(
        _yaml([staged_with_split]), encoding="utf-8"
    )
    (golden / "probes.yaml").write_text(_yaml([in_golden]), encoding="utf-8")
    sealed_dir_for(golden).mkdir()
    (sealed_dir_for(golden) / "probes.yaml").write_text(_yaml([dev_in_sealed]), encoding="utf-8")
    problems = "\n".join(_problems(golden))
    assert "staged items get their split from `splits --propose`" in problems
    assert "sealed items belong in the sealed directory" in problems
    assert "only sealed items belong in the sealed directory" in problems
    assert "sealed item not in the sealed freeze" in problems


def test_an_unassigned_item_outside_staging_is_refused(
    golden: Path, items: list[GoldenItem]
) -> None:
    new = [_new(base, k) for k, base in enumerate(_bases(items, 1), start=1)]
    (golden / "probes.yaml").write_text(_yaml(new), encoding="utf-8")
    assert any("no split" in p for p in _problems(golden))
    # ... and no evaluation runs it before its split is assigned.
    _, problem = sealed.restrict(new, sealed=False)
    assert problem is not None
    assert "no split" in problem


def test_follow_up_provenance_rules(items: list[GoldenItem]) -> None:
    base = _bases(items, 1)[0]
    with pytest.raises(ValidationError, match="authored_f2 needs cohort f2"):
        GoldenItem.model_validate(_new(base, 1, cohort=None).model_dump())
    old = base.model_dump() | {"split": "sealed", "owner_review": None}
    with pytest.raises(ValidationError, match="only follow-up items"):
        GoldenItem.model_validate(old)
    wrong_batch = _new(base, 1).model_dump() | {
        "split": "sealed",
        "owner_review": {"batch": "H5", "status": "pending"},
    }
    with pytest.raises(ValidationError, match="sealed review batches are named S"):
        GoldenItem.model_validate(wrong_batch)
    # A cohort is metadata: it never changes a label digest, and the golden fingerprint of
    # items that leave it unset is the one the committed baselines record.
    assert provenance.label_digest(_new(base, 1)) == provenance.label_digest(
        _new(base, 1, cohort=None, label_source="authored_phase13")
    )
    assert (
        golden_version(items)["sha256"]
        == baseline.load(REPO / "evals" / "baselines" / "retrieval-A.json")["golden"]["sha256"]
    )


def test_shares_divide_new_items_and_keep_units_whole(items: list[GoldenItem]) -> None:
    shares = splits.parse_shares("dev=0.2,held_out=0.45,sealed=0.35")
    fresh = [i.model_copy(update={"split": None}) for i in items]
    first, second = splits.propose(fresh, shares), splits.propose(fresh, shares)
    assert first == second
    counts = Counter(first.values())
    for name, share in shares.items():
        assert abs(counts[name] / len(fresh) - share) <= 0.03, counts
    proposed = [i.model_copy(update={"split": first[i.id]}) for i in fresh]
    assert not [p for p in splits.check(proposed) if "straddles" in p.message]
    # Committed splits never move; a new item joining a committed unit inherits its split.
    unit = next(u for u in splits.units(items) if len(u) > 1 and u[0].split == "held_out")
    joined = [i.model_copy(update={"split": None}) if i.id == unit[0].id else i for i in items]
    again = splits.propose(joined, shares)
    assert again[unit[0].id] == "held_out"
    assert all(again[i.id] == i.split for i in items if i.id != unit[0].id)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("dev=0.5,held_out=0.4", "sum to"),
        ("dev=0.5,test=0.5", "unknown or repeated"),
        ("dev=1.2,sealed=-0.2", "positive"),
        ("dev=x,sealed=1", "not a number"),
    ],
)
def test_shares_are_validated(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        splits.parse_shares(text)


def test_new_review_batches_follow_the_reviewed_ones(items: list[GoldenItem]) -> None:
    held = [i for i in items if i.split == "held_out"]
    batches = splits.assign_batches(held, prefix="S", start=3)
    assert {b[0] for b in batches.values()} == {"S"}
    assert min(int(b[1:]) for b in batches.values()) == 3
    assert splits.assign_batches(held) == {
        i.id: i.owner_review.batch for i in held if i.owner_review
    }


def test_write_moves_staged_items_and_the_freeze_and_packet_cover_them(
    golden: Path, items: list[GoldenItem]
) -> None:
    new = _stage(golden, items, 6)
    common = ["--data", str(DATA), "--golden", str(golden)]
    shares = "dev=0.34,held_out=0.33,sealed=0.33"
    assert main(["dataset", "splits", "--propose", "--shares", shares, "--write", *common]) == 0
    assert not staging_dir_for(golden).exists() or not list(staging_dir_for(golden).iterdir())
    placed = {i.id: (p.parent, i) for p, i in load_all(DATA, golden).goldens}
    splits_seen = Counter(placed[n.id][1].split for n in new)
    assert set(splits_seen) == {"dev", "held_out", "sealed"}, splits_seen
    for n in new:
        where, item = placed[n.id]
        assert where == (sealed_dir_for(golden) if item.split == "sealed" else golden)
        if item.split == "dev":
            assert item.owner_review is None
        else:
            assert item.owner_review is not None
            assert item.owner_review.status == "pending"
            assert item.owner_review.batch == ("S1" if item.split == "sealed" else "H5")
    # Not yet frozen: the lint says so until freeze-labels records them.
    assert any("not in the sealed freeze" in p for p in _problems(golden))
    assert main(["dataset", "freeze-labels", "--reason", "F2 probes", *common]) == 0
    freeze = provenance.load_freeze(golden)
    assert freeze is not None
    assert set(freeze.sealed) == {n.id for n in new if placed[n.id][1].split == "sealed"}
    assert _problems(golden) == []
    # Without the sealed directory (every default load) the sealed freeze is not checked.
    assert [str(p) for p in lint(load_dataset(DATA, golden))] == []
    packet = render_packet(load_all(DATA, golden))
    assert {"S1.md", "H5.md"} <= set(packet)
    assert "sealed items)" in packet["S1.md"].splitlines()[0]
    assert packet["H1.md"] == (REPO / "docs" / "golden-review" / "H1.md").read_text()
    # A sealed label change is a recorded change, like a held-out one.
    sealed_id = next(iter(freeze.sealed))
    _, item = placed[sealed_id]
    edited = item.model_copy(update={"notes": item.notes + " Edited."})
    everything = [i for _, i in load_all(DATA, golden).goldens]
    changed = [edited if i.id == sealed_id else i for i in everything]
    with pytest.raises(ValueError, match="frozen sealed labels changed"):
        provenance.update(
            changed, freeze, reason="r", item_ids=[], phase3_correction=False, commit="c"
        )


def test_sealed_runs_need_a_reason_are_logged_and_never_become_baselines(
    tmp_path: Path, items: list[GoldenItem]
) -> None:
    def args(**kw: Any) -> argparse.Namespace:
        return argparse.Namespace(**({"sealed": False, "reason": None} | kw))

    assert sealed.argument_problem(args()) is None
    assert "needs --reason" in str(sealed.argument_problem(args(sealed=True)))
    assert "only for --sealed" in str(sealed.argument_problem(args(reason="why")))
    chosen = items[:3]
    for n in (1, 2):
        entry = sealed.record_access(
            tmp_path, kind="retrieval", reason=f" look {n} ", items=chosen, settings={"k": n}
        )
        assert entry["reason"] == f"look {n}"
    log = json.loads(sealed.access_path(tmp_path).read_text())
    assert log["schema"] == sealed.SCHEMA
    assert [e["items"] for e in log["looks"]] == [3, 3]
    report = {"summary": {"security": {}, "errors": {}}, "items": [{"split": "sealed"}]}
    with pytest.raises(baseline.BaselineError, match="sealed run is never a baseline"):
        baseline.write_baseline(report, tmp_path / "b.json", label="A", reason="r")


def test_held_out_results_are_reported_per_cohort() -> None:
    results = [
        SimpleNamespace(cohort=None),
        SimpleNamespace(cohort="f2"),
        SimpleNamespace(cohort=None),
    ]
    groups = dict(held_out_cohorts(results))
    assert {name: len(rs) for name, rs in groups.items()} == {"f2": 1, "phase13": 2}
