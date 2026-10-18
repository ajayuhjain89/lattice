"""What a viewer may know about sources and their syncs (security-model §10, SEC-14;
ingestion-design §8; design-system B12 "Sources page").

Two views, each computed for the viewer at request time, inside one snapshot:

* **member** (``member_sources``) -- only sources in which the member can read at least
  one active document, with the count of *those* documents: computed inside the
  member's authorization predicate (``authz.readable_documents_predicate``), never a
  source total. A source whose every document is unreadable to them does not exist for
  them. Its sync state is shown because they can read from it.
* **administrator** (``admin_sources``, ``admin_failures``) -- every source (admins
  configure them): per-source aggregate totals only (active documents, the last run's
  counts and failure codes, last sync times), and the latest requested job. Never a
  breakdown by account, sensitivity, department, group or document. A failed record is
  shown as its stage, fixed code and templated parameters; its identifier appears only
  when the administrator can read the corresponding active document, otherwise its
  opaque reference (``rec_…``). The one exception is ``access_unmappable:account``,
  which shows the unmapped *external account reference* (mapping it is the admin's job;
  the account directory is admin-visible) -- never the record's title or content.

Both views read through row-level security bound to the viewer's organization.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Table, and_, func, select, text
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz import AuthorizationContext, Document, readable_documents_predicate
from lattice.jobs.queue import JobView, latest_jobs
from lattice.sources.admin import next_action
from lattice.sources.models import Source, SourceSyncRun, SyncRecordFailure

DOCUMENTS = Document.__table__
SOURCES = Source.__table__
RUNS = SourceSyncRun.__table__
FAILURES = SyncRecordFailure.__table__
for _table in (DOCUMENTS, SOURCES, RUNS, FAILURES):
    assert isinstance(_table, Table)  # noqa: S101 - declarative models map to Tables

FAILURE_LIMIT: Final = 100
# Parameters a failure may show: a schema field name. The external account reference
# only for access_unmappable:account (see the module docstring).
_SHOWN_PARAMS: Final = frozenset({"field"})
_UNMAPPED_ACCOUNT: Final = "access_unmappable:account"
_RUN_COUNTS: Final = (
    "records_seen",
    "created",
    "updated",
    "access_updated",
    "unchanged",
    "deleted",
    "failed",
    "chunks_embedded",
    "embeddings_reused",
    "chunks_policy_excluded",
    "embedding_failures",
)


@dataclass(frozen=True)
class MemberSource:
    id: uuid.UUID
    name: str
    connector_type: str
    readable_documents: int
    last_synced_at: datetime | None
    last_succeeded_at: datetime | None
    last_run_status: str | None


@dataclass(frozen=True)
class RunSummary:
    id: uuid.UUID
    mode: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    counts: dict[str, int]
    deletion_guard_tripped: bool
    error_code: str | None


@dataclass(frozen=True)
class FailureCount:
    stage: str
    code: str
    count: int


@dataclass(frozen=True)
class AdminSource:
    id: uuid.UUID
    key: str
    name: str
    connector_type: str
    status: str
    account_scoped: bool
    default_sensitivity: str
    authority: str
    documents: int
    last_synced_at: datetime | None
    last_succeeded_at: datetime | None
    acl_synced_at: datetime | None
    last_run: RunSummary | None
    failure_codes: list[FailureCount]
    job: JobView | None
    # P12.6: who configures it, the edit version, its current import, the latest upload's
    # refusal (when newer than the current import), and the next action (server-decided).
    managed_by: str = "operator"
    version: int = 1
    import_status: str | None = None
    rejected_upload_code: str | None = None
    next_action: str = "none"


@dataclass(frozen=True)
class FailureView:
    stage: str
    code: str
    params: dict[str, str | int]
    # "identifier": the record's external ID (the admin can read its document);
    # "opaque": the keyed reference only.
    record_kind: str
    record: str
    created_at: datetime


_IMPORTS_SQL = text(
    """
    SELECT s.id, ci.status AS import_status, ci.created_at AS import_created_at,
           ci.check_error_code,
           (SELECT count(*) FROM jsonb_array_elements(
              coalesce(ci.check_result -> 'unresolved_references', '[]'::jsonb)) ref
            WHERE NOT EXISTS (
              SELECT 1 FROM account_external_refs m WHERE m.organization_id = :o
                AND m.source_id = s.id AND m.external_ref = ref ->> 'ref'
                AND m.account_id IS NOT NULL)) AS check_unmapped,
           lu.rejected_code AS latest_rejected_code, lu.created_at AS latest_at
    FROM sources s
    LEFT JOIN source_imports ci ON ci.organization_id = s.organization_id AND ci.id = s.import_id
    LEFT JOIN LATERAL (
      SELECT i.rejected_code, i.created_at FROM source_imports i
      WHERE i.organization_id = :o AND i.source_id = s.id
      ORDER BY i.created_at DESC, i.id LIMIT 1) lu ON true
    WHERE s.organization_id = :o
    """
)
# References the latest run of each source could not map and that are still unmapped.
_UNMAPPED_AFTER_RUN_SQL = text(
    """
    WITH latest AS (
      SELECT DISTINCT ON (source_id) source_id, id FROM source_sync_runs
      WHERE organization_id = :o ORDER BY source_id, started_at DESC, id
    )
    SELECT l.source_id, count(DISTINCT f.message_params ->> 'external_account_ref')
    FROM latest l JOIN sync_record_failures f
      ON f.organization_id = :o AND f.sync_run_id = l.id
    WHERE f.error_code = 'access_unmappable:account' AND f.message_params ? 'external_account_ref'
      AND NOT EXISTS (
        SELECT 1 FROM account_external_refs m WHERE m.organization_id = :o
          AND m.source_id = l.source_id
          AND m.external_ref = f.message_params ->> 'external_account_ref'
          AND m.account_id IS NOT NULL)
    GROUP BY l.source_id
    """
)


def _latest_runs() -> Any:
    return (
        select(RUNS)
        .ext(distinct_on(RUNS.c.source_id))
        .order_by(RUNS.c.source_id, RUNS.c.started_at.desc(), RUNS.c.id)
        .subquery("latest_run")
    )


async def member_sources(session: AsyncSession, ctx: AuthorizationContext) -> list[MemberSource]:
    org = ctx.organization_id
    readable = (
        select(DOCUMENTS.c.source_id, func.count().label("n"))
        .where(
            DOCUMENTS.c.organization_id == org,
            DOCUMENTS.c.status == "active",
            readable_documents_predicate(ctx, DOCUMENTS),
        )
        .group_by(DOCUMENTS.c.source_id)
        .subquery("readable")
    )
    latest = _latest_runs()
    rows = await session.execute(
        select(
            SOURCES.c.id,
            SOURCES.c.name,
            SOURCES.c.connector_type,
            readable.c.n,
            SOURCES.c.last_synced_at,
            SOURCES.c.last_succeeded_at,
            latest.c.status,
        )
        .join(readable, readable.c.source_id == SOURCES.c.id)
        .outerjoin(latest, latest.c.source_id == SOURCES.c.id)
        # A paused source's content stays searchable (P12.6): it stays listed.
        .where(
            SOURCES.c.organization_id == org,
            SOURCES.c.status.in_(("active", "paused")),
            readable.c.n > 0,
        )
        .order_by(SOURCES.c.name, SOURCES.c.id)
    )
    return [MemberSource(*row) for row in rows]


async def admin_sources(session: AsyncSession, organization_id: uuid.UUID) -> list[AdminSource]:
    """Every source of the organization with aggregate totals. The caller checks the
    administrator capability."""
    documents = (
        select(DOCUMENTS.c.source_id, func.count().label("n"))
        .where(DOCUMENTS.c.organization_id == organization_id, DOCUMENTS.c.status == "active")
        .group_by(DOCUMENTS.c.source_id)
        .subquery("totals")
    )
    latest = _latest_runs()
    rows = (
        await session.execute(
            select(
                SOURCES,
                func.coalesce(documents.c.n, 0).label("documents"),
                latest.c.id.label("run_id"),
                *[latest.c[c].label(f"run_{c}") for c in (*_RUN_COUNTS, "mode", "status")],
                latest.c.started_at.label("run_started_at"),
                latest.c.finished_at.label("run_finished_at"),
                latest.c.deletion_guard_tripped.label("run_guard"),
                latest.c.error_code.label("run_error_code"),
            )
            .outerjoin(documents, documents.c.source_id == SOURCES.c.id)
            .outerjoin(latest, latest.c.source_id == SOURCES.c.id)
            .where(SOURCES.c.organization_id == organization_id)
            .order_by(SOURCES.c.name, SOURCES.c.id)
        )
    ).all()
    run_ids = [r.run_id for r in rows if r.run_id is not None]
    codes: dict[uuid.UUID, list[FailureCount]] = {}
    if run_ids:
        for run_id, stage, code, count in await session.execute(
            select(FAILURES.c.sync_run_id, FAILURES.c.stage, FAILURES.c.error_code, func.count())
            .where(
                FAILURES.c.organization_id == organization_id,
                FAILURES.c.sync_run_id.in_(run_ids),
            )
            .group_by(FAILURES.c.sync_run_id, FAILURES.c.stage, FAILURES.c.error_code)
            .order_by(func.count().desc(), FAILURES.c.stage, FAILURES.c.error_code)
        ):
            codes.setdefault(run_id, []).append(FailureCount(stage, code, int(count)))
    jobs = await latest_jobs(session, organization_id)
    imports = {r.id: r for r in await session.execute(_IMPORTS_SQL, {"o": organization_id})}
    unmapped = {
        source_id: int(n)
        for source_id, n in await session.execute(_UNMAPPED_AFTER_RUN_SQL, {"o": organization_id})
    }
    result: list[AdminSource] = []
    for r in rows:
        run = (
            RunSummary(
                id=r.run_id,
                mode=r.run_mode,
                status=r.run_status,
                started_at=r.run_started_at,
                finished_at=r.run_finished_at,
                counts={c: int(getattr(r, f"run_{c}")) for c in _RUN_COUNTS},
                deletion_guard_tripped=bool(r.run_guard),
                error_code=r.run_error_code,
            )
            if r.run_id is not None
            else None
        )
        imp = imports.get(r.id)
        job = jobs.get(r.id)
        active = job is not None and job.status in ("queued", "running")
        has_import = imp is not None and imp.import_status is not None
        invalid = imp is not None and has_import and imp.check_error_code is not None
        rejected = (
            imp.latest_rejected_code
            if imp is not None
            and imp.latest_rejected_code is not None
            and (imp.import_created_at is None or imp.latest_at > imp.import_created_at)
            else None
        )
        result.append(
            AdminSource(
                id=r.id,
                key=str(r.key),
                name=r.name,
                connector_type=r.connector_type,
                status=r.status,
                account_scoped=r.account_scoped,
                default_sensitivity=r.default_sensitivity,
                authority=r.authority,
                documents=int(r.documents),
                last_synced_at=r.last_synced_at,
                last_succeeded_at=r.last_succeeded_at,
                acl_synced_at=r.acl_synced_at,
                last_run=run,
                failure_codes=codes.get(r.run_id, [])[:10] if r.run_id else [],
                job=job,
                managed_by=r.managed_by,
                version=int(r.version),
                import_status=imp.import_status if imp is not None else None,
                rejected_upload_code=rejected,
                next_action=(
                    "none"
                    if r.status == "deleting"
                    else next_action(
                        status=r.status,
                        managed_by=r.managed_by,
                        has_import=has_import,
                        import_invalid=bool(invalid),
                        check_unmapped=bool(imp is not None and imp.check_unmapped),
                        job_active=active,
                        latest_run=r.run_status,
                        unmapped_after_run=unmapped.get(r.id, 0),
                    )
                ),
            )
        )
    return result


async def admin_failures(
    session: AsyncSession, ctx: AuthorizationContext, source_id: uuid.UUID
) -> list[FailureView] | None:
    """The failed records of the source's latest run, for an administrator. None when
    the source does not exist in the organization."""
    org = ctx.organization_id
    exists = (
        await session.execute(
            select(SOURCES.c.id).where(SOURCES.c.organization_id == org, SOURCES.c.id == source_id)
        )
    ).scalar_one_or_none()
    if exists is None:
        return None
    run_id = (
        await session.execute(
            select(RUNS.c.id)
            .where(RUNS.c.organization_id == org, RUNS.c.source_id == source_id)
            .order_by(RUNS.c.started_at.desc(), RUNS.c.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if run_id is None:
        return []
    # The record's identifier only if the administrator can read that active document.
    readable = (
        select(DOCUMENTS.c.id)
        .where(
            DOCUMENTS.c.organization_id == org,
            DOCUMENTS.c.source_id == source_id,
            DOCUMENTS.c.external_id == FAILURES.c.external_id,
            DOCUMENTS.c.status == "active",
            readable_documents_predicate(ctx, DOCUMENTS),
        )
        .exists()
    )
    rows = await session.execute(
        select(
            FAILURES.c.stage,
            FAILURES.c.error_code,
            FAILURES.c.message_params,
            FAILURES.c.external_id,
            FAILURES.c.opaque_ref,
            FAILURES.c.created_at,
            and_(FAILURES.c.external_id.is_not(None), readable).label("readable"),
        )
        .where(FAILURES.c.organization_id == org, FAILURES.c.sync_run_id == run_id)
        .order_by(FAILURES.c.stage, FAILURES.c.error_code, FAILURES.c.opaque_ref)
        .limit(FAILURE_LIMIT)
    )
    views: list[FailureView] = []
    for stage, code, params, external_id, opaque_ref, created_at, is_readable in rows:
        shown = {k: v for k, v in (params or {}).items() if k in _SHOWN_PARAMS}
        if code == _UNMAPPED_ACCOUNT and "external_account_ref" in (params or {}):
            shown["external_account_ref"] = params["external_account_ref"]
        identifier = bool(is_readable)
        views.append(
            FailureView(
                stage=stage,
                code=code,
                params=shown,
                record_kind="identifier" if identifier else "opaque",
                record=str(external_id) if identifier else str(opaque_ref),
                created_at=created_at,
            )
        )
    return views


async def source_exists(
    session: AsyncSession, organization_id: uuid.UUID, source_id: uuid.UUID
) -> bool:
    found = await session.execute(
        text("SELECT 1 FROM sources WHERE organization_id = :o AND id = :s"),
        {"o": organization_id, "s": source_id},
    )
    return found.first() is not None
