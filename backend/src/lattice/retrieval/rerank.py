"""rerank_v1 and evidence selection (retrieval-design §6-7). Deterministic; no model.

::

    final = rrf
          * (1 + 0.15 * account_match)      # visible account named in the question or scope
          * (1 + 0.10 * authority_weight)   # system_of_record 1, working 0.5, conversational 0
          * (1 + 0.20 * recency * temporal_intent)   # recency = exp(-age_days / 60)
          * (1 + 0.50 * identifier_match)   # found by the exact identifier channel

Every factor is bounded (the largest possible combined boost is about 2.3 times), so the
reranker reorders: it never removes a candidate, and no single signal can bury the
rest of the fused list. In particular:

* **Recency is not truth.** It applies only when the question asks for the current
  state, and older evidence stays in the list; a newer Slack message cannot suppress
  an older decision record that contradicts it.
* **Authority is not truth.** A decision record ranks somewhat above a conversation
  about the same thing; both remain. Whether they conflict, and which is current, is
  Phase 6's job -- and only explicit supersession metadata may settle it.
* **Account match** uses only the visible account of a candidate (D-8): a hidden
  account can never earn a boost.

Selection then walks the reranked list: identical chunk text (same organization,
same content hash) is kept once; at most ``max_chunks_per_document`` chunks per
document come first, and the chunks that cap held back fill any remaining positions.
"""

import math
import uuid
from collections.abc import Collection, Sequence
from datetime import datetime

from lattice.retrieval.models import FusedCandidate, RankedCandidate, RetrievalConfig

AUTHORITY_WEIGHT = {"system_of_record": 1.0, "working": 0.5, "conversational": 0.0}


def features(
    fused: FusedCandidate,
    *,
    account_targets: Collection[uuid.UUID],
    temporal_intent: bool,
    now: datetime,
    config: RetrievalConfig,
) -> dict[str, float]:
    c = fused.candidate
    recency = 0.0
    if c.occurred_at is not None:
        age_days = max(0.0, (now - c.occurred_at).total_seconds() / 86400)
        recency = math.exp(-age_days / config.recency_scale_days)
    return {
        "account_match": 1.0
        if c.visible_account_id is not None and c.visible_account_id in account_targets
        else 0.0,
        "authority_weight": AUTHORITY_WEIGHT.get(c.authority, 0.0),
        "recency": round(recency, 6),
        "temporal_intent": 1.0 if temporal_intent else 0.0,
        "identifier_match": 1.0 if "exact" in fused.ranks else 0.0,
    }


def final_score(rrf: float, f: dict[str, float], config: RetrievalConfig) -> float:
    return (
        rrf
        * (1 + config.weight_account_match * f["account_match"])
        * (1 + config.weight_authority * f["authority_weight"])
        * (1 + config.weight_recency * f["recency"] * f["temporal_intent"])
        * (1 + config.weight_identifier * f["identifier_match"])
    )


def rerank(
    fused: Sequence[FusedCandidate],
    *,
    account_targets: Collection[uuid.UUID],
    temporal_intent: bool,
    now: datetime,
    config: RetrievalConfig,
) -> list[RankedCandidate]:
    scored = [
        (
            f,
            final_score(
                f.rrf_score,
                feats := features(
                    f,
                    account_targets=account_targets,
                    temporal_intent=temporal_intent,
                    now=now,
                    config=config,
                ),
                config,
            ),
            feats,
        )
        for f in fused
    ]
    scored.sort(key=lambda item: (-item[1], -item[0].rrf_score, item[0].fused_rank))
    return [
        RankedCandidate(fused=f, features=feats, final_score=score, final_rank=position)
        for position, (f, score, feats) in enumerate(scored, start=1)
    ]


def select_evidence(
    ranked: Sequence[RankedCandidate], *, limit: int, per_document: int
) -> list[RankedCandidate]:
    """Walk the reranked list: identical text (same organization and content hash) is
    kept once; at most ``per_document`` chunks per document in a first pass, so one
    long document cannot fill the list. Chunks held back by that cap are deferred, not
    dropped: they fill the remaining positions, in rank order."""
    selected: list[RankedCandidate] = []
    deferred: list[RankedCandidate] = []
    seen_text: set[tuple[uuid.UUID, bytes]] = set()
    per_doc: dict[tuple[uuid.UUID, uuid.UUID], int] = {}
    for r in ranked:
        c = r.fused.candidate
        text_key = (c.organization_id, c.content_hash)
        if text_key in seen_text:
            continue
        seen_text.add(text_key)
        doc_key = (c.organization_id, c.document_id)
        if per_doc.get(doc_key, 0) >= per_document:
            deferred.append(r)
            continue
        per_doc[doc_key] = per_doc.get(doc_key, 0) + 1
        selected.append(r)
        if len(selected) == limit:
            return selected
    return [*selected, *deferred][:limit]
