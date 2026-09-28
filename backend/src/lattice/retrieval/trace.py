"""The retrieval trace and its log line (data-model §7 ``retrieval``, SEC-8).

The trace is returned with the result, to the reader's own request (and, from Phase 6,
stored in ``query_traces`` under the asker's re-authorized view). It holds
identifiers, ranks, scores, counts, codes and durations -- never the question, document
text, titles, account or source names -- and every chunk ID and count in it concerns
rows the reader may read.

The operational log line is narrower still: IDs, versions, codes, counts of permitted
candidates and durations.
"""

import logging
import uuid
from collections.abc import Mapping, Sequence

from lattice.authz import AuthorizationContext
from lattice.log import log_event
from lattice.retrieval.models import (
    Candidate,
    FusedCandidate,
    RankedCandidate,
    RetrievalConfig,
    RetrievalTrace,
    SemanticOutcome,
    TraceCandidate,
    TraceRanked,
)

logger = logging.getLogger(__name__)

TRACE_STAGE_CAP = 50


def _stage(candidates: Sequence[Candidate]) -> tuple[TraceCandidate, ...]:
    return tuple(
        TraceCandidate.of(c.chunk_id, c.rank, c.score) for c in candidates[:TRACE_STAGE_CAP]
    )


def build_trace(
    *,
    request_id: uuid.UUID,
    ctx: AuthorizationContext,
    config: RetrievalConfig,
    semantic: SemanticOutcome,
    question_sensitivity: str,
    stages: Mapping[str, Sequence[Candidate]],
    fused: Sequence[FusedCandidate],
    ranked: Sequence[RankedCandidate],
    final_count: int,
    timings_ms: Mapping[str, float],
) -> RetrievalTrace:
    return RetrievalTrace(
        request_id=request_id,
        organization_id=ctx.organization_id,
        membership_id=ctx.membership_id,
        policy_version=ctx.policy_version,
        config_version=config.version,
        rerank_version=config.rerank_version,
        rrf_k=config.rrf_k,
        limits=config.limits(),
        embedding_provider=semantic.provider,
        embedding_model=semantic.model,
        embedding_deployment=semantic.deployment,
        question_sensitivity=question_sensitivity,
        semantic_status=semantic.status,
        semantic_reason=semantic.reason,
        degraded=semantic.degraded,
        counts={
            "exact": len(stages["exact"]),
            "lexical": len(stages["lexical"]),
            "semantic": len(stages["semantic"]),
            "merged": len(fused),
            "reranked": len(ranked),
            "final": final_count,
        },
        timings_ms={k: round(v, 2) for k, v in timings_ms.items()},
        stages={name: _stage(candidates) for name, candidates in stages.items()}
        | {
            "fused": tuple(
                TraceCandidate.of(f.candidate.chunk_id, f.fused_rank, f.rrf_score)
                for f in fused[:TRACE_STAGE_CAP]
            )
        },
        reranked=tuple(
            TraceRanked(
                chunk_id=r.fused.candidate.chunk_id,
                rank=r.final_rank,
                matched_by=r.fused.matched_by,
                rrf_score=round(r.fused.rrf_score, 8),
                features=dict(r.features),
                final_score=round(r.final_score, 8),
            )
            for r in ranked[:TRACE_STAGE_CAP]
        ),
    )


def log_trace(trace: RetrievalTrace) -> None:
    log_event(
        logger,
        logging.INFO,
        "retrieval.completed",
        retrieval_request_id=str(trace.request_id),
        organization_id=str(trace.organization_id),
        membership_id=str(trace.membership_id),
        policy_version=trace.policy_version,
        config_version=trace.config_version,
        semantic_status=trace.semantic_status,
        semantic_reason=trace.semantic_reason,
        question_sensitivity=trace.question_sensitivity,
        degraded=trace.degraded,
        **{f"count_{k}": v for k, v in trace.counts.items()},
        duration_ms=trace.timings_ms.get("total"),
    )
