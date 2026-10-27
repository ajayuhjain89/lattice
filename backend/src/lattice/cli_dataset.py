"""``lattice dataset splits | freeze-labels | review-packet`` (P13.2; phase-13-plan EC-7,
EC-8, D-41). Files only: no database, no model.

    lattice dataset splits [--propose [--write]] [--json]
        The split report (denominators per stratum, class coverage, shared target
        records, owner-review workload). --propose prints a group-aware stratified split
        for items that have none; --write records it in the golden files, with an
        owner-review batch (status pending) for every new held-out item.
    lattice dataset freeze-labels --reason R [--item ID ...] [--phase3-correction]
        Record label digests (lattice.dataset.provenance): the first run freezes the
        Phase 3 and held-out labels; later runs record the named items' changes only.
    lattice dataset review-packet [--out DIR] [--check]
        Render the product owner's held-out review packet (one Markdown file per batch);
        --check fails if the committed packet differs from the labels.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from lattice.dataset import provenance, review, splits
from lattice.dataset.distractors import DATA
from lattice.dataset.lint import Dataset, load_dataset
from lattice.dataset.manifests import GoldenItem

GOLDEN = DATA.parents[1] / "evals" / "golden"
PACKET = DATA.parents[1] / "docs" / "golden-review"


def add_parsers(
    dataset_commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    split_cmd = dataset_commands.add_parser("splits", help="Golden dev/held-out split report")
    split_cmd.add_argument("--propose", action="store_true")
    split_cmd.add_argument("--write", action="store_true")
    split_cmd.add_argument("--json", action="store_true")
    freeze = dataset_commands.add_parser("freeze-labels", help="Record golden label digests")
    freeze.add_argument("--reason", required=True)
    freeze.add_argument("--item", action="append", default=[])
    freeze.add_argument("--phase3-correction", action="store_true")
    packet = dataset_commands.add_parser("review-packet", help="Render the owner review packet")
    packet.add_argument("--out", type=Path, default=PACKET)
    packet.add_argument("--check", action="store_true")
    for command in (split_cmd, freeze, packet):
        command.add_argument("--data", type=Path, default=DATA)
        command.add_argument("--golden", type=Path, default=GOLDEN)


def run(args: argparse.Namespace) -> int:
    data = load_dataset(args.data, args.golden)
    if data.problems:
        for problem in data.problems:
            print(problem, file=sys.stderr)
        return 1
    items = [item for _, item in data.goldens]
    if args.dataset_command == "splits":
        return _splits(data.goldens, items, args)
    if args.dataset_command == "freeze-labels":
        return _freeze(items, args)
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
        proposed = {i: s for i, s in splits.propose(items).items() if i in _unassigned(items)}
        if not args.write:
            for item_id, split in sorted(proposed.items()):
                print(f"{item_id}: {split}")
            return 0
        held = [i for i in items if proposed.get(i.id, i.split) == "held_out"]
        batches = splits.assign_batches(held)
        for path, item in goldens:
            if item.id not in proposed:
                continue
            lines = [f"  split: {proposed[item.id]}\n"]
            if proposed[item.id] == "held_out" and item.owner_review is None:
                lines.append(f"  owner_review: {{batch: {batches[item.id]}, status: pending}}\n")
            _set_fields(path, item.id, lines)
        print(f"wrote splits for {len(proposed)} items")
        return 0
    data = splits.report(items)
    print(json.dumps(data, indent=1) if args.json else splits.render(data), end="")
    problems = splits.check(items)
    for problem in problems:
        print(f"{problem.where}: {problem.message}", file=sys.stderr)
    return 1 if problems else 0


def _unassigned(items: list[GoldenItem]) -> set[str]:
    return {i.id for i in items if i.split is None}


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _freeze(items: list[GoldenItem], args: argparse.Namespace) -> int:
    current = provenance.load_freeze(args.golden)
    try:
        freeze, changed = provenance.update(
            items,
            current,
            reason=args.reason,
            item_ids=args.item,
            phase3_correction=args.phase3_correction,
            commit=_commit(),
        )
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    provenance.write_freeze(args.golden, freeze)
    print(
        f"label freeze: {len(freeze.phase3)} Phase 3, {len(freeze.held_out)} held-out items; "
        f"{len(changed)} recorded"
    )
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
