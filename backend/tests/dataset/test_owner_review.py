"""Recording the product owner's review decisions (D-41; phase-13-followups F1, F2d):
``lattice dataset record-review`` approves batches and items, applies corrections as
recorded label changes, leaves every other line alone, writes nothing unless every
decision applies, and never prints a sealed label. Files only."""

import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from lattice.cli import main
from lattice.dataset import provenance
from lattice.dataset.lint import lint, load_all, sealed_dir_for
from lattice.dataset.manifests import GoldenItem
from lattice.dataset.review import render_packet

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "synthetic"
GOLDEN = REPO / "evals" / "golden"
CLAIM = "Probe correction: the owner forbids this claim."


@pytest.fixture
def golden(tmp_path: Path) -> Path:
    target = tmp_path / "golden"
    shutil.copytree(GOLDEN, target)
    shutil.copytree(sealed_dir_for(GOLDEN), sealed_dir_for(target))
    return target


def _items(golden: Path) -> dict[str, tuple[Path, GoldenItem]]:
    return {i.id: (p, i) for p, i in load_all(DATA, golden).goldens}


def _batch(golden: Path, batch: str) -> list[GoldenItem]:
    return sorted(
        (i for _, i in _items(golden).values() if i.owner_review and i.owner_review.batch == batch),
        key=lambda i: i.id,
    )


def _texts(golden: Path) -> dict[Path, str]:
    dirs = (golden, sealed_dir_for(golden), golden / "provenance")
    return {p: p.read_text(encoding="utf-8") for d in dirs for p in sorted(d.glob("*.*"))}


def _record(
    golden: Path, decisions: Mapping[str, object], *extra: str, where: Path | None = None
) -> int:
    path = where or golden.parent / "decisions.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(decisions, sort_keys=False), encoding="utf-8")
    common = ["--data", str(DATA), "--golden", str(golden)]
    return main(["dataset", "record-review", str(path), *extra, *common])


def _correction(note: str = "The renewal plan forbids this claim (doc:x#1).") -> dict[str, object]:
    return {"decision": "correct", "note": note, "add": {"expected.forbidden_claims": [CLAIM]}}


def test_batches_and_items_are_recorded_and_the_rest_is_untouched(
    golden: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    h5 = _batch(golden, "H5")
    fixed, asked = h5[0], h5[1]
    assert fixed.expected is not None
    before = _texts(golden)
    decisions = {
        "reviewed_on": "2026-10-30",
        "approve": ["H5"],
        "items": {
            fixed.id: _correction(),
            asked.id: {"decision": "corrections_requested", "note": "Discuss the dates."},
        },
    }
    assert _record(golden, decisions) == 0
    out = capsys.readouterr().out
    assert "H5: 13 approved, 1 corrected, 1 corrections requested" in out
    items = _items(golden)
    for item in h5:
        _, now = items[item.id]
        assert now.owner_review is not None
        assert now.owner_review.batch == "H5"
        assert str(now.owner_review.reviewed_on) == "2026-10-30"
        if item.id == asked.id:
            assert now.owner_review.status == "corrections_requested"
            assert now.owner_review.notes == "Discuss the dates."
            assert now.label_source == "authored_f2"
            assert provenance.label_digest(now) == provenance.label_digest(item)
            continue
        assert now.owner_review.status == "approved"
        assert now.label_source == "owner_reviewed"
        assert now.cohort == "f2"  # the seen/fresh slices survive review
        if item.id != fixed.id:
            assert provenance.label_digest(now) == provenance.label_digest(item)
    _, corrected = items[fixed.id]
    assert corrected.expected is not None
    assert corrected.expected.forbidden_claims == [*fixed.expected.forbidden_claims, CLAIM]
    assert (
        corrected.model_copy(
            update={
                "expected": corrected.expected.model_copy(
                    update={"forbidden_claims": fixed.expected.forbidden_claims}
                ),
                "owner_review": fixed.owner_review,
                "label_source": fixed.label_source,
            }
        )
        == fixed
    )
    freeze = provenance.load_freeze(golden)
    assert freeze is not None
    assert freeze.held_out[fixed.id] == provenance.label_digest(corrected)
    assert freeze.changes[-1]["items"] == fixed.id
    assert "doc:x#1" in freeze.changes[-1]["reason"]
    # Only the review lines and the corrected field changed; other batches are untouched.
    after = _texts(golden)
    changed = {p for p in before if before[p] != after[p]}
    homes = {items[i.id][0] for i in h5}
    assert changed == homes | {golden / "provenance" / "label-freeze.json"}
    for path in homes:
        old, new = before[path].splitlines(), after[path].splitlines()
        gone = [line for line in old if line not in new]
        assert all(
            line.startswith(("  label_source: ", "  owner_review: ", "  expected:", "    "))
            for line in gone
        ), gone
    assert lint(load_all(DATA, golden)) == []
    packet = render_packet(load_all(DATA, golden))
    assert "| [H5](H5.md) | 15 | 0 | 0 | 15 | 14 | 0 |" in packet["README.md"]
    # Recording the same file again changes nothing.
    assert _record(golden, decisions) == 0
    assert "H5: 15 already recorded" in capsys.readouterr().out
    assert _texts(golden) == after


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        ({"decision": "correct", "note": "n"}, "a correction needs set, add or remove"),
        ({"decision": "correct", "add": {"expected.forbidden_claims": ["x"]}}, "needs a note"),
        ({"decision": "corrections_requested"}, "needs a note"),
        ({"decision": "approve", "set": {"question": "x?"}}, "only a correction changes labels"),
        ({"decision": "correct", "note": "n", "set": {"split": "dev"}}, "not a label field"),
        ({"decision": "correct", "note": "n", "set": {"owner_review.notes": "x"}}, "not a label"),
        (
            {"decision": "correct", "note": "n", "remove": {"expected.relevant": ["doc:none#9"]}},
            "not in",
        ),
        (
            {"decision": "correct", "note": "n", "set": {"expected.status": "maybe"}},
            "expected.status",
        ),
    ],
)
def test_nothing_is_written_unless_every_decision_applies(
    golden: Path, decision: dict[str, object], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    h6 = _batch(golden, "H6")
    before = _texts(golden)
    decisions = {"reviewed_on": "2026-10-30", "approve": ["H6"], "items": {h6[0].id: decision}}
    assert _record(golden, decisions) == 1
    assert message in capsys.readouterr().err
    assert _texts(golden) == before


def test_a_correction_the_lint_rejects_is_refused(
    golden: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    item = next(i for i in _batch(golden, "H5") if i.expected is not None)
    bad = {"decision": "correct", "note": "n", "set": {"expected.conflict.kind": "superseded"}}
    before = _texts(golden)
    decisions = {"reviewed_on": "2026-10-30", "items": {item.id: bad}}
    assert _record(golden, decisions, "--dry-run") == 1
    assert "lint: " in capsys.readouterr().err
    assert _record(golden, decisions) == 1
    assert "superseded expectation without supersession metadata" in capsys.readouterr().err
    assert _texts(golden) == before


@pytest.mark.parametrize(
    ("decisions", "message"),
    [
        ({"reviewed_on": "2026-10-30", "approve": ["H99"]}, "no review batch H99"),
        (
            {"reviewed_on": "2026-10-30", "items": {"no-such-item": {"decision": "approve"}}},
            "no reviewed item",
        ),
        ({"approve": ["H5"]}, "reviewed_on"),
    ],
)
def test_unknown_batches_items_and_missing_dates_are_refused(
    golden: Path, decisions: dict[str, object], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _texts(golden)
    assert _record(golden, decisions) == 1
    assert message in capsys.readouterr().err
    assert _texts(golden) == before


def test_dev_items_are_not_reviewed(golden: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dev = next(i for _, i in _items(golden).values() if i.split == "dev")
    decisions = {"reviewed_on": "2026-10-30", "items": {dev.id: {"decision": "approve"}}}
    assert _record(golden, decisions) == 1
    assert "no reviewed item" in capsys.readouterr().err


def test_a_phase3_correction_needs_the_flag(
    golden: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    freeze = provenance.load_freeze(golden)
    assert freeze is not None
    item = next(i for i in _batch(golden, "H3") if i.id in freeze.phase3 and i.expected is not None)
    decisions = {"reviewed_on": "2026-10-30", "items": {item.id: _correction()}}
    before = _texts(golden)
    assert _record(golden, decisions) == 1
    assert "--phase3-correction" in capsys.readouterr().err
    assert _texts(golden) == before
    assert _record(golden, decisions, "--phase3-correction") == 0
    freeze = provenance.load_freeze(golden)
    assert freeze is not None
    _, now = _items(golden)[item.id]
    assert freeze.phase3[item.id] == freeze.held_out[item.id] == provenance.label_digest(now)
    assert lint(load_all(DATA, golden)) == []


def test_dry_run_reports_without_writing(golden: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = _texts(golden)
    assert _record(golden, {"reviewed_on": "2026-10-30", "approve": ["H7"]}, "--dry-run") == 0
    assert "H7: 14 approved" in capsys.readouterr().out
    assert _texts(golden) == before


def test_sealed_corrections_stay_in_the_sealed_directory_and_are_never_printed(
    golden: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s1 = [i for i in _batch(golden, "S1") if i.expected is not None]
    target = s1[0]
    decisions = {
        "reviewed_on": "2026-10-30",
        "approve": ["S1"],
        "items": {target.id: _correction("Sealed note: the owner's evidence.")},
    }
    # A sealed correction carries labels, so its file lives with the sealed items.
    assert _record(golden, decisions) == 1
    assert "evals/golden-sealed/decisions" in capsys.readouterr().err
    # Approving a sealed batch carries no labels and may come from anywhere.
    assert _record(golden, {"reviewed_on": "2026-10-30", "approve": ["S2"]}) == 0
    out = capsys.readouterr().out
    assert "S2: 13 approved" in out
    where = sealed_dir_for(golden) / "decisions" / "S1.yaml"
    assert _record(golden, decisions, where=where) == 0
    out = capsys.readouterr().out
    assert "S1: 12 approved, 1 corrected" in out
    sealed_ids = {i.id for _, i in _items(golden).values() if i.split == "sealed"}
    assert not any(item_id in out for item_id in sealed_ids)
    assert CLAIM not in out
    assert "owner's evidence" not in out
    freeze = provenance.load_freeze(golden)
    assert freeze is not None
    # The freeze is outside the sealed directory: its reason names the batch, not the note.
    assert "owner's evidence" not in freeze.changes[-1]["reason"]
    _, now = _items(golden)[target.id]
    assert now.owner_review is not None
    assert now.owner_review.notes is not None
    assert "owner's evidence" in now.owner_review.notes
    assert freeze.sealed[target.id] == provenance.label_digest(now)
    # The decisions directory is not an item file: the lint still loads cleanly.
    assert lint(load_all(DATA, golden)) == []
