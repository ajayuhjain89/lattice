"""Sealed evaluations (phase-13-followups §7.4).

    lattice eval retrieval|answer --sealed --reason R

Sealed items live in ``evals/golden-sealed`` and are evaluated only at a checkpoint's
final decision. A sealed run loads that directory, evaluates its items alone and writes
its report under ``<out>/sealed/``. Such a report is never a baseline
(``baseline.write_baseline`` refuses one). Every sealed look is appended to
``evals/golden/provenance/sealed-access.json`` *before* the run starts, so a run that
fails still counts. Without ``--sealed``, the runners never load sealed items. They also
refuse items that have no split yet: no run looks at an item before its split is
assigned and frozen.
"""

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lattice.dataset.lint import Dataset, load_dataset, sealed_dir_for
from lattice.dataset.manifests import GoldenItem
from lattice.dataset.provenance import current_commit

SCHEMA = "lattice-sealed-access/1"
ACCESS_FILE = Path("provenance") / "sealed-access.json"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--sealed",
        action="store_true",
        help="evaluate the sealed split only: a checkpoint's final decision (logged)",
    )
    parser.add_argument("--reason", help="why this sealed look is needed (required with --sealed)")


def argument_problem(args: argparse.Namespace) -> str | None:
    if args.sealed and not (args.reason or "").strip():
        return "--sealed needs --reason: every sealed look is recorded"
    if args.reason and not args.sealed:
        return "--reason is only for --sealed runs"
    return None


def load(data: Path, golden: Path, *, sealed: bool) -> Dataset:
    return load_dataset(data, golden, sealed_dir=sealed_dir_for(golden) if sealed else None)


def restrict(items: Sequence[GoldenItem], *, sealed: bool) -> tuple[list[GoldenItem], str | None]:
    """The items a run may evaluate, or why it may not run."""
    chosen = [i for i in items if (i.split == "sealed") == sealed]
    unassigned = sorted(i.id for i in chosen if i.split is None)
    if unassigned:
        return [], (
            f"{len(unassigned)} selected item(s) have no split ({', '.join(unassigned[:3])}"
            f"{', ...' if len(unassigned) > 3 else ''}): assign and freeze splits first"
        )
    if sealed and not chosen:
        return [], "no sealed items selected"
    return chosen, None


def access_path(golden_dir: Path) -> Path:
    return golden_dir / ACCESS_FILE


def record_access(
    golden_dir: Path,
    *,
    kind: str,
    reason: str,
    items: Sequence[GoldenItem],
    settings: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one sealed look to the access log and return it."""
    path = access_path(golden_dir)
    log: dict[str, Any] = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {"schema": SCHEMA, "looks": []}
    )
    if log.get("schema") != SCHEMA:
        raise ValueError(f"{path}: unknown schema {log.get('schema')!r}")
    entry = {
        "at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "commit": current_commit(),
        "kind": kind,
        "reason": reason.strip(),
        "items": len(items),
        "settings": settings,
    }
    log["looks"].append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=1) + "\n", encoding="utf-8")
    return entry
