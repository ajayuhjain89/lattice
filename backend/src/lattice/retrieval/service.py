"""Retrieval orchestration: one request, one authorization snapshot.

``retrieve`` opens ``Database.tenant_snapshot`` (REPEATABLE READ, READ ONLY, bound to
the session's organization) and, inside it, resolves the ``AuthorizationContext`` and
runs every read of the request -- account visibility, query egress settings, the exact,
lexical and semantic statements, evidence hydration and both E4 checks. A permission
change committed while the request runs applies to the next request; this one never
mixes two permission states.

Order of work::

    analyse the query (deterministic)           → identifiers, temporal intent
    visible accounts                            → scope account, account mentions
    question sensitivity (D-13), query egress   → embed the query concurrently, or not
    exact, lexical (SQL with the predicate)
    semantic (SQL with the predicate), if the query vector exists
    E4: assert_all_readable(every candidate's document)    ← abort, never filter
    RRF fusion → rerank_v1 → selection
    hydrate evidence (SQL with the predicate)
    E4: assert_all_readable(every evidence document)
    trace, log line

Semantic failure of any kind degrades to exact + lexical with an explicit outcome; it
never fails the request. A failed E4 check always does.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from datetime import time as clock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz import (
    AuthorizationContext,
    assert_all_readable,
    resolve_authorization_context,
    visible_accounts_predicate,
)
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorDetail, LatticeError
from lattice.providers.embedding import EmbeddingProvider
from lattice.retrieval.evidence import QuestionTerms, hydrate
from lattice.retrieval.exact import exact_candidates
from lattice.retrieval.fusion import fuse
from lattice.retrieval.lexical import QUESTION_FRAME_WORDS, lexical_candidates
from lattice.retrieval.models import (
    DEFAULT_CONFIG,
    SOURCE_TYPE_KINDS,
    Candidate,
    RetrievalConfig,
    RetrievalQuery,
    RetrievalResult,
    RetrievalScope,
    SemanticOutcome,
    VisibleAccount,
)
from lattice.retrieval.permitted import ACCOUNTS, ResolvedScope
from lattice.retrieval.query import (
    AccountNames,
    extract_identifiers,
    has_temporal_intent,
    mentioned_accounts,
    normalize,
)
from lattice.retrieval.rerank import rerank, select_evidence
from lattice.retrieval.semantic import (
    embed_query,
    query_egress_block,
    question_policy,
    semantic_candidates,
)
from lattice.retrieval.terms import lexical_weights, question_statistics
from lattice.retrieval.trace import build_trace, log_trace


class RetrievalScopeUnavailable(LatticeError):
    """The scope names an account the reader may not see -- or one that does not
    exist. The two are refused identically, so a scope cannot confirm an account."""

    def __init__(self) -> None:
        super().__init__(
            ErrorCode.VALIDATION_FAILED,
            details=[ErrorDetail(field="scope.account", issue="unknown_account")],
        )


class RetrievalQueryEmpty(LatticeError):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.VALIDATION_FAILED,
            details=[ErrorDetail(field="text", issue="empty_after_normalization")],
        )


async def visible_accounts(session: AsyncSession, ctx: AuthorizationContext) -> list[AccountNames]:
    """The accounts this reader may know exist -- the only accounts a scope may name or
    a mention may resolve to (security-model §10)."""
    rows = await session.execute(
        select(ACCOUNTS.c.id, ACCOUNTS.c.key, ACCOUNTS.c.name, ACCOUNTS.c.aliases)
        .where(visible_accounts_predicate(ctx, ACCOUNTS))
        .order_by(ACCOUNTS.c.key)
    )
    return [AccountNames(r.id, str(r.key), r.name, tuple(r.aliases)) for r in rows]


def resolve_scope(scope: RetrievalScope, visible: list[AccountNames]) -> ResolvedScope:
    account_id = None
    if scope.account is not None:
        wanted = scope.account.casefold()  # account keys are case-insensitive (citext)
        match = [a for a in visible if a.key.casefold() == wanted]
        if not match:
            raise RetrievalScopeUnavailable()
        account_id = match[0].id
    kinds = tuple(sorted({kind for t in scope.source_types for kind in SOURCE_TYPE_KINDS[t]}))
    since = datetime.combine(scope.since, clock.min, UTC) if scope.since else None
    until = (
        datetime.combine(scope.until + timedelta(days=1), clock.min, UTC) if scope.until else None
    )
    return ResolvedScope(account_id=account_id, document_kinds=kinds, since=since, until=until)


async def _timed[T](timings: dict[str, float], name: str, work: Awaitable[T]) -> T:
    start = time.perf_counter()
    try:
        return await work
    finally:
        timings[name] = (time.perf_counter() - start) * 1000


async def retrieve_with_context(
    session: AsyncSession,
    ctx: AuthorizationContext,
    query: RetrievalQuery,
    provider: EmbeddingProvider | None,
    *,
    config: RetrievalConfig = DEFAULT_CONFIG,
    now: datetime | None = None,
    on_candidates: Callable[[dict[str, list[Candidate]]], None] | None = None,
) -> RetrievalResult:
    """Retrieve inside an open snapshot bound to ``ctx.organization_id``, where ``ctx``
    was resolved. ``on_candidates`` (tests and evaluation) observes each channel's
    candidates exactly as the database returned them."""
    started = time.perf_counter()
    timings: dict[str, float] = {}
    request_id = uuid.uuid4()
    now = now or datetime.now(UTC)

    text = normalize(query.text)
    if not text:
        raise RetrievalQueryEmpty()
    identifiers = extract_identifiers(text, config.max_identifiers)
    temporal_intent = has_temporal_intent(text)
    visible = await _timed(timings, "accounts", visible_accounts(session, ctx))
    scope = resolve_scope(query.scope, visible)
    mentions = mentioned_accounts(text, visible)

    org_egress, sensitivity = await question_policy(session, ctx, query.classification)
    block = await query_egress_block(session, ctx, provider, sensitivity, org_egress)
    embedding: asyncio.Task[list[float] | SemanticOutcome] | None = None
    if block is None and provider is not None:
        embedding = asyncio.create_task(
            _timed(timings, "embed_query", embed_query(provider, text, config))
        )
    semantic: list[Candidate] = []
    outcome: SemanticOutcome
    terms = QuestionTerms(
        text=text,
        excluded=" ".join(
            (QUESTION_FRAME_WORDS, *(label for a in mentions for label in a.labels()))
        ),
        text_search_config=config.text_search_config,
    )
    try:
        weights = None
        if config.lexical_weighted:
            statistics = await _timed(
                timings,
                "question_statistics",
                question_statistics(
                    session,
                    ctx,
                    text,
                    terms.excluded,
                    config.text_search_config,
                    [a.id for a in mentions],
                ),
            )
            weights = lexical_weights(statistics, config)
        exact = await _timed(
            timings, "exact", exact_candidates(session, ctx, scope, identifiers, config)
        )
        lexical = await _timed(
            timings,
            "lexical",
            lexical_candidates(
                session,
                ctx,
                scope,
                text,
                config,
                identifiers=identifiers,
                mentioned_account_ids=[a.id for a in mentions],
                weights=weights,
            ),
        )
        if embedding is None or provider is None:
            assert block is not None  # noqa: S101 - no task only when blocked
            outcome = block
        else:
            vector = await embedding
            if isinstance(vector, SemanticOutcome):
                outcome = vector
            else:
                semantic = await _timed(
                    timings,
                    "semantic",
                    semantic_candidates(session, ctx, scope, vector, provider.model, config),
                )
                outcome = SemanticOutcome(
                    status="matched" if semantic else "no_matches",
                    provider=provider.provider,
                    model=provider.model,
                    deployment=provider.deployment,
                )
    finally:
        if embedding is not None and not embedding.done():
            embedding.cancel()

    stages = {"exact": exact, "lexical": lexical, "semantic": semantic}
    if on_candidates is not None:
        on_candidates({name: list(candidates) for name, candidates in stages.items()})

    # E4 over everything the database returned, before any of it is used.
    await _timed(
        timings,
        "tripwire",
        assert_all_readable(session, ctx, {c.document_id for c in exact + lexical + semantic}),
    )

    fusion_started = time.perf_counter()
    fused = fuse((exact, lexical, semantic), k=config.rrf_k, pool=config.fused_pool)
    account_targets = {a.id for a in mentions} | (
        {scope.account_id} if scope.account_id is not None else set()
    )
    ranked = rerank(
        fused,
        account_targets=account_targets,
        temporal_intent=temporal_intent,
        now=now,
        config=config,
    )
    selected = select_evidence(
        ranked, limit=query.limit, per_document=config.max_chunks_per_document
    )
    timings["fuse_rerank"] = (time.perf_counter() - fusion_started) * 1000

    evidence, question_terms = await _timed(
        timings, "hydrate", hydrate(session, ctx, selected, terms)
    )
    # E4 again at the boundary: nothing leaves retrieval without passing it.
    await assert_all_readable(session, ctx, {e.document_id for e in evidence})
    timings["total"] = (time.perf_counter() - started) * 1000

    trace = build_trace(
        request_id=request_id,
        ctx=ctx,
        config=config,
        semantic=outcome,
        question_sensitivity=sensitivity,
        stages=stages,
        fused=fused,
        ranked=ranked,
        final_count=len(evidence),
        timings_ms=timings,
    )
    log_trace(trace)
    return RetrievalResult(
        evidence=tuple(evidence),
        semantic=outcome,
        question_sensitivity=sensitivity,
        identifiers=identifiers,
        account_mentions=tuple(VisibleAccount(id=a.id, key=a.key, name=a.name) for a in mentions),
        temporal_intent=temporal_intent,
        question_terms=question_terms,
        trace=trace,
    )


async def retrieve(
    db: Database,
    *,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    query: RetrievalQuery,
    provider: EmbeddingProvider | None,
    config: RetrievalConfig = DEFAULT_CONFIG,
    now: datetime | None = None,
) -> RetrievalResult:
    """Both IDs must come from the server-side session (``Principal``)."""
    async with db.tenant_snapshot(organization_id) as session:
        ctx = await resolve_authorization_context(
            session, organization_id=organization_id, membership_id=membership_id
        )
        return await retrieve_with_context(session, ctx, query, provider, config=config, now=now)
