"""Item-level baselines and run comparison (P13.1; phase-13-plan EC-6 and §9.1).

    lattice eval baseline --report RUN --label A --out evals/baselines/retrieval-A.json --reason R
    lattice eval compare --baseline BASELINE --report RUN [--cross-platform]

A **baseline** is a compact, committed snapshot of one evaluation run: what makes runs
comparable (kind, configuration, corpus fingerprint, golden-set version and -- for
configurations whose ranking depends on the database's floating-point arithmetic -- the
database architecture), the summary with denominators, and per item the outcome of every
target (first evidence rank), every target record, the selection fate of every missed
target, the top of the ranking and a digest of the whole ranking. Answer baselines hold
per run the expected and actual status, whether a model was called and which relevant
targets reached the prompt. Record keys, IDs, codes and ranks only: no question,
evidence or answer text (S10). Full reports stay under ``evals/reports/`` (ignored).

**Compare** refuses runs that are not comparable (another kind, corpus, golden set,
embedding model or database architecture; exit 2), then reports per item and per
category with denominators:

* *security* -- any security metric of the run above 0 is an unconditional blocker
  (exit 1), whatever else is true;
* *regressions* -- a target whose first rank worsens across a k boundary (1, 5, 10, 20),
  an item losing every target found within 10, a category whose R@10 drops by more than
  2 points (slices under 10 items are marked small: the items are the finding, not the
  percentage); for answers a run whose status stops matching its expectation, a relevant
  target leaving the prompt, an abstention no longer caught before the model;
* *drift* -- any difference while the configuration versions are identical has no
  declared cause: it fails (exit 1) even when it looks like an improvement;
* *intended changes* -- a different configuration version (retrieval config, rerank,
  limit, generation model) declares the change; differences are then reported and only
  regressions fail;
* ``--cross-platform`` compares a run from another database architecture and lists
  every item whose ranking or outcome differs as platform variance instead of refusing.

A baseline is never overwritten silently: writing over an existing file needs
``--replace`` and a reason, and the new file records the digest of the one it replaces.
"""

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from lattice.answering.prompt import DEFAULT_PROMPT

SCHEMA = "lattice-eval-baseline/1"
KS = (1, 5, 10, 20)
PRIMARY_K = 10
TOP = 10
CATEGORY_DROP = 0.02  # EC-6: more than 2 points
SMALL_SLICE = 10

EXIT_OK, EXIT_FAILED, EXIT_INCOMPARABLE = 0, 1, 2


class BaselineError(Exception):
    """A baseline cannot be written (exists, unsafe run, missing reason)."""


# --- loading ------------------------------------------------------------------------------


def load(path: Path) -> dict[str, Any]:
    """A baseline or a report (``report.json``, or the directory holding it)."""
    if path.is_dir():
        path = path / "report.json"
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def normalize(report: Any) -> dict[str, Any]:
    """A report object (``RetrievalReport``/``AnswerReport``) as its JSON form."""
    data: dict[str, Any] = json.loads(json.dumps(report.to_json(), default=str))
    return data


def is_baseline(data: Mapping[str, Any]) -> bool:
    return data.get("schema") == SCHEMA


# --- snapshots ----------------------------------------------------------------------------


def _security_total(report: Mapping[str, Any]) -> int:
    return int(sum(report["summary"]["security"].values()))


def _ranking_digest(evidence: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(
        [[e["rank"], e["record"], e["anchors"], e["matched_by"]] for e in evidence],
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _chunk_key(e: Mapping[str, Any]) -> str:
    return str(e["record"]) + (f"#{e['anchors'][0]}" if e["anchors"] else "")


def _retrieval_configuration(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "embedding_provider": report.get("embedding_provider"),
        "embedding_model": report.get("embedding_model"),
        "config_version": report.get("config_version"),
        "rerank_version": report.get("rerank_version"),
        "limit": report.get("limit"),
        # phase-13-followups O13 (F5.0): batching was the only mode before the field, so
        # only one question per call is a declared change; batched runs compare with
        # baselines from before it.
        "query_embedding": (
            "per_question" if report.get("query_embedding") == "per_question" else None
        ),
    }


def _answer_configuration(report: Mapping[str, Any]) -> dict[str, Any]:
    generation = report.get("generation", {})
    return {
        "generation_provider": generation.get("provider"),
        "generation_model": generation.get("model"),
        "embedding_provider": report.get("embedding_provider"),
        "embedding_model": report.get("embedding_model"),
        "judge": report.get("judge"),
        # P13.3; reports and baselines from before the gate have none.
        "gate_version": report.get("gate_version"),
        # P13.5; reports and baselines from before it have none.
        "retrieval_config_version": report.get("retrieval_config_version"),
        # phase-13-followups §12 (E3): only another prompt than the product's is a declared
        # change, so the product's prompt compares with baselines from before the field.
        "prompt_config": _prompt_config(report),
    }


def _prompt_config(report: Mapping[str, Any]) -> dict[str, Any] | None:
    config = report.get("prompt_config")
    default = {"version": DEFAULT_PROMPT.version, "max_evidence": DEFAULT_PROMPT.max_evidence}
    return None if config in (None, default) else dict(config)


def snapshot(report: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    """The compact, comparable form of a report (a baseline without provenance)."""
    if is_baseline(report):
        return dict(report)
    kind = report.get("kind", "retrieval")
    semantic = report.get("embedding_provider") is not None
    common = {
        "schema": SCHEMA,
        "kind": kind,
        "label": label,
        # Exact and lexical ranking is platform-independent (verified on aarch64 and
        # x86-64); semantic ranking is not (evaluation-plan §15).
        "platform": report.get("database_platform") if semantic else None,
        "corpus": report.get("corpus_fingerprint", {}),
        "golden": report.get("golden_version", {}),
        "security_total": _security_total(report),
    }
    if kind == "answer":
        return common | {
            "configuration": _answer_configuration(report),
            "summary": _answer_summary(report["summary"]),
            "items": {
                f"{r['id']}|{r['egress_config']}": {
                    "category": r["category"],
                    "persona": r["persona"],
                    "split": r.get("split"),
                    "expected": r["expected_status"],
                    "status": r["status"],
                    "reason": r["status_reason"],
                    "error": r["error"],
                    "model_called": r.get("model_called", False),
                    "relevant": r.get("relevant", []),
                    "in_prompt": r["targets_in_prompt"],
                    "cited": r["targets_cited"],
                }
                for r in report["items"]
            },
        }
    return common | {
        "configuration": _retrieval_configuration(report),
        "summary": _retrieval_summary(report["summary"]),
        "items": {
            r["id"]: {
                "category": r["category"],
                "persona": r["persona"],
                "split": r.get("split"),
                "label_source": r.get("label_source"),
                "error": r["error"],
                "targets": r["found_at"],
                "documents": r.get("documents_found_at", {}),
                "misses": {m["target"]: m["cause"] for m in r["misses"]},
                "identifier_hit": r.get("identifier_hit"),
                "precision@10": r.get("precision10"),
                "top": [_chunk_key(e) for e in r["evidence"][:TOP]],
                "ranking": _ranking_digest(r["evidence"]),
            }
            for r in report["items"]
        },
    }


def _retrieval_summary(s: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("items_with_targets", "recall@10", "mrr", "document_recall@10", "ndcg@10")

    def block(b: Mapping[str, Any]) -> dict[str, Any]:
        return {k: b.get(k) for k in ("items", *keys, "recall@1", "recall@5", "recall@20")}

    return {
        "overall": block(s["overall"]),
        "by_category": {c: block(b) for c, b in s["by_category"].items()},
        "by_target_count": {n: block(b) for n, b in s.get("by_target_count", {}).items()},
        "by_split": {n: block(b) for n, b in s.get("by_split", {}).items()},
        "by_label_source": {n: block(b) for n, b in s.get("by_label_source", {}).items()},
        "exact_identifier": s.get("exact_identifier"),
        "precision@10": s.get("precision@10"),
    }


def _answer_summary(s: Mapping[str, Any]) -> dict[str, Any]:
    f = s.get("foundation", {})
    return {
        "ran": s["ran"],
        "status_accuracy": s["status_accuracy"],
        "status_confusion": f.get("status_confusion"),
        "context_recall": f.get("context_recall"),
        "abstention": f.get("abstention"),
        "conflict_coverage": f.get("conflict_coverage"),
    }


def write_baseline(
    report: Mapping[str, Any], path: Path, *, label: str, reason: str, replace: bool = False
) -> dict[str, Any]:
    """Write ``report``'s snapshot to ``path``. Never silently: an existing file needs
    ``replace`` (and its digest is recorded); a run with security failures or errors is
    never a baseline."""
    if not reason.strip():
        raise BaselineError("a baseline needs a reason")
    if _security_total(report):
        raise BaselineError("a run with security failures cannot become a baseline")
    errors = report["summary"].get("errors") or {}
    if errors:
        raise BaselineError(f"a run with errors cannot become a baseline: {errors}")
    if any(r.get("split") == "sealed" for r in report.get("items", [])):
        raise BaselineError("a sealed run is never a baseline (phase-13-followups §7.4)")
    data = snapshot(report, label=label)
    data["created"] = {"on": date.today().isoformat(), "reason": reason.strip()}
    if path.exists():
        if not replace:
            raise BaselineError(f"{path} exists; pass --replace with a reason to supersede it")
        data["created"]["supersedes"] = hashlib.sha256(path.read_bytes()).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(data))
    return data


def dumps(data: Mapping[str, Any]) -> str:
    """One line per item, per summary slice and per header field: compact, and a diff
    shows exactly the items and slices that moved."""

    def compact(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"))

    def block(value: Mapping[str, Any], indent: str) -> str:
        rows = []
        for key, child in value.items():
            if (
                isinstance(child, dict)
                and child
                and all(isinstance(v, dict) for v in child.values())
            ):
                inner = ",\n".join(
                    f"{indent}  {compact(k)}: {compact(v)}" for k, v in child.items()
                )
                rows.append(f"{indent}{compact(key)}: {{\n{inner}\n{indent}}}")
            else:
                rows.append(f"{indent}{compact(key)}: {compact(child)}")
        return ",\n".join(rows)

    head = block({k: v for k, v in data.items() if k not in ("summary", "items")}, " ")
    summary = block(data["summary"], "  ")
    items = block(data["items"], "  ")
    return f'{{\n{head},\n "summary": {{\n{summary}\n }},\n "items": {{\n{items}\n }}\n}}\n'


# --- comparison ---------------------------------------------------------------------------


@dataclass
class Finding:
    item: str
    kind: str
    detail: str
    regression: bool = False


@dataclass
class Comparison:
    kind: str
    verdict: str
    incomparable: list[str] = field(default_factory=list)
    intended: list[str] = field(default_factory=list)
    platform: dict[str, Any] = field(default_factory=dict)
    security: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    categories: list[dict[str, Any]] = field(default_factory=list)
    overall: dict[str, Any] = field(default_factory=dict)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def regressions(self) -> list[Finding]:
        return [f for f in self.findings if f.regression]

    @property
    def exit_code(self) -> int:
        if self.verdict == "incomparable":
            return EXIT_INCOMPARABLE
        if self.verdict in ("security", "drift", "regressed"):
            return EXIT_FAILED
        return EXIT_OK

    def to_json(self) -> dict[str, Any]:
        return asdict(self) | {"exit_code": self.exit_code}


def _bucket(rank: int | None) -> int:
    """The smallest k in KS that contains ``rank`` (len(KS) when none does)."""
    return next((i for i, k in enumerate(KS) if rank is not None and rank <= k), len(KS))


def _rank_text(rank: int | None) -> str:
    return "-" if rank is None else str(rank)


def _target_findings(
    item: str, kind: str, base: Mapping[str, int | None], run: Mapping[str, int | None]
) -> Iterable[Finding]:
    for label in sorted(set(base) | set(run)):
        if label not in base or label not in run:
            yield Finding(item, f"{kind}_set_changed", label)
            continue
        before, after = base[label], run[label]
        if before == after:
            continue
        b, a = _bucket(before), _bucket(after)
        move = f"{label}: {_rank_text(before)} -> {_rank_text(after)}"
        if a > b:
            yield Finding(item, f"{kind}_lost@{KS[b]}", move, regression=True)
        elif a < b:
            yield Finding(item, f"{kind}_gained@{KS[a]}", move)
        else:
            yield Finding(item, f"{kind}_rank_changed", move)


def _found_within(targets: Mapping[str, int | None], k: int) -> int:
    return sum(1 for r in targets.values() if r is not None and r <= k)


def _retrieval_item_findings(
    item: str, base: Mapping[str, Any], run: Mapping[str, Any]
) -> list[Finding]:
    out: list[Finding] = []
    if base["error"] != run["error"]:
        out.append(Finding(item, "error_changed", f"{base['error']} -> {run['error']}", True))
    out += _target_findings(item, "target", base["targets"], run["targets"])
    out += [
        f
        for f in _target_findings(item, "document", base["documents"], run["documents"])
        if f.kind != "document_rank_changed"
    ]
    if _found_within(base["targets"], PRIMARY_K) and not _found_within(run["targets"], PRIMARY_K):
        out.append(Finding(item, "item_lost_all@10", "no target within 10 any more", True))
    for label in sorted(set(base["misses"]) & set(run["misses"])):
        if base["misses"][label] != run["misses"][label]:
            out.append(
                Finding(
                    item,
                    "selection_changed",
                    f"{label}: {base['misses'][label]} -> {run['misses'][label]}",
                )
            )
    if base.get("identifier_hit") is True and run.get("identifier_hit") is False:
        out.append(Finding(item, "identifier_hit_lost", "named record no longer at rank 1", True))
    if base["ranking"] != run["ranking"]:
        first = next(
            (
                i
                for i, (x, y) in enumerate(zip(base["top"], run["top"], strict=False), start=1)
                if x != y
            ),
            None,
        )
        where = f"first difference at rank {first}" if first else "beyond the stored top 10"
        out.append(Finding(item, "ranking_changed", where))
    return out


def _answer_item_findings(
    item: str, base: Mapping[str, Any], run: Mapping[str, Any]
) -> list[Finding]:
    out: list[Finding] = []
    if (base["status"], base["reason"]) != (run["status"], run["reason"]):
        was_right = base["status"] == base["expected"]
        is_right = run["status"] == run["expected"]
        out.append(
            Finding(
                item,
                "status_changed",
                f"{base['status']}/{base['reason']} -> {run['status']}/{run['reason']} "
                f"(expected {run['expected']})",
                regression=was_right and not is_right,
            )
        )
    if base["error"] != run["error"]:
        out.append(Finding(item, "error_changed", f"{base['error']} -> {run['error']}", True))
    if base["model_called"] != run["model_called"]:
        lost_catch = (
            base["expected"] == "insufficient_evidence"
            and not base["model_called"]
            and run["model_called"]
        )
        out.append(
            Finding(
                item,
                "model_call_changed",
                f"{base['model_called']} -> {run['model_called']}",
                regression=lost_catch,
            )
        )
    for label in sorted(set(base["in_prompt"]) - set(run["in_prompt"])):
        out.append(Finding(item, "target_left_prompt", label, regression=True))
    for label in sorted(set(run["in_prompt"]) - set(base["in_prompt"])):
        out.append(Finding(item, "target_entered_prompt", label))
    return out


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _retrieval_slice(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [i for i in items if i["targets"]]
    recall = [_found_within(i["targets"], PRIMARY_K) / len(i["targets"]) for i in scored]
    rr = [
        1 / min(firsts) if (firsts := [r for r in i["targets"].values() if r is not None]) else 0.0
        for i in scored
    ]
    docs = [
        _found_within(i["documents"], PRIMARY_K) / len(i["documents"])
        for i in scored
        if i["documents"]
    ]
    return {
        "n": len(scored),
        "recall@10": _mean(recall),
        "mrr": _mean(rr),
        "doc_recall@10": _mean(docs),
    }


def _answer_slice(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(items)
    with_relevant = [r for r in rows if r["relevant"] and r["expected"] in ("answered", "partial")]
    return {
        "n": len(rows),
        "status_correct": sum(1 for r in rows if r["status"] == r["expected"]),
        "context_recall": _mean([len(r["in_prompt"]) / len(r["relevant"]) for r in with_relevant]),
        "model_calls": sum(1 for r in rows if r["model_called"]),
    }


def _slices(
    kind: str, base: Mapping[str, Mapping[str, Any]], run: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    group = "expected" if kind == "answer" else "category"
    measure = _answer_slice if kind == "answer" else _retrieval_slice
    names = sorted({v[group] for v in base.values()} | {v[group] for v in run.values()})
    # P13.2: the same rows per split (dev, held_out), when both sides record splits.
    split_names = sorted(
        {f"split:{v['split']}" for v in run.values() if v.get("split")}
        & {f"split:{v['split']}" for v in base.values() if v.get("split")}
    )

    def members(rows: Mapping[str, Mapping[str, Any]], name: str) -> Iterable[Mapping[str, Any]]:
        if name.startswith("split:"):
            return (v for v in rows.values() if f"split:{v.get('split')}" == name)
        return (v for v in rows.values() if v[group] == name)

    rows = []
    for name in [*names, *split_names]:
        b = measure(members(base, name))
        r = measure(members(run, name))
        row: dict[str, Any] = {"slice": name, "baseline": b, "run": r}
        if kind == "retrieval" and b["recall@10"] is not None and r["recall@10"] is not None:
            delta = round(r["recall@10"] - b["recall@10"], 4)
            row["delta_recall@10"] = delta
            row["regression"] = delta < -CATEGORY_DROP
        if kind == "answer":
            row["regression"] = r["status_correct"] < b["status_correct"]
        row["small_slice"] = max(b["n"], r["n"]) < SMALL_SLICE
        rows.append(row)
    overall = {"baseline": measure(base.values()), "run": measure(run.values())}
    return overall, rows


def _identity(data: Mapping[str, Any]) -> dict[str, Any]:
    config = data["configuration"]
    return {
        k: config.get(k) for k in ("embedding_provider", "embedding_model", "generation_provider")
    }


def _versions(data: Mapping[str, Any]) -> dict[str, Any]:
    config = data["configuration"]
    return {k: v for k, v in config.items() if k not in _identity(data)}


def compare(
    baseline: Mapping[str, Any],
    run: Mapping[str, Any],
    *,
    allow_golden_change: bool = False,
    cross_platform: bool = False,
) -> Comparison:
    """Compare a run (report or baseline) with a baseline (or another run)."""
    security = dict(run.get("summary", {}).get("security", {}))
    base = snapshot(baseline, label="baseline")
    new = snapshot(run, label="run")
    if not security:
        security = {"security_total": new["security_total"]}
    result = Comparison(kind=new["kind"], verdict="identical", security=security)
    result.platform = {"baseline": base["platform"], "run": new["platform"]}

    if base["kind"] != new["kind"]:
        result.incomparable.append(f"kind: {base['kind']} vs {new['kind']}")
    if base["corpus"].get("sha256") != new["corpus"].get("sha256"):
        result.incomparable.append(
            f"corpus fingerprint: {str(base['corpus'].get('sha256'))[:12]} vs "
            f"{str(new['corpus'].get('sha256'))[:12]} ({base['corpus'].get('chunks')} vs "
            f"{new['corpus'].get('chunks')})"
        )
    golden_changed = base["golden"].get("sha256") != new["golden"].get("sha256")
    if golden_changed and not allow_golden_change:
        result.incomparable.append(
            f"golden set: {str(base['golden'].get('sha256'))[:12]} ({base['golden'].get('items')} "
            f"items) vs {str(new['golden'].get('sha256'))[:12]} ({new['golden'].get('items')} "
            "items); --allow-golden-change compares the common items"
        )
    if base["kind"] == new["kind"] and _identity(base) != _identity(new):
        result.incomparable.append(f"configuration: {_identity(base)} vs {_identity(new)}")
    if base["platform"] != new["platform"] and not cross_platform:
        result.incomparable.append(
            f"database platform: {base['platform']} vs {new['platform']} (semantic ranking "
            "depends on it; use the matching baseline or --cross-platform)"
        )
    versions = _versions(base), _versions(new)
    for key in sorted(versions[0].keys() | versions[1].keys()):
        before, after = versions[0].get(key), versions[1].get(key)
        if base["kind"] == new["kind"] and after != before:
            result.intended.append(f"{key}: {before} -> {after}")

    if any(security.values()):
        result.verdict = "security"
    if result.incomparable:
        if result.verdict != "security":
            result.verdict = "incomparable"
        return result

    common = sorted(set(base["items"]) & set(new["items"]))
    result.added = sorted(set(new["items"]) - set(base["items"]))
    result.removed = sorted(set(base["items"]) - set(new["items"]))
    item_findings = _answer_item_findings if new["kind"] == "answer" else _retrieval_item_findings
    for item in common:
        result.findings += item_findings(item, base["items"][item], new["items"][item])
    shared_base = {i: base["items"][i] for i in common}
    shared_run = {i: new["items"][i] for i in common}
    result.overall, result.categories = _slices(new["kind"], shared_base, shared_run)
    for row in result.categories:
        if row.get("regression"):
            note = " (small slice: the items above are the finding)" if row["small_slice"] else ""
            b, r = row["baseline"], row["run"]
            metric = "status correct" if new["kind"] == "answer" else "recall@10"
            key = "status_correct" if new["kind"] == "answer" else "recall@10"
            detail = f"{metric} {b[key]} (n {b['n']}) -> {r[key]} (n {r['n']}){note}"
            split = row["slice"].startswith("split:")
            result.findings.append(
                Finding(
                    row["slice"] if split else f"category:{row['slice']}",
                    "split_regressed" if split else "category_regressed",
                    detail,
                    True,
                )
            )

    if result.verdict == "security":
        return result
    if not result.findings and not result.added and not result.removed:
        result.verdict = "identical"
    elif cross_platform and base["platform"] != new["platform"]:
        result.verdict = "platform_variant"
    elif not result.intended and any(not f.kind.endswith("_set_changed") for f in result.findings):
        # A golden change explains changed labels (and added or removed items), never
        # a different outcome for an item whose labels are the same.
        result.verdict = "drift"
    elif result.regressions:
        result.verdict = "regressed"
    else:
        result.verdict = "changed"
    return result


# --- rendering ----------------------------------------------------------------------------

VERDICTS = {
    "identical": "IDENTICAL: no item, target or category differs",
    "changed": "CHANGED: differences under a declared configuration change; no regression",
    "regressed": "REGRESSED: a declared change has regressions that must be explained",
    "drift": "UNEXPLAINED DRIFT: results differ while the configuration is unchanged",
    "platform_variant": "PLATFORM VARIANT: differences between database architectures",
    "security": "SECURITY FAILURE: blocker (every security metric must be 0)",
    "incomparable": "INCOMPARABLE: these runs cannot be compared",
}


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def render(result: Comparison) -> str:
    lines = [
        f"# Evaluation compare ({result.kind})",
        "",
        f"**{VERDICTS[result.verdict]}** (exit {result.exit_code})",
        "",
        f"Database platform: baseline `{result.platform.get('baseline') or 'independent'}`, "
        f"run `{result.platform.get('run') or 'independent'}`.",
        "",
        f"Security: {result.security}",
    ]
    if result.incomparable:
        lines += ["", "## Not comparable", "", *(f"- {r}" for r in result.incomparable)]
        return "\n".join(lines) + "\n"
    lines += [
        "",
        f"Declared configuration changes: {'; '.join(result.intended) or 'none'}.",
        f"Items added: {', '.join(result.added) or 'none'}; removed: "
        f"{', '.join(result.removed) or 'none'}.",
        "",
    ]
    if result.kind == "answer":
        lines += [
            "## By expected status (runs; correct status; context recall; model calls)",
            "",
            "| Expected | n | Correct | Context recall | Model calls |",
            "| -------- | -: | ------- | -------------- | ----------- |",
        ]
        for row in [{"slice": "**overall**", **result.overall}, *result.categories]:
            b, r = row["baseline"], row["run"]
            small = " (small)" if row.get("small_slice") else ""
            lines.append(
                f"| {row['slice']}{small} | {b['n']} -> {r['n']} | {b['status_correct']} -> "
                f"{r['status_correct']} | {_fmt(b['context_recall'])} -> "
                f"{_fmt(r['context_recall'])} | {b['model_calls']} -> {r['model_calls']} |"
            )
    else:
        lines += [
            "## By category (items with targets; R@10; MRR; document R@10)",
            "",
            "| Category | n | R@10 | Δ R@10 | MRR | Doc R@10 |",
            "| -------- | -: | ---- | -----: | --- | -------- |",
        ]
        for row in [{"slice": "**overall**", **result.overall}, *result.categories]:
            b, r = row["baseline"], row["run"]
            small = " (small)" if row.get("small_slice") else ""
            delta = (
                r["recall@10"] - b["recall@10"]
                if b["recall@10"] is not None and r["recall@10"] is not None
                else None
            )
            flag = " **regressed**" if row.get("regression") else ""
            lines.append(
                f"| {row['slice']}{small} | {b['n']} -> {r['n']} | {_fmt(b['recall@10'])} -> "
                f"{_fmt(r['recall@10'])} | {_fmt(delta)}{flag} | {_fmt(b['mrr'])} -> "
                f"{_fmt(r['mrr'])} | {_fmt(b['doc_recall@10'])} -> {_fmt(r['doc_recall@10'])} |"
            )
    by_item: dict[str, list[Finding]] = defaultdict(list)
    for f in result.findings:
        by_item[f.item].append(f)
    lines += [
        "",
        f"## Item-level findings ({len(by_item)} items; {len(result.regressions)} regressions)",
        "",
    ]
    if not by_item:
        lines.append("None.")
    for item in sorted(by_item, key=lambda i: (not any(f.regression for f in by_item[i]), i)):
        lines.append(f"- `{item}`")
        for f in by_item[item]:
            mark = "REGRESSION " if f.regression else ""
            lines.append(f"  - {mark}{f.kind}: {f.detail}")
    return "\n".join(lines) + "\n"
