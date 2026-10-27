"""The product owner's review packet for held-out golden items (D-41; P13.2).

    lattice dataset review-packet [--out docs/golden-review] [--check]

One Markdown file per review batch (``H1.md``, ...) plus an index. Each item shows what a
reviewer needs without reading code: the question, the persona and what they may read,
the expected status, the exact record keys with excerpts from the synthetic source, the
required facts, what must stay out, open points, the proposed label decisions, and a
place to approve or request a correction. Everything is rendered from the golden files
and the synthetic dataset, so the packet is reproducible and ``--check`` proves the
committed packet matches the labels. Only synthetic data appears; canary strings in
excerpts are redacted. Nothing here is a model judgement.
"""

from collections import defaultdict

from lattice.dataset.access import OrgWorld
from lattice.dataset.lint import Dataset
from lattice.dataset.manifests import CANARY, KEY_REF, Expected, GoldenItem
from lattice.dataset.sources import ChannelInfo, Part, Record

EXCERPT = 360
PARTS_PER_RECORD = 2

_STATUS = {
    "answered": "answered from the evidence",
    "partial": "partly answered; the evidence does not cover everything asked",
    "insufficient_evidence": "no answer: nothing the persona may read answers it",
    "evidence_only": "evidence list only: the decisive evidence may not be sent to the AI model",
    "ambiguous": "a clarifying question offering the listed accounts",
    "unsupported_request": "declined: Lattice answers questions, it does not act",
}


def _clean(text: str, limit: int = EXCERPT) -> str:
    text = CANARY.sub("[canary]", " ".join(text.split()))
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def persona_scope(world: OrgWorld, persona: str) -> str:
    email = world.personas[persona]
    member = next(m for m in world.manifest.members if m.email == email)
    names = {a.key: a.name for a in world.manifest.accounts}
    if member.account_scope == "all":
        accounts = "all accounts"
    else:
        accounts = ", ".join(names[k] for k in member.accounts) or "no accounts"
    groups = [g.name for g in world.manifest.groups if email in g.members]
    groups += [
        f"#{info.channel.name} (private)"
        for info in world.raw.slack_channels.values()
        if info.private and email in world.channel_members(info)
    ]
    admin = "; Lattice administrator (no content access from it)" if member.is_admin else ""
    return (
        f"{member.display_name}, {member.role_label} ({world.slug}): clearance "
        f"{member.clearance}; accounts: {accounts}; departments: "
        f"{', '.join(member.departments) or 'none'}; groups: {', '.join(groups) or 'none'}"
        f"{admin}"
    )


def _record_line(data: Dataset, item: GoldenItem, key: str, *, excerpt: bool) -> list[str]:
    found = data.resolve(item.organization, key)
    if isinstance(found, str):
        return [f"- `{key}` — {found}"]
    record, part = found
    world = data.worlds[record.organization]
    readers = sorted(world.readers(record))
    who = "everyone" if len(readers) == len(world.personas) else ", ".join(readers) or "nobody"
    if record.organization != item.organization:
        access = f"another organization ({record.organization}): never readable"
    else:
        access = f"{world.level(record) or 'not indexed'}; readable by {who}"
    lines = [f"- `{key}` — {_title(record)} ({access})"]
    if excerpt:
        parts: list[Part] = [part] if part is not None else list(record.parts[:PARTS_PER_RECORD])
        for p in parts:
            lines.append(f"  > {_clean(p.text)}")
        if part is None and len(record.parts) > PARTS_PER_RECORD:
            lines.append(f"  > … ({len(record.parts) - PARTS_PER_RECORD} more parts)")
    return lines


def _title(record: Record) -> str:
    if isinstance(record.origin, ChannelInfo):
        return record.title
    return f"“{record.title}”, updated {record.updated_at.date().isoformat()}"


def _expectation(data: Dataset, item: GoldenItem, config: str, e: Expected) -> list[str]:
    status = f"**{e.status}** — {_STATUS[e.status]}"
    if e.status_reason:
        status += f" (reason `{e.status_reason}`)"
    out = [f"*Egress configuration `{config}`*", "", f"Expected status: {status}", ""]
    sections = (
        ("Relevant evidence (must be retrieved and used)", e.relevant, True),
        (
            "Relevant but withheld from the AI model by the egress policy",
            e.not_used_for_answer,
            True,
        ),
        ("Acceptable supporting evidence (may appear without penalty)", e.acceptable, False),
        (
            "Must never appear (the persona may not read it, or another tenant's)",
            e.must_not_retrieve,
            False,
        ),
    )
    for heading, keys, excerpt in sections:
        if keys:
            out.append(f"{heading}:")
            for key in keys:
                out += _record_line(data, item, key, excerpt=excerpt)
            out.append("")
    if e.required_facts:
        facts = [f"- {f}" for f in e.required_facts]
        out += ["Required facts (the answer must state):", *facts, ""]
    if e.forbidden_claims:
        out += ["Forbidden claims:", *(f"- {f}" for f in e.forbidden_claims), ""]
    if e.must_not_contain:
        shown = ["[canary string]" if CANARY.fullmatch(s) else s for s in e.must_not_contain]
        out += [f"Must not contain anywhere: {', '.join(f'`{s}`' for s in shown)}", ""]
    extras = []
    if e.conflict.kind != "none":
        records = ", ".join(f"`{r}`" for r in e.conflict.records)
        extras.append(f"conflict `{e.conflict.kind}` between {records}")
    if e.freshness:
        newest = e.freshness.newest_evidence_on
        extras.append(
            f"freshness: stale={e.freshness.stale}"
            + (f", newest evidence {newest}" if newest else "")
        )
    if e.clarification:
        extras.append(f"clarification choices: {', '.join(e.clarification.choices)}")
    if e.hidden_accounts:
        extras.append(f"accounts that must stay hidden: {', '.join(e.hidden_accounts)}")
    if e.account_metadata_hidden:
        hidden = ", ".join(e.account_metadata_hidden)
        extras.append(f"readable evidence shown without its account: {hidden}")
    if extras:
        out += ["Other expectations:", *(f"- {x}" for x in extras), ""]
    return out


def _open_points(data: Dataset, item: GoldenItem) -> list[str]:
    points = []
    for _, e in item.expectations():
        whole = [k for k in e.relevant if (m := KEY_REF.fullmatch(k)) and m["part"] is None]
        if whole:
            points.append(
                "Whole records are labelled relevant (any part counts): "
                + ", ".join(f"`{k}`" for k in whole)
                + ". Confirm that no single part is the real target."
            )
        if e.status == "partial":
            points.append("Partial: confirm what the readable evidence cannot answer.")
        if e.status == "insufficient_evidence":
            points.append("Confirm that no record the persona may read answers the question.")
    if item.scope.account or item.scope.source_types or item.scope.since or item.scope.until:
        points.append(
            "The scope filters retrieval; evidence outside it is not expected even if relevant."
        )
    if not item.exhaustive:
        points.append(
            "Labels are not exhaustive: unlabelled readable evidence is unknown, not irrelevant."
        )
    return list(dict.fromkeys(points))


def render_item(data: Dataset, item: GoldenItem) -> str:
    world = data.worlds[item.organization]
    scope = item.scope.model_dump(exclude_defaults=True, mode="json")
    lines = [
        f"### {item.id}",
        "",
        f"**Question:** {item.question}",
        "",
        f"- Persona: {persona_scope(world, item.persona)}",
        f"- Category: `{item.category}`; classes: {', '.join(item.classes) or '—'}",
        f"- Scope: {scope or 'none'}",
        f"- Label source: `{item.label_source}`"
        + (f"; group `{item.paraphrase_group}`" if item.paraphrase_group else "")
        + (f"; same question as `{item.same_question_group}`" if item.same_question_group else ""),
        "",
    ]
    for config, expected in item.expectations():
        lines += _expectation(data, item, config, expected)
    if item.exhaustive_review is not None:
        r = item.exhaustive_review
        how = "every readable part in scope" if r.scope_all else f"terms {', '.join(r.terms)}"
        verdicts = defaultdict(list)
        for key, verdict in sorted(r.candidates.items()):
            verdicts[verdict].append(key)
        lines += [
            f"Exhaustive labels (engineering review {r.reviewed_on}, {how}; "
            f"{len(r.candidates)} candidates): "
            + "; ".join(f"{v} {len(k)}" for v, k in sorted(verdicts.items())),
            "",
            *(f"- unrelated: `{k}`" for k in verdicts.get("unrelated", [])),
            "",
        ]
    lines += ["Author's note: " + " ".join(item.notes.split()), ""]
    points = _open_points(data, item)
    if points:
        lines += ["Open points for the reviewer:", *(f"- {p}" for p in points), ""]
    lines += [
        "Decision:",
        "- [ ] Approve these labels",
        "- [ ] Request a correction (write what is wrong and the record that shows it):",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def render_packet(data: Dataset) -> dict[str, str]:
    held = [i for _, i in data.goldens if i.split == "held_out"]
    by_batch: dict[str, list[GoldenItem]] = defaultdict(list)
    for item in held:
        by_batch[item.owner_review.batch if item.owner_review else "unbatched"].append(item)
    order = sorted(by_batch, key=lambda b: (len(b), b))
    files = {}
    index = [
        "# Held-out golden labels: product-owner review (D-41)",
        "",
        "Generated by `lattice dataset review-packet` from `evals/golden/` and",
        "`data/synthetic/`; do not edit by hand (`--check` fails if this differs from the",
        "labels). Every label here was written by engineering (`authored_phase3` or",
        "`authored_phase13`) and is a provisional expectation until you approve it. Nothing",
        "in this packet is a model judgement.",
        "",
        "How to review: for each item, check that the relevant evidence really answers the",
        "question for that persona, that the required facts are stated in the excerpts, that",
        "nothing the persona may read is missing from the evidence, and that the expected",
        "status is honest. Tick *Approve* or write the correction. Engineering then records",
        "approved items as `owner_reviewed` (and corrections in their own commit); held-out",
        "labels are frozen (`evals/golden/provenance/label-freeze.json`), so a correction is",
        "a recorded change.",
        "",
        "| Batch | Items | Phase 3 | Phase 13 | Approved | Pending |",
        "| ----- | ----: | ------: | -------: | -------: | ------: |",
    ]
    for batch in order:
        items = sorted(by_batch[batch], key=lambda i: i.id)
        approved = sum(1 for i in items if i.owner_review and i.owner_review.status == "approved")
        pending = sum(1 for i in items if i.owner_review and i.owner_review.status == "pending")
        p3 = sum(1 for i in items if i.label_source == "authored_phase3")
        p13 = sum(1 for i in items if i.label_source == "authored_phase13")
        index.append(
            f"| [{batch}]({batch}.md) | {len(items)} | {p3} | {p13} | {approved} | {pending} |"
        )
        body = [
            f"# Review batch {batch} ({len(items)} held-out items)",
            "",
            "See [README.md](README.md) for how to review. Record keys: `doc:<id>#<section>`,",
            "`ticket:<id>#c<comment>`, `chat:<channel>:<thread or day>#<message>`; another",
            "organization's record is prefixed `veridian/` or `halden/`.",
            "",
            *(render_item(data, item) for item in sorted(items, key=_order)),
        ]
        files[f"{batch}.md"] = "\n".join(body).rstrip("\n-") + "\n"
    total = len(held)
    index += ["", f"Total: {total} held-out items.", ""]
    files["README.md"] = "\n".join(index)
    return files


def _order(item: GoldenItem) -> tuple[str, str]:
    return (item.paraphrase_group or item.same_question_group or item.id, item.id)
