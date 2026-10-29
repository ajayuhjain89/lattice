"""Retrieval evaluation over the golden set (docs/evaluation-plan.md §5, §7).

    lattice eval retrieval [--embedding fake|openai|none] [--category C] [--persona P]
                           [--no-rerank] [--compare BASELINE]

Every golden item's question is retrieved the way the product retrieves: as the
runtime role (``lattice_app``) through row-level security, with the persona's
``AuthorizationContext`` resolved from the database inside the request's snapshot.
Retrieval is given the question and the item's scope -- nothing about who should see
what, and nothing about the expected answer.

Ground truth never comes from the code under test:

* every chunk ID is mapped back to its dataset record (``doc:<id>``, ``ticket:<id>``,
  ``chat:<channel>:<thread>``) and part anchors by the operator role reading the
  corpus table by table, independently of retrieval;
* whether a persona may read a record is decided by the dataset's expected-access
  oracle (``lattice.dataset.access``: the reference policy over the raw fixture
  metadata, Phase 3) -- not by the database.

Metrics:

* **Recall@k** (k = 1, 5, 10, 20): per item, the fraction of its target evidence found
  among the first k evidence chunks, averaged over items that have targets (macro).
  Targets are the union of ``relevant`` and ``not_used_for_answer`` over the item's egress
  configurations: which documents retrieval should find does not depend on the AI
  egress setting (that decides what may reach a model, Phase 6). A part key
  (``doc:x#3``, ``ticket:T#c3``, ``chat:C:ts#msg``) is found by a chunk covering that
  part; a record key by any chunk of the record. A record key is dropped when the item
  also names one of its parts (the part is the stricter target). Unreadable or
  inaccessible evidence can never count: only permitted evidence is ever returned.
* **MRR**: reciprocal rank of the first target found (within the returned list).
* **Document recall@k** (P13.1): the fraction of the item's target *records* with any
  chunk among the first k (a part target counts for its record).
* **nDCG@10** (P13.1, informational): gain 2 for the first chunk satisfying each target,
  1 for the first chunk of each ``acceptable`` key, 0 otherwise. Unlabelled is not
  irrelevant, so it is a lower bound and gates nothing.
* **Exact-identifier hit** (P13.1): items whose question names the display reference of
  one of their own target records (matched by the operator corpus read, not by the
  retrieval code's extractor); a hit is that record at evidence rank 1.
* **Precision@10** (P13.2): only over items with ``exhaustive`` labels (phase-13-plan
  EC-3; the lint checks each one's candidate review): the share of the first 10 evidence
  chunks that satisfy a target or an ``acceptable`` key, with the item's ideal (the
  labelled parts it could reach, at most 10) beside it. Never averaged with other items;
  every other item has no precision.
* **Splits and provenance** (P13.2): every block is also reported per split (``dev``,
  ``held_out``; EC-7) and per label source (EC-8). Held-out results are reported, never
  tuned against.
* **Stage latency** (P13.1): the retrieval trace's own stage timings, p50/p95/max. They
  name the machine they ran on; the M1 is not a production benchmark.
* **Security** (each must be 0, never averaged): chunks the oracle says the persona
  cannot read, in *any* stage (the exact, lexical and semantic candidate lists as the
  database returned them, the fused pool, the evidence); another organization's
  chunks; ``must_not_retrieve`` hits; evidence showing a structured account the oracle
  says must be hidden (D-8); account mentions of accounts the persona may not see;
  canary strings of unreadable records in evidence text, and ``must_not_contain``
  literals in evidence from a record the persona may not read.
* **Informational** (not a retrieval violation): a ``must_not_contain`` literal found in
  a record the persona *may* read -- the injection payloads planted in the
  prompt-injection tickets the questions are about. Retrieval must return them as
  plain data; the answer must never contain them (Phase 6).

For each target missed in the top 10, the report says where it was lost: never
returned by any channel, returned but outside the fused pool, or fused but ranked
below 10 (including selection: per-document cap, duplicate text).

Every report also records what makes two runs comparable (``lattice eval compare``,
``lattice.evals.baseline``): the corpus fingerprint, the golden-set version, the
retrieval configuration and the database server's architecture -- semantic ranking
depends on it (pgvector's float32 distances differ in the last bits between aarch64
and x86-64, and exactly tied fake vectors then order differently; evaluation-plan §15).

``--no-rerank`` is the rerank ablation: every rerank_v1 weight 0, so evidence follows
the RRF order. ``--set NAME=VALUE`` changes one ``RetrievalConfig`` tunable for the run
(the experiments of evaluation-plan §18+ are reproduced this way). Both are evaluation
configurations with their own version string, never the product's.
"""

import hashlib
import json
import math
import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from typing import Any

from sqlalchemy import text

from lattice.authz import AuthorizationInvariantViolation, resolve_authorization_context
from lattice.dataset.lint import Dataset
from lattice.dataset.manifests import AS_OF, CANARY, KEY_REF, GoldenItem
from lattice.db.engine import Database
from lattice.errors import LatticeError
from lattice.evals.stats import distribution
from lattice.ingestion.chunking import CHUNKER_VERSION
from lattice.ingestion.embedding_input import EMBEDDING_INPUT_VERSION
from lattice.providers.embedding import (
    EmbeddingError,
    EmbeddingProvider,
    Purpose,
    RecordingEmbeddingProvider,
)
from lattice.retrieval import (
    DEFAULT_CONFIG,
    RetrievalConfig,
    RetrievalQuery,
    RetrievalResult,
    RetrievalScope,
    retrieve_with_context,
)
from lattice.retrieval.models import CHANNELS, Candidate
from lattice.retrieval.query import normalize
from lattice.retrieval.semantic import query_egress_block, question_policy

PREFIX = {"document_repository": "doc", "ticketing": "ticket", "chat_export": "chat"}
KS = (1, 5, 10, 20)
EVAL_LIMIT = 20
PRIMARY_K = 10


@dataclass(frozen=True)
class ChunkRef:
    organization: str
    record: str
    anchors: tuple[str, ...]


@dataclass(frozen=True, order=True)
class Target:
    organization: str
    record: str
    part: str | None

    def label(self, home: str) -> str:
        prefix = "" if self.organization == home else f"{self.organization}/"
        return f"{prefix}{self.record}" + (f"#{self.part}" if self.part else "")

    def satisfied_by(self, ref: ChunkRef) -> bool:
        return (
            ref.organization == self.organization
            and ref.record == self.record
            and (self.part is None or self.part in ref.anchors)
        )


def parse_key(key: str, home: str) -> Target:
    match = KEY_REF.fullmatch(key)
    if match is None:
        raise ValueError(f"malformed record key {key!r}")
    return Target(match["org"] or home, match["ref"], match["part"])


def targets(item: GoldenItem) -> list[Target]:
    parsed = {
        parse_key(key, item.organization)
        for _, expected in item.expectations()
        for key in (*expected.relevant, *expected.not_used_for_answer)
    }
    with_parts = {(t.organization, t.record) for t in parsed if t.part is not None}
    return sorted(
        t for t in parsed if t.part is not None or (t.organization, t.record) not in with_parts
    )


def acceptable(item: GoldenItem) -> list[Target]:
    return sorted(
        {
            parse_key(key, item.organization)
            for _, expected in item.expectations()
            for key in expected.acceptable
        }
    )


def forbidden(item: GoldenItem) -> list[Target]:
    return sorted(
        {
            parse_key(key, item.organization)
            for _, expected in item.expectations()
            for key in expected.must_not_retrieve
        }
    )


# --- query embeddings, prefetched under the same egress policy ----------------------


@dataclass
class PrefetchedQueries:
    """Evaluation-only wrapper: the golden questions' query vectors are requested up
    front, in the provider's own batch size, so a quota-limited external provider sees
    a handful of requests instead of one per item. Retrieval still calls ``embed(...,
    "query")`` exactly as in the product and gets the identical vector (same normalized
    text, same purpose); anything not prefetched goes to the provider.

    A question is prefetched only if the product's own egress decision
    (``question_policy`` + ``query_egress_block``, as that organization, with no
    declaration -- how the harness retrieves) would send it. Prefetching never sends
    a question retrieval would not."""

    inner: EmbeddingProvider
    cache: dict[str, list[float]] = field(default_factory=dict)

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def dimensions(self) -> int:
        return self.inner.dimensions

    @property
    def deployment(self) -> Any:
        return self.inner.deployment

    @property
    def retention_mode(self) -> str:
        return self.inner.retention_mode

    @property
    def training_opt_out(self) -> bool:
        return self.inner.training_opt_out

    @property
    def max_batch(self) -> int:
        return self.inner.max_batch

    async def prefetch(
        self, app: Database, identities: "Identities", items: Sequence[GoldenItem]
    ) -> None:
        by_org: dict[str, set[str]] = defaultdict(set)
        for item in items:
            by_org[item.organization].add(normalize(item.question))
        for slug, questions in sorted(by_org.items()):
            org_id = identities.org_ids[slug]
            async with app.tenant_snapshot(org_id) as session:
                member = next(
                    (m for (o, _), m in identities.memberships.items() if o == slug), None
                )
                if member is None:
                    continue
                ctx = await resolve_authorization_context(
                    session, organization_id=org_id, membership_id=member
                )
                org, sensitivity = await question_policy(session, ctx, None)
                if await query_egress_block(session, ctx, self.inner, sensitivity, org):
                    continue
            pending = sorted(q for q in questions if q and q not in self.cache)
            size = max(1, self.inner.max_batch)
            for start in range(0, len(pending), size):
                batch = pending[start : start + size]
                try:
                    vectors = await self.inner.embed(batch, "query")
                except EmbeddingError:
                    return  # retrieval asks (and degrades) per item, as in the product
                self.cache.update(zip(batch, vectors, strict=True))

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        if purpose == "query" and texts and all(t in self.cache for t in texts):
            return [self.cache[t] for t in texts]
        return await self.inner.embed(texts, purpose)


# --- identities and the corpus map (operator reads, independent of retrieval) --------


@dataclass
class Identities:
    org_ids: dict[str, uuid.UUID]
    memberships: dict[tuple[str, str], uuid.UUID]  # (organization, persona) -> membership


async def load_identities(owner: Database, dataset: Dataset) -> Identities:
    org_ids: dict[str, uuid.UUID] = {}
    memberships: dict[tuple[str, str], uuid.UUID] = {}
    for slug, world in dataset.worlds.items():
        async with owner.unscoped_transaction() as s:
            org_id = (
                await s.execute(text("SELECT id FROM organizations WHERE slug = :s"), {"s": slug})
            ).scalar_one_or_none()
        if org_id is None:
            raise LookupError(f"organization {slug} is not seeded")
        org_ids[slug] = org_id
        async with owner.tenant_transaction(org_id) as s:
            by_email = dict(
                (
                    await s.execute(
                        text(
                            "SELECT u.email, m.id FROM memberships m "
                            "JOIN users u ON u.id = m.user_id"
                        )
                    )
                ).all()
            )
        for persona, email in world.personas.items():
            if email in by_email:
                memberships[(slug, persona)] = by_email[email]
    return Identities(org_ids, memberships)


@dataclass
class Corpus:
    chunks: dict[uuid.UUID, ChunkRef]
    embeddings: dict[str, dict[str, int]]  # organization -> "status/model" -> chunks
    # (organization, record) -> the record's display reference, where it has one.
    display_refs: dict[tuple[str, str], str] = field(default_factory=dict)
    # What the corpus is, independent of its random IDs (lattice.evals.baseline).
    fingerprint: dict[str, Any] = field(default_factory=dict)


async def load_corpus(owner: Database, org_ids: dict[str, uuid.UUID]) -> Corpus:
    chunks: dict[uuid.UUID, ChunkRef] = {}
    embeddings: dict[str, dict[str, int]] = {}
    display_refs: dict[tuple[str, str], str] = {}
    digest = hashlib.sha256(f"{CHUNKER_VERSION}|{EMBEDDING_INPUT_VERSION}".encode())
    counts_by_org: dict[str, int] = {}
    for slug, org_id in sorted(org_ids.items()):
        async with owner.tenant_transaction(org_id) as s:
            rows = await s.execute(
                text(
                    "SELECT c.id, s.connector_type, d.external_id, c.source_anchors, "
                    "d.display_ref, c.ordinal, encode(c.content_hash, 'hex'), "
                    "encode(c.embedding_input_hash, 'hex'), md5(c.tsv::text), "
                    "coalesce(md5(c.embedding::text), '-'), c.embedding_status, "
                    "coalesce(c.embedding_model, '-') "
                    "FROM chunks c JOIN documents d ON d.id = c.document_id "
                    "JOIN sources s ON s.id = d.source_id"
                )
            )
            lines = []
            for chunk_id, connector, external_id, anchors, display_ref, *rest in rows:
                record = f"{PREFIX[connector]}:{external_id}"
                chunks[chunk_id] = ChunkRef(slug, record, tuple(anchors))
                if display_ref:
                    display_refs[(slug, record)] = display_ref
                lines.append("|".join((slug, record, *(str(v) for v in rest))))
            counts_by_org[slug] = len(lines)
            for line in sorted(lines):
                digest.update(line.encode() + b"\n")
            counts = await s.execute(
                text(
                    "SELECT embedding_status || '/' || coalesce(embedding_model, '-'), count(*) "
                    "FROM chunks GROUP BY 1 ORDER BY 1"
                )
            )
            embeddings[slug] = {str(k): int(v) for k, v in counts}
    fingerprint = {
        "sha256": digest.hexdigest(),
        "chunks": counts_by_org,
        "chunker": CHUNKER_VERSION,
        "embedding_input": EMBEDDING_INPUT_VERSION,
    }
    return Corpus(chunks, embeddings, display_refs, fingerprint)


_LATE_FIELDS = ("cohort",)  # phase-13-followups F2


def golden_version(items: Sequence[GoldenItem]) -> dict[str, Any]:
    """The golden items' content (labels included), independent of file layout. Fields
    added after the first baselines (``_LATE_FIELDS``) count only once set, so adding
    them does not move the fingerprint of the items that leave them unset."""
    digest = hashlib.sha256()
    for item in sorted(items, key=lambda i: i.id):
        unset = {name for name in _LATE_FIELDS if getattr(item, name) is None}
        digest.update(item.model_dump_json(exclude=unset).encode() + b"\n")
    return {"sha256": digest.hexdigest(), "items": len(items)}


async def database_platform(db: Database) -> str:
    """The database server's architecture (``aarch64``, ``x86_64``): semantic ranking
    depends on it, exact and lexical ranking do not (evaluation-plan §15)."""
    async with db.unscoped_transaction() as s:
        version = str((await s.execute(text("SELECT version()"))).scalar_one())
    match = re.search(r" on ([A-Za-z0-9_]+)-", version)
    return match.group(1) if match else "unknown"


def rerank_ablation(config: RetrievalConfig = DEFAULT_CONFIG) -> RetrievalConfig:
    """Evaluation only: every rerank_v1 weight 0, so the final order is the RRF order
    (``rerank`` sorts by score, then RRF score, then fused rank)."""
    return replace(
        config,
        version=f"{config.version}+rrf-only",
        rerank_version="none",
        weight_account_match=0.0,
        weight_authority=0.0,
        weight_recency=0.0,
        weight_identifier=0.0,
    )


def config_overrides(
    pairs: Sequence[str], config: RetrievalConfig = DEFAULT_CONFIG
) -> RetrievalConfig:
    """Evaluation only: ``NAME=VALUE`` pairs (VALUE as JSON, else a string) applied to
    ``config``; the version names them, so the run never passes for the product's."""
    if not pairs:
        return config
    tunable = {f.name for f in fields(RetrievalConfig)} - {"version", "rerank_version"}
    changes: dict[str, Any] = {}
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        if not sep or name not in tunable:
            raise ValueError(f"not a retrieval tunable: {pair!r}")
        try:
            changes[name] = json.loads(raw)
        except json.JSONDecodeError:
            changes[name] = raw
    return replace(config, version=f"{config.version}+{','.join(sorted(pairs))}", **changes)


# --- one item -------------------------------------------------------------------------


@dataclass
class Miss:
    target: str
    channels: list[str]  # channels whose candidates contained it
    fused_rank: int | None
    reranked_rank: int | None
    evidence_rank: int | None
    cause: str


@dataclass
class ItemResult:
    id: str
    category: str
    persona: str
    organization: str
    question: str
    same_question_group: str | None
    tags: list[str]
    split: str | None = None
    label_source: str | None = None
    cohort: str | None = None
    error: str | None = None
    semantic_status: str | None = None
    semantic_reason: str | None = None
    targets: list[str] = field(default_factory=list)
    found_at: dict[str, int | None] = field(default_factory=dict)
    recall: dict[str, float] = field(default_factory=dict)
    reciprocal_rank: float | None = None
    # Target records (a part target counts for its record) -> first evidence rank.
    documents_found_at: dict[str, int | None] = field(default_factory=dict)
    document_recall: dict[str, float] = field(default_factory=dict)
    ndcg10: float | None = None
    # Target records whose display reference the question names; None when there are none.
    identifier_records: list[str] = field(default_factory=list)
    identifier_hit: bool | None = None
    # Exhaustive items only: hits in the first 10 / 10, and the best reachable value.
    precision10: float | None = None
    precision10_ideal: float | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    hit_channels: dict[str, list[str]] = field(default_factory=dict)
    violations: list[dict[str, str]] = field(default_factory=list)
    # Not violations: e.g. an injection payload inside a readable record's own text.
    informational: list[dict[str, str]] = field(default_factory=list)
    misses: list[Miss] = field(default_factory=list)


def _check_stage(
    stage: str,
    chunk_ids: Iterable[uuid.UUID],
    *,
    item: GoldenItem,
    dataset: Dataset,
    corpus: Corpus,
    banned: Sequence[Target],
    out: list[dict[str, str]],
) -> None:
    world = dataset.worlds[item.organization]
    for chunk_id in chunk_ids:
        ref = corpus.chunks.get(chunk_id)
        if ref is None:
            out.append({"kind": "unknown_chunk", "stage": stage, "record": str(chunk_id)})
            continue
        if ref.organization != item.organization:
            record_label = f"{ref.organization}/{ref.record}"
            out.append({"kind": "cross_org", "stage": stage, "record": record_label})
            continue
        record = world.raw.records.get(ref.record)
        if record is None or not world.can_read(item.persona, record):
            out.append({"kind": "unauthorized", "stage": stage, "record": ref.record})
        # A forbidden key forbids its whole record, whatever part it names.
        if any(t.organization == ref.organization and t.record == ref.record for t in banned):
            out.append({"kind": "must_not_retrieve", "stage": stage, "record": ref.record})


def _check_evidence_metadata(
    result: RetrievalResult,
    *,
    item: GoldenItem,
    dataset: Dataset,
    corpus: Corpus,
    out: list[dict[str, str]],
    info: list[dict[str, str]],
) -> None:
    world = dataset.worlds[item.organization]
    hidden = {a for _, e in item.expectations() for a in e.hidden_accounts}
    for evidence in result.evidence:
        ref = corpus.chunks.get(evidence.chunk_id)
        record = world.raw.records.get(ref.record) if ref is not None else None
        shown = evidence.account.key if evidence.account is not None else None
        expected = world.shown_account(item.persona, record) if record is not None else None
        if shown is not None and shown != expected:
            label = ref.record if ref else "?"
            out.append({"kind": "account_metadata_leak", "stage": "evidence", "record": label})
        for found in CANARY.findall(f"{evidence.title}\n{evidence.text}"):
            canary = next((c for c in dataset.canaries if c.string == found), None)
            owner = dataset.worlds.get(canary.organization) if canary else None
            canary_record = (
                owner.raw.records.get(canary.record.split("#")[0]) if owner and canary else None
            )
            if (
                canary is None
                or canary.organization != item.organization
                or canary_record is None
                or not world.can_read(item.persona, canary_record)
            ):
                out.append({"kind": "canary", "stage": "evidence", "record": found})
        for literal in {s for _, e in item.expectations() for s in e.must_not_contain}:
            if CANARY.fullmatch(literal):
                continue  # canaries: checked above, with their records' readability
            if literal in evidence.text or literal in evidence.title:
                readable = record is not None and world.can_read(item.persona, record)
                # A literal inside a record the persona may read is that record's own
                # content -- e.g. the exfiltration URL planted in an injection ticket the
                # item is about. Retrieving it is correct; keeping it out of the answer
                # is Phase 6's check. Anywhere else it is a violation.
                found = {"stage": "evidence", "record": literal}
                if readable:
                    info.append({"kind": "injection_payload_in_readable_evidence", **found})
                else:
                    out.append({"kind": "must_not_contain", **found})
    for mention in result.account_mentions:
        if mention.key in hidden or not world.sees_account(item.persona, mention.key):
            out.append(
                {"kind": "hidden_account_mention", "stage": "analysis", "record": mention.key}
            )


def _diagnose(
    target: Target,
    *,
    observed: dict[str, list[Candidate]],
    result: RetrievalResult,
    corpus: Corpus,
    home: str,
) -> Miss:
    def hit(chunk_id: uuid.UUID) -> bool:
        ref = corpus.chunks.get(chunk_id)
        return ref is not None and target.satisfied_by(ref)

    channels: list[str] = [c for c in CHANNELS if any(hit(x.chunk_id) for x in observed.get(c, []))]
    fused = [f.rank for f in result.trace.stages["fused"] if hit(f.chunk_id)]
    reranked = [r.rank for r in result.trace.reranked if hit(r.chunk_id)]
    evidence = [e.rank for e in result.evidence if hit(e.chunk_id)]
    if not channels:
        cause = "not_retrieved"
    elif not fused:
        cause = "outside_fused_pool"
    elif not evidence:
        cause = "not_selected"  # fused and reranked, but below the limit or dropped by selection
    else:
        cause = "ranked_below_k"
    return Miss(
        target=target.label(home),
        channels=channels,
        fused_rank=min(fused, default=None),
        reranked_rank=min(reranked, default=None),
        evidence_rank=min(evidence, default=None),
        cause=cause,
    )


async def evaluate_item(
    app: Database,
    item: GoldenItem,
    *,
    dataset: Dataset,
    identities: Identities,
    corpus: Corpus,
    provider: EmbeddingProvider | None,
    config: RetrievalConfig,
    now: datetime,
) -> ItemResult:
    out = ItemResult(
        id=item.id,
        category=item.category,
        persona=item.persona,
        organization=item.organization,
        question=item.question,
        same_question_group=item.same_question_group,
        tags=list(item.tags),
        split=item.split,
        label_source=item.label_source,
        cohort=item.cohort,
    )
    wanted = targets(item)
    out.targets = [t.label(item.organization) for t in wanted]
    org_id = identities.org_ids[item.organization]
    membership = identities.memberships.get((item.organization, item.persona))
    if membership is None:
        out.error = "persona_not_seeded"
        return out
    query = RetrievalQuery(
        text=item.question,
        scope=RetrievalScope(
            account=item.scope.account,
            source_types=tuple(item.scope.source_types),
            since=item.scope.since,
            until=item.scope.until,
        ),
        limit=EVAL_LIMIT,
    )
    observed: dict[str, list[Candidate]] = {}

    def observe(stages: dict[str, list[Candidate]]) -> None:
        observed.update(stages)

    try:
        async with app.tenant_snapshot(org_id) as session:
            ctx = await resolve_authorization_context(
                session, organization_id=org_id, membership_id=membership
            )
            result = await retrieve_with_context(
                session, ctx, query, provider, config=config, now=now, on_candidates=observe
            )
    except AuthorizationInvariantViolation:
        out.error = "security_invariant_violated"
        out.violations.append({"kind": "tripwire", "stage": "retrieval", "record": "-"})
        # The candidates the database returned are still checked below.
        result = None
    except LatticeError as exc:
        out.error = exc.code.value
        return out

    banned = forbidden(item)
    for stage in CHANNELS:
        _check_stage(
            stage,
            (c.chunk_id for c in observed.get(stage, [])),
            item=item,
            dataset=dataset,
            corpus=corpus,
            banned=banned,
            out=out.violations,
        )
    if result is None:
        return out
    _check_stage(
        "fused",
        (f.chunk_id for f in result.trace.stages["fused"]),
        item=item,
        dataset=dataset,
        corpus=corpus,
        banned=banned,
        out=out.violations,
    )
    _check_stage(
        "evidence",
        (e.chunk_id for e in result.evidence),
        item=item,
        dataset=dataset,
        corpus=corpus,
        banned=banned,
        out=out.violations,
    )
    _check_evidence_metadata(
        result,
        item=item,
        dataset=dataset,
        corpus=corpus,
        out=out.violations,
        info=out.informational,
    )

    out.semantic_status = result.semantic.status
    out.semantic_reason = result.semantic.reason
    out.counts = dict(result.trace.counts)
    for evidence in result.evidence:
        ref = corpus.chunks.get(evidence.chunk_id)
        out.evidence.append(
            {
                "rank": evidence.rank,
                "record": ref.record if ref else "?",
                "anchors": list(ref.anchors) if ref else [],
                "matched_by": list(evidence.matched_by),
                "account": evidence.account.key if evidence.account else None,
            }
        )
    for target in wanted:
        label = target.label(item.organization)
        ranks = [
            e.rank
            for e in result.evidence
            if (ref := corpus.chunks.get(e.chunk_id)) is not None and target.satisfied_by(ref)
        ]
        first = min(ranks, default=None)
        out.found_at[label] = first
        if first is not None and first <= PRIMARY_K:
            hit = result.evidence[first - 1]
            out.hit_channels[label] = list(hit.matched_by)
        else:
            out.misses.append(
                _diagnose(
                    target, observed=observed, result=result, corpus=corpus, home=item.organization
                )
            )
    out.timings_ms = dict(result.trace.timings_ms)
    if wanted:
        for k in KS:
            found = sum(1 for r in out.found_at.values() if r is not None and r <= k)
            out.recall[f"@{k}"] = found / len(wanted)
        firsts = [r for r in out.found_at.values() if r is not None]
        out.reciprocal_rank = 1 / min(firsts) if firsts else 0.0
        _document_metrics(out, wanted, result, corpus, item)
        out.ndcg10 = _ndcg(wanted, acceptable(item), result, corpus)
    if item.exhaustive:
        _precision(out, [*wanted, *acceptable(item)], result, corpus)
    return out


def _precision(
    out: ItemResult, labelled: Sequence[Target], result: RetrievalResult, corpus: Corpus
) -> None:
    """Precision@10 over an exhaustive item: a chunk counts when it satisfies any target
    or acceptable key. The ideal is what the labelled chunks of the corpus allow."""
    refs = _evidence_refs(result, corpus)[:PRIMARY_K]
    hits = sum(1 for ref in refs if ref is not None and any(t.satisfied_by(ref) for t in labelled))
    reachable = sum(
        1 for ref in corpus.chunks.values() if any(t.satisfied_by(ref) for t in labelled)
    )
    out.precision10 = hits / PRIMARY_K
    out.precision10_ideal = min(reachable, PRIMARY_K) / PRIMARY_K


def _evidence_refs(result: RetrievalResult, corpus: Corpus) -> list[ChunkRef | None]:
    return [corpus.chunks.get(e.chunk_id) for e in result.evidence]


def _document_metrics(
    out: ItemResult,
    wanted: Sequence[Target],
    result: RetrievalResult,
    corpus: Corpus,
    item: GoldenItem,
) -> None:
    refs = _evidence_refs(result, corpus)
    records = sorted({(t.organization, t.record) for t in wanted})
    for organization, record in records:
        label = Target(organization, record, None).label(item.organization)
        ranks = [
            rank
            for rank, ref in enumerate(refs, start=1)
            if ref is not None and (ref.organization, ref.record) == (organization, record)
        ]
        out.documents_found_at[label] = min(ranks, default=None)
    for k in KS:
        found = sum(1 for r in out.documents_found_at.values() if r is not None and r <= k)
        out.document_recall[f"@{k}"] = found / len(records)
    named = [
        (organization, record)
        for organization, record in records
        if organization == item.organization
        and (ref := corpus.display_refs.get((organization, record)))
        and re.search(
            rf"(?<![A-Za-z0-9-]){re.escape(ref)}(?![A-Za-z0-9-])", item.question, re.IGNORECASE
        )
    ]
    if named:
        out.identifier_records = [record for _, record in named]
        first = refs[0] if refs else None
        out.identifier_hit = first is not None and (first.organization, first.record) in named


def _ndcg(
    wanted: Sequence[Target],
    accepted: Sequence[Target],
    result: RetrievalResult,
    corpus: Corpus,
    k: int = PRIMARY_K,
) -> float:
    """Gain 2 for the first chunk satisfying each target, 1 for the first chunk of each
    acceptable key that is not itself a target; informational (a lower bound)."""
    targets_left = list(wanted)
    accepted_left = [a for a in accepted if a not in wanted]
    dcg = 0.0
    for rank, ref in enumerate(_evidence_refs(result, corpus)[:k], start=1):
        if ref is None:
            continue
        gain = 0
        hit = next((t for t in targets_left if t.satisfied_by(ref)), None)
        if hit is not None:
            targets_left.remove(hit)
            gain = 2
        else:
            other = next((a for a in accepted_left if a.satisfied_by(ref)), None)
            if other is not None:
                accepted_left.remove(other)
                gain = 1
        dcg += gain / math.log2(rank + 1)
    ideal = ([2] * len(wanted) + [1] * len([a for a in accepted if a not in wanted]))[:k]
    idcg = sum(g / math.log2(rank + 1) for rank, g in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 0.0


# --- the run ----------------------------------------------------------------------------


@dataclass
class RetrievalReport:
    generated_at: str
    embedding_provider: str | None
    embedding_model: str | None
    embedding_deployment: str | None
    # Query embeddings this run requested: calls, texts, characters (and, for
    # providers that count them, HTTP requests and rate-limit answers).
    provider_usage: dict[str, int]
    config_version: str
    rerank_version: str
    corpus_embeddings: dict[str, dict[str, int]]
    summary: dict[str, Any]
    items: list[ItemResult]
    # Comparability (lattice.evals.baseline): defaults keep older reports loadable.
    kind: str = "retrieval"
    limit: int = EVAL_LIMIT
    corpus_fingerprint: dict[str, Any] = field(default_factory=dict)
    golden_version: dict[str, Any] = field(default_factory=dict)
    database_platform: str = "unknown"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def security_failures(self) -> int:
        return int(sum(self.summary["security"].values()))


def _recall_block(results: Sequence[ItemResult]) -> dict[str, Any]:
    scored = [r for r in results if r.recall]
    block: dict[str, Any] = {"items": len(results), "items_with_targets": len(scored)}
    for k in KS:
        block[f"recall@{k}"] = (
            round(sum(r.recall[f"@{k}"] for r in scored) / len(scored), 4) if scored else None
        )
    block["mrr"] = (
        round(sum(r.reciprocal_rank or 0.0 for r in scored) / len(scored), 4) if scored else None
    )
    for k in KS:
        block[f"document_recall@{k}"] = (
            round(sum(r.document_recall[f"@{k}"] for r in scored) / len(scored), 4)
            if scored
            else None
        )
    block["ndcg@10"] = (
        round(sum(r.ndcg10 or 0.0 for r in scored) / len(scored), 4) if scored else None
    )
    return block


def _precision_block(results: Sequence[ItemResult]) -> dict[str, Any]:
    rated = [r for r in results if r.precision10 is not None]
    if not rated:
        return {"items": 0, "value": None, "ideal": None}
    return {
        "items": len(rated),
        "value": round(sum(r.precision10 or 0.0 for r in rated) / len(rated), 4),
        "ideal": round(sum(r.precision10_ideal or 0.0 for r in rated) / len(rated), 4),
    }


def _target_count(result: ItemResult) -> str:
    n = len(result.targets)
    return "3+" if n >= 3 else str(n)


def held_out_cohorts(results: Sequence[Any]) -> list[tuple[str, list[Any]]]:
    """Held-out results per cohort (phase-13-followups §7.2): ``phase13`` items were seen
    during Phase 13, ``f2`` items are the follow-up additions."""
    groups: dict[str, list[Any]] = defaultdict(list)
    for r in results:
        groups[r.cohort or "phase13"].append(r)
    return sorted(groups.items())


def summarize(results: Sequence[ItemResult]) -> dict[str, Any]:
    violations = Counter(v["kind"] for r in results for v in r.violations)
    by_category: dict[str, list[ItemResult]] = defaultdict(list)
    by_persona: dict[str, list[ItemResult]] = defaultdict(list)
    by_split: dict[str, list[ItemResult]] = defaultdict(list)
    by_source: dict[str, list[ItemResult]] = defaultdict(list)
    groups: dict[str, list[ItemResult]] = defaultdict(list)
    for r in results:
        by_category[r.category].append(r)
        by_persona[r.persona].append(r)
        by_split[r.split or "unassigned"].append(r)
        by_source[r.label_source or "unknown"].append(r)
        if r.same_question_group:
            groups[r.same_question_group].append(r)
    channel_hits: Counter[str] = Counter()
    only_by: Counter[str] = Counter()
    for r in results:
        for channels in r.hit_channels.values():
            channel_hits.update(channels)
            if len(channels) == 1:
                only_by[channels[0]] += 1
    miss_causes = Counter(m.cause for r in results for m in r.misses)
    by_target_count: dict[str, list[ItemResult]] = defaultdict(list)
    for r in results:
        if r.recall:
            by_target_count[_target_count(r)].append(r)
    named = [r for r in results if r.identifier_hit is not None]
    stages = sorted({stage for r in results for stage in r.timings_ms})
    same_question = {}
    for group, members in sorted(groups.items()):
        sets = {
            r.persona: sorted({e["record"] for e in r.evidence if e["rank"] <= PRIMARY_K})
            for r in members
        }
        same_question[group] = {
            "personas": {
                r.persona: {
                    "item": r.id,
                    "recall@10": r.recall.get("@10"),
                    "evidence_records": sets[r.persona],
                    "violations": len(r.violations),
                }
                for r in members
            },
            "distinct_evidence_sets": len({tuple(s) for s in sets.values()}) == len(sets),
        }
    return {
        "overall": _recall_block(results),
        "by_category": {c: _recall_block(rs) for c, rs in sorted(by_category.items())},
        "by_persona": {p: _recall_block(rs) for p, rs in sorted(by_persona.items())},
        "by_target_count": {n: _recall_block(rs) for n, rs in sorted(by_target_count.items())},
        "by_split": {n: _recall_block(rs) for n, rs in sorted(by_split.items())},
        "by_held_out_cohort": {
            n: _recall_block(rs) for n, rs in held_out_cohorts(by_split.get("held_out", []))
        },
        "by_split_category": {
            split: {
                c: _recall_block([r for r in rs if r.category == c])
                for c in sorted({r.category for r in rs})
            }
            for split, rs in sorted(by_split.items())
        },
        "by_label_source": {n: _recall_block(rs) for n, rs in sorted(by_source.items())},
        "exact_identifier": {
            "items": len(named),
            "hits_at_rank_1": sum(1 for r in named if r.identifier_hit),
            "rate": (
                round(sum(1 for r in named if r.identifier_hit) / len(named), 4) if named else None
            ),
        },
        "precision@10": {
            **_precision_block(results),
            "by_split": {n: _precision_block(rs) for n, rs in sorted(by_split.items())},
            "labels": "engineering-reviewed exhaustive labels (provisional until owner review)",
        },
        "latency_ms": {
            stage: distribution([r.timings_ms[stage] for r in results if stage in r.timings_ms])
            for stage in stages
        },
        "channel_hits_top10": dict(sorted(channel_hits.items())),
        "found_only_by_top10": dict(sorted(only_by.items())),
        "semantic_outcomes": dict(
            sorted(Counter(f"{r.semantic_status}:{r.semantic_reason}" for r in results).items())
        ),
        "miss_causes_top10": dict(sorted(miss_causes.items())),
        "errors": dict(sorted(Counter(r.error for r in results if r.error).items())),
        "security": {
            "unauthorized_candidates": violations["unauthorized"],
            "cross_org_candidates": violations["cross_org"],
            "must_not_retrieve_hits": violations["must_not_retrieve"],
            "account_metadata_leaks": violations["account_metadata_leak"],
            "hidden_account_mentions": violations["hidden_account_mention"],
            "canary_hits": violations["canary"] + violations["must_not_contain"],
            "unknown_chunks": violations["unknown_chunk"],
            "tripwire_aborts": violations["tripwire"],
        },
        "same_question_groups": same_question,
        "informational": {
            "injection_payloads_in_readable_evidence": sum(
                1 for r in results for i in r.informational
            ),
        },
    }


def select_items(
    dataset: Dataset,
    *,
    categories: Sequence[str] = (),
    personas: Sequence[str] = (),
    item_ids: Sequence[str] = (),
) -> list[GoldenItem]:
    return [
        item
        for _, item in dataset.goldens
        if (not categories or item.category in categories)
        and (not personas or item.persona in personas)
        and (not item_ids or item.id in item_ids)
    ]


async def evaluate_retrieval(
    app: Database,
    owner: Database,
    dataset: Dataset,
    provider: EmbeddingProvider | None,
    *,
    items: Sequence[GoldenItem] | None = None,
    config: RetrievalConfig = DEFAULT_CONFIG,
    now: datetime = AS_OF,
    progress: Callable[[ItemResult], None] | None = None,
    prefetch: bool = True,
) -> RetrievalReport:
    identities = await load_identities(owner, dataset)
    corpus = await load_corpus(owner, identities.org_ids)
    platform = await database_platform(owner)
    selected = list(items if items is not None else select_items(dataset))
    recording = RecordingEmbeddingProvider(provider) if provider is not None else None
    queries = PrefetchedQueries(recording) if recording is not None else None
    if queries is not None and prefetch:
        await queries.prefetch(app, identities, selected)
    results: list[ItemResult] = []
    for item in selected:
        result = await evaluate_item(
            app,
            item,
            dataset=dataset,
            identities=identities,
            corpus=corpus,
            provider=queries,
            config=config,
            now=now,
        )
        results.append(result)
        if progress is not None:
            progress(result)
    usage: dict[str, int] = {}
    if recording is not None:
        usage = {
            "calls": len(recording.calls),
            "texts": len(recording.texts),
            "characters": sum(len(t) for t in recording.texts),
        }
        for counter in ("requests", "rate_limited"):
            value = getattr(provider, counter, None)
            if isinstance(value, int):
                usage[counter] = value
    return RetrievalReport(
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        embedding_provider=None if provider is None else provider.provider,
        embedding_model=None if provider is None else provider.model,
        embedding_deployment=None if provider is None else provider.deployment,
        provider_usage=usage,
        config_version=config.version,
        rerank_version=config.rerank_version,
        corpus_embeddings=corpus.embeddings,
        summary=summarize(results),
        items=results,
        corpus_fingerprint=corpus.fingerprint,
        golden_version=golden_version(selected),
        database_platform=platform,
    )


# --- rendering ----------------------------------------------------------------------------


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _precision_text(p: dict[str, Any]) -> str:
    if not p.get("items"):
        return "Precision@10: not computed (no item has exhaustive labels)."
    splits = "; ".join(
        f"{name} {_fmt(b['value'])} of ideal {_fmt(b['ideal'])} (n {b['items']})"
        for name, b in p.get("by_split", {}).items()
        if b["items"]
    )
    return (
        f"Precision@10 over {p['items']} exhaustive items: {_fmt(p['value'])} (ideal "
        f"{_fmt(p['ideal'])}); {splits}. Labels: {p.get('labels', '-')}."
    )


def render_markdown(report: RetrievalReport) -> str:
    s = report.summary
    lines = [
        "# Retrieval evaluation",
        "",
        f"Generated {report.generated_at}; config `{report.config_version}`, "
        f"`{report.rerank_version}`; query embedding "
        f"`{report.embedding_provider or 'none'}` / `{report.embedding_model or '-'}`.",
        "",
        *(
            [
                "**FakeEmbeddingProvider is a deterministic test double (hashed bag of words). "
                "These numbers gate security and engineering behaviour; they are not a "
                "semantic-quality benchmark.**",
                "",
            ]
            if report.embedding_provider == "fake"
            else []
        ),
        f"Query embedding usage: {report.provider_usage or 'none'}; deployment "
        f"`{report.embedding_deployment or '-'}`.",
        "",
        f"Corpus `{report.corpus_fingerprint.get('sha256', '-')[:12]}` "
        f"({report.corpus_fingerprint.get('chunks', {})}); golden "
        f"`{report.golden_version.get('sha256', '-')[:12]}` "
        f"({report.golden_version.get('items', '-')} items); database "
        f"`{report.database_platform}`.",
        "",
        "Corpus embeddings: "
        + "; ".join(
            f"{org}: " + ", ".join(f"{k} {v}" for k, v in counts.items())
            for org, counts in report.corpus_embeddings.items()
        ),
        "",
        "## Security (each must be 0)",
        "",
        "| Metric | Count |",
        "| ------ | ----: |",
        *(f"| {k} | {v} |" for k, v in s["security"].items()),
        "",
        "## Recall",
        "",
        "| Slice | Items | With targets | R@1 | R@5 | R@10 | R@20 | MRR |",
        "| ----- | ----: | -----------: | --: | --: | ---: | ---: | --: |",
    ]

    def row(name: str, b: dict[str, Any]) -> str:
        return (
            f"| {name} | {b['items']} | {b['items_with_targets']} | {_fmt(b['recall@1'])} | "
            f"{_fmt(b['recall@5'])} | {_fmt(b['recall@10'])} | {_fmt(b['recall@20'])} | "
            f"{_fmt(b['mrr'])} |"
        )

    header = lines[-2:]
    lines.append(row("**overall**", s["overall"]))
    lines += [row(c, b) for c, b in s["by_category"].items()]
    lines += ["", "### By persona", "", *header]
    lines += [row(p, b) for p, b in s["by_persona"].items()]
    lines += ["", "### By number of targets", "", *header]
    lines += [row(n, b) for n, b in s["by_target_count"].items()]
    lines += ["", "### By split (EC-7: held_out is reported, never tuned against)", "", *header]
    lines += [row(n, b) for n, b in s.get("by_split", {}).items()]
    lines += [row(f"held_out ({n})", b) for n, b in s.get("by_held_out_cohort", {}).items()]
    lines += ["", "### By label source (EC-8: engineering labels are provisional)", "", *header]
    lines += [row(n, b) for n, b in s.get("by_label_source", {}).items()]

    def doc_row(name: str, b: dict[str, Any]) -> str:
        return (
            f"| {name} | {b['items_with_targets']} | {_fmt(b['document_recall@1'])} | "
            f"{_fmt(b['document_recall@5'])} | {_fmt(b['document_recall@10'])} | "
            f"{_fmt(b['document_recall@20'])} | {_fmt(b['ndcg@10'])} |"
        )

    ident = s["exact_identifier"]
    lines += [
        "",
        "## Document recall and nDCG@10 (informational: unlabelled is not irrelevant)",
        "",
        "| Slice | With targets | Doc R@1 | Doc R@5 | Doc R@10 | Doc R@20 | nDCG@10 |",
        "| ----- | -----------: | ------: | ------: | -------: | -------: | ------: |",
        doc_row("**overall**", s["overall"]),
        *(doc_row(c, b) for c, b in s["by_category"].items()),
        "",
        f"Exact identifier (target record named by the question at rank 1): "
        f"{ident['hits_at_rank_1']}/{ident['items']}. {_precision_text(s['precision@10'])}",
        "",
        "## Retrieval stage latency (ms; this machine and database, not a benchmark)",
        "",
        "| Stage | n | p50 | p95 | max |",
        "| ----- | -: | --: | --: | --: |",
        *(
            f"| {stage} | {d['n']} | {d['p50']} | {d['p95']} | {d['max']} |"
            for stage, d in s["latency_ms"].items()
        ),
    ]
    lines += [
        "",
        "## Channels",
        "",
        f"Targets found in the top 10, by channel that matched them: {s['channel_hits_top10']}; "
        f"found by one channel only: {s['found_only_by_top10']}.",
        "",
        f"Semantic outcomes: {s['semantic_outcomes']}.",
        "",
        f"Misses in the top 10 by cause: {s['miss_causes_top10']}. "
        f"Errors: {s['errors'] or 'none'}.",
        "",
        "## Same question, different persona",
        "",
        "| Group | Persona | Item | R@10 | Evidence records (top 10) | Violations |",
        "| ----- | ------- | ---- | ---: | ------------------------- | ---------: |",
    ]
    for group, g in s["same_question_groups"].items():
        for persona, p in g["personas"].items():
            lines.append(
                f"| {group} | {persona} | {p['item']} | {_fmt(p['recall@10'])} | "
                f"{len(p['evidence_records'])} records | {p['violations']} |"
            )
    lines += [
        "",
        "## Misses (top 10)",
        "",
        "| Item | Target | Cause | Channels | Fused | Reranked |",
        "| ---- | ------ | ----- | -------- | ----: | -------: |",
    ]
    for r in report.items:
        for m in r.misses:
            lines.append(
                f"| {r.id} | `{m.target}` | {m.cause} | {','.join(m.channels) or '-'} | "
                f"{m.fused_rank or '-'} | {m.reranked_rank or '-'} |"
            )
    return "\n".join(lines) + "\n"
