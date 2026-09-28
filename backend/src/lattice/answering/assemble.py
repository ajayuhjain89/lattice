"""Deterministic answer assembly (retrieval-design §9 step 8, §10, §11).

Everything here is computed by code from validated claims and database metadata --
never from model text:

* **status** -- the model's status is advisory. No valid direct-answer claim means the
  answer is withheld (``insufficient_evidence`` / ``claims_unverified`` when claims were
  rejected, ``no_relevant_evidence`` otherwise); removed claims make it ``partial``;
  ``unsupported_request`` (D-11) shows no claims at all;
* **supersession** -- only an explicit source link between two *readable* documents in
  the evidence (retrieval exposes no other link, D-14). Added whether or not the model
  noticed it; never inferred from dates, rank or authority;
* **potential conflicts** -- model-proposed, validated position by position; kept only
  with two valid positions from two documents; reclassified as ``superseded`` when a
  link exists between the positions' documents. The summary line states facts only
  (which is the most recent) and never declares a winner;
* **freshness** -- the newest cited evidence against a per-kind threshold;
* **notices** -- evidence not sent to the model because of the egress policy, removed
  claims, stale or superseded citations, degraded semantic search.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from lattice.answering.models import (
    Citation,
    Claim,
    Clarification,
    ClarificationChoice,
    Conflict,
    ConflictPosition,
    EvidenceItem,
    Freshness,
    Notice,
    Supersession,
)
from lattice.answering.prompt import evidence_date
from lattice.answering.validate import Validation, ValidClaim
from lattice.retrieval import Evidence, VisibleAccount

STALE_DAYS: Final = {"ticket": 30, "chat_thread": 30, "chat_channel_day": 30, "doc": 180}
STALE_DAYS_SYSTEM_OF_RECORD_DOC: Final = 365
KIND_LABEL: Final = {
    "doc": "document",
    "ticket": "ticket",
    "chat_thread": "Slack thread",
    "chat_channel_day": "Slack conversation",
}

MESSAGES: Final = {
    ("partial", "claims_removed"): (
        "Some statements couldn't be verified against sources and were removed."
    ),
    ("partial", "incomplete"): "The sources answer only part of this question.",
    ("insufficient_evidence", "no_relevant_evidence"): (
        "I couldn't find evidence you have access to that answers this."
    ),
    ("insufficient_evidence", "claims_unverified"): (
        "Lattice couldn't verify an answer against the sources it found, so none is shown. "
        "Review the evidence below."
    ),
    ("evidence_only", "egress_policy"): (
        "Lattice found sources you can access, but your organization's AI data policy "
        "doesn't allow sending them to the AI model, so no summary was drafted. Review the "
        "sources below."
    ),
    ("evidence_only", "generation_unavailable"): (
        "No AI model is available to draft an answer right now. Review the sources below."
    ),
    ("ambiguous", "multiple_accounts"): "Which account do you mean?",
    ("ambiguous", "missing_referent"): "Lattice needs a little more detail to answer this.",
    ("unsupported_request", "action_request"): (
        "Lattice answers questions from your sources. It can't send, change or do anything "
        "on your behalf, so nothing was done."
    ),
    ("failed", "provider_unavailable"): (
        "The AI model couldn't be reached, so no answer was drafted. Review the sources below."
    ),
    ("failed", "generation_invalid"): (
        "The AI model didn't return a usable answer. Review the sources below."
    ),
    ("failed", "citation_validation_failed"): (
        "Lattice withheld this answer because a citation check failed. Review the sources below."
    ),
}


def _date(value: datetime | None) -> str:
    return f"{value.day} {value:%b %Y}" if value else "an unknown date"


def evidence_items(
    evidence: Sequence[Evidence],
    classifications: dict[uuid.UUID, str],
    used: set[uuid.UUID],
    *,
    excluded_by_egress: set[uuid.UUID],
    relevant: set[uuid.UUID],
) -> list[EvidenceItem]:
    out: list[EvidenceItem] = []
    for e in evidence:
        reason = (
            None
            if e.chunk_id in used
            else "egress_policy"
            if e.chunk_id in excluded_by_egress
            else "relevance_floor"
            if e.chunk_id not in relevant
            else "prompt_budget"
        )
        out.append(
            EvidenceItem.model_validate(
                {
                    "evidence_id": e.chunk_id,
                    "document_id": e.document_id,
                    "rank": e.rank,
                    "source_type": e.source_type,
                    "document_kind": e.document_kind,
                    "source_name": e.source_name,
                    "title": e.title,
                    "display_ref": e.display_ref,
                    "uri": e.uri,
                    "heading_path": list(e.heading_path),
                    "author": e.author,
                    "occurred_at": e.occurred_at,
                    "document_updated_at": e.document_updated_at,
                    "version": e.version,
                    "authority": e.authority,
                    "classification": classifications.get(e.document_id, "restricted"),
                    "account": (
                        {"key": e.account.key, "name": e.account.name} if e.account else None
                    ),
                    "text": e.text,
                    "supersedes_document_id": e.supersedes_document_id,
                    "superseded_by_document_ids": list(e.superseded_by_document_ids),
                    "used_for_answer": e.chunk_id in used,
                    "not_used_reason": reason if e.chunk_id not in used else None,
                }
            )
        )
    return out


def to_claim(claim: ValidClaim) -> Claim:
    return Claim(
        text=claim.text,
        role=claim.role,
        citations=[
            Citation(
                evidence_id=c.chunk_id,
                document_id=c.document_id,
                quote=c.quote,
                quote_start=c.start,
                quote_end=c.end,
            )
            for c in claim.citations
        ],
    )


def _documents(evidence: Sequence[Evidence]) -> dict[uuid.UUID, Evidence]:
    """One representative evidence item per document (its best-ranked chunk)."""
    out: dict[uuid.UUID, Evidence] = {}
    for e in sorted(evidence, key=lambda x: x.rank):
        out.setdefault(e.document_id, e)
    return out


def supersessions(evidence: Sequence[Evidence]) -> list[Conflict]:
    """Every explicit link between two documents both present in the evidence."""
    documents = _documents(evidence)
    out: list[Conflict] = []
    for newer in documents.values():
        older_id = newer.supersedes_document_id
        if older_id is None or older_id not in documents:
            continue
        older = documents[older_id]
        out.append(
            Conflict(
                kind="superseded",
                topic=None,
                summary=(
                    f'"{newer.title}" replaces "{older.title}", as recorded by its source. '
                    f"The replacement is dated {_date(evidence_date(newer))}."
                ),
                positions=[],
                supersession=Supersession(
                    superseded_document_id=older.document_id,
                    superseding_document_id=newer.document_id,
                    since=evidence_date(newer),
                ),
            )
        )
    return sorted(out, key=lambda c: str(c.supersession and c.supersession.superseding_document_id))


def merge_conflicts(proposed: list[Conflict], links: list[Conflict]) -> list[Conflict]:
    """Model-proposed (validated) conflicts, plus every supersession link between
    evidence documents that no proposed item already shows."""
    shown = {
        (c.supersession.superseded_document_id, c.supersession.superseding_document_id)
        for c in proposed
        if c.supersession is not None
    }
    return proposed + [
        c
        for c in links
        if c.supersession is not None
        and (c.supersession.superseded_document_id, c.supersession.superseding_document_id)
        not in shown
    ]


def _linked(a: Evidence, b: Evidence) -> tuple[Evidence, Evidence] | None:
    """(older, newer) if an explicit supersession link joins the two documents."""
    if a.supersedes_document_id == b.document_id or a.document_id in b.superseded_by_document_ids:
        return b, a
    if b.supersedes_document_id == a.document_id or b.document_id in a.superseded_by_document_ids:
        return a, b
    return None


def conflicts(validation: Validation, evidence: Sequence[Evidence]) -> list[Conflict]:
    documents = _documents(evidence)
    out: list[Conflict] = []
    for proposed in validation.conflicts:
        positions: list[tuple[Evidence, ValidClaim]] = []
        for position in proposed.positions:
            first = position.citations[0]
            positions.append((documents[first.document_id], position))
        positions.sort(key=lambda p: (evidence_date(p[0]), p[0].rank))
        views = [
            ConflictPosition(
                claim=to_claim(claim),
                document_id=doc.document_id,
                title=doc.title,
                source_type=doc.source_type,
                authority=doc.authority,
                occurred_at=evidence_date(doc),
                version=doc.version,
            )
            for doc, claim in positions
        ]
        link = next(
            (
                pair
                for i, (a, _) in enumerate(positions)
                for (b, _) in positions[i + 1 :]
                if (pair := _linked(a, b)) is not None
            ),
            None,
        )
        if link is not None:
            older, newer = link
            out.append(
                Conflict(
                    kind="superseded",
                    topic=proposed.topic,
                    summary=(
                        f'"{newer.title}" replaces "{older.title}", as recorded by its source.'
                    ),
                    positions=views,
                    supersession=Supersession(
                        superseded_document_id=older.document_id,
                        superseding_document_id=newer.document_id,
                        since=evidence_date(newer),
                    ),
                )
            )
            continue
        latest = positions[-1][0]
        out.append(
            Conflict(
                kind="potential_conflict",
                topic=proposed.topic,
                summary=(
                    "These sources may disagree. The most recent is the "
                    f'{KIND_LABEL.get(latest.document_kind, "source")} "{latest.title}" '
                    f"({_date(evidence_date(latest))}). Lattice can't confirm which is current."
                ),
                positions=views,
            )
        )
    return out


def freshness(cited: Sequence[Evidence], evidence: Sequence[Evidence], now: datetime) -> Freshness:
    newest_evidence = max((evidence_date(e) for e in evidence), default=None)
    if not cited:
        return Freshness(
            newest_cited_at=None,
            newest_evidence_at=newest_evidence,
            is_stale=False,
            threshold_days=None,
            note=None,
        )
    newest = max(cited, key=evidence_date)
    kinds = [e.document_kind for e in cited]
    dominant = max(sorted(set(kinds)), key=kinds.count)
    threshold = STALE_DAYS.get(dominant, 180)
    if dominant == "doc" and all(
        e.authority == "system_of_record" for e in cited if e.document_kind == "doc"
    ):
        threshold = STALE_DAYS_SYSTEM_OF_RECORD_DOC
    newest_at = evidence_date(newest)
    stale = (now - newest_at).days > threshold
    return Freshness(
        newest_cited_at=newest_at,
        newest_evidence_at=newest_evidence,
        is_stale=stale,
        threshold_days=threshold,
        note=(
            f"The most recent evidence available to you is from {newest_at.strftime('%B %Y')}. "
            "Newer information may exist in systems Lattice doesn't index."
            if stale
            else f"Newest evidence: {_date(newest_at)}."
        ),
    )


def notices(
    *,
    excluded: Sequence[Evidence],
    used: Sequence[Evidence],
    removed: int,
    cited: Sequence[Evidence],
    evidence: Sequence[Evidence],
    stale: Freshness,
    semantic_degraded: bool,
) -> list[Notice]:
    out: list[Notice] = []
    if excluded:
        newest_used = max((evidence_date(e) for e in used), default=None)
        newer = newest_used is None or max(evidence_date(e) for e in excluded) > newest_used
        count = len({e.document_id for e in excluded})
        noun = "source" if count == 1 else "sources"
        verb = "wasn't" if count == 1 else "weren't"
        out.append(
            Notice(
                code="egress_excluded",
                count=count,
                message=(
                    f"{count} {noun} you can access {verb} sent to the AI model because of "
                    "your organization's AI data policy"
                    + (", including the most recent evidence" if newer and used else "")
                    + ". Review them below."
                ),
            )
        )
    if removed:
        out.append(
            Notice(
                code="claims_removed",
                count=removed,
                message=MESSAGES[("partial", "claims_removed")],
            )
        )
    if stale.is_stale and stale.note:
        out.append(Notice(code="stale_evidence", message=stale.note))
    documents = _documents(evidence)
    for e in _documents(cited).values():
        if not e.superseded_by_document_ids:
            continue
        successor = next(
            (documents[d] for d in e.superseded_by_document_ids if d in documents), None
        )
        tail = (
            f'"{successor.title}" ({_date(evidence_date(successor))})'
            if successor
            else "a newer document you can access"
        )
        out.append(
            Notice(
                code="cites_superseded",
                message=f'This answer cites "{e.title}", which has been replaced by {tail}.',
            )
        )
    if semantic_degraded:
        out.append(
            Notice(
                code="semantic_degraded",
                message=(
                    "Meaning-based search wasn't available for this question; the evidence "
                    "comes from keyword and reference search."
                ),
            )
        )
    return out


def decide_status(validation: Validation | None) -> tuple[str, str | None]:
    """Status and reason after a model call (validation None: no usable draft)."""
    if validation is None:
        return "failed", "generation_invalid"
    if validation.model_status == "unsupported_request":
        return "unsupported_request", "action_request"
    direct = [c for c in validation.claims if c.role == "direct_answer"]
    if not direct:
        if validation.model_status == "ambiguous" and validation.clarifying_question:
            return "ambiguous", "missing_referent"
        if validation.claims_rejected:
            return "insufficient_evidence", "claims_unverified"
        return "insufficient_evidence", "no_relevant_evidence"
    if validation.claims_rejected:
        return "partial", "claims_removed"
    if validation.model_status == "partial":
        return "partial", "incomplete"
    return "answered", None


def clarification_for(
    accounts: Sequence[VisibleAccount] = (), question: str | None = None
) -> Clarification:
    return Clarification(
        question=question or MESSAGES[("ambiguous", "multiple_accounts")],
        choices=[ClarificationChoice(key=a.key, name=a.name) for a in accounts],
    )
