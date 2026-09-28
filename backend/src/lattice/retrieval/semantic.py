"""Semantic retrieval with pgvector (retrieval-design §5), and the query-side AI egress
policy (security-model §6).

**Query egress.** The question itself can carry confidential information, so embedding
it is an egress decision like embedding a document (``lattice.providers.egress``):

* its **effective sensitivity** (D-13) is the more restrictive of the organization's
  trusted default (``org_settings.default_query_sensitivity``) and the caller's
  declaration (``RetrievalQuery.classification``). A declaration can raise it, never
  lower it; nothing about the provider or its credential enters the computation;
* the provider must be **recorded** for this organization (``model_providers``,
  purpose ``embedding``) -- the record ingestion wrote when it embedded documents
  with it -- with a known trust class that is the one it actually runs as, a class the
  organization allows, and acceptable data terms (``provider_unapproved``,
  ``deployment_unknown``, ``provider_deployment_mismatch``, ``deployment_not_allowed``,
  ``provider_data_use``; D-15);
* the effective sensitivity must be at or below the provider's effective level:
  ``min(class ceiling, approval)`` -- the external level for ``external_api``, the
  organization-controlled level for ``organization_controlled``, the approval alone
  for ``local`` (``query_classification``).

Otherwise nothing is sent (``policy_excluded``). There is no fallback to another
provider: a question excluded from an external provider is never sent to it, while a
``local`` provider approved for its sensitivity may still embed it -- the question
never leaves the deployment. Exact and lexical retrieval run in every case.

**Embedding space.** A query vector is compared only with vectors of the same space:
same provider, same model (``chunks.embedding_model``), same dimensions. Vectors are
recorded with their model name, and a failed ``reindex --reembed`` keeps the previous
model's vectors in place, so several spaces can coexist; the statement selects the
provider's model explicitly. If the provider's dimensions differ from the stored
column, or another recorded provider uses the same model name (the space could not be
told apart), semantic retrieval is skipped (``incompatible_space``) -- vectors are
never compared merely because they have 1024 dimensions.

**Validity.** Only ``embedding_status = 'embedded' AND embedding IS NOT NULL`` rows of
that model participate: never failed, pending, policy-excluded or cleared vectors.
Ingestion guarantees a stored vector belongs to the chunk's current embedding input
(Phase 4); this statement still asks for valid rows only.

**Degradation.** Any failure here -- policy, provider outage, timeout, a malformed or
wrongly sized vector -- yields a ``SemanticOutcome`` and no semantic candidates; exact
and lexical retrieval carry on. The outcome distinguishes ``policy_excluded``,
``provider_failed`` and ``no_matches``. Codes only, never provider text.

The provider is called through ``EmbeddingProvider.embed(..., purpose="query")``; no
vendor detail is known here.
"""

import asyncio
import math
from collections.abc import Sequence

from sqlalchemy import Float, Select, Text, bindparam, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz import AuthorizationContext
from lattice.db.types import Vector
from lattice.ingestion.models import EMBEDDING_DIMENSIONS
from lattice.providers import records
from lattice.providers.egress import OrgEgress, may_send, provider_decision, question_sensitivity
from lattice.providers.embedding import EmbeddingError, EmbeddingProvider
from lattice.retrieval.models import (
    Candidate,
    QueryClassification,
    RetrievalConfig,
    SemanticOutcome,
)
from lattice.retrieval.permitted import (
    CHANNEL_OPTION,
    CHUNKS,
    ResolvedScope,
    candidate_columns,
    chunks_with_documents,
    permitted_chunk_conditions,
    stable_order,
    to_candidates,
)


def _outcome(
    provider: EmbeddingProvider | None, status: str, reason: str | None = None
) -> SemanticOutcome:
    return SemanticOutcome.model_validate(
        {
            "status": status,
            "reason": reason,
            "provider": None if provider is None else provider.provider,
            "model": None if provider is None else provider.model,
            "deployment": None if provider is None else provider.deployment,
        }
    )


async def question_policy(
    session: AsyncSession, ctx: AuthorizationContext, declared: QueryClassification | None
) -> tuple[OrgEgress, QueryClassification]:
    """The organization's egress settings and the question's effective sensitivity
    (D-13), read in the request's snapshot."""
    org = await records.org_egress(session, ctx.organization_id)
    return org, question_sensitivity(org.default_query_sensitivity, declared)


async def query_egress_block(
    session: AsyncSession,
    ctx: AuthorizationContext,
    provider: EmbeddingProvider | None,
    sensitivity: str,
    org: OrgEgress,
) -> SemanticOutcome | None:
    """Why the question may not be sent to ``provider`` -- or None if it may.
    ``sensitivity`` is the effective one (``question_policy``)."""
    if provider is None:
        return _outcome(None, "not_configured")
    if provider.dimensions != EMBEDDING_DIMENSIONS:
        return _outcome(provider, "incompatible_space", "dimensions")
    registered = await records.embedding_records(session, ctx.organization_id, provider.model)
    mine = [r for r in registered if r.provider == provider.provider]
    if not mine:
        return _outcome(provider, "policy_excluded", "provider_unapproved")
    if len(mine) != len(registered):
        return _outcome(provider, "incompatible_space", "model_ambiguous")
    decision = provider_decision(
        org,
        mine[0],
        deployment=provider.deployment,
        training_opt_out=provider.training_opt_out,
    )
    if decision.level is None:
        return _outcome(provider, "policy_excluded", decision.reason)
    if not may_send(sensitivity, decision.level):
        return _outcome(provider, "policy_excluded", "query_classification")
    return None


async def embed_query(
    provider: EmbeddingProvider, text: str, config: RetrievalConfig
) -> list[float] | SemanticOutcome:
    """The query vector, or the ``provider_failed`` outcome explaining its absence."""
    try:
        vectors = await asyncio.wait_for(
            provider.embed([text], "query"), timeout=config.query_embed_timeout_seconds
        )
    except TimeoutError:
        return _outcome(provider, "provider_failed", "embedding_failed:timeout")
    except EmbeddingError as exc:
        return _outcome(provider, "provider_failed", exc.code)
    if not isinstance(vectors, list) or len(vectors) != 1 or not isinstance(vectors[0], list):
        return _outcome(provider, "provider_failed", "embedding_failed:malformed_response")
    vector = vectors[0]
    if len(vector) != EMBEDDING_DIMENSIONS:
        return _outcome(provider, "provider_failed", "embedding_failed:wrong_dimensions")
    if not all(isinstance(v, float | int) and math.isfinite(v) for v in vector) or not any(vector):
        return _outcome(provider, "provider_failed", "embedding_failed:malformed_response")
    return [float(v) for v in vector]


def _vector_text(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def semantic_statement(
    ctx: AuthorizationContext,
    scope: ResolvedScope,
    vector: Sequence[float],
    model: str,
    config: RetrievalConfig,
) -> Select[tuple[object, ...]]:
    query_vector = cast(
        bindparam("query_vector", _vector_text(vector), type_=Text), Vector(EMBEDDING_DIMENSIONS)
    )
    distance = CHUNKS.c.embedding.op("<=>", return_type=Float)(query_vector)  # cosine
    return (
        select(*candidate_columns(), (1.0 - distance).label("score"))
        .select_from(chunks_with_documents(ctx))
        .where(
            *permitted_chunk_conditions(ctx, scope),
            # Valid vectors of this embedding space only.
            CHUNKS.c.embedding_status == "embedded",
            CHUNKS.c.embedding.is_not(None),
            CHUNKS.c.embedding_model == model,
        )
        .order_by(distance, *stable_order())
        .limit(config.k_semantic)
        .execution_options(**{CHANNEL_OPTION: "semantic"})
    )


async def semantic_candidates(
    session: AsyncSession,
    ctx: AuthorizationContext,
    scope: ResolvedScope,
    vector: Sequence[float],
    model: str,
    config: RetrievalConfig,
) -> list[Candidate]:
    rows = (await session.execute(semantic_statement(ctx, scope, vector, model, config))).all()
    return to_candidates(rows, "semantic")
