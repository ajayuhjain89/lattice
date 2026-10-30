"""Golden-set provenance, splits and the owner review packet (P13.2; phase-13-plan EC-7,
EC-8, D-41). Files only; the lint mutations in test_dataset_lint.py cover each rule's
failure, these cover the committed state and the tools."""

from collections import Counter
from pathlib import Path

import pytest

from lattice.cli import main
from lattice.dataset import provenance, splits
from lattice.dataset.lint import Dataset, load_all, load_dataset
from lattice.dataset.manifests import EvalClass, GoldenItem

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "synthetic"
GOLDEN = REPO / "evals" / "golden"


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_dataset(DATA, GOLDEN)


@pytest.fixture(scope="module")
def items(dataset: Dataset) -> list[GoldenItem]:
    return [item for _, item in dataset.goldens]


def test_phase3_labels_are_frozen_as_they_were(items: list[GoldenItem]) -> None:
    freeze = provenance.load_freeze(GOLDEN)
    assert freeze is not None
    phase3 = [i for i in items if i.label_source == "authored_phase3"]
    assert len(phase3) == len(freeze.phase3) == 89
    assert all(provenance.label_digest(i) == freeze.phase3[i.id] for i in phase3)
    # Metadata never changes a digest: reviewing or re-splitting is not a label change.
    item = phase3[0]
    moved = item.model_copy(update={"split": "held_out", "classes": [], "paraphrase_group": "x"})
    assert provenance.label_digest(moved) == provenance.label_digest(item)


def test_every_held_out_label_is_frozen_and_pending_review(items: list[GoldenItem]) -> None:
    freeze = provenance.load_freeze(GOLDEN)
    assert freeze is not None
    held = [i for i in items if i.split == "held_out"]
    assert {i.id for i in held} == set(freeze.held_out)
    for item in held:
        assert item.owner_review is not None
        approved = item.owner_review.status == "approved"
        assert (item.label_source == "owner_reviewed") == approved


def test_the_set_is_large_and_covers_every_class_in_both_splits(items: list[GoldenItem]) -> None:
    assert len(items) >= 150
    for name in EvalClass.__args__:  # type: ignore[attr-defined]
        sides = Counter(i.split for i in items if name in i.classes)
        assert sides["dev"] >= 1, (name, sides)
        assert sides["held_out"] >= 1, (name, sides)
    for category in {i.category for i in items}:
        sides = Counter(i.split for i in items if i.category == category)
        assert sides["dev"] >= 1, (category, sides)
        assert sides["held_out"] >= 1, (category, sides)


def test_the_held_out_share_is_about_a_third(items: list[GoldenItem]) -> None:
    # P13.2 split its items about a third held-out; F2 added items in its own shares.
    before = [i for i in items if i.cohort is None]
    share = sum(1 for i in before if i.split == "held_out") / len(before)
    assert 0.30 <= share <= 0.40


def test_f2_meets_its_held_out_and_sealed_minimums() -> None:
    """phase-13-followups §7.2: held-out >= 100 items with targets, sealed >= 40, and the
    F2 items divided dev 25 / held-out 70 / sealed 51 (130 approved plus 16 top-ups)."""
    everything = [i for _, i in load_all(DATA, GOLDEN).goldens]
    f2 = Counter(i.split for i in everything if i.cohort == "f2")
    assert f2 == {"dev": 25, "held_out": 70, "sealed": 51}
    with_targets = Counter(i.split for i in everything if splits.target_records(i))
    assert with_targets["held_out"] >= 100
    assert with_targets["sealed"] >= 40
    sealed_categories = Counter(i.category for i in everything if i.split == "sealed")
    assert all(sealed_categories[c] >= 2 for c in {i.category for i in everything})


def test_proposals_keep_committed_splits_and_are_deterministic(items: list[GoldenItem]) -> None:
    assert splits.propose(items) == {i.id: i.split for i in items}
    fresh = [i.model_copy(update={"split": None}) for i in items]
    first, second = splits.propose(fresh), splits.propose(fresh)
    assert first == second
    proposed = [i.model_copy(update={"split": first[i.id]}) for i in fresh]
    assert not [p for p in splits.check(proposed) if "straddles" in p.message]
    assert 0.30 <= Counter(first.values())["held_out"] / len(items) <= 0.40


def test_near_duplicate_questions_on_different_splits_are_refused(
    items: list[GoldenItem],
) -> None:
    dev = next(i for i in items if i.split == "dev" and not i.paraphrase_group)
    held = next(
        i
        for i in items
        if i.split == "held_out" and not i.paraphrase_group and not i.same_question_group
    )
    twin = held.model_copy(update={"question": dev.question.rstrip("?.") + " please?"})
    problems = splits.check([i if i.id != held.id else twin for i in items])
    assert any("near-duplicate" in p.message and held.id in p.where for p in problems)
    assert splits.similarity("What is our refund window?", "What's our refund windows?") == 1.0


def test_units_follow_both_kinds_of_group(items: list[GoldenItem]) -> None:
    unit_of = {i.id: n for n, u in enumerate(splits.units(items)) for i in u}
    # Same question (different personas) and same information need (other words).
    assert unit_of["visibility-commitments-maya"] == unit_of["acme-scim-commitment-01"]
    assert unit_of["para-kestrel-assertion-daniel"] == unit_of["kw-kestrel-assertion-priya"]
    assert unit_of["lookup-hal-4821-priority"] == unit_of["cross-org-hal-4821-felix"]


def test_review_batches_are_the_right_size() -> None:
    everything = [i for _, i in load_all(DATA, GOLDEN).goldens]
    batches = Counter(i.owner_review.batch for i in everything if i.owner_review)
    assert batches
    assert all(10 <= n <= 16 for n in batches.values()), batches
    # Each round's batches are assigned over that round's items, numbered after the
    # batches already under review (H1-H4 from P13.2; H5 onwards and S1 onwards from F2).
    rounds = [
        ([i for i in everything if i.split == "held_out" and i.cohort is None], "H", 1),
        ([i for i in everything if i.split == "held_out" and i.cohort == "f2"], "H", 5),
        ([i for i in everything if i.split == "sealed"], "S", 1),
    ]
    for members, prefix, start in rounds:
        assert splits.assign_batches(members, prefix=prefix, start=start) == {
            i.id: i.owner_review.batch for i in members if i.owner_review
        }


def test_the_committed_review_packet_matches_the_labels() -> None:
    assert main(["dataset", "review-packet", "--check"]) == 0


def test_the_packet_redacts_canaries_and_shows_every_held_out_item(dataset: Dataset) -> None:
    from lattice.dataset.manifests import CANARY
    from lattice.dataset.review import render_packet

    files = render_packet(dataset)
    text = "\n".join(files.values())
    assert not CANARY.search(text)
    held = [i for _, i in dataset.goldens if i.split == "held_out"]
    assert all(f"### {i.id}\n" in text for i in held)
    assert text.count("- [ ] Approve these labels") == len(held)


def test_freeze_changes_are_named_and_recorded(items: list[GoldenItem]) -> None:
    freeze = provenance.load_freeze(GOLDEN)
    assert freeze is not None
    held = next(
        i
        for i in items
        if i.split == "held_out" and i.label_source == "authored_phase13" and i.expected
    )
    assert held.expected is not None
    edited = held.model_copy(
        update={"expected": held.expected.model_copy(update={"forbidden_claims": ["x"]})}
    )
    changed = [edited if i.id == held.id else i for i in items]
    with pytest.raises(ValueError, match="name it with --item"):
        provenance.update(
            changed, freeze, reason="r", item_ids=[], phase3_correction=False, commit="c"
        )
    phase3 = next(i for i in items if i.label_source == "authored_phase3")
    with pytest.raises(ValueError, match="phase3-correction"):
        provenance.update(
            items, freeze, reason="r", item_ids=[phase3.id], phase3_correction=False, commit="c"
        )
    fresh = provenance.load_freeze(GOLDEN)
    updated, ids = provenance.update(
        changed,
        fresh,
        reason="owner correction",
        item_ids=[held.id],
        phase3_correction=False,
        commit="c",
    )
    assert ids == [held.id]
    assert updated.held_out[held.id] == provenance.label_digest(edited)
    assert updated.changes[-1]["reason"] == "owner correction"
