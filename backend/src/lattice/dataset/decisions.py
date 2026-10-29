"""Recording the product owner's review decisions (D-41; phase-13-followups F1, F2d).

    lattice dataset record-review FILE [--phase3-correction] [--dry-run]

FILE (YAML) holds the owner's decisions on held-out and sealed items::

    reviewed_on: 2026-10-30
    approve: [H1, H2]                # batches approved in full, apart from the items below
    items:
      <item id>:
        decision: correct            # approve | correct | corrections_requested
        note: what was wrong, and the record that shows it
        set: {expected.status: partial}                  # replace a value
        add: {expected.relevant: ["doc:x#2"]}            # append to a list
        remove: {expected.acceptable: ["doc:y#1"]}       # take out of a list

An approved item gets ``owner_review`` ``approved`` on ``reviewed_on`` and
``label_source: owner_reviewed`` (its ``cohort`` stays, so held-out results keep their
seen and fresh slices). A correction changes the named label fields, then approves the
item with the note in ``owner_review.notes`` and records the new digest in the label
freeze (a Phase 3 item only with ``--phase3-correction``). ``corrections_requested``
records the note and leaves the labels alone. Items the file does not name stay as
they are; recording the same file twice changes nothing.

Edits are targeted: each item's ``label_source`` and ``owner_review`` lines and, for a
correction, the keys it changes (``expected.forbidden_claims``, not all of ``expected``),
written in the files' style, so every other line stays as written.
Nothing is written unless every decision applies and, on a temporary copy, every item
reloads as planned and the whole lint passes (``--dry-run`` stops after that check).

Sealed items (phase-13-followups §7) are recorded without being read: the output counts
them per batch and never names one or prints a label. A sealed correction carries labels,
so its decisions file must live under ``evals/golden-sealed/decisions/``, and its freeze
reason names only the batch; approving a sealed batch carries none and may come from
any file.
"""

import copy
import json
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lattice.dataset import provenance
from lattice.dataset.lint import (
    Dataset,
    error_text,
    lint,
    load_all,
    sealed_dir_for,
    staging_dir_for,
)
from lattice.dataset.manifests import METADATA_FIELDS, GoldenItem, OwnerReview, load_yaml

DECISIONS_DIR = "decisions"  # under the sealed directory, for sealed corrections
_STATUS = {
    "approve": "approved",
    "correct": "approved",
    "corrections_requested": "corrections_requested",
}
_OUTCOMES = ("approved", "corrected", "corrections requested", "already recorded")


class ItemDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["approve", "correct", "corrections_requested"]
    note: str | None = None
    set: dict[str, Any] = Field(default_factory=dict)
    add: dict[str, list[Any]] = Field(default_factory=dict)
    remove: dict[str, list[Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        changes = bool(self.set or self.add or self.remove)
        if self.decision == "approve" and changes:
            raise ValueError("only a correction changes labels")
        if self.decision != "approve" and not (self.note or "").strip():
            raise ValueError(f"{self.decision} needs a note")
        if self.decision == "correct" and not changes:
            raise ValueError("a correction needs set, add or remove")
        return self


class Decisions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reviewed_on: date
    approve: list[str] = Field(default_factory=list)
    items: dict[str, ItemDecision] = Field(default_factory=dict)


@dataclass
class Summary:
    counts: dict[str, Counter[str]] = field(default_factory=dict)  # batch -> outcome -> n
    corrected_held_out: list[str] = field(default_factory=list)
    corrections: int = 0
    written: bool = False

    def lines(self) -> list[str]:
        out = []
        for batch in sorted(self.counts, key=lambda b: (b[0] != "H", int(b[1:]))):
            counts = self.counts[batch]
            out.append(f"{batch}: " + ", ".join(f"{counts[o]} {o}" for o in _OUTCOMES if counts[o]))
        out += [f"  corrected: {item_id}" for item_id in self.corrected_held_out]
        out.append(f"label freeze: {self.corrections} corrections recorded")
        if not self.written:
            out.append("dry run: nothing written")
        return out


def record(
    data: Dataset,
    decisions_path: Path,
    *,
    phase3_correction: bool,
    dry_run: bool,
    commit: str,
) -> Summary:
    """Apply the decisions in ``decisions_path`` to the items of ``data`` (``load_all``).
    Raises ValueError, listing every problem, and writes nothing if any decision fails."""
    try:
        decisions = Decisions.model_validate(load_yaml(decisions_path))
    except ValidationError as exc:
        raise ValueError(f"{decisions_path.name}: {error_text(exc)}") from exc
    freeze = provenance.load_freeze(data.golden_dir)
    if freeze is None:
        raise ValueError("no label freeze: run `lattice dataset freeze-labels` first")
    sealed_dir = sealed_dir_for(data.golden_dir)
    plan = _Plan(data.goldens, decisions)
    plan.check_names()
    sealed_home = (sealed_dir / DECISIONS_DIR).resolve()
    sealed_file = decisions_path.resolve().is_relative_to(sealed_home)
    for item_id, decision in sorted(plan.chosen.items()):
        plan.decide(item_id, decision, sealed_file=sealed_file)
    plan.raise_problems()
    corrections = plan.freeze_corrections(freeze, phase3_correction, commit)
    summary = Summary(
        counts=plan.counts,
        corrected_held_out=sorted(i for i in plan.corrected if plan.split(i) == "held_out"),
        corrections=corrections,
    )
    _verify(data, plan, freeze)
    if dry_run:
        return summary
    _write(data, plan, freeze)
    summary.written = True
    return summary


class _Plan:
    def __init__(self, goldens: list[tuple[Path, GoldenItem]], decisions: Decisions) -> None:
        self.decisions = decisions
        self.home = {i.id: p for p, i in goldens}
        self.before = {i.id: i for _, i in goldens}
        self.after = dict(self.before)
        self.reviewed = {
            i.id: i
            for _, i in goldens
            if i.split in ("held_out", "sealed") and i.owner_review is not None
        }
        self.batches: dict[str, list[str]] = {}
        for item in sorted(self.reviewed.values(), key=lambda i: i.id):
            assert item.owner_review is not None  # noqa: S101 - filtered above
            self.batches.setdefault(item.owner_review.batch, []).append(item.id)
        self.chosen: dict[str, ItemDecision] = {}
        self.texts: dict[Path, str] = {}
        self.raw: dict[Path, list[dict[str, Any]]] = {}
        self.counts: dict[str, Counter[str]] = {}
        self.corrected: dict[str, str] = {}  # item id -> freeze reason
        self.problems: list[str] = []

    def split(self, item_id: str) -> str | None:
        return self.before[item_id].split

    def label(self, item_id: str) -> str:
        """How output names an item: a sealed one only by its batch and position."""
        item = self.reviewed[item_id]
        assert item.owner_review is not None  # noqa: S101 - reviewed items carry one
        if item.split != "sealed":
            return item_id
        batch = item.owner_review.batch
        return f"{batch} item {self.batches[batch].index(item_id) + 1}"

    def check_names(self) -> None:
        for batch in self.decisions.approve:
            if batch not in self.batches:
                self.problems.append(f"no review batch {batch}")
                continue
            for item_id in self.batches[batch]:
                self.chosen[item_id] = ItemDecision(decision="approve")
        for item_id, decision in self.decisions.items.items():
            if item_id not in self.reviewed:
                self.problems.append(
                    f"{item_id}: no reviewed item (held-out or sealed) has this id"
                )
                continue
            self.chosen[item_id] = decision
        self.raise_problems()

    def raise_problems(self) -> None:
        if self.problems:
            raise ValueError("\n".join(self.problems))

    def decide(self, item_id: str, decision: ItemDecision, *, sealed_file: bool) -> None:
        item = self.reviewed[item_id]
        review = item.owner_review
        assert review is not None  # noqa: S101 - reviewed items carry one
        counts = self.counts.setdefault(review.batch, Counter())
        label = self.label(item_id)
        if item.split == "sealed" and decision.decision != "approve" and not sealed_file:
            self.problems.append(
                f"{label}: a sealed correction belongs in a decisions file under "
                f"evals/golden-sealed/{DECISIONS_DIR}/"
            )
            return
        status = _STATUS[decision.decision]
        note = decision.note.strip() if decision.note else None
        if review.status == status and (decision.decision == "approve" or review.notes == note):
            counts["already recorded"] += 1
            return
        new_review = OwnerReview(
            batch=review.batch, status=status, reviewed_on=self.decisions.reviewed_on, notes=note
        )
        source = "owner_reviewed" if status == "approved" else item.label_source
        path = self.home[item_id]
        fields: dict[str, Any] = item.model_dump()
        touched: set[tuple[str, ...]] = set()
        if decision.decision == "correct":
            try:
                fields = copy.deepcopy(self._raw(path, item_id))
                touched = _apply(fields, decision)
            except ValueError as exc:
                self.problems.append(f"{label}: {exc}")
                return
        fields |= {"owner_review": new_review.model_dump(), "label_source": source}
        try:
            new = GoldenItem.model_validate(fields)
        except ValidationError as exc:
            self.problems.append(f"{label}: {error_text(exc)}")
            return
        if touched and provenance.label_digest(new) == provenance.label_digest(item):
            self.problems.append(f"{label}: the correction changes no label")
            return
        text = self.texts.get(path) or path.read_text(encoding="utf-8")
        try:
            text = _replace_review(text, item_id, new_review, source)
            for changed in sorted(touched):
                text = _replace_path(text, item_id, changed, fields)
        except ValueError as exc:
            self.problems.append(f"{label}: {exc}")
            return
        self.texts[path] = text
        self.after[item_id] = new
        if touched:
            counts["corrected"] += 1
            reason = (
                f"owner review {review.batch}: correction (the owner's note is in the item's "
                "owner_review)"
                if item.split == "sealed"
                else f"owner review {review.batch}: {note}"
            )
            self.corrected[item_id] = reason
        elif status == "approved":
            counts["approved"] += 1
        else:
            counts["corrections requested"] += 1

    def _raw(self, path: Path, item_id: str) -> dict[str, Any]:
        if path not in self.raw:
            self.raw[path] = load_yaml(path)
        return next(row for row in self.raw[path] if row.get("id") == item_id)

    def freeze_corrections(self, freeze: provenance.Freeze, phase3: bool, commit: str) -> int:
        """Record each correction in ``freeze``, one change record per item."""
        current = dict(self.before)
        for item_id, reason in sorted(self.corrected.items()):
            current[item_id] = self.after[item_id]
            try:
                provenance.update(
                    list(current.values()),
                    freeze,
                    reason=reason,
                    item_ids=[item_id],
                    phase3_correction=phase3,
                    commit=commit,
                )
            except ValueError as exc:
                self.problems.append(f"{self.label(item_id)}: {exc}")
        self.raise_problems()
        return len(self.corrected)


def _apply(fields: dict[str, Any], decision: ItemDecision) -> set[tuple[str, ...]]:
    """Apply the decision's changes to an item's raw fields; the paths changed. Errors
    name the field and the value's position, never the value."""
    touched: set[tuple[str, ...]] = set()
    changes = [("set", decision.set), ("add", decision.add), ("remove", decision.remove)]
    for op, paths in changes:
        for dotted, value in paths.items():
            keys = dotted.split(".")
            if (
                keys[0] == "id"
                or keys[0] in METADATA_FIELDS
                or keys[0] not in GoldenItem.model_fields
            ):
                raise ValueError(f"{dotted}: not a label field")
            parent = fields
            for key in keys[:-1]:
                parent = parent.setdefault(key, {})
                if not isinstance(parent, dict):
                    raise ValueError(f"{dotted}: {key} is not a mapping")
            last = keys[-1]
            if op == "set":
                parent[last] = value
            else:
                current = parent.get(last) or []
                if not isinstance(current, list):
                    raise ValueError(f"{dotted}: not a list")
                current = list(current)
                for n, entry in enumerate(value, start=1):
                    if op == "add":
                        if entry in current:
                            raise ValueError(f"{dotted}: value {n} to add is already in the list")
                        current.append(entry)
                    elif entry in current:
                        current.remove(entry)
                    else:
                        raise ValueError(f"{dotted}: value {n} to remove is not in the list")
                parent[last] = current
            touched.add(tuple(keys))
    return touched


def _block(text: str, item_id: str) -> re.Match[str]:
    block = re.search(rf"^- id: {re.escape(item_id)}\n(?:(?!- |#).*\n|\n)*", text, re.M)
    if block is None:
        raise ValueError("no block for this item in its file")
    return block


def _json(value: Any) -> str:
    """A scalar in the golden files' style: JSON, which is also YAML."""
    return json.dumps(value, ensure_ascii=False)


def _flow(value: Any) -> str:
    """A value on one line, as YAML flow."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {_flow(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_flow(v) for v in value) + "]"
    if isinstance(value, date):
        return value.isoformat()
    return _json(value)


def _replace_review(text: str, item_id: str, review: OwnerReview, source: str) -> str:
    block = _block(text, item_id)
    body = block.group(0)
    parts = [f"batch: {review.batch}", f"status: {review.status}"]
    if review.reviewed_on is not None:
        parts.append(f"reviewed_on: {review.reviewed_on.isoformat()}")
    if review.notes is not None:
        parts.append(f"notes: {_json(review.notes)}")
    line = "  owner_review: {" + ", ".join(parts) + "}"
    body, n_review = re.subn(r"^  owner_review: .*$", lambda _: line, body, count=1, flags=re.M)
    body, n_source = re.subn(
        r"^  label_source: \S+$", f"  label_source: {source}", body, count=1, flags=re.M
    )
    if n_review != 1 or n_source != 1:
        raise ValueError("no single-line owner_review and label_source lines")
    return text[: block.start()] + body + text[block.end() :]


def _replace_path(text: str, item_id: str, path: tuple[str, ...], fields: dict[str, Any]) -> str:
    """Rewrite the deepest written key of ``path`` in an item's block (adding the first
    missing one at the end of its parent), leaving every other line as written."""
    block = _block(text, item_id)
    body = block.group(0)
    start, end = 0, len(body.rstrip("\n")) + 1
    indent = 2
    value: Any = fields
    for depth, key in enumerate(path):
        value = value[key]
        region = _region(body, start, end, indent, key)
        if region is None:
            body = body[:end] + _render(key, value, indent, inline=None) + body[end:]
            break
        line_end = body.index("\n", region.start()) + 1
        inline = line_end == region.end() and body[region.start() : line_end].strip() != f"{key}:"
        if depth == len(path) - 1 or inline:  # the target, or a parent written on one line
            new = _render(key, value, indent, inline=inline)
            body = body[: region.start()] + new + body[region.end() :]
            break
        start, end, indent = line_end, region.end(), indent + 2
    return text[: block.start()] + body + text[block.end() :]


def _region(body: str, start: int, end: int, indent: int, key: str) -> re.Match[str] | None:
    """A key's lines at ``indent`` within ``body[start:end]``: its own line, then deeper
    lines and block-sequence entries."""
    pad = " " * indent
    pattern = re.compile(rf"^{pad}{re.escape(key)}:(?: .*)?\n(?:(?:{pad}  |{pad}- ).*\n)*", re.M)
    return pattern.search(body, start, end)


def _render(key: str, value: Any, indent: int, *, inline: bool | None) -> str:
    """``key: value`` at ``indent``: on one line if it was (or, for a new key, if it
    fits in 100 columns), else as a block."""
    pad = " " * indent
    one_line = f"{pad}{key}: {_flow(value)}\n"
    if inline is None:
        inline = len(one_line) <= 101
    if inline or not value or not isinstance(value, list | dict):
        return one_line
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return f"{pad}{key}:\n" + "".join(f"{pad}  - {_json(v)}\n" for v in value)
    dumped = yaml.safe_dump(
        {key: value}, sort_keys=False, default_flow_style=None, width=100, allow_unicode=True
    )
    return "".join(f"{pad}{line}\n" for line in dumped.splitlines())


def _verify(data: Dataset, plan: _Plan, freeze: provenance.Freeze) -> None:
    """Apply the plan to a temporary copy of the golden directories, then check that every
    item reloads as planned and the whole lint passes. Lint problems are printed for
    held-out and dev files; sealed ones are only counted."""
    sealed_dir = sealed_dir_for(data.golden_dir)
    dirs = [data.golden_dir, sealed_dir, staging_dir_for(data.golden_dir)]
    with tempfile.TemporaryDirectory() as tmp:
        copies = {d: Path(tmp) / d.name for d in dirs if d.exists()}
        for source, target in copies.items():
            shutil.copytree(source, target)
        for path, text in plan.texts.items():
            (copies[path.parent] / path.name).write_text(text, encoding="utf-8")
        golden = copies[data.golden_dir]
        provenance.write_freeze(golden, freeze)
        reloaded = load_all(data.root, golden)
        problems = lint(reloaded)  # the load problems, then the lint's
        sealed_copy = str(Path(tmp) / sealed_dir.name)
    now = {i.id: i for _, i in reloaded.goldens}
    wrong = [
        plan.label(i) if i in plan.reviewed else i
        for i, expected in plan.after.items()
        if now.get(i) != expected
    ]
    if not problems and (wrong or set(now) != set(plan.after)):
        detail = ", ".join(wrong) or "items missing"
        raise ValueError(f"the edited files would not reload as planned ({detail})")
    if problems:
        sealed_ids = {i for i in plan.before if plan.split(i) == "sealed"}
        sealed = [p for p in problems if p.where.startswith(sealed_copy) or p.where in sealed_ids]
        shown = [f"lint: {Path(p.where).name}: {p.message}" for p in problems if p not in sealed]
        if sealed:
            shown.append(f"lint: {len(sealed)} problems in sealed items")
        raise ValueError("\n".join(shown))


def _write(data: Dataset, plan: _Plan, freeze: provenance.Freeze) -> None:
    for path, text in plan.texts.items():
        path.write_text(text, encoding="utf-8")
    provenance.write_freeze(data.golden_dir, freeze)
