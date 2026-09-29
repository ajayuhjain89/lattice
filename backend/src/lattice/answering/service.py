"""Ask: authorized evidence in, a validated answer out (architecture §4).

::

    snapshot (REPEATABLE READ, READ ONLY, organization bound)
      AuthorizationContext → retrieval (predicate in SQL, E4 twice)
      → classifications of the evidence (readable only) → generation access
        (configuration, invocation rule, question sensitivity, credential reference)
    [snapshot closed -- no transaction is held during a model call]
    no evidence                     → insufficient_evidence / no_relevant_evidence
    ≥ 2 visible accounts named      → ambiguous / multiple_accounts (choices)
    no provider may be called       → evidence_only (egress_policy | generation_unavailable)
    evidence above the level        → kept out of the prompt, returned "not used"
    prompt (fixed instructions + nonce-delimited question and evidence, handles only)
    → generation → claim-level validation → (one regeneration if anything failed)
    → deterministic assembly → response invariant → trace (one INSERT) → Answer

The model never receives evidence the asker cannot read: the prompt is built only from
``RetrievalResult.evidence`` (authorized in SQL, re-verified by E4 and again when its
classification is read), filtered by the egress level. Nothing the model returns is an
input to authorization, retrieval or configuration.
"""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from lattice.answering import assemble
from lattice.answering.models import ANSWER_SCHEMA, Answer, AskRequest, Claim, EvidenceItem
from lattice.answering.policy import GenerationAccess, resolve_generation
from lattice.answering.prompt import (
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    SYSTEM_INSTRUCTIONS,
    Prompt,
    budget,
    build_prompt,
    with_feedback,
)
from lattice.answering.validate import Validation, validate_draft
from lattice.authz import resolve_authorization_context, summarize_context
from lattice.db.engine import Database
from lattice.log import log_event
from lattice.providers.embedding import EmbeddingProvider
from lattice.providers.generation import GenerationError, GenerationRequest
from lattice.providers.generation_factory import GenerationFactory
from lattice.providers.secrets import SecretStore
from lattice.retrieval import (
    DEFAULT_CONFIG,
    RetrievalConfig,
    RetrievalQuery,
    RetrievalResult,
    VisibleAccount,
    retrieve_with_context,
)
from lattice.retrieval.query import normalize
from lattice.tracing.recorder import record_trace

logger = logging.getLogger(__name__)

EVIDENCE_LIMIT: Final = 12  # the prompt budget's item count (retrieval-design §7.1)
GENERATION_TIMEOUT_SECONDS: Final = 360.0  # outer bound; adapters have their own
# The relevance floor (retrieval-design §7.1, §11): an evidence item may enter the
# prompt only if the exact or lexical channel matched it, or its semantic similarity is
# at or above a threshold calibrated for that embedding model; with no such item, no
# model is called (insufficient_evidence). No model is calibrated yet, so a
# semantic-only match never passes on its own (nearest neighbours always exist; they
# are not evidence of relevance). Items below the floor stay in the asker's list.
SEMANTIC_FLOOR: Final[dict[str, float]] = {}
# Reasons meaning "the policy forbids it", as opposed to "nothing is available".
POLICY_REASONS: Final = frozenset(
    {
        "deployment_unknown",
        "provider_deployment_mismatch",
        "deployment_not_allowed",
        "provider_unapproved",
        "provider_data_use",
        "policy_unrecognised",
        "question_classification",
    }
)


@dataclass(frozen=True)
class AskDependencies:
    """What a request may use: the query-embedding provider (retrieval), how to build the
    organization's generation provider from its configuration, and where credential
    references resolve. None of these decides what may be sent."""

    embedding: EmbeddingProvider | None
    generation: GenerationFactory
    secrets: SecretStore


@dataclass
class _Calls:
    count: int = 0
    schema_failures: int = 0
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    timings_ms: list[float] = field(default_factory=list)
    regenerated: bool = False


@dataclass(frozen=True)
class AskOutcome:
    answer: Answer
    trace: dict[str, Any]  # the query_traces row


async def _generate(
    access: GenerationAccess, prompt: Prompt, calls: _Calls, feedback: list[str] | None
) -> Validation | None:
    """One generation call and its validation; None when the provider failed."""
    assert access.provider is not None  # noqa: S101 - checked by the caller
    request = GenerationRequest(
        system=SYSTEM_INSTRUCTIONS,
        prompt=prompt.text if feedback is None else with_feedback(prompt, feedback),
        schema=ANSWER_SCHEMA,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    calls.count += 1
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            access.provider.generate(request), timeout=GENERATION_TIMEOUT_SECONDS
        )
    except TimeoutError:
        calls.error = "generation_failed:timeout"
        return None
    except GenerationError as exc:
        calls.error = exc.code
        return None
    finally:
        calls.timings_ms.append(round((time.perf_counter() - started) * 1000, 1))
    calls.input_tokens += result.input_tokens or 0
    calls.output_tokens += result.output_tokens or 0
    validation = validate_draft(result.text, prompt.by_handle)
    if not validation.schema_ok:
        calls.schema_failures += 1
    return validation


def _relevant(retrieval: RetrievalResult) -> set[uuid.UUID]:
    """Chunk IDs that pass the relevance floor."""
    floor = SEMANTIC_FLOOR.get(retrieval.semantic.model or "")
    semantic = {c.chunk_id: c.score for c in retrieval.trace.stages.get("semantic", ())}
    return {
        e.chunk_id
        for e in retrieval.evidence
        if {"exact", "lexical"} & set(e.matched_by)
        or (floor is not None and semantic.get(e.chunk_id, -1.0) >= floor)
    }


def _ambiguous(question: str, mentions: Sequence[VisibleAccount]) -> bool:
    """Retrieval reports every visible account whose name or alias occurs in the
    question (Phase 5 leaves what to ask to Phase 6). A question that names an account
    in full is specific -- "Acme Corp" also contains Acme Health's alias "Acme" -- and
    several accounts named in full are a comparison, not an ambiguity. Only two or more
    accounts matched by bare aliases need a clarifying choice. Only accounts the asker
    may see are ever involved."""
    if len(mentions) < 2:
        return False
    return not any(
        re.search(rf"(?<!\w){re.escape(a.name)}(?!\w)", question, re.IGNORECASE) for a in mentions
    )


def _response_invariant(claims: list[Claim], evidence: list[EvidenceItem]) -> bool:
    """Every citation references an evidence item of this response that was sent to the
    model (and so is readable by the asker); every quote is exactly its highlighted span."""
    items = {e.evidence_id: e for e in evidence}
    for claim in claims:
        for c in claim.citations:
            item = items.get(c.evidence_id)
            if item is None or not item.used_for_answer or item.document_id != c.document_id:
                return False
            if item.text[c.quote_start : c.quote_end] != c.quote:
                return False
    return True


async def answer_question(
    db: Database,
    *,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    request: AskRequest,
    deps: AskDependencies,
    config: RetrievalConfig = DEFAULT_CONFIG,
    now: datetime | None = None,
    nonce: str | None = None,
) -> AskOutcome:
    """The whole pipeline, without persisting the trace (``ask`` persists it). Both IDs
    must come from the server-side session."""
    started = time.perf_counter()
    now = now or datetime.now(UTC)
    trace_id = uuid.uuid4()
    timings: dict[str, Any] = {}

    async with db.tenant_snapshot(organization_id) as session:
        ctx = await resolve_authorization_context(
            session, organization_id=organization_id, membership_id=membership_id
        )
        retrieval: RetrievalResult = await retrieve_with_context(
            session,
            ctx,
            RetrievalQuery(
                text=request.question,
                scope=request.scope,
                limit=EVIDENCE_LIMIT,
                classification=request.classification,
            ),
            deps.embedding,
            config=config,
            now=now,
        )
        relevant_ids = _relevant(retrieval)
        relevant = bool(relevant_ids)
        ambiguous = request.scope.account is None and _ambiguous(
            request.question, retrieval.account_mentions
        )
        policy_started = time.perf_counter()
        access = await resolve_generation(
            session,
            ctx,
            retrieval.evidence,
            retrieval.question_sensitivity,
            factory=deps.generation,
            secrets=deps.secrets,
            invoke=relevant and not ambiguous,
        )
        timings["policy"] = round((time.perf_counter() - policy_started) * 1000, 1)
    timings["retrieval"] = retrieval.trace.timings_ms

    evidence = list(retrieval.evidence)
    candidates = [e for e in evidence if e.chunk_id in relevant_ids]
    permitted = [e for e in candidates if access.permits(e)]
    excluded = [e for e in candidates if not access.permits(e)] if access.level else []
    fits, _ = budget(permitted)
    calls = _Calls()
    validation: Validation | None = None
    prompt: Prompt | None = None
    status: str
    reason: str | None
    clarification = None

    if not relevant:
        status, reason = "insufficient_evidence", "no_relevant_evidence"
    elif ambiguous:
        status, reason = "ambiguous", "multiple_accounts"
        clarification = assemble.clarification_for(retrieval.account_mentions)
    elif access.provider is None:
        status = "evidence_only"
        reason = "egress_policy" if access.reason in POLICY_REASONS else "generation_unavailable"
    elif not fits:
        status, reason = "evidence_only", "egress_policy"
    else:
        prompt = build_prompt(normalize(request.question), fits, nonce=nonce)
        generation_started = time.perf_counter()
        first = await _generate(access, prompt, calls, None)
        validation = first if first is not None and first.schema_ok else None
        if first is not None and (not first.schema_ok or first.rejections):
            calls.regenerated = True
            feedback = (
                [r.feedback() for r in first.rejections]
                if first.schema_ok
                else ["the output did not match the JSON schema"]
            )
            second = await _generate(access, prompt, calls, feedback)
            if second is not None and second.schema_ok:
                validation = second
        timings["generation"] = round((time.perf_counter() - generation_started) * 1000, 1)
        timings["generation_calls"] = calls.timings_ms
        if validation is None and calls.error is not None and calls.schema_failures == 0:
            status, reason = "failed", "provider_unavailable"
        else:
            status, reason = assemble.decide_status(validation)
            if status == "ambiguous" and validation is not None:
                clarification = assemble.clarification_for(question=validation.clarifying_question)

    used_ids = {e.chunk_id for e in prompt.by_handle.values()} if prompt else set()
    items = assemble.evidence_items(
        evidence,
        access.classifications,
        used_ids,
        excluded_by_egress={e.chunk_id for e in evidence if not access.permits(e)},
        relevant=relevant_ids,
    )
    show_claims = status in ("answered", "partial")
    claims = [assemble.to_claim(c) for c in validation.claims] if validation and show_claims else []
    conflicts = assemble.merge_conflicts(
        assemble.conflicts(validation, evidence) if validation and show_claims else [],
        assemble.supersessions(evidence),
    )
    shown = claims + [p.claim for k in conflicts for p in k.positions]
    error_code = calls.error if status == "failed" else None
    # The response invariant runs before anything is derived from the citations.
    if not _response_invariant(shown, items):
        # A bug, never an expected outcome: withhold everything the model produced.
        status, reason = "failed", "citation_validation_failed"
        error_code = "citation_validation_failed"
        claims, conflicts, shown = [], assemble.supersessions(evidence), []
        log_event(
            logger,
            logging.ERROR,
            "answer.citation_invariant_violated",
            trace_id=str(trace_id),
            organization_id=str(organization_id),
        )
    by_chunk = {e.chunk_id: e for e in evidence}
    cited = [by_chunk[c.evidence_id] for claim in shown for c in claim.citations]
    fresh = assemble.freshness(cited, evidence, now)
    removed = validation.claims_rejected if validation is not None and claims else 0
    notice_list = assemble.notices(
        excluded=excluded,
        used=[by_chunk[i] for i in used_ids],
        removed=removed,
        cited=cited,
        evidence=evidence,
        stale=fresh,
        semantic_degraded=retrieval.semantic.degraded,
    )

    answer = Answer(
        trace_id=trace_id,
        status=status,
        status_reason=reason,
        message=assemble.MESSAGES.get((status, reason or "")),
        claims=claims,
        conflicts=conflicts,
        unknowns=validation.unknowns if validation is not None and show_claims else [],
        clarification=clarification,
        evidence=items,
        freshness=fresh,
        notices=notice_list,
        question_sensitivity=retrieval.question_sensitivity,
    )
    timings["total"] = round((time.perf_counter() - started) * 1000, 1)
    trace = {
        "id": trace_id,
        "organization_id": organization_id,
        "membership_id": membership_id,
        "question_text": request.question,
        "question_sensitivity": retrieval.question_sensitivity,
        "scope": request.scope.model_dump(mode="json", exclude_none=True),
        "authz_snapshot": summarize_context(ctx),
        "retrieval": retrieval.trace.model_dump(mode="json"),
        "evidence_chunk_ids": [e.chunk_id for e in evidence],
        "prompt_chunk_ids": sorted(used_ids),
        "cited_chunk_ids": sorted({c.evidence_id for claim in shown for c in claim.citations}),
        "egress": {
            "provider": access.provider_name,
            "model": access.model,
            "deployment": access.deployment,
            "effective_level": access.level,
            "decision_reason": access.reason,
            "question_sensitivity": retrieval.question_sensitivity,
            "excluded_by_egress_policy": len(excluded),
            "excluded_by_prompt_budget": len(permitted) - len(fits),
            "below_relevance_floor": len(evidence) - len(candidates),
            "prompt_version": PROMPT_VERSION if prompt else None,
        },
        "validation": {
            "generation_calls": calls.count,
            "regenerated": calls.regenerated,
            "schema_failures": calls.schema_failures,
            "claims_proposed": validation.claims_proposed if validation else 0,
            "claims_valid": len(validation.claims) if validation else 0,
            "claims_rejected": validation.claims_rejected if validation else 0,
            "rejection_codes": validation.rejection_codes() if validation else {},
            "conflicts_proposed": validation.conflicts_proposed if validation else 0,
            "conflicts_discarded": validation.conflicts_discarded if validation else 0,
            "model_status": validation.model_status if validation else None,
        },
        "answer": answer.model_dump(mode="json", exclude={"evidence", "trace_id"}),
        "status": status,
        "status_reason": reason,
        "error_code": error_code,
        "timings_ms": timings,
        "usage": {"input_tokens": calls.input_tokens, "output_tokens": calls.output_tokens},
    }
    log_event(
        logger,
        logging.INFO,
        "answer.completed",
        trace_id=str(trace_id),
        organization_id=str(organization_id),
        membership_id=str(membership_id),
        status=status,
        status_reason=reason,
        error_code=error_code,
        provider=access.provider_name,
        model=access.model,
        decision_reason=access.reason,
        evidence=len(evidence),
        prompt_evidence=len(used_ids),
        generation_calls=calls.count,
        claims=len(claims),
        duration_ms=timings["total"],
    )
    return AskOutcome(answer=answer, trace=trace)


async def ask(
    db: Database,
    *,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    request: AskRequest,
    deps: AskDependencies,
    config: RetrievalConfig = DEFAULT_CONFIG,
    now: datetime | None = None,
) -> AskOutcome:
    outcome = await answer_question(
        db,
        organization_id=organization_id,
        membership_id=membership_id,
        request=request,
        deps=deps,
        config=config,
        now=now,
    )
    await record_trace(db, outcome.trace)
    return outcome
