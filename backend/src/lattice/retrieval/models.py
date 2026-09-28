"""Typed retrieval contracts: the query, the limits, candidates, evidence and the trace.

Only what the current product needs. Everything a client can send lives in
``RetrievalQuery``, and none of it is authority: there is no organization, membership,
role, clearance, department, group, grant or account assignment field (``extra``
fields are rejected). The organization and the reader come from the server-side
session; every access fact comes from the database. Scope filters only narrow.
"""

import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Channel = Literal["exact", "lexical", "semantic"]
CHANNELS: Final[tuple[Channel, ...]] = ("exact", "lexical", "semantic")

# The product's source types; each maps to the document kinds it stores.
SourceType = Literal["doc", "ticket", "chat"]
SOURCE_TYPE_KINDS: Final[dict[SourceType, tuple[str, ...]]] = {
    "doc": ("doc",),
    "ticket": ("ticket",),
    "chat": ("chat_thread", "chat_channel_day"),
}
KIND_SOURCE_TYPE: Final[dict[str, SourceType]] = {
    kind: source_type for source_type, kinds in SOURCE_TYPE_KINDS.items() for kind in kinds
}

# How sensitive the *question text* is, for the AI egress policy on query embedding
# (security-model §6). Declared by the caller; it can only make egress stricter.
QueryClassification = Literal["internal", "confidential", "restricted"]

SemanticStatus = Literal[
    "matched",  # the query was embedded and compatible vectors matched
    "no_matches",  # embedded, but no permitted chunk has a compatible vector
    "policy_excluded",  # the egress policy forbids sending this query to the provider
    "provider_failed",  # outage, timeout, rejected or malformed response
    "not_configured",  # no embedding provider configured for retrieval
    "incompatible_space",  # the provider's vectors are not comparable with stored ones
]

MAX_QUERY_CHARS: Final = 1000
DEFAULT_LIMIT: Final = 10
MAX_LIMIT: Final = 20

_TEXT_SEARCH_CONFIGS: Final = frozenset({"english", "simple"})


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    """Every tunable of a retrieval request, versioned and recorded in the trace.

    Limits bound the work per request: no stage reads more than its ``k_*`` rows into
    Python, and the evidence list is at most ``MAX_LIMIT`` chunks. Weights are the
    rerank_v1 features of retrieval-design §6.
    """

    version: str = "retrieval-6"
    rerank_version: str = "rerank_v1"
    max_identifiers: int = 5
    k_exact: int = 50
    k_lexical: int = 50
    k_semantic: int = 50
    fused_pool: int = 40
    max_chunks_per_document: int = 3
    rrf_k: int = 60
    text_search_config: str = "english"
    # all_terms: websearch_to_tsquery (every term must match; the Phase 0 design).
    # any_term: any of the question's lexemes may match; ranked by ts_rank_cd.
    # coverage: any lexeme may match; ranked by how many distinct lexemes of the
    #   question the chunk contains, then by ts_rank_cd.
    lexical_mode: Literal["all_terms", "any_term", "coverage"] = "coverage"
    # An identifier in the question also scores as a phrase (+1) in lexical ranking.
    lexical_identifier_phrases: bool = True
    # A visible account named in the question counts as a matched term (+1) for
    # documents of that account (D-10: query time, visible accounts only).
    lexical_account_terms: bool = True
    query_embed_timeout_seconds: float = 5.0
    weight_account_match: float = 0.15
    weight_authority: float = 0.10
    weight_recency: float = 0.20
    weight_identifier: float = 0.50
    recency_scale_days: float = 60.0

    def __post_init__(self) -> None:
        # Rendered into SQL as a constant: never anything but a known configuration.
        if self.text_search_config not in _TEXT_SEARCH_CONFIGS:
            raise ValueError("unknown text search configuration")
        for name in ("k_exact", "k_lexical", "k_semantic", "fused_pool", "rrf_k"):
            if not 1 <= getattr(self, name) <= 200:
                raise ValueError(f"{name} out of range")
        if not 1 <= self.max_chunks_per_document <= MAX_LIMIT:
            raise ValueError("max_chunks_per_document out of range")

    def limits(self) -> dict[str, int]:
        return {
            "max_query_chars": MAX_QUERY_CHARS,
            "max_identifiers": self.max_identifiers,
            "k_exact": self.k_exact,
            "k_lexical": self.k_lexical,
            "k_semantic": self.k_semantic,
            "fused_pool": self.fused_pool,
            "max_chunks_per_document": self.max_chunks_per_document,
            "max_limit": MAX_LIMIT,
        }


DEFAULT_CONFIG: Final = RetrievalConfig()


class RetrievalScope(BaseModel):
    """Optional filters. Each is ANDed with the authorization predicate: it can narrow
    what the reader may see, never widen it. ``account`` must name an account the
    reader may know exists (``authz.visible_accounts_predicate``); a hidden or unknown
    account is refused identically."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account: str | None = Field(default=None, min_length=1, max_length=128)
    source_types: tuple[SourceType, ...] = ()
    since: date | None = None
    until: date | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.since is not None and self.until is not None and self.since > self.until:
            raise ValueError("since must not be after until")
        return self


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    scope: RetrievalScope = Field(default_factory=RetrievalScope)
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    classification: QueryClassification = "internal"


# --- Inside the pipeline --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """One chunk a retriever's SQL statement returned, as it came out of the database.

    Every row was selected inside the authorization predicate. The account is the
    *visible* one only (a LEFT JOIN through ``visible_accounts_predicate``): for a
    document readable through a grant on an out-of-scope account it is ``None``, so the
    hidden account never reaches application code (D-8, D-10). No access columns.
    """

    organization_id: uuid.UUID
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    channel: Channel
    rank: int  # 1-based, within the channel
    score: float  # the channel's own score: 1.0 exact, ts_rank_cd, cosine similarity
    document_kind: str
    authority: str
    occurred_at: datetime | None
    content_hash: bytes
    visible_account_id: uuid.UUID | None
    # Tie-break stable across databases (IDs are not): (external_id, ordinal).
    sort_key: tuple[str, int]

    @property
    def key(self) -> tuple[uuid.UUID, uuid.UUID]:
        """Resource identity: the organization is part of it."""
        return (self.organization_id, self.chunk_id)


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    candidate: Candidate  # metadata (identical across channels for one chunk)
    ranks: Mapping[Channel, int]
    scores: Mapping[Channel, float]
    rrf_score: float
    fused_rank: int

    @property
    def matched_by(self) -> tuple[Channel, ...]:
        return tuple(c for c in CHANNELS if c in self.ranks)


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    fused: FusedCandidate
    features: Mapping[str, float]
    final_score: float
    final_rank: int


# --- Output ------------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class VisibleAccount(_Frozen):
    """An account the reader may know exists (``authz.visible_accounts_predicate``)."""

    id: uuid.UUID
    key: str
    name: str


class Evidence(_Frozen):
    """One permitted chunk, with what Phase 6 needs to cite, date and relate it.

    Only information the reader may receive: the document is readable (SQL predicate,
    then the E4 tripwire); ``account`` is present only when the account is visible
    (D-8), never substituted; supersession links name only *readable* documents. No
    access attributes, grants, groups, departments or other tenants' identifiers.
    """

    rank: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    source_type: SourceType
    document_kind: str
    source_name: str
    title: str
    display_ref: str | None
    uri: str | None
    heading_path: tuple[str, ...]
    source_anchors: tuple[str, ...]
    ordinal: int
    text: str
    char_start: int
    char_end: int
    author: str | None
    occurred_at: datetime | None
    document_created_at: datetime | None
    document_updated_at: datetime
    version: int
    authority: str
    account: VisibleAccount | None
    # Explicit supersession (deterministic source metadata), readable documents only.
    supersedes_document_id: uuid.UUID | None
    superseded_by_document_ids: tuple[uuid.UUID, ...]
    matched_by: tuple[Channel, ...]
    channel_ranks: dict[Channel, int]
    rrf_score: float
    features: dict[str, float]
    final_score: float


class SemanticOutcome(_Frozen):
    status: SemanticStatus
    # A fixed code (``query_classification``, ``provider_unapproved``,
    # ``embedding_failed:timeout``, ...). Never provider text.
    reason: str | None = None
    provider: str | None = None
    model: str | None = None

    @property
    def degraded(self) -> bool:
        """Semantic retrieval could not contribute for a reason other than having
        found nothing."""
        return self.status not in ("matched", "no_matches")


class TraceCandidate(_Frozen):
    chunk_id: uuid.UUID
    rank: int
    score: float

    @classmethod
    def of(cls, chunk_id: uuid.UUID, rank: int, score: float) -> "TraceCandidate":
        return cls(
            chunk_id=chunk_id, rank=rank, score=round(score, 6) if math.isfinite(score) else 0.0
        )


class TraceRanked(_Frozen):
    chunk_id: uuid.UUID
    rank: int
    matched_by: tuple[Channel, ...]
    rrf_score: float
    features: dict[str, float]
    final_score: float


class RetrievalTrace(_Frozen):
    """What happened, in identifiers and measurements (data-model §7 ``retrieval``).

    No question text, no document text or titles, no account or source names. Every
    chunk ID in it is one the reader may read (candidates are selected inside the
    predicate), and every count is a count of permitted rows: nothing here depends on
    content the reader cannot see.
    """

    request_id: uuid.UUID
    organization_id: uuid.UUID
    membership_id: uuid.UUID
    policy_version: str
    config_version: str
    rerank_version: str
    rrf_k: int
    limits: dict[str, int]
    embedding_provider: str | None
    embedding_model: str | None
    semantic_status: SemanticStatus
    semantic_reason: str | None
    degraded: bool
    counts: dict[str, int]
    timings_ms: dict[str, float]
    stages: dict[str, tuple[TraceCandidate, ...]]
    reranked: tuple[TraceRanked, ...]


class RetrievalResult(_Frozen):
    evidence: tuple[Evidence, ...]
    semantic: SemanticOutcome
    identifiers: tuple[str, ...]
    # Accounts named in the question, among the accounts the reader may see only.
    account_mentions: tuple[VisibleAccount, ...]
    temporal_intent: bool
    trace: RetrievalTrace
