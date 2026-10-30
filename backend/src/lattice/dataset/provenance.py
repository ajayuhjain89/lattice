"""Label provenance and label freezes for the golden set (P13.2; phase-13-plan EC-7, EC-8).

    lattice dataset freeze-labels --reason R [--item ID ...] [--phase3-correction]

A **label digest** is the SHA-256 of everything an item expects -- question, persona,
scope, every expectation, tags, notes -- and, for an exhaustive item, its exhaustive
review. It excludes the fields that only place the item in the evaluation (provenance,
split, groups, classes, owner review; ``manifests.METADATA_FIELDS``), so reviewing or
re-splitting an item never changes its digest, and the digest of a Phase 3 item equals
the digest of the same item before P13.2 added those fields.

``evals/golden/provenance/label-freeze.json`` holds three freezes, checked by the lint:

* ``phase3`` -- the 89 items authored in Phase 3, as they were at 22fd800. Their labels
  are the historical benchmark: a factual correction is made in its own commit, with
  ``--phase3-correction``, the evidence in the reason, and a change record.
* ``held_out`` -- every held-out item, frozen before any tuning (EC-7). Changing a
  frozen held-out label needs the product owner (D-41) and leaves a change record.
* ``sealed`` -- every sealed item (phase-13-followups §7), frozen with the same rules as
  held_out. Sealed items load only with the sealed directory, so the lint checks this
  freeze only when it was loaded (``lattice dataset lint`` always loads it).

The freeze is never rewritten silently: an item whose digest no longer matches is a lint
problem until ``freeze-labels`` records the change and its reason.
"""

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from lattice.dataset.manifests import METADATA_FIELDS, GoldenItem

SCHEMA = "lattice-golden-label-freeze/2"
_READABLE = (SCHEMA, "lattice-golden-label-freeze/1")  # /1 had no sealed freeze
FREEZE_FILE = Path("provenance") / "label-freeze.json"


def label_digest(item: GoldenItem) -> str:
    data: dict[str, Any] = item.model_dump(mode="json", exclude=set(METADATA_FIELDS))
    if item.exhaustive_review is not None:
        data["exhaustive_review"] = item.exhaustive_review.model_dump(mode="json")
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class Freeze:
    phase3: dict[str, str] = field(default_factory=dict)
    phase3_frozen_at: str = ""
    held_out: dict[str, str] = field(default_factory=dict)
    held_out_frozen_at: str = ""
    sealed: dict[str, str] = field(default_factory=dict)
    changes: list[dict[str, str]] = field(default_factory=list)

    def frozen(self, split: str) -> dict[str, str]:
        """The freeze of a frozen split (``held_out`` or ``sealed``)."""
        return self.held_out if split == "held_out" else self.sealed

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "phase3": {
                "frozen_at": self.phase3_frozen_at,
                "items": dict(sorted(self.phase3.items())),
            },
            "held_out": {
                "frozen_at": self.held_out_frozen_at,
                "items": dict(sorted(self.held_out.items())),
            },
            "sealed": {"items": dict(sorted(self.sealed.items()))},
            "changes": self.changes,
        }


def current_commit() -> str:
    """The short commit of the working tree (recorded with freezes and sealed looks)."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def freeze_path(golden_dir: Path) -> Path:
    return golden_dir / FREEZE_FILE


def load_freeze(golden_dir: Path) -> Freeze | None:
    path = freeze_path(golden_dir)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema") not in _READABLE:
        raise ValueError(f"{path}: unknown schema {raw.get('schema')!r}")
    return Freeze(
        phase3=dict(raw["phase3"]["items"]),
        phase3_frozen_at=raw["phase3"]["frozen_at"],
        held_out=dict(raw["held_out"]["items"]),
        held_out_frozen_at=raw["held_out"]["frozen_at"],
        sealed=dict(raw.get("sealed", {}).get("items", {})),
        changes=list(raw.get("changes", [])),
    )


def write_freeze(golden_dir: Path, freeze: Freeze) -> None:
    path = freeze_path(golden_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(freeze.to_json(), indent=1) + "\n", encoding="utf-8")


def check(
    items: list[GoldenItem], freeze: Freeze | None, *, sealed_loaded: bool = False
) -> list[tuple[str, str]]:
    """(item or file, problem) for every item whose labels left their freeze. The sealed
    freeze is checked only when the sealed items were loaded."""
    if freeze is None:
        return [(str(FREEZE_FILE), "missing: run `lattice dataset freeze-labels`")]
    problems: list[tuple[str, str]] = []
    by_id = {item.id: item for item in items}
    for item_id, digest in sorted(freeze.phase3.items()):
        item = by_id.get(item_id)
        if item is None:
            problems.append((item_id, "a Phase 3 item was removed (the benchmark keeps it)"))
            continue
        if item.label_source in ("authored_phase13", "authored_f2"):
            problems.append((item_id, f"a Phase 3 item cannot be labelled {item.label_source}"))
        if label_digest(item) != digest:
            problems.append(
                (
                    item_id,
                    "Phase 3 labels changed: a correction needs its own commit and "
                    "`freeze-labels --phase3-correction --item ... --reason <evidence>`",
                )
            )
    for item in items:
        if item.label_source == "authored_phase3" and item.id not in freeze.phase3:
            problems.append((item.id, "authored_phase3 but not in the Phase 3 freeze"))
    for split in ("held_out", "sealed") if sealed_loaded else ("held_out",):
        name = "held-out" if split == "held_out" else "sealed"
        frozen = freeze.frozen(split)
        for item_id, digest in sorted(frozen.items()):
            item = by_id.get(item_id)
            if item is None:
                problems.append((item_id, f"a frozen {name} item was removed"))
            elif item.split != split:
                problems.append((item_id, f"a frozen {name} item moved out of {split}"))
            elif label_digest(item) != digest:
                problems.append(
                    (
                        item_id,
                        f"frozen {name} labels changed: needs the product owner and "
                        "`freeze-labels --item ... --reason ...`",
                    )
                )
        if frozen or split == "sealed":
            for item in items:
                if item.split == split and item.id not in frozen:
                    problems.append((item.id, f"{split} item not in the {name} freeze"))
    return problems


def update(
    items: list[GoldenItem],
    freeze: Freeze | None,
    *,
    reason: str,
    item_ids: list[str],
    phase3_correction: bool,
    commit: str,
    today: date | None = None,
) -> tuple[Freeze, list[str]]:
    """Record new digests. With no freeze yet, freezes every Phase 3 and held-out item.
    Otherwise only ``item_ids`` may change (refusing anything else), each with a change
    record; Phase 3 items only with ``phase3_correction``. Held-out and sealed items not
    yet frozen are frozen. Returns the freeze and what changed. ``items`` must include
    the sealed items (``lint.load_all``) once any are frozen."""
    if not reason.strip():
        raise ValueError("a label freeze change needs a reason")
    on = (today or date.today()).isoformat()
    by_id = {item.id: item for item in items}
    if freeze is None:
        new = Freeze(
            phase3={i.id: label_digest(i) for i in items if i.label_source == "authored_phase3"},
            phase3_frozen_at=commit,
            held_out={i.id: label_digest(i) for i in items if i.split == "held_out"},
            held_out_frozen_at=commit,
            changes=[{"on": on, "items": "all", "reason": reason.strip()}],
        )
        return new, sorted(new.phase3) + sorted(new.held_out)
    changed: list[str] = []
    for item_id in item_ids:
        item = by_id.get(item_id)
        if item is None:
            raise ValueError(f"no golden item {item_id}")
        if item_id in freeze.phase3:
            if not phase3_correction:
                raise ValueError(f"{item_id} is a Phase 3 item: pass --phase3-correction")
            freeze.phase3[item_id] = label_digest(item)
            changed.append(item_id)
        for split in ("held_out", "sealed"):
            frozen = freeze.frozen(split)
            if item.split == split:
                frozen[item_id] = label_digest(item)
                changed.append(item_id)
            elif item_id in frozen:
                del frozen[item_id]
                changed.append(item_id)
    # Held-out and sealed items frozen for the first time (added after the first freeze).
    for item in items:
        if item.split in ("held_out", "sealed") and item.id not in freeze.frozen(item.split):
            freeze.frozen(item.split)[item.id] = label_digest(item)
            changed.append(item.id)
    # Like the lint, the sealed freeze is checked only when sealed items were loaded
    # (``freeze-labels`` always loads them); some loaded and others missing is refused.
    sealed_loaded = any(item.split == "sealed" for item in items)
    for split in ("held_out", "sealed") if sealed_loaded else ("held_out",):
        name = "held-out" if split == "held_out" else "sealed"
        for item_id, digest in freeze.frozen(split).items():
            if item_id not in by_id:
                raise ValueError(f"{item_id}: frozen {name} item not loaded")
            if item_id not in item_ids and label_digest(by_id[item_id]) != digest:
                raise ValueError(f"{item_id}: frozen {name} labels changed; name it with --item")
    for item_id, digest in freeze.phase3.items():
        if item_id not in item_ids and label_digest(by_id[item_id]) != digest:
            raise ValueError(f"{item_id}: Phase 3 labels changed; name it with --item")
    if changed:
        freeze.changes.append(
            {"on": on, "items": ", ".join(sorted(set(changed))), "reason": reason.strip()}
        )
    return freeze, sorted(set(changed))
