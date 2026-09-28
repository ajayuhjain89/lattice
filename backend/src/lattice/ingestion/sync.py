"""The sync pipeline (docs/ingestion-design.md §1):

    connector → validate → normalize → map access (fail closed) → hash → decide
    → chunk → embedding decision (egress policy) → embed (batched, outside
    transactions) → persist (one transaction per document) → deletion sweep (guarded)
    → supersession links → embedding reconciliation → sync result

Modes:

* ``full`` -- snapshot sync: create, update, ACL-update, quarantine, delete (guarded).
* ``acl_only`` -- re-read access hints and groups; update access columns and source
  grants of existing documents; never re-chunk or re-embed. Embeddings change only
  where the new classification changes what the egress policy allows.
* ``reindex`` -- like ``full`` without the deletion sweep, optionally re-chunking every
  document (``rechunk``, e.g. after a chunker change) and re-embedding every eligible
  chunk (``reembed``, e.g. a provider change or a raised egress level).

``dry_run`` reads everything, writes nothing (it runs in a read-only snapshot), makes no
embedding call, and reports what a real run would do.

Idempotent: an unchanged sync writes no document, chunk, grant or group. Only the
operational run row and the source's ``last_synced_at`` change.
"""

import hashlib
import logging
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.connectors.contract import Connector, RecordError, SourceRecord, SourceUnavailable
from lattice.connectors.registry import build_connector
from lattice.db.engine import Database
from lattice.ingestion import store
from lattice.ingestion.access import LEVELS, AccessFailure, MappedAccess, OrgDirectory, map_access
from lattice.ingestion.canonical import CanonicalDocument, NormalizeFailure, canonicalize
from lattice.ingestion.chunking import chunk, estimate_tokens
from lattice.ingestion.embedding_input import embedding_input, input_hash, lexical_header
from lattice.log import log_event
from lattice.providers.egress import DEFAULT_LEVEL, effective_level, may_send
from lattice.providers.embedding import EmbeddingError, EmbeddingProvider
from lattice.sources.config import SourceConfig

Mode = Literal["full", "acl_only", "reindex"]
Action = Literal["create", "update", "acl", "unchanged", "skip"]

DELETION_GUARD_RATIO = 0.20
_log = logging.getLogger("lattice.ingestion")


@dataclass(frozen=True)
class SyncOptions:
    mode: Mode = "full"
    dry_run: bool = False
    allow_mass_deletion: bool = False
    rechunk: bool = False
    reembed: bool = False
    guard_ratio: float = DELETION_GUARD_RATIO
    batch_size: int = 50


@dataclass
class SyncResult:
    source: str
    mode: Mode
    dry_run: bool
    status: str = "running"
    run_id: uuid.UUID | None = None
    error_code: str | None = None
    records_seen: int = 0
    created: int = 0
    updated: int = 0
    access_updated: int = 0
    unchanged: int = 0
    skipped: int = 0  # acl_only: records with no document yet
    failed: int = 0
    quarantined: int = 0
    deleted: int = 0
    would_delete: int = 0
    chunks_embedded: int = 0
    embeddings_reused: int = 0
    chunks_policy_excluded: int = 0
    embedding_failures: int = 0
    memberships_regrouped: int = 0
    deletion_guard_tripped: bool = False
    failure_codes: Counter[str] = field(default_factory=Counter)

    def run_counts(self) -> dict[str, int | bool | str | None]:
        return {
            "records_seen": self.records_seen,
            "created": self.created,
            "updated": self.updated,
            "access_updated": self.access_updated,
            "unchanged": self.unchanged,
            "deleted": self.deleted,
            "failed": self.failed,
            "chunks_embedded": self.chunks_embedded,
            "embeddings_reused": self.embeddings_reused,
            "chunks_policy_excluded": self.chunks_policy_excluded,
            "embedding_failures": self.embedding_failures,
            "deletion_guard_tripped": self.deletion_guard_tripped,
            "error_code": self.error_code,
        }


class SyncError(Exception):
    """A source-level problem: nothing (or, once a run is open, nothing further) is
    written, the run is ``failed`` and no deletion sweep runs."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _Plan:
    record: SourceRecord
    doc: CanonicalDocument
    access: MappedAccess
    prior: store.ExistingDocument | None
    action: Action
    document_id: uuid.UUID
    eligible: bool
    chunks: list[store.ChunkRow] = field(default_factory=list)
    embed_failed: str | None = None


@asynccontextmanager
async def _source_lock(db: Database, org: uuid.UUID, key: str) -> AsyncIterator[None]:
    """One sync per source at a time: a session-level advisory lock held on a dedicated
    connection for the whole run (the per-document transactions stay short)."""
    name = f"lattice-sync:{org}:{key}"
    async with db.engine.connect() as conn:
        locked: bool = (
            await conn.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:n, 0))"), {"n": name}
            )
        ).scalar_one()
        await conn.commit()
        if not locked:
            raise SyncError("source_busy")
        try:
            yield
        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:n, 0))"), {"n": name}
            )
            await conn.commit()


def _batches(
    items: Iterator[SourceRecord | RecordError], size: int
) -> Iterator[list[SourceRecord | RecordError]]:
    batch: list[SourceRecord | RecordError] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _chunk_rows(plan: _Plan) -> list[store.ChunkRow]:
    doc = plan.doc
    rows: list[store.ChunkRow] = []
    seen: Counter[bytes] = Counter()
    for ordinal, draft in enumerate(chunk(doc)):
        body = doc.body[draft.char_start : draft.char_end]
        content_hash = hashlib.sha256(body.encode()).digest()
        occurrence = seen[content_hash]
        seen[content_hash] += 1
        payload = embedding_input(doc.kind, doc.title, draft.heading_path, body)
        rows.append(
            store.ChunkRow(
                id=store.chunk_id_for(plan.document_id, content_hash, occurrence),
                ordinal=ordinal,
                text=body,
                char_start=draft.char_start,
                char_end=draft.char_end,
                heading_path=draft.heading_path,
                source_anchors=draft.source_anchors,
                occurred_at=draft.occurred_at or doc.updated_at,
                author_display=draft.author_display,
                content_hash=content_hash,
                embedding_input_hash=input_hash(payload),
                embedding_input=payload,
                token_count=estimate_tokens(body),
                lexical_header=lexical_header(doc.title, draft.heading_path),
                embedding=None,
                embedding_model=None,
                embedding_status="policy_excluded" if not plan.eligible else "pending",
            )
        )
    return rows


class Syncer:
    def __init__(
        self,
        db: Database,
        organization_id: uuid.UUID,
        config: SourceConfig,
        export_path: Path,
        provider: EmbeddingProvider | None,
        options: SyncOptions,
    ) -> None:
        self.db = db
        self.org = organization_id
        self.config = config
        self.path = export_path
        self.provider = provider
        self.options = options
        self.result = SyncResult(config.key, options.mode, options.dry_run)
        self.failures: list[store.Failure] = []
        self.allowed: list[str] = []
        self.level: str | None = None
        self.connector: Connector = build_connector(config.connector_type, export_path)
        self.source: store.SourceRow | None = None

    def _tx(self) -> AbstractAsyncContextManager[AsyncSession]:
        if self.options.dry_run:
            return self.db.tenant_snapshot(self.org)
        return self.db.tenant_transaction(self.org)

    def _fail(
        self,
        stage: str,
        code: str,
        *,
        external_id: str | None = None,
        location: str | None = None,
        params: dict[str, str | int] | None = None,
        count: bool = True,
    ) -> None:
        self.failures.append(
            store.Failure(stage, code, external_id, location, tuple(sorted((params or {}).items())))
        )
        self.result.failure_codes[code] += 1
        if count:
            self.result.failed += 1

    # --- entry point -----------------------------------------------------------------------

    async def run(self) -> SyncResult:
        async with self._tx() as s:
            directory = await store.load_directory(s, self.org)
            try:
                store.check_config(self.config, directory)
                if self.options.dry_run:
                    source = await store.get_source(s, self.org, self.config.key)
                    if source is not None and source.status != "active":
                        raise store.SourceConfigurationError(
                            f"source_config:source_{source.status}"
                        )
                else:
                    source = await store.upsert_source(s, self.org, self.config, str(self.path))
            except store.SourceConfigurationError as exc:
                raise SyncError(exc.code) from None
        self.source = source

        if self.options.dry_run:
            try:
                await self._run(directory)
            except (SourceUnavailable, store.SourceConfigurationError) as exc:
                raise SyncError(exc.code) from None
            self.result.status = "dry_run"
            return self.result
        assert source is not None  # noqa: S101 - upserted above
        async with _source_lock(self.db, self.org, self.config.key):
            async with self.db.tenant_transaction(self.org) as s:
                self.result.run_id = await store.open_run(s, self.org, source.id, self.options.mode)
            try:
                await self._run(directory)
            except (SourceUnavailable, store.SourceConfigurationError) as exc:
                self.result.status = "failed"
                self.result.error_code = exc.code
            except BaseException:
                # An unexpected error (a bug, a lost connection, an interrupt): record the
                # run as aborted -- never left "running" -- and let the error surface.
                self.result.status = "aborted"
                self.result.error_code = "internal_error"
                await self._close(source)
                raise
            await self._close(source)
        return self.result

    async def _close(self, source: store.SourceRow) -> None:
        r = self.result
        if r.status not in ("failed", "aborted"):
            r.status = (
                "partial"
                if r.failed or r.embedding_failures or r.deletion_guard_tripped
                else "succeeded"
            )
        assert r.run_id is not None  # noqa: S101 - opened in run()
        async with self.db.tenant_transaction(self.org) as s:
            await store.record_failures(s, self.org, r.run_id, source.opaque_ref_key, self.failures)
            await store.close_run(
                s,
                self.org,
                source.id,
                r.run_id,
                status=r.status,
                counts=r.run_counts(),
                acl_synced=r.status not in ("failed", "aborted"),
            )
        log_event(
            _log,
            logging.INFO,
            "ingestion.sync",
            source_id=str(source.id),
            run_id=str(r.run_id),
            mode=r.mode,
            status=r.status,
            error_code=r.error_code,
            **{k: v for k, v in r.run_counts().items() if isinstance(v, int)},
        )

    # --- the pipeline -------------------------------------------------------------------------

    async def _run(self, directory: OrgDirectory) -> None:
        conn = self.connector
        source = self.source
        conn.check()

        async with self._tx() as s:
            org_level = await store.org_egress_level(s, self.org)
            approved: str = DEFAULT_LEVEL
            if self.provider is not None:
                if self.options.dry_run:
                    approved = (
                        await store.provider_approved_level(
                            s, self.org, self.provider.provider, self.provider.model
                        )
                        or DEFAULT_LEVEL
                    )
                else:
                    approved = await store.register_provider(
                        s,
                        self.org,
                        provider=self.provider.provider,
                        model=self.provider.model,
                        deployment=self.provider.deployment,
                        retention_mode=self.provider.retention_mode,
                        training_opt_out=self.provider.training_opt_out,
                    )
            self.level = effective_level(org_level, approved)
            self.allowed = [lvl for lvl in LEVELS if may_send(lvl, self.level)]

            groups = conn.access_groups()
            if self.options.dry_run:
                known = (
                    await store.source_groups(s, self.org, source.id) if source is not None else {}
                )
                source_groups = {
                    g.external_ref: known.get(g.external_ref)
                    or uuid.uuid5(uuid.NAMESPACE_URL, f"dry-run:{g.external_ref}")
                    for g in groups
                }
                existing = (
                    await store.existing_documents(s, self.org, source.id)
                    if source is not None
                    else {}
                )
            else:
                assert source is not None  # noqa: S101
                source_groups, regrouped = await store.sync_groups(
                    s, self.org, source, groups, remove_stale=False
                )
                self.result.memberships_regrouped += regrouped
                existing = await store.existing_documents(s, self.org, source.id)
        directory = replace(directory, source_groups=source_groups)

        seen: set[str] = set()
        unidentified = 0
        for batch in _batches(conn.records(), self.options.batch_size):
            plans: list[_Plan] = []
            quarantine: list[uuid.UUID] = []
            for item in batch:
                self.result.records_seen += 1
                if isinstance(item, RecordError):
                    self._fail(
                        item.stage,
                        item.code,
                        external_id=item.external_id,
                        location=item.location,
                        params=item.params,
                    )
                    if item.external_id:
                        seen.add(item.external_id)
                        self._quarantine_prior(existing.get(item.external_id), quarantine)
                    else:
                        unidentified += 1
                    continue
                plan = self._plan(item, source, directory, existing, seen, quarantine)
                if plan is not None:
                    plans.append(plan)
            await self._apply(plans, quarantine)
            if not self.options.dry_run and self.failures:
                assert source is not None  # noqa: S101 - not a dry run
                assert self.result.run_id is not None  # noqa: S101 - opened in run()
                async with self.db.tenant_transaction(self.org) as s:
                    await store.record_failures(
                        s, self.org, self.result.run_id, source.opaque_ref_key, self.failures
                    )
                self.failures = []

        if self.options.mode == "full":
            await self._sweep(source, existing, seen, unidentified)
        if self.options.dry_run:
            return
        assert source is not None  # noqa: S101
        async with self.db.tenant_transaction(self.org) as s:
            if self.options.mode != "acl_only":
                await store.resolve_supersession(s, self.org, source.id)
            if (
                self.options.mode == "full"
                and not self.result.deletion_guard_tripped
                and not unidentified
            ):
                _, regrouped = await store.sync_groups(
                    s, self.org, source, groups, remove_stale=True
                )
                self.result.memberships_regrouped += regrouped
        await self._reconcile_embeddings(source)

    def _plan(
        self,
        record: SourceRecord,
        source: store.SourceRow | None,
        directory: OrgDirectory,
        existing: dict[str, store.ExistingDocument],
        seen: set[str],
        quarantine: list[uuid.UUID],
    ) -> _Plan | None:
        ext = record.external_id
        if ext in seen:
            self._fail("validate", "duplicate_external_id", external_id=ext)
            return None
        seen.add(ext)
        prior = existing.get(ext)
        try:
            doc = canonicalize(record, self.config.authority)
        except NormalizeFailure as exc:
            self._fail("normalize", exc.code, external_id=ext)
            self._quarantine_prior(prior, quarantine)
            return None
        access = map_access(record, self.config, directory)
        if isinstance(access, AccessFailure):
            self._fail("map_access", access.code, external_id=ext, params=access.params)
            self._quarantine_prior(prior, quarantine)
            return None

        action: Action
        content_changed = prior is None or prior.content_hash != doc.content_hash
        access_changed = prior is None or prior.access_hash != access.access_hash
        if prior is None:
            action = "skip" if self.options.mode == "acl_only" else "create"
        elif self.options.mode == "acl_only":
            action = "acl" if access_changed or prior.status != "active" else "unchanged"
        elif content_changed or prior.status != "active" or self.options.rechunk:
            action = "update"
        elif access_changed:
            action = "acl"
        else:
            action = "unchanged"
        source_id = source.id if source is not None else uuid.UUID(int=0)
        return _Plan(
            record=record,
            doc=doc,
            access=access,
            prior=prior,
            action=action,
            document_id=prior.id if prior else store.document_id_for(source_id, ext),
            eligible=may_send(access.sensitivity, self.level),
        )

    @staticmethod
    def _quarantine_prior(
        prior: store.ExistingDocument | None, quarantine: list[uuid.UUID]
    ) -> None:
        """A record that fails any stage leaves its existing document unreadable rather
        than readable on access metadata the source may no longer state (fail closed).
        The next sync that reads the record successfully restores it."""
        if prior is not None and prior.status == "active":
            quarantine.append(prior.id)

    async def _apply(self, plans: list[_Plan], quarantine: list[uuid.UUID]) -> None:
        r = self.result
        writes = [p for p in plans if p.action in ("create", "update")]
        for plan in writes:
            plan.chunks = _chunk_rows(plan)

        if self.options.dry_run:
            for plan in plans:
                self._count(plan.action)
            r.quarantined += len(quarantine)
            return

        await self._assign_embeddings(writes)
        assert self.source is not None  # noqa: S101 - not a dry run
        plan_source_id = self.source.id
        for plan in plans:
            if plan.action in ("unchanged", "skip"):
                self._count(plan.action)
                continue
            try:
                async with self.db.tenant_transaction(self.org) as s:
                    if plan.action == "acl":
                        r.chunks_policy_excluded += await store.update_access(
                            s, self.org, plan.document_id, plan.access, eligible=plan.eligible
                        )
                    else:
                        prior = plan.prior
                        version = (
                            1
                            if prior is None
                            else prior.version + (prior.content_hash != plan.doc.content_hash)
                        )
                        await store.write_document(
                            s,
                            self.org,
                            plan_source_id,
                            plan.doc,
                            plan.access,
                            document_id=plan.document_id,
                            version=version,
                            chunks=plan.chunks,
                            rechunked=self.options.rechunk,
                        )
            except DBAPIError:
                self._fail("persist", "persist_failed", external_id=plan.record.external_id)
                continue
            self._count(plan.action)
            if plan.embed_failed:
                self._fail(
                    "embed", plan.embed_failed, external_id=plan.record.external_id, count=False
                )
            r.chunks_policy_excluded += sum(
                c.embedding_status == "policy_excluded" for c in plan.chunks
            )
        for document_id in quarantine:
            async with self.db.tenant_transaction(self.org) as s:
                r.quarantined += await store.quarantine(s, self.org, document_id)

    def _count(self, action: Action) -> None:
        r = self.result
        if action == "create":
            r.created += 1
        elif action == "update":
            r.updated += 1
        elif action == "acl":
            r.access_updated += 1
        elif action == "skip":
            r.skipped += 1
        else:
            r.unchanged += 1

    async def _assign_embeddings(self, plans: list[_Plan]) -> None:
        """Reuse vectors of unchanged chunk inputs; embed the rest if a provider is
        configured and the egress policy allows; otherwise record why not."""
        priors = [p.document_id for p in plans if p.prior is not None]
        async with self.db.tenant_transaction(self.org) as s:
            old = await store.existing_chunks(s, self.org, priors)

        needed: list[tuple[_Plan, store.ChunkRow]] = []
        for plan in plans:
            if not plan.eligible:
                continue
            reusable = {
                c.embedding_input_hash: c
                for c in old.get(plan.document_id, [])
                if c.embedding_status == "embedded"
                and c.embedding is not None
                and (self.provider is None or c.embedding_model == self.provider.model)
                and not self.options.reembed
            }
            for row in plan.chunks:
                hit = reusable.get(row.embedding_input_hash)
                if hit is not None:
                    row.embedding, row.embedding_model = hit.embedding, hit.embedding_model
                    row.embedding_status = "embedded"
                    self.result.embeddings_reused += 1
                elif self.provider is not None:
                    needed.append((plan, row))
                else:
                    row.embedding_status = "pending"

        if self.provider is None or not needed:
            return
        size = max(1, self.provider.max_batch)
        for start in range(0, len(needed), size):
            batch = needed[start : start + size]
            try:
                vectors = await self.provider.embed(
                    [row.embedding_input for _, row in batch], "document"
                )
            except EmbeddingError as exc:
                for plan, row in batch:
                    row.embedding_status = "failed"
                    plan.embed_failed = exc.code
                self.result.embedding_failures += len(batch)
                continue
            for (_, row), vector in zip(batch, vectors, strict=True):
                row.embedding, row.embedding_model = vector, self.provider.model
                row.embedding_status = "embedded"
            self.result.chunks_embedded += len(batch)

    async def _sweep(
        self,
        source: store.SourceRow | None,
        existing: dict[str, store.ExistingDocument],
        seen: set[str],
        unidentified: int,
    ) -> None:
        """Delete documents whose records disappeared from a full snapshot -- unless
        that looks like a broken export rather than real deletions (the guard).

        A record that failed before it could be identified (a corrupt line or file)
        could be any unseen document, whose access the source may have changed: those
        documents are quarantined -- unreadable, not deleted -- until a sync reads
        them again. A mass deletion or an empty snapshot only stops the sweep; the
        unseen documents keep the access the source last stated."""
        if not self.connector.capabilities.snapshot:
            return
        unseen = [ext for ext in existing if ext not in seen]
        if not unseen:
            return
        reason: str | None = None
        if unidentified:
            reason = "deletion_guard:unidentified_failures"  # a broken record could be any
        elif not seen:
            reason = "deletion_guard:empty_snapshot"
        elif len(unseen) > 1 and len(unseen) / len(existing) > self.options.guard_ratio:
            reason = "deletion_guard:mass_deletion"
        overridable = reason != "deletion_guard:unidentified_failures"
        if reason is not None and not (self.options.allow_mass_deletion and overridable):
            self.result.deletion_guard_tripped = True
            self.result.error_code = reason
            self.result.would_delete = len(unseen)
            if not overridable:
                suspects = [existing[ext].id for ext in unseen if existing[ext].status == "active"]
                if self.options.dry_run:
                    self.result.quarantined += len(suspects)
                    return
                for document_id in suspects:
                    async with self.db.tenant_transaction(self.org) as s:
                        self.result.quarantined += await store.quarantine(s, self.org, document_id)
            return
        if self.options.dry_run:
            self.result.would_delete = len(unseen)
            return
        async with self.db.tenant_transaction(self.org) as s:
            self.result.deleted += await store.delete_documents(
                s, self.org, [existing[ext].id for ext in unseen]
            )

    async def _reconcile_embeddings(self, source: store.SourceRow) -> None:
        """Make stored vectors match the egress policy now in force: drop vectors that
        may no longer be sent; fill pending, failed and newly eligible chunks."""
        async with self.db.tenant_transaction(self.org) as s:
            self.result.chunks_policy_excluded += await store.exclude_ineligible(
                s, self.org, source.id, self.allowed
            )
            if self.provider is None:
                await store.mark_pending(s, self.org, source.id, self.allowed)
                return
            targets = await store.embedding_targets(
                s, self.org, source.id, self.allowed, reembed=self.options.reembed
            )
        size = max(1, self.provider.max_batch)
        for start in range(0, len(targets), size):
            batch = targets[start : start + size]
            payload = [embedding_input(t.kind, t.title, t.heading_path, t.text) for t in batch]
            try:
                vectors = await self.provider.embed(payload, "document")
            except EmbeddingError:
                # A failed re-embed keeps the vector it would have replaced.
                updates: list[tuple[uuid.UUID, list[float] | None, str | None, str]] = [
                    (t.chunk_id, None, None, "failed")
                    for t in batch
                    if t.embedding_status != "embedded"
                ]
                self.result.embedding_failures += len(batch)
            else:
                updates = [
                    (t.chunk_id, v, self.provider.model, "embedded")
                    for t, v in zip(batch, vectors, strict=True)
                ]
                self.result.chunks_embedded += len(batch)
            async with self.db.tenant_transaction(self.org) as s:
                await store.store_embeddings(s, self.org, updates)


async def sync_source(
    db: Database,
    organization_id: uuid.UUID,
    config: SourceConfig,
    export_path: Path,
    provider: EmbeddingProvider | None,
    options: SyncOptions | None = None,
) -> SyncResult:
    syncer = Syncer(db, organization_id, config, export_path, provider, options or SyncOptions())
    return await syncer.run()
