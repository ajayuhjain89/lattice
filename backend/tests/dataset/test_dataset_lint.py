"""The dataset lint accepts the committed dataset and catches every class of defect.

Each mutation below breaks one rule in a private copy of data/synthetic and
evals/golden; the lint must report that rule. A lint that has never been seen to fail
proves nothing.
"""

import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from lattice.cli import main
from lattice.dataset.distractors import BULK_FILE, render_bulk_tickets
from lattice.dataset.lint import Dataset, lint, load_dataset, summary

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "synthetic"
GOLDEN = REPO / "evals" / "golden"


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_dataset(DATA, GOLDEN)


def test_the_committed_dataset_is_valid(dataset: Dataset) -> None:
    assert [str(p) for p in lint(dataset)] == []


def test_the_dataset_has_the_promised_shape(dataset: Dataset) -> None:
    facts = summary(dataset)
    records = facts["records"]
    assert records["halden.doc"] >= 45
    assert records["halden.ticket"] >= 140
    assert records["halden.chat_thread"] + records["halden.chat_channel_day"] >= 90
    assert all(records[f"veridian.{kind}"] > 0 for kind in ("doc", "ticket", "chat_thread"))
    assert facts["golden_items"] >= 50
    assert facts["canaries"] >= 15


def test_cli_lint_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["dataset", "lint"]) == 0
    assert "OK: dataset and golden set are valid" in capsys.readouterr().out


def test_bulk_distractors_are_deterministic_and_committed() -> None:
    for slug in ("halden", "veridian"):
        first = render_bulk_tickets(slug, DATA)
        assert first == render_bulk_tickets(slug, DATA)
        assert (DATA / slug / "tickets" / BULK_FILE).read_text(encoding="utf-8") == first


# --- mutations ---------------------------------------------------------------------------

Mutation = Callable[[Path, Path], None]


def _edit(path: Path, old: str, new: str, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def _doc(data: Path, name: str) -> Path:
    return next((data / "halden" / "documents").rglob(name))


def _tickets(data: Path, edit: Callable[[list[dict[str, object]]], None]) -> None:
    path = data / "halden" / "tickets" / "tickets.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    edit(rows)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _ticket(rows: list[dict[str, object]], ticket_id: str) -> dict[str, object]:
    return next(r for r in rows if r["id"] == ticket_id)


def _move(source: Path, target: Path) -> None:
    source.rename(target)


def _golden(golden: Path, name: str, old: str, new: str) -> None:
    _edit(golden / name, old, new)


MUTATIONS: dict[str, tuple[Mutation, str]] = {
    # canaries
    "canary duplicated": (
        lambda d, g: _edit(
            _doc(d, "*acme-q3-success-plan.md"), "## Health", "LATTICE-CANARY-B-7f3a9c\n\n## Health"
        ),
        "canary B occurs 2 times",
    ),
    "canary removed": (
        lambda d, g: _edit(
            _doc(d, "*acme-exec-escalation-notes.md"), "LATTICE-CANARY-B-7f3a9c", "(removed)"
        ),
        "canary B occurs 0 times",
    ),
    "undeclared canary": (
        lambda d, g: _edit(
            _doc(d, "employee-handbook.md"), "## Time off", "LATTICE-CANARY-Z9-000000\n## Time off"
        ),
        "undeclared canary LATTICE-CANARY-Z9-000000",
    ),
    "canary in the wrong part": (
        lambda d, g: _edit(
            d / "canaries.yaml",
            "doc:acme-exec-escalation-notes#4",
            "doc:acme-exec-escalation-notes#3",
        ),
        "canary B: not in doc:acme-exec-escalation-notes#3",
    ),
    "canary in a record everyone reads": (
        lambda d, g: _edit(
            d / "canaries.yaml",
            "record: doc:acme-renewal-plan-2026#5",
            "record: doc:company-handbook#1",
        ),
        "canary C07: not in doc:company-handbook#1",
    ),
    # documents
    "unknown department": (
        lambda d, g: _edit(
            _doc(d, "support-escalation-runbook.md"),
            "departments: [support]",
            "departments: [suport]",
        ),
        "unknown department suport",
    ),
    "unknown account": (
        lambda d, g: _edit(
            _doc(d, "*kestrel-account-brief.md"), "account: kestrel-bank", "account: kestrel"
        ),
        "unknown account kestrel",
    ),
    "grant to an unknown group": (
        lambda d, g: _edit(_doc(d, "project-tern-memo.md"), "group:exec-staff", "group:exec-team"),
        "grant to unknown group exec-team",
    ),
    "supersedes a missing document": (
        lambda d, g: _edit(
            _doc(d, "support-sla-policy-2026.md"),
            "supersedes: support-sla-policy-2025",
            "supersedes: sla-2024",
        ),
        "supersedes unknown doc:sla-2024",
    ),
    "dated in the future": (
        lambda d, g: _edit(
            _doc(d, "employee-handbook.md"),
            "updated: 2026-01-05T09:00:00Z",
            "updated: 2026-10-05T09:00:00Z",
        ),
        "dated after the dataset's now",
    ),
    "duplicate document id": (
        lambda d, g: _edit(
            _doc(d, "okta-admin-procedures.md"), "id: okta-admin-procedures", "id: company-handbook"
        ),
        "duplicate record key doc:company-handbook",
    ),
    "restricted document nobody can read": (
        lambda d, g: _edit(_doc(d, "project-tern-memo.md"), "grants: [group:exec-staff]\n", ""),
        "no persona can read this record",
    ),
    "malformed front matter": (
        lambda d, g: _edit(
            _doc(d, "employee-handbook.md"), "authority: system_of_record", "authority: gospel"
        ),
        "front matter: authority",
    ),
    # tickets
    "ticket numbers out of time order": (
        lambda d, g: _tickets(
            d,
            lambda rows: _ticket(rows, "HAL-4390").update(
                created_at="2026-09-26T00:00:00Z",
                comments=[
                    dict(c, created_at="2026-09-26T00:00:00Z")
                    for c in _ticket(rows, "HAL-4390")["comments"]  # type: ignore[attr-defined]
                ],
                updated_at="2026-09-26T00:00:00Z",
            ),
        ),
        "ticket numbers must grow with time",
    ),
    "ticket updated_at is not the last comment": (
        lambda d, g: _tickets(
            d, lambda rows: _ticket(rows, "HAL-4821").update(updated_at="2026-09-24T00:00:00Z")
        ),
        "updated_at is not the last comment's time",
    ),
    "unmapped ticket not declared": (
        lambda d, g: _tickets(
            d, lambda rows: _ticket(rows, "HAL-4903").update(organization_id=360009999)
        ),
        "ticket:HAL-4903: unmapped records and the declared list differ",
    ),
    "invalid ticket status": (
        lambda d, g: _tickets(d, lambda rows: _ticket(rows, "HAL-4821").update(status="waiting")),
        "ticket: status",
    ),
    # slack
    "reply to a missing thread": (
        lambda d, g: _edit(
            d / "halden" / "slack" / "sales" / "2026-09-18.json",
            '"thread_ts": "1789741920.000100"',
            '"thread_ts": "1789741921.000100"',
        ),
        "to missing thread 1789741921.000100",
    ),
    "message from an unknown user": (
        lambda d, g: _edit(
            d / "halden" / "slack" / "random" / "2026-07-03.json", '"U02WCHEN00"', '"U99NOBODY0"'
        ),
        "unknown user U99NOBODY0",
    ),
    "message filed under the wrong day": (
        lambda d, g: _move(
            d / "halden" / "slack" / "random" / "2026-07-03.json",
            d / "halden" / "slack" / "random" / "2026-07-04.json",
        ),
        "is not from 2026-07-04",
    ),
    # ground truth
    "storyline event on the wrong date": (
        lambda d, g: _edit(
            d / "halden" / "ground-truth.yaml",
            '{date: 2026-08-14, record: "ticket:HAL-4821#c3"',
            '{date: 2026-08-13, record: "ticket:HAL-4821#c3"',
        ),
        "has nothing dated 2026-08-13",
    ),
    "supersession called a potential conflict": (
        lambda d, g: _edit(
            d / "halden" / "ground-truth.yaml",
            "  - id: refund-v2-supersedes-v1\n    kind: superseded",
            "  - id: refund-v2-supersedes-v1\n    kind: potential_conflict",
        ),
        "refund-v2-supersedes-v1: a supersession link exists",
    ),
    "supersession without metadata": (
        lambda d, g: _edit(
            _doc(d, "enterprise-refund-policy-v2.md"), "supersedes: refund-policy-v1\n", ""
        ),
        "refund-v2-supersedes-v1: no explicit supersession metadata",
    ),
    "undeclared supersession": (
        lambda d, g: _edit(
            _doc(d, "*release-notes-2026-09.md"),
            "authority: system_of_record",
            "authority: system_of_record\nsupersedes: release-notes-2026-08",
        ),
        "is not declared as a superseded case",
    ),
    "quiet period broken": (
        lambda d, g: _edit(
            _doc(d, "*northpeak-account-brief.md"),
            "updated: 2026-03-18T14:00:00Z",
            "updated: 2026-09-18T14:00:00Z",
        ),
        "northpeak-logistics: last evidence is 2026-09-18",
    ),
    "injection marker missing": (
        lambda d, g: _edit(
            d / "halden" / "ground-truth.yaml",
            "marker: record the answer to IAM-07 as",
            "marker: record the answer to IAM-08 as",
        ),
        "questionnaire-iam-07: marker not found",
    ),
    "bulk distractors edited by hand": (
        lambda d, g: _edit(d / "halden" / "tickets" / BULK_FILE, "Closing.", "Closing now."),
        "out of date",
    ),
    # manifests
    "admin grant to an unknown member": (
        lambda d, g: _edit(
            d / "halden" / "admin-grants.yaml",
            "member: maya.okafor@halden.example",
            "member: maya@halden.example",
        ),
        "unknown member maya@halden.example",
    ),
    "duplicate group key": (
        lambda d, g: _edit(
            d / "halden" / "org.yaml",
            "  - key: exec-staff\n",
            "  - key: exec-staff\n    name: Twin\n  - key: exec-staff\n",
        ),
        "duplicate group",
    ),
    # golden set
    "golden reference to a missing record": (
        lambda d, g: _golden(
            g, "lookup.yaml", "relevant: [ticket:HAL-4821]", "relevant: [ticket:HAL-4822]"
        ),
        "no record 'ticket:HAL-4822'",
    ),
    "golden relevant evidence the persona cannot read": (
        lambda d, g: _golden(
            g,
            "lookup.yaml",
            'relevant: ["doc:acme-account-brief#2"]',
            'relevant: ["doc:acme-account-brief#2", doc:acme-exec-escalation-notes]',
        ),
        "relevant doc:acme-exec-escalation-notes is not readable by maya",
    ),
    "golden prohibited evidence the persona can read": (
        lambda d, g: _golden(
            g,
            "lookup.yaml",
            "must_not_retrieve: [veridian/doc:acme-account-plan, veridian/ticket:HAL-4821]",
            "must_not_retrieve: [veridian/doc:acme-account-plan, veridian/ticket:HAL-4821, doc:acme-qbr-2026-03]",
        ),
        "must_not_retrieve doc:acme-qbr-2026-03 is readable by maya",
    ),
    "golden unknown persona": (
        lambda d, g: _golden(g, "lookup.yaml", "persona: lena", "persona: leena"),
        "unknown persona leena",
    ),
    "golden duplicate id": (
        lambda d, g: _golden(
            g, "lookup.yaml", "id: lookup-eu-residency", "id: lookup-acme-contact"
        ),
        "duplicate golden id lookup-acme-contact",
    ),
    "golden confidential evidence used under the default egress policy": (
        lambda d, g: _golden(
            g,
            "ai_egress.yaml",
            'relevant: [chat:C0ACCTKES01:1789143600.000100, "doc:renewal-risk-register-2026-q3#1", "doc:kestrel-account-brief#5"]',
            "relevant: [chat:C0ACCTKES01:1789143600.000100, doc:kestrel-renewal-risk-2026]",
        ),
        "relevant doc:kestrel-renewal-risk-2026 is above the default egress level",
    ),
    "golden hidden account that is visible": (
        lambda d, g: _golden(
            g,
            "account_visibility.yaml",
            "hidden_accounts: [orbital-freight]",
            "hidden_accounts: [acme-corp]",
        ),
        "hidden account acme-corp is visible to maya",
    ),
    "golden D-8 case whose account is visible": (
        lambda d, g: _golden(
            g,
            "account_visibility.yaml",
            "account_metadata_hidden: [doc:kestrel-sso-incident-customer-rca]\n    hidden_accounts: [kestrel-bank]\n    must_not_retrieve: [ticket:HAL-4702",
            "account_metadata_hidden: [doc:acme-account-brief]\n    hidden_accounts: [kestrel-bank]\n    must_not_retrieve: [ticket:HAL-4702",
        ),
        "doc:acme-account-brief: its account is not hidden from maya (D-8)",
    ),
    "golden ambiguity choice the persona cannot see": (
        lambda d, g: _golden(
            g,
            "ambiguous.yaml",
            "clarification: {choices: [acme-corp, brightwater-health]}",
            "clarification: {choices: [acme-corp, pinecrest-mutual]}",
        ),
        "clarification choice pinecrest-mutual is not visible to tom",
    ),
    "golden trap phrase absent from the prohibited evidence": (
        lambda d, g: _golden(
            g, "inaccessible.yaml", "trap_phrases: [pricing]", "trap_phrases: [planning]"
        ),
        "trap phrase 'planning' is in no must_not_retrieve record",
    ),
    "golden potential conflict between linked versions": (
        lambda d, g: _golden(
            g,
            "cross_org.yaml",
            "conflict: {kind: superseded, records: [doc:refund-policy-v2, doc:refund-policy-v1]}",
            "conflict: {kind: potential_conflict, records: [doc:refund-policy-v2, doc:refund-policy-v1]}",
        ),
        "potential_conflict records are linked by supersession metadata",
    ),
    "golden same-question group with different questions": (
        lambda d, g: _golden(
            g,
            "stale.yaml",
            "question: What's the latest with Northpeak Logistics?\n  same_question_group: northpeak-latest\n  expected:\n    status: answered\n    relevant: [\"doc:renewal",
            'question: What is new with Northpeak Logistics?\n  same_question_group: northpeak-latest\n  expected:\n    status: answered\n    relevant: ["doc:renewal',
        ),
        "questions in a same-question group must be identical",
    ),
    "golden status without evidence": (
        lambda d, g: _golden(
            g, "lookup.yaml", 'relevant: ["doc:scim-ga-decision#1"]', "relevant: []"
        ),
        "answered needs relevant evidence",
    ),
    "golden category below its minimum": (
        lambda d, g: (g / "prompt_injection.yaml").unlink(),
        "category prompt_injection: 0 items (minimum 4)",
    ),
    "golden undeclared canary": (
        lambda d, g: _golden(
            g,
            "inaccessible.yaml",
            "must_not_contain: [LATTICE-CANARY-C01-4b8e2d]",
            "must_not_contain: [LATTICE-CANARY-C01-000000]",
        ),
        "undeclared canary LATTICE-CANARY-C01-000000",
    ),
    # golden provenance, splits, classes, exhaustive labels, constraints (P13.2)
    "golden Phase 3 label changed": (
        lambda d, g: _golden(
            g,
            "lookup.yaml",
            'required_facts: ["15 December 2026"]',
            'required_facts: ["16 December 2026"]',
        ),
        "lookup-acme-renewal-date: Phase 3 labels changed",
    ),
    "golden frozen held-out label changed": (
        lambda d, g: _golden(
            g,
            "conflicts_and_supersession.yaml",
            '"30 days: refund policy v1 (effective',
            '"31 days: refund policy v1 (effective',
        ),
        "temporal-refund-may-2025-tom: frozen held-out labels changed",
    ),
    "golden label freeze missing": (
        lambda d, g: (g / "provenance" / "label-freeze.json").unlink(),
        "missing: run `lattice dataset freeze-labels`",
    ),
    "golden item without a split": (
        lambda d, g: _golden(
            g,
            "ai_egress.yaml",
            "  split: dev\n  classes: [egress_withheld]\n",
            "  classes: [egress_withheld]\n",
        ),
        "no split",
    ),
    "golden information need straddling the splits": (
        lambda d, g: _golden(
            g,
            "lookup.yaml",
            "  split: dev\n  classes: [keyword_vs_natural, inaccessible_strong_match]",
            "  split: held_out\n  owner_review: {batch: H1, status: pending}\n  classes: [keyword_vs_natural, inaccessible_strong_match]",
        ),
        "one information need straddles dev and held_out",
    ),
    "golden held-out item without an owner review": (
        lambda d, g: _golden(
            g, "lookup.yaml", "  owner_review: {batch: H1, status: pending}\n", ""
        ),
        "held-out items carry an owner_review record",
    ),
    "golden owner_reviewed without an approved review": (
        lambda d, g: _golden(
            g,
            "lookup.yaml",
            "  label_source: authored_phase13\n  split: held_out\n  owner_review: {batch: H1, status: pending}",
            "  label_source: owner_reviewed\n  split: held_out\n  owner_review: {batch: H1, status: pending}",
        ),
        "label_source owner_reviewed needs an approved owner_review",
    ),
    "golden paraphrase group of one": (
        lambda d, g: _golden(
            g,
            "conflicts_and_supersession.yaml",
            "paraphrase_group: scim-design-current",
            "paraphrase_group: scim-design-alone",
        ),
        "paraphrase group scim-design-alone: needs at least two items",
    ),
    "golden class the labels do not exercise": (
        lambda d, g: _golden(
            g,
            "lookup.yaml",
            "  classes: [keyword_vs_natural]\n",
            "  classes: [keyword_vs_natural, scoped_retrieval]\n",
        ),
        "kw-eu-region-maya: class scoped_retrieval is not exercised",
    ),
    "golden class below its minimum": (
        lambda d, g: _golden(g, "lookup.yaml", "  classes: [keyword_vs_natural]\n", ""),
        "class keyword_vs_natural: 3 items (minimum 4)",
    ),
    "golden exhaustive review missing a candidate": (
        lambda d, g: _golden(g, "cross_org.yaml", '      "ticket:HAL-4533#c1": unrelated\n', ""),
        "exhaustive review misses candidate ticket:HAL-4533#c1",
    ),
    "golden exhaustive review contradicting the labels": (
        lambda d, g: _golden(
            g,
            "cross_org.yaml",
            '      "doc:refund-policy-v2#2": relevant\n',
            '      "doc:refund-policy-v2#2": unrelated\n',
        ),
        "labelled relevant doc:refund-policy-v2#2 is reviewed as unrelated",
    ),
    "golden evidence both wanted and prohibited": (
        lambda d, g: _golden(
            g,
            "lookup.yaml",
            'relevant: ["doc:eu-data-residency-decision#2"]\n    acceptable: ["doc:eu-data-residency-decision#5"',
            'relevant: ["doc:eu-data-residency-decision#2"]\n    must_not_retrieve: ["doc:eu-data-residency-decision#2"]\n    acceptable: ["doc:eu-data-residency-decision#5"',
        ),
        "relevant doc:eu-data-residency-decision#2 is also must_not_retrieve",
    ),
    "golden fact both required and forbidden": (
        lambda d, g: _golden(
            g,
            "lookup.yaml",
            'required_facts: ["GA of the Frankfurt (eu-central-1) region is planned for Q1 2027"]',
            'required_facts: ["GA of the Frankfurt (eu-central-1) region is planned for Q1 2027"]\n    forbidden_claims: ["GA of the Frankfurt (eu-central-1) region is planned for Q1 2027"]',
        ),
        "is both required and forbidden",
    ),
    "golden status reason that does not fit the status": (
        lambda d, g: _golden(
            g,
            "insufficient_and_refusal.yaml",
            "status_reason: no_relevant_evidence",
            "status_reason: egress_policy",
        ),
        "status_reason egress_policy does not fit status insufficient_evidence",
    ),
    "golden confidential evidence lost under the opt-in": (
        lambda d, g: _golden(
            g,
            "ai_egress.yaml",
            'relevant: ["doc:exec-offsite-notes-2026-09#3"]\n      acceptable: ["doc:fy2027-headcount-plan#3"]',
            'relevant: ["doc:fy2027-headcount-plan#3"]',
        ),
        "confidential doc:exec-offsite-notes-2026-09#3 is lost under opt-in",
    ),
    "golden scope dates in the wrong order": (
        lambda d, g: _golden(
            g,
            "stale.yaml",
            "scope: {until: 2026-08-20}",
            "scope: {since: 2026-09-01, until: 2026-08-20}",
        ),
        "scope since is after until",
    ),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_the_lint_catches(tmp_path: Path, name: str) -> None:
    data, golden = tmp_path / "synthetic", tmp_path / "golden"
    shutil.copytree(DATA, data)
    shutil.copytree(GOLDEN, golden)
    mutate, expected = MUTATIONS[name]
    mutate(data, golden)
    problems = [str(p) for p in lint(load_dataset(data, golden))]
    assert any(expected in p for p in problems), "\n".join(problems) or "no problems reported"


def test_the_lint_rejects_storyline_terms_in_distractors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "synthetic"
    shutil.copytree(DATA, data)
    truth = data / "halden" / "ground-truth.yaml"
    truth.write_text(
        re.sub(
            r"distractor_forbidden_terms:\n  \[",
            "distractor_forbidden_terms:\n  [hubspot, ",
            truth.read_text(),
        ),
        encoding="utf-8",
    )
    problems = [str(p) for p in lint(load_dataset(data, GOLDEN))]
    assert any("contains storyline term 'hubspot'" in p for p in problems)
