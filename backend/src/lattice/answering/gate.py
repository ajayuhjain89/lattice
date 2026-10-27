"""The relevance gate (P13.3; phase-13-plan §11, gaps G1, G3, G9; retrieval-design §7.1).

Deterministic: no model call. Every evidence item is classified from its own signals and
from term statistics of the chunks the asker may read -- permitted rows only, so the
outcome is the same with or without documents the asker cannot read (SEC-5, S4):

* **identifier** -- the exact channel matched it: the question names its record;
* **coverage** -- its *weighted coverage* of the question is at least ``min_coverage``;
* **semantic** -- its similarity is at or above the floor calibrated for that embedding
  model (a model without a calibrated floor never passes on similarity alone: nearest
  neighbours always exist);
* **partial** -- it contains some content lexeme, below the coverage threshold;
* **account_only** -- lexically matched, but only through the account the question
  names or words that are not content terms;
* **no_signal** -- nothing above.

Weighted coverage: the question's *content lexemes* are its own lexemes (the text-search
configuration drops stopwords) except the names and aliases of the visible accounts it
mentions; a named visible account counts as one more term, covered by that account's
chunks. Each term weighs ``ln((N + 1) / (n + 1)) + 1`` -- ``N`` the permitted chunks,
``n`` those containing it (smoothed inverse document frequency) -- so a word every chunk
has weighs least, never nothing (a reader with a handful of chunks still has a gate), and
a word no permitted chunk has weighs most. An item's coverage is the share of the total
weight it contains. "What is Acme Corp's NPS score?" over Acme chunks that never mention an NPS
covers only the account and "score": the rarest word, the one the question is about, is
missing.

``identifier``, ``coverage`` and ``semantic`` are **decisive**. The gate decides *whether*
to answer, not what the answer may draw on: once it passes, every item a channel other
than the semantic one matched (``partial`` and ``account_only`` included, as before
P13.3) joins the decisive ones in the prompt, so context recall is kept; ``no_signal``
items -- semantic-only below the floor -- never enter it (G3: above the floor they do).
The answer-level decision:

* no decisive item → ``insufficient_evidence`` / ``no_relevant_evidence``: no model call
  (G1);
* decisive items exist but the egress policy holds every one back → ``evidence_only`` /
  ``egress_policy``: a weak remainder is not drafted from (G9);
* otherwise the admitted items the policy permits go to the prompt.

Items below the gate stay in the asker's evidence list (never filtered from retrieval).
The trace records each item's code and numbers -- counts, weights, scores and codes, never
the lexemes (they are the asker's words: S10).
"""

import math
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from lattice.retrieval import Evidence, RetrievalResult
from lattice.retrieval.terms import MAX_TERMS, TermStatistics

GateCode = Literal["identifier", "coverage", "semantic", "partial", "account_only", "no_signal"]
DECISIVE: Final = frozenset({"identifier", "coverage", "semantic"})
ADMITTED: Final = DECISIVE | {"partial", "account_only"}
GateDecision = Literal["pass", "no_decisive_evidence", "decisive_held_back"]


@dataclass(frozen=True, slots=True)
class GateConfig:
    """The gate's tunables, versioned and recorded in the trace. Calibrated on the dev
    split only (phase-13-plan EC-7, D-39); held-out results are reported, never tuned."""

    version: str = "gate_v1"
    # Dev split (evaluation-plan §17): A (no semantic signal) stops 11/17 expected-abstain
    # runs and refuses 4/81 answerable ones; C (local mxbai, floor below) 11/17 and 2/81.
    min_coverage: float = 0.50
    # Cosine similarity floors per embedding model *and revision* (as recorded on its
    # vectors): another model or revision has none until it is calibrated.
    semantic_floor: Mapping[str, float] = field(
        default_factory=lambda: {"mxbai-embed-large-v1@b33106f585b9": 0.76}
    )


DEFAULT_GATE: Final = GateConfig()


@dataclass(frozen=True, slots=True)
class GateItem:
    chunk_id: uuid.UUID
    code: GateCode
    terms_matched: int
    coverage: float | None  # None when the question has no weighed term
    semantic: float | None  # the item's similarity, when the semantic channel scored it

    def to_json(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.chunk_id),
            "code": self.code,
            "terms_matched": self.terms_matched,
            "coverage": self.coverage,
            "semantic": self.semantic,
        }


@dataclass(frozen=True, slots=True)
class Gate:
    items: tuple[GateItem, ...]
    question_terms: int
    total_weight: float
    semantic_floor: float | None
    config: GateConfig

    @property
    def decisive(self) -> set[uuid.UUID]:
        return {i.chunk_id for i in self.items if i.code in DECISIVE}

    @property
    def admitted(self) -> set[uuid.UUID]:
        return {i.chunk_id for i in self.items if i.code in ADMITTED}

    def decide(
        self, permits: Callable[[Evidence], bool], evidence: Sequence[Evidence]
    ) -> GateDecision:
        """``permits`` is the egress decision for one evidence item."""
        decisive = self.decisive
        if not decisive:
            return "no_decisive_evidence"
        if not any(permits(e) for e in evidence if e.chunk_id in decisive):
            return "decisive_held_back"
        return "pass"

    def to_json(self, decision: GateDecision | None) -> dict[str, Any]:
        return {
            "version": self.config.version,
            "min_coverage": self.config.min_coverage,
            "semantic_floor": self.semantic_floor,
            "question_terms": self.question_terms,
            "total_weight": round(self.total_weight, 4),
            "decision": decision,
            "items": [i.to_json() for i in self.items],
        }


def _weight(chunks: int, containing: int) -> float:
    return math.log((chunks + 1) / (min(containing, chunks) + 1)) + 1.0


def _code(
    e: Evidence,
    coverage: float | None,
    semantic: float | None,
    floor: float | None,
    cfg: GateConfig,
) -> GateCode:
    if "exact" in e.matched_by:
        return "identifier"
    if coverage is not None and coverage >= cfg.min_coverage:
        return "coverage"
    if floor is not None and semantic is not None and semantic >= floor:
        return "semantic"
    if e.terms_matched:
        return "partial"
    if "lexical" in e.matched_by:
        return "account_only"
    return "no_signal"


def evaluate(
    retrieval: RetrievalResult, stats: TermStatistics, config: GateConfig = DEFAULT_GATE
) -> Gate:
    floor = config.semantic_floor.get(retrieval.semantic.model or "")
    scores = {c.chunk_id: c.score for c in retrieval.trace.stages.get("semantic", ())}
    weights = {
        t: _weight(stats.chunks, stats.document_frequency.get(t, 0))
        for t in retrieval.question_terms[:MAX_TERMS]
    }
    mentioned = {a.id for a in retrieval.account_mentions}
    account_weight = _weight(stats.chunks, stats.account_chunks) if mentioned else 0.0
    total = sum(weights.values()) + account_weight
    items = []
    for e in retrieval.evidence:
        covered = sum(weights.get(t, 0.0) for t in e.terms_matched)
        if e.account is not None and e.account.id in mentioned:
            covered += account_weight
        coverage = round(covered / total, 4) if total > 0 else None
        semantic = scores.get(e.chunk_id)
        items.append(
            GateItem(
                chunk_id=e.chunk_id,
                code=_code(e, coverage, semantic, floor, config),
                terms_matched=len(e.terms_matched),
                coverage=coverage,
                semantic=semantic,
            )
        )
    return Gate(
        items=tuple(items),
        question_terms=len(weights),
        total_weight=total,
        semantic_floor=floor,
        config=config,
    )
