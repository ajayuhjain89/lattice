"""Semantic retrieval with pgvector (retrieval-design §5), and the query-side AI egress
policy (security-model §6).

**Query egress.** The question itself can carry confidential information, so embedding
it is an egress decision like embedding a document. Before any provider call:

* the provider must be recorded for this organization in ``model_providers``
  (purpose ``embedding``) -- the record ingestion wrote when it embedded documents with
  it. An unrecorded provider was never approved to receive anything: nothing is sent
  (``policy_excluded`` / ``provider_unapproved``);
* the effective level is ``min(org_settings.external_egress_level, the provider's
  approved_for_level)`` (``lattice.providers.egress``), and the query's declared
  classification (``RetrievalQuery.classification``, default ``internal``) must be at
  or below it. Otherwise nothing is sent (``policy_excluded`` /
  ``query_classification``).

The declaration comes from the caller and can only make egress stricter: it never
widens what is sent or what may be read.

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
from lattice.orgs.models import OrgSettings
from lattice.providers.egress import DEFAULT_LEVEL, effective_level, may_send
from lattice.providers.embedding import EmbeddingError, EmbeddingProvider
from lattice.providers.models import ModelProvider
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
        }
    )


async def query_egress_block(
    session: AsyncSession,
    ctx: AuthorizationContext,
    provider: EmbeddingProvider | None,
    classification: QueryClassification,
) -> SemanticOutcome | None:
    """Why the query may not be sent to ``provider`` -- or None if it may. Read in the
    request's snapshot, so the decision and the search see the same settings."""
    if provider is None:
        return _outcome(None, "not_configured")
    if provider.dimensions != EMBEDDING_DIMENSIONS:
        return _outcome(provider, "incompatible_space", "dimensions")
    registered = (
        await session.execute(
            select(ModelProvider.provider, ModelProvider.approved_for_level).where(
                ModelProvider.organization_id == ctx.organization_id,
                ModelProvider.purpose == "embedding",
                ModelProvider.model == provider.model,
            )
        )
    ).all()
    approved = [level for name, level in registered if name == provider.provider]
    if not approved:
        return _outcome(provider, "policy_excluded", "provider_unapproved")
    if any(name != provider.provider for name, _ in registered):
        return _outcome(provider, "incompatible_space", "model_ambiguous")
    org_level = (
        await session.execute(
            select(OrgSettings.external_egress_level).where(
                OrgSettings.organization_id == ctx.organization_id
            )
        )
    ).scalar_one_or_none() or DEFAULT_LEVEL
    if not may_send(classification, effective_level(org_level, approved[0])):
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
