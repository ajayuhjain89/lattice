"""Reciprocal Rank Fusion (retrieval-design §6, ADR-004).

``rrf(chunk) = Σ over channels that returned it of 1 / (k + rank)`` with ``k = 60``.
Rank-based, because exact (constant), ``ts_rank_cd`` and cosine similarity are not on
comparable scales; no score normalization.

A chunk returned by several channels is one fused candidate that remembers every
channel that found it (``matched_by``) with its rank and score there. Identity is
``(organization_id, chunk_id)``: the organization is part of a resource's identity,
so two tenants' rows can never be merged into one.

Ordering is total and deterministic: fused score, then the best rank in any channel,
then the chunk's stable key, then its ID.
"""

import uuid
from collections.abc import Iterable, Sequence

from lattice.retrieval.models import Candidate, Channel, FusedCandidate


def rrf_contribution(rank: int, k: int) -> float:
    return 1.0 / (k + rank)


def fuse(channels: Iterable[Sequence[Candidate]], *, k: int, pool: int) -> list[FusedCandidate]:
    """The top ``pool`` fused candidates."""
    first: dict[tuple[uuid.UUID, uuid.UUID], Candidate] = {}
    ranks: dict[tuple[uuid.UUID, uuid.UUID], dict[Channel, int]] = {}
    scores: dict[tuple[uuid.UUID, uuid.UUID], dict[Channel, float]] = {}
    for candidates in channels:
        for c in candidates:
            first.setdefault(c.key, c)
            channel_ranks = ranks.setdefault(c.key, {})
            if c.channel in channel_ranks:  # a channel lists a chunk once; keep its best
                continue
            channel_ranks[c.channel] = c.rank
            scores.setdefault(c.key, {})[c.channel] = c.score

    def rrf(key: tuple[uuid.UUID, uuid.UUID]) -> float:
        return sum(rrf_contribution(r, k) for r in ranks[key].values())

    ordered = sorted(
        first,
        key=lambda key: (
            -rrf(key),
            min(ranks[key].values()),
            first[key].sort_key,
            str(key[0]),
            str(key[1]),
        ),
    )
    return [
        FusedCandidate(
            candidate=first[key],
            ranks=dict(ranks[key]),
            scores=dict(scores[key]),
            rrf_score=rrf(key),
            fused_rank=position,
        )
        for position, key in enumerate(ordered[:pool], start=1)
    ]
