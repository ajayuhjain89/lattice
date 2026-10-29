"""Development, held-out and sealed splits of the golden set (P13.2; phase-13-plan EC-7;
phase-13-followups §7).

    lattice dataset splits [--propose [--shares dev=F,held_out=F,sealed=F] [--write]] [--json]

**Units.** Items that ask for the same information need stay on one side: the split unit
is a connected component of ``same_question_group`` (the identical question asked by
different personas) and ``paraphrase_group`` (the same need in other words, or a variant
using the same decisive answer facts). The lint refuses a unit that straddles both
splits, and questions so similar that they are near-duplicates (content-word Jaccard
>= ``NEAR_DUPLICATE``) on different sides unless they are one unit.

**Proposal** (``--propose``). Deterministic and group-aware: units are taken largest
first (ties by a hash of the unit's item IDs) and each goes to the split that keeps the
held-out share of every stratum closest to ``HELD_OUT_SHARE`` -- category,
organization, persona, expected status, label source, number of target records,
evaluation class, security tag. The committed ``split`` values are the source of truth;
the proposal only fills items that have none, so adding items later never reshuffles
the existing splits.

**Shares** (``--shares``; F2). The new items alone are divided between the named splits
in the given shares, stratified among themselves on the same strata: the committed
splits are already balanced, and a split with no committed items (sealed) can only be
balanced over the new ones. A new item in a unit with a committed item inherits that
unit's split (a paraphrase of a dev item goes to dev) and is not counted in the shares.

**Report.** Denominators per split for each stratum, class coverage, and the target
records that appear on both sides (the same document can serve different information
needs; that overlap is reported, not forbidden).
"""

import hashlib
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from lattice.dataset.manifests import KEY_REF, GoldenItem

HELD_OUT_SHARE = 0.35
NEAR_DUPLICATE = 0.75
SPLITS = ("dev", "held_out", "sealed")

_WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_STOP = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "now",
        "of",
        "on",
        "or",
        "our",
        "so",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "to",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)


def _content_words(question: str) -> frozenset[str]:
    text = re.sub(r"['\u2019](s|re|ve|ll|d|t)\b", "", question.lower())
    words = (w for w in _WORD.findall(text) if w not in _STOP)
    return frozenset(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)


def similarity(a: str, b: str) -> float:
    x, y = _content_words(a), _content_words(b)
    return len(x & y) / len(x | y) if x | y else 0.0


def units(items: Sequence[GoldenItem]) -> list[list[GoldenItem]]:
    """Connected components over same_question_group and paraphrase_group."""
    parent = {item.id: item.id for item in items}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_group: dict[str, list[str]] = defaultdict(list)
    for item in items:
        if item.same_question_group:
            by_group[f"q:{item.same_question_group}"].append(item.id)
        if item.paraphrase_group:
            by_group[f"p:{item.paraphrase_group}"].append(item.id)
    for members in by_group.values():
        for other in members[1:]:
            parent[find(other)] = find(members[0])
    components: dict[str, list[GoldenItem]] = defaultdict(list)
    for item in items:
        components[find(item.id)].append(item)
    return sorted(components.values(), key=lambda u: sorted(i.id for i in u))


def near_duplicates(items: Sequence[GoldenItem]) -> list[tuple[GoldenItem, GoldenItem, float]]:
    """Pairs of different units whose questions are near-duplicates."""
    unit_of = {item.id: n for n, unit in enumerate(units(items)) for item in unit}
    out = []
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            if unit_of[a.id] == unit_of[b.id]:
                continue
            score = similarity(a.question, b.question)
            if score >= NEAR_DUPLICATE:
                out.append((a, b, round(score, 3)))
    return out


def target_records(item: GoldenItem) -> set[str]:
    """Records (``[org/]kind:id``) of the item's relevant and not-used evidence."""
    out = set()
    for _, expected in item.expectations():
        for key in (*expected.relevant, *expected.not_used_for_answer):
            match = KEY_REF.fullmatch(key)
            if match:
                out.add(f"{match['org'] or item.organization}/{match['ref']}")
    return out


def _statuses(item: GoldenItem) -> list[str]:
    return sorted({e.status for _, e in item.expectations()})


def _targets_bucket(item: GoldenItem) -> str:
    n = len(target_records(item))
    return "3+" if n >= 3 else str(n)


def strata(item: GoldenItem) -> list[str]:
    """The strata an item counts in, ``dimension:value``."""
    values = [
        f"category:{item.category}",
        f"organization:{item.organization}",
        f"persona:{item.organization}/{item.persona}",
        f"label_source:{item.label_source}",
        f"targets:{_targets_bucket(item)}",
        f"security:{'security' in item.tags}",
        *(f"status:{s}" for s in _statuses(item)),
        *(f"class:{c}" for c in item.classes),
    ]
    return values


def _unit_key(unit: Sequence[GoldenItem]) -> tuple[int, str]:
    ids = ",".join(sorted(i.id for i in unit))
    return (-len(unit), hashlib.sha256(ids.encode()).hexdigest())


def _committed(unit: Sequence[GoldenItem]) -> str | None:
    fixed = {i.split for i in unit if i.split is not None}
    return next((s for s in reversed(SPLITS) if s in fixed), None)


OVERALL_WEIGHT = 25.0  # the overall held-out share dominates any single stratum


def propose(items: Sequence[GoldenItem], shares: dict[str, float] | None = None) -> dict[str, str]:
    """A split for every item without one, keeping committed splits as they are."""
    if shares is not None:
        return _propose_shares(items, shares)
    totals: Counter[str] = Counter(s for item in items for s in strata(item))
    totals["overall"] = len(items)
    weight = {s: OVERALL_WEIGHT if s == "overall" else 1.0 for s in totals}
    held: Counter[str] = Counter()
    assigned: dict[str, str] = {}
    free: list[tuple[list[GoldenItem], Counter[str]]] = []
    for unit in sorted(units(items), key=_unit_key):
        counts = Counter(s for item in unit for s in strata(item))
        counts["overall"] = len(unit)
        split = _committed(unit)
        if split is not None:
            for item in unit:
                assigned[item.id] = split
            if split == "held_out":
                held.update(counts)
        else:
            free.append((unit, counts))

    def delta(counts: Counter[str], sign: int) -> float:
        # Change of sum_s w_s (held_s - share * total_s)^2 / total_s when the unit moves
        # into (sign 1) or out of (sign -1) held_out.
        out = 0.0
        for s, n in counts.items():
            before = held[s] - HELD_OUT_SHARE * totals[s]
            after = before + sign * n
            out += weight[s] * (after * after - before * before) / totals[s]
        return out

    in_held = [False] * len(free)
    for n, (_, counts) in enumerate(free):  # greedy, largest units first
        if delta(counts, 1) < 0:
            held.update(counts)
            in_held[n] = True
    for _ in range(100):  # then single-unit moves while the deviation falls
        moved = False
        for n, (_, counts) in enumerate(free):
            sign = -1 if in_held[n] else 1
            if delta(counts, sign) < -1e-9:
                if in_held[n]:
                    held.subtract(counts)
                else:
                    held.update(counts)
                in_held[n] = not in_held[n]
                moved = True
        if not moved:
            break
    for n, (unit, _) in enumerate(free):
        for item in unit:
            assigned[item.id] = "held_out" if in_held[n] else "dev"
    return assigned


def parse_shares(text: str) -> dict[str, float]:
    """``dev=0.17,held_out=0.48,sealed=0.35`` -> shares that sum to 1."""
    shares: dict[str, float] = {}
    for part in text.split(","):
        name, _, value = part.partition("=")
        name = name.strip()
        if name not in SPLITS or name in shares:
            raise ValueError(f"unknown or repeated split {name!r} (one of {', '.join(SPLITS)})")
        try:
            shares[name] = float(value)
        except ValueError:
            raise ValueError(f"share of {name} is not a number: {value!r}") from None
        if shares[name] <= 0:
            raise ValueError(f"share of {name} must be positive")
    if abs(sum(shares.values()) - 1) > 1e-6:
        raise ValueError(f"shares sum to {sum(shares.values()):.3f}, not 1")
    return shares


def _propose_shares(items: Sequence[GoldenItem], shares: dict[str, float]) -> dict[str, str]:
    assigned: dict[str, str] = {}
    free: list[tuple[list[GoldenItem], Counter[str]]] = []
    for unit in sorted(units(items), key=_unit_key):
        split = _committed(unit)
        if split is not None:
            for item in unit:
                assigned[item.id] = split
            continue
        counts = Counter(s for item in unit for s in strata(item))
        counts["overall"] = len(unit)
        free.append((unit, counts))
    totals: Counter[str] = Counter()
    for _, counts in free:
        totals.update(counts)
    names = sorted(shares)
    weight = {s: OVERALL_WEIGHT if s == "overall" else 1.0 for s in totals}
    held: dict[str, Counter[str]] = {name: Counter() for name in names}

    def delta(counts: Counter[str], name: str, sign: int) -> float:
        # Change of sum_s w_s (n_s - share * total_s)^2 / total_s for split ``name`` when
        # the unit moves into (sign 1) or out of (sign -1) it.
        out = 0.0
        for s, n in counts.items():
            before = held[name][s] - shares[name] * totals[s]
            after = before + sign * n
            out += weight[s] * (after * after - before * before) / totals[s]
        return out

    where: list[str] = []
    for _, counts in free:  # greedy, largest units first
        best = min(names, key=lambda name: (delta(counts, name, 1), names.index(name)))
        held[best].update(counts)
        where.append(best)
    for _ in range(100):  # then single-unit moves while the deviation falls
        moved = False
        for n, (_, counts) in enumerate(free):
            here = where[n]
            leave = delta(counts, here, -1)
            gains = {name: leave + delta(counts, name, 1) for name in names if name != here}
            target = min(gains, key=lambda name: (gains[name], names.index(name)), default=None)
            if target is not None and gains[target] < -1e-9:
                held[here].subtract(counts)
                held[target].update(counts)
                where[n] = target
                moved = True
        if not moved:
            break
    for n, (unit, _) in enumerate(free):
        for item in unit:
            assigned[item.id] = where[n]
    return assigned


@dataclass
class SplitProblem:
    where: str
    message: str


def check(items: Sequence[GoldenItem], unassigned_ok: Iterable[str] = ()) -> list[SplitProblem]:
    """Split problems; items in ``unassigned_ok`` (staged, being authored) may have none."""
    allowed = set(unassigned_ok)
    problems = []
    for item in items:
        if item.split is None and item.id not in allowed:
            problems.append(SplitProblem(item.id, "no split (`lattice dataset splits --propose`)"))
    for unit in units(items):
        sides = {i.split for i in unit if i.split is not None}
        if len(sides) > 1:
            ids = ", ".join(sorted(i.id for i in unit))
            named = " and ".join(sorted(sides))
            problems.append(SplitProblem(ids, f"one information need straddles {named}"))
    for a, b, score in near_duplicates(items):
        if a.split != b.split:
            problems.append(
                SplitProblem(
                    f"{a.id} / {b.id}",
                    f"near-duplicate questions ({score}) on different splits: group them",
                )
            )
    paraphrases: dict[str, list[GoldenItem]] = defaultdict(list)
    for item in items:
        if item.paraphrase_group:
            paraphrases[item.paraphrase_group].append(item)
    for name, members in sorted(paraphrases.items()):
        if len(members) < 2:
            problems.append(SplitProblem(f"paraphrase group {name}", "needs at least two items"))
        questions = Counter(m.question for m in members)
        for question, count in questions.items():
            asked = {m.same_question_group for m in members if m.question == question}
            if count > 1 and (len(asked) != 1 or None in asked):
                problems.append(
                    SplitProblem(
                        f"paraphrase group {name}",
                        "identical questions belong in a same_question_group",
                    )
                )
    return problems


def _table(items: Iterable[GoldenItem]) -> dict[str, dict[str, int]]:
    table: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(SPLITS, 0))
    for item in items:
        for s in strata(item):
            table[s][item.split or "unassigned"] = table[s].get(item.split or "unassigned", 0) + 1
    return {k: dict(v) for k, v in sorted(table.items())}


def report(items: Sequence[GoldenItem]) -> dict[str, Any]:
    split_counts = Counter(item.split or "unassigned" for item in items)
    records: dict[str, set[str]] = {split: set() for split in SPLITS}
    for item in items:
        if item.split in records:
            records[item.split] |= target_records(item)
    shared = sorted(records["dev"] & records["held_out"])
    unit_list = units(items)
    held_items = [i for i in items if i.split == "held_out"]
    review = Counter(
        (i.owner_review.status if i.owner_review else "no review record") for i in held_items
    )
    sealed_items = [i for i in items if i.split == "sealed"]
    with_targets = Counter(i.split or "unassigned" for i in items if target_records(i))
    return {
        "items": len(items),
        "splits": dict(sorted(split_counts.items())),
        "held_out_share": round(split_counts["held_out"] / len(items), 3) if items else None,
        "units": {
            "total": len(unit_list),
            "multi_item": sum(1 for u in unit_list if len(u) > 1),
            "largest": max((len(u) for u in unit_list), default=0),
        },
        "strata": _table(items),
        "with_targets": dict(sorted(with_targets.items())),
        "target_records": {
            "dev": len(records["dev"]),
            "held_out": len(records["held_out"]),
            "sealed": len(records["sealed"]),
            "shared": len(shared),
            "shared_records": shared,
            # Decisive-record overlap (F2): sealed target records that dev items also target.
            "sealed_shared_with_dev": len(records["dev"] & records["sealed"]),
        },
        "owner_review": {
            "held_out_items": len(held_items),
            "status": dict(sorted(review.items())),
            "batches": dict(
                sorted(Counter(i.owner_review.batch for i in held_items if i.owner_review).items())
            ),
            "sealed_items": len(sealed_items),
            "sealed_status": dict(
                sorted(
                    Counter(
                        (i.owner_review.status if i.owner_review else "no review record")
                        for i in sealed_items
                    ).items()
                )
            ),
        },
        "exhaustive": {
            split: sum(1 for i in items if i.exhaustive and i.split == split) for split in SPLITS
        },
    }


def render(data: dict[str, Any]) -> str:
    lines = [
        "# Golden set splits",
        "",
        f"{data['items']} items: "
        + ", ".join(f"{k} {v}" for k, v in data["splits"].items())
        + f" (held-out share {data['held_out_share']}); {data['units']['total']} split units, "
        f"{data['units']['multi_item']} with several items (largest {data['units']['largest']}).",
        "",
        "| Stratum | dev | held_out | sealed |",
        "| ------- | --: | -------: | -----: |",
    ]
    lines += [
        f"| {name} | {row.get('dev', 0)} | {row.get('held_out', 0)} | {row.get('sealed', 0)} |"
        for name, row in data["strata"].items()
    ]
    t = data["target_records"]
    r = data["owner_review"]
    lines += [
        "",
        "Items with targets: "
        + ", ".join(f"{k} {v}" for k, v in data["with_targets"].items())
        + ".",
        "",
        f"Target records: dev {t['dev']}, held_out {t['held_out']}, sealed {t['sealed']}; "
        f"dev and held_out share {t['shared']}, dev and sealed {t['sealed_shared_with_dev']} "
        "(the same record serving different information needs).",
        "",
        f"Owner review (D-41): {r['held_out_items']} held-out items; status "
        + ", ".join(f"{k} {v}" for k, v in r["status"].items())
        + "; batches "
        + ", ".join(f"{k} {v}" for k, v in r["batches"].items())
        + f". Sealed: {r['sealed_items']} items"
        + "".join(f", {k} {v}" for k, v in r["sealed_status"].items())
        + ".",
        "",
        f"Exhaustive items: dev {data['exhaustive']['dev']}, held_out "
        f"{data['exhaustive']['held_out']}, sealed {data['exhaustive']['sealed']}.",
    ]
    return "\n".join(lines) + "\n"


BATCH_MIN, BATCH_MAX = 10, 15  # D-41: batches of about 10-15 questions
_THEMES = (
    "lookup",
    "cross_document",
    "cross_source",
    "potential_conflicts",
    "supersession",
    "stale",
    "insufficient_evidence",
    "refusal",
    "ai_egress",
    "inaccessible",
    "account_visibility",
    "ambiguous",
    "prompt_injection",
    "cross_org",
)


def assign_batches(
    held_out: Sequence[GoldenItem], *, prefix: str = "H", start: int = 1
) -> dict[str, str]:
    """Owner-review batches (``H1``, ``H2``, ...; ``S1``... for sealed items, numbered from
    ``start`` so new batches follow the reviewed ones) for held-out items: units in theme
    order (their first item's category), never split, as even as the units allow within
    ``BATCH_MIN``-``BATCH_MAX`` items."""
    ordered = sorted(
        units(held_out),
        key=lambda u: (min(_THEMES.index(i.category) for i in u), sorted(i.id for i in u)),
    )
    total = sum(len(u) for u in ordered)
    count = max(1, math.ceil(total / BATCH_MAX))
    batches: dict[str, str] = {}
    number, size, done = 1, 0, 0
    for unit in ordered:
        target = (total - done + size) / (count - number + 1)
        if size and number < count and (size + len(unit) > BATCH_MAX or size >= target):
            number, size = number + 1, 0
        for item in sorted(unit, key=lambda i: i.id):
            batches[item.id] = f"{prefix}{start + number - 1}"
        size += len(unit)
        done += len(unit)
    return batches
