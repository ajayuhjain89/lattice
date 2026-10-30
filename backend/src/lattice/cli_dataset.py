"""``lattice dataset splits | freeze-labels | review-packet | record-review`` (P13.2;
phase-13-plan EC-7, EC-8, D-41; phase-13-followups §7). Files only: no database, no
model. Every command loads the sealed (``evals/golden-sealed``) and staging
(``evals/golden-staging``) items too.

    lattice dataset splits [--propose [--shares dev=F,held_out=F,sealed=F] [--write]] [--json]
        The split report (denominators per stratum, class coverage, shared target
        records, owner-review workload). --propose prints a group-aware stratified split
        for items that have none (--shares: the new items divided in those shares);
        --write records it, with an owner-review batch (status pending) for every new
        held-out (H<n>) and sealed (S<n>) item, numbered after the existing batches, and
        moves each staged or sealed item into the directory of its split.
    lattice dataset freeze-labels --reason R [--item ID ...] [--phase3-correction]
        Record label digests (lattice.dataset.provenance): the first run freezes the
        Phase 3 and held-out labels; later runs record the named items' changes only.
    lattice dataset review-packet [--out DIR] [--check]
        Render the product owner's held-out review packet (one Markdown file per batch);
        --check fails if the committed packet differs from the labels.
    lattice dataset record-review FILE [--phase3-correction] [--dry-run]
        Record the owner's review decisions (lattice.dataset.decisions): approvals,
        corrections as recorded label changes, and requested corrections.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from lattice.dataset import decisions, provenance, review, splits
from lattice.dataset.distractors import DATA
from lattice.dataset.lint import Dataset, load_all, sealed_dir_for
from lattice.dataset.manifests import GoldenItem

GOLDEN = DATA.parents[1] / "evals" / "golden"
PACKET = DATA.parents[1] / "docs" / "golden-review"


def add_parsers(
    dataset_commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    split_cmd = dataset_commands.add_parser("splits", help="Golden dev/held-out split report")
    split_cmd.add_argument("--propose", action="store_true")
    split_cmd.add_argument("--shares", help="e.g. dev=0.17,held_out=0.48,sealed=0.35")
    split_cmd.add_argument("--write", action="store_true")
    split_cmd.add_argument("--json", action="store_true")
    freeze = dataset_commands.add_parser("freeze-labels", help="Record golden label digests")
    freeze.add_argument("--reason", required=True)
    freeze.add_argument("--item", action="append", default=[])
    freeze.add_argument("--phase3-correction", action="store_true")
    packet = dataset_commands.add_parser("review-packet", help="Render the owner review packet")
    packet.add_argument("--out", type=Path, default=PACKET)
    packet.add_argument("--check", action="store_true")
    record = dataset_commands.add_parser(
        "record-review", help="Record the owner's review decisions"
    )
    record.add_argument("decisions", type=Path)
    record.add_argument("--phase3-correction", action="store_true")
    record.add_argument("--dry-run", action="store_true")
    for command in (split_cmd, freeze, packet, record):
        command.add_argument("--data", type=Path, default=DATA)
        command.add_argument("--golden", type=Path, default=GOLDEN)


def run(args: argparse.Namespace) -> int:
    data = load_all(args.data, args.golden)
    if data.problems:
        for problem in data.problems:
            print(problem, file=sys.stderr)
        return 1
    items = [item for _, item in data.goldens]
    if args.dataset_command == "splits":
        return _splits(data.goldens, items, args)
    if args.dataset_command == "freeze-labels":
        return _freeze(items, args)
    if args.dataset_command == "record-review":
        return _record(data, args)
    return _packet(data, args)


def _set_fields(path: Path, item_id: str, lines: list[str]) -> None:
    """Insert ``lines`` after the item's ``label_source`` line."""
    text = path.read_text(encoding="utf-8")
    block = re.search(rf"^- id: {re.escape(item_id)}\n.*?^  label_source: \S+\n", text, re.M | re.S)
    if block is None:
        raise ValueError(f"{path.name}: {item_id} has no label_source line")
    end = block.end()
    path.write_text(text[:end] + "".join(lines) + text[end:], encoding="utf-8")


def _splits(
    goldens: list[tuple[Path, GoldenItem]], items: list[GoldenItem], args: argparse.Namespace
) -> int:
    if args.propose:
        try:
            shares = splits.parse_shares(args.shares) if args.shares else None
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        unassigned = _unassigned(items)
        proposed = {i: s for i, s in splits.propose(items, shares).items() if i in unassigned}
        if not args.write:
            for item_id, split in sorted(proposed.items()):
                print(f"{item_id}: {split}")
            print(f"proposed: {dict(sorted(Counter(proposed.values()).items()))}")
            return 0
        batches = _new_batches(items, proposed)
        sealed_dir = sealed_dir_for(args.golden)
        for path, item in goldens:
            if item.id not in proposed:
                continue
            split = proposed[item.id]
            lines = [f"  split: {split}\n"]
            if item.id in batches and item.owner_review is None:
                lines.append(f"  owner_review: {{batch: {batches[item.id]}, status: pending}}\n")
            _set_fields(path, item.id, lines)
            home = sealed_dir if split == "sealed" else args.golden
            if path.parent != home:
                _move_item(path, home / path.name, item.id)
        counts = dict(sorted(Counter(proposed.values()).items()))
        print(f"wrote splits for {len(proposed)} items: {counts}")
        return 0
    data = splits.report(items)
    print(json.dumps(data, indent=1) if args.json else splits.render(data), end="")
    problems = splits.check(items)
    for problem in problems:
        print(f"{problem.where}: {problem.message}", file=sys.stderr)
    return 1 if problems else 0


def _unassigned(items: list[GoldenItem]) -> set[str]:
    return {i.id for i in items if i.split is None}


def _new_batches(items: list[GoldenItem], proposed: dict[str, str]) -> dict[str, str]:
    """Review batches for newly assigned held-out and sealed items, numbered after the
    existing batches of their kind (reviewed batches are never reopened)."""
    batches: dict[str, str] = {}
    for split, prefix in (("held_out", "H"), ("sealed", "S")):
        new = [i for i in items if proposed.get(i.id) == split and i.owner_review is None]
        used = [
            int(i.owner_review.batch[1:])
            for i in items
            if i.owner_review and i.owner_review.batch.startswith(prefix)
        ]
        if new:
            batches |= splits.assign_batches(new, prefix=prefix, start=max(used, default=0) + 1)
    return batches


def _move_item(source: Path, target: Path, item_id: str) -> None:
    """Move one item's block (``- id: ...`` up to the next item or top-level comment) to
    the end of ``target``, which starts with ``source``'s header comment when new."""
    text = source.read_text(encoding="utf-8")
    block = re.search(rf"^- id: {re.escape(item_id)}\n(?:(?!- |#).*\n|\n)*", text, re.M)
    if block is None:
        raise ValueError(f"{source.name}: no block for {item_id}")
    rest = text[: block.start()] + text[block.end() :]
    if target.exists():
        head = target.read_text(encoding="utf-8").rstrip("\n") + "\n\n"
    else:
        header = re.match(r"(?:#.*\n)*", text)
        head = (header.group(0) if header else "") + "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(head + block.group(0).rstrip("\n") + "\n", encoding="utf-8")
    if re.search(r"^- id: ", rest, re.M):
        source.write_text(rest.rstrip("\n") + "\n", encoding="utf-8")
    else:
        source.unlink()


def _freeze(items: list[GoldenItem], args: argparse.Namespace) -> int:
    current = provenance.load_freeze(args.golden)
    try:
        freeze, changed = provenance.update(
            items,
            current,
            reason=args.reason,
            item_ids=args.item,
            phase3_correction=args.phase3_correction,
            commit=provenance.current_commit(),
        )
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    provenance.write_freeze(args.golden, freeze)
    print(
        f"label freeze: {len(freeze.phase3)} Phase 3, {len(freeze.held_out)} held-out, "
        f"{len(freeze.sealed)} sealed items; {len(changed)} recorded"
    )
    return 0


def _record(data: Dataset, args: argparse.Namespace) -> int:
    try:
        summary = decisions.record(
            data,
            args.decisions,
            phase3_correction=args.phase3_correction,
            dry_run=args.dry_run,
            commit=provenance.current_commit(),
        )
    except ValueError as exc:
        for line in str(exc).splitlines():
            print(f"refused: {line}", file=sys.stderr)
        return 1
    print("\n".join(summary.lines()))
    return 0


def _packet(data: Dataset, args: argparse.Namespace) -> int:
    rendered = review.render_packet(data)
    if args.check:
        stale = [
            name
            for name, text in rendered.items()
            if not (args.out / name).exists()
            or (args.out / name).read_text(encoding="utf-8") != text
        ]
        extra = sorted(p.name for p in args.out.glob("*.md") if p.name not in rendered)
        for name in [*stale, *extra]:
            print(f"out of date: {args.out / name}", file=sys.stderr)
        return 1 if stale or extra else 0
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (args.out / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(rendered)} files to {args.out}")
    return 0
