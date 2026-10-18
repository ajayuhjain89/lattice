"""Sources operated in the product (Phase 12, P12.6; phase-12-plan C7-C9; ADR-017).

Every function reads through row-level security bound to the administrator's
organization; the caller checks the administrator capability first (E7). A source of
another organization is invisible, so it answers exactly as a random ID does.

* **Create**: an export source *managed by the organization* (connector type, key, name,
  default sensitivity and departments, authority, account scoping). It holds no
  documents and grants nobody anything; its documents get their access from their
  records and these defaults at sync, through the Phase 4 mapping (fail closed).
* **Edit**: name, defaults, authority, account scoping, channel sensitivities (chat, by
  channel ID). Nothing indexed changes until the next sync. A change that would make
  records readable to more people (a lower default, fewer or no departments, account
  scoping off, a lower channel level) is previewed as ``widens`` and must be
  acknowledged. Connector type and key never change; operator-managed sources stay the
  sources file's.
* **Pause / resume**: ``paused`` refuses requested syncs (the worker refuses a queued
  one too); content stays searchable. Resuming starts nothing.
* **Disconnect**: the existing purge (``ingestion.lifecycle``), from the product: a
  preview with counts, the key typed, a reason; refused while a sync runs; imports
  deleted; mappings go with the source (C10); accounts, traces and audit stay.

Each write is one fenced database function (migration 0022) and its ``source.*`` audit
event in the same transaction; the 0022 triggers refuse to commit one without the other.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final, cast

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.access.audit import record_event
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorDetail, LatticeError
from lattice.imports.limits import ImportLimits
from lattice.imports.store import ImportStore
from lattice.log import log_event
from lattice.sources.admin_models import (
    DepartmentRef,
    DisconnectPreview,
    DisconnectRequest,
    DisconnectResult,
    FailureCodeCount,
    ImportCheck,
    ImportOut,
    NewSourceRequest,
    NextAction,
    SettingChange,
    SourceChangePreview,
    SourceChangeRequest,
    SourceCreated,
    SourceDetail,
    SourceJob,
    SourceSettings,
    SourceVersion,
    UnresolvedReference,
    UploadLimits,
    VersionedRequest,
)

SOURCE_CREATED: Final = "source.created"
SOURCE_UPDATED: Final = "source.updated"
SOURCE_PAUSED: Final = "source.paused"
SOURCE_RESUMED: Final = "source.resumed"
SOURCE_DISCONNECTED: Final = "source.disconnected"
_FENCED: Final = "LA001"
_LEVEL_RANK: Final = {"internal": 0, "confidential": 1, "restricted": 2}
_log = logging.getLogger("lattice.sources")


# --- refusals ---------------------------------------------------------------------------


def _invalid(field: str, issue: str) -> LatticeError:
    return LatticeError(
        ErrorCode.VALIDATION_FAILED, details=[ErrorDetail(field=field, issue=issue)]
    )


def refusal(reason: str) -> LatticeError:
    """The API's answer to a fixed refusal reason (this module's or the database's)."""
    if reason == "not_administrator":
        return LatticeError(ErrorCode.PERMISSION_DENIED)
    if reason in ("source_not_found", "import_not_found"):
        return LatticeError(ErrorCode.NOT_FOUND)
    if reason == "source_changed":
        return LatticeError(
            ErrorCode.CONFLICT,
            "This source changed after you reviewed it.",
            details=[ErrorDetail(field="version", issue="source_changed")],
        )
    if reason == "key_taken":
        return LatticeError(
            ErrorCode.CONFLICT,
            "Another source of this organization already has this key.",
            details=[ErrorDetail(field="key", issue="taken")],
        )
    if reason in ("sync_running", "upload_in_progress", "job_active"):
        return LatticeError(
            ErrorCode.CONFLICT,
            {
                "sync_running": "A sync of this source is running. Try again when it finishes.",
                "upload_in_progress": "An upload to this source is already being received.",
                "job_active": "A check or sync of this source is already queued or running.",
            }[reason],
            details=[ErrorDetail(field="source", issue=reason)],
        )
    field = {
        "key_invalid": "key",
        "connector_invalid": "connector_type",
        "name_invalid": "name",
        "sensitivity_invalid": "default_sensitivity",
        "authority_invalid": "authority",
        "unknown_department": "default_departments",
        "channels_invalid": "channel_sensitivities",
        "format_invalid": "import",
    }.get(reason, "source" if reason in _SOURCE_STATE else "change")
    return _invalid(field, reason)


_SOURCE_STATE: Final = frozenset(
    {
        "operator_managed",
        "source_paused",
        "source_not_paused",
        "source_not_disconnected",
        "import_required",
        "import_invalid",
        "widening_not_acknowledged",
        "confirmation_mismatch",
        "no_change",
    }
)


def fenced_reason(exc: DBAPIError) -> str | None:
    """The fixed reason of a fenced-function refusal (SQLSTATE LA001), else None."""
    orig = exc.orig
    for candidate in (orig, getattr(orig, "__cause__", None)):
        if candidate is not None and getattr(candidate, "sqlstate", None) == _FENCED:
            message = getattr(candidate, "message", None) or str(candidate)
            return str(message).strip().splitlines()[0]
    return None


@asynccontextmanager
async def fenced() -> AsyncIterator[None]:
    """Translate the database's refusals into the API's errors."""
    try:
        yield
    except DBAPIError as exc:
        reason = fenced_reason(exc)
        if reason is None:
            raise
        raise refusal(reason) from None


# --- reads ------------------------------------------------------------------------------

_SOURCE_SQL = text(
    """
    SELECT s.id, s.key, s.connector_type, s.managed_by, s.status, s.version, s.name,
           s.config, s.default_sensitivity, s.authority, s.account_scoped, s.import_id,
           s.created_at, s.last_synced_at, s.last_succeeded_at,
           (SELECT count(*) FROM documents d WHERE d.organization_id = s.organization_id
              AND d.source_id = s.id AND d.status = 'active') AS documents
    FROM sources s WHERE s.organization_id = :o AND s.id = :s AND s.status <> 'deleting'
    """
)
_IMPORT_COLUMNS = (
    "id, status, format, size_bytes, sha256, entries, ignored_entries, expanded_bytes, "
    "rejected_code, created_at, received_at, ready_at, superseded_at, checked_at, "
    "check_result, check_error_code"
)
_JOB_SQL = text(
    "SELECT id, kind, mode, status, attempts, error_code, created_at, started_at, "
    "finished_at, sync_run_id, import_id FROM jobs WHERE organization_id = :o "
    "AND source_id = :s ORDER BY created_at DESC, id LIMIT 1"
)
_LATEST_RUN_SQL = text(
    "SELECT status, started_at FROM source_sync_runs WHERE organization_id = :o "
    "AND source_id = :s ORDER BY started_at DESC, id LIMIT 1"
)
_MAPPED_SQL = text(
    "SELECT external_ref FROM account_external_refs WHERE organization_id = :o "
    "AND source_id = :s AND account_id IS NOT NULL AND external_ref = ANY(:r)"
)
# References the latest run could not map and that are still unmapped now.
_RUN_UNMAPPED_SQL = text(
    """
    SELECT count(DISTINCT f.message_params ->> 'external_account_ref')
    FROM sync_record_failures f
    WHERE f.organization_id = :o AND f.sync_run_id = (
        SELECT r.id FROM source_sync_runs r WHERE r.organization_id = :o AND r.source_id = :s
        ORDER BY r.started_at DESC, r.id LIMIT 1)
      AND f.error_code = 'access_unmappable:account'
      AND f.message_params ? 'external_account_ref'
      AND NOT EXISTS (
        SELECT 1 FROM account_external_refs m WHERE m.organization_id = :o
          AND m.source_id = :s AND m.external_ref = f.message_params ->> 'external_account_ref'
          AND m.account_id IS NOT NULL)
    """
)


def upload_limits(limits: ImportLimits, connector_type: str, available: bool) -> UploadLimits:
    return UploadLimits(
        available=available,
        formats=["zip", "jsonl"] if connector_type == "ticketing" else ["zip"],
        max_upload_bytes=limits.max_upload_bytes,
        max_expanded_bytes=limits.max_expanded_bytes,
        max_file_bytes=limits.max_file_bytes,
        max_entries=limits.max_entries,
    )


async def _departments(
    session: AsyncSession, organization_id: uuid.UUID, keys: list[str]
) -> list[DepartmentRef]:
    if not keys:
        return []
    rows = await session.execute(
        text("SELECT key, name FROM departments WHERE organization_id = :o AND key = ANY(:k)"),
        {"o": organization_id, "k": keys},
    )
    names = {str(k): n for k, n in rows}
    return [DepartmentRef(key=k, name=names.get(k)) for k in sorted(keys)]


def _channel_levels(config: dict[str, Any]) -> dict[str, str]:
    channels = config.get("channels") or {}
    return {
        cid: str(c["sensitivity"])
        for cid, c in sorted(channels.items())
        if isinstance(c, dict) and c.get("sensitivity")
    }


async def _settings(session: AsyncSession, organization_id: uuid.UUID, row: Any) -> SourceSettings:
    config: dict[str, Any] = dict(row.config or {})
    return SourceSettings(
        name=row.name,
        default_sensitivity=row.default_sensitivity,
        default_departments=await _departments(
            session, organization_id, [str(d) for d in config.get("default_departments") or []]
        ),
        authority=row.authority,
        account_scoped=bool(row.account_scoped),
        channel_sensitivities=_channel_levels(config)
        if row.connector_type == "chat_export"
        else {},
    )


async def _check(
    session: AsyncSession, organization_id: uuid.UUID, source_id: uuid.UUID, row: Any
) -> ImportCheck | None:
    if row.checked_at is None:
        return None
    result: dict[str, Any] = dict(row.check_result or {})
    refs = [r for r in result.get("unresolved_references") or [] if isinstance(r, dict)]
    mapped: set[str] = set()
    if refs:
        found = await session.execute(
            _MAPPED_SQL,
            {"o": organization_id, "s": source_id, "r": [str(x["ref"]) for x in refs]},
        )
        values: list[object] = list(found.scalars())
        mapped = {str(value) for value in values}
    codes = result.get("failure_codes") or {}
    return ImportCheck(
        checked_at=row.checked_at,
        error_code=row.check_error_code,
        records_seen=int(result.get("records_seen", 0)),
        would_create=int(result.get("would_create", 0)),
        would_update=int(result.get("would_update", 0)),
        would_update_access=int(result.get("would_update_access", 0)),
        unchanged=int(result.get("unchanged", 0)),
        invalid_records=int(result.get("invalid_records", 0)),
        would_quarantine=int(result.get("would_quarantine", 0)),
        would_delete=int(result.get("would_delete", 0)),
        deletion_guard=result.get("deletion_guard"),
        failure_codes=[
            FailureCodeCount(code=c, count=int(n))
            for c, n in sorted(codes.items(), key=lambda item: (-int(item[1]), item[0]))
        ],
        unresolved_references=[
            UnresolvedReference(
                external_ref=str(r["ref"]),
                records=int(r["records"]),
                state="mapped" if str(r["ref"]) in mapped else "unmapped",
            )
            for r in refs
        ],
        unresolved_total=int(result.get("unresolved_total", len(refs))),
    )


async def _import(
    session: AsyncSession,
    organization_id: uuid.UUID,
    source_id: uuid.UUID,
    row: Any,
    *,
    current: bool,
) -> ImportOut:
    return ImportOut(
        id=row.id,
        status=row.status,
        format=row.format,
        current=current,
        size_bytes=row.size_bytes,
        sha256=None if row.sha256 is None else bytes(row.sha256).hex(),
        entries=row.entries,
        ignored_entries=row.ignored_entries,
        expanded_bytes=row.expanded_bytes,
        rejected_code=row.rejected_code,
        created_at=row.created_at,
        received_at=row.received_at,
        ready_at=row.ready_at,
        superseded_at=row.superseded_at,
        check=await _check(session, organization_id, source_id, row) if current else None,
    )


def sync_blocker(
    *, status: str, managed_by: str, has_import: bool, import_invalid: bool, other_job: bool
) -> str | None:
    """Why a sync cannot be requested now (the sync request refuses the same), else None."""
    if status == "paused":
        return "source_paused"
    if other_job:
        return "job_active"
    if managed_by == "organization":
        if not has_import:
            return "import_required"
        if import_invalid:
            return "import_invalid"
    return None


def next_action(
    *,
    status: str,
    managed_by: str,
    has_import: bool,
    import_invalid: bool,
    check_unmapped: bool,
    job_active: bool,
    latest_run: str | None,
    unmapped_after_run: int,
) -> NextAction:
    """The one thing to do next, from backend facts only (the register and the record)."""
    if status == "paused":
        return "resume"
    if job_active:
        return "wait"
    if managed_by == "organization":
        if not has_import:
            return "upload_import"
        if import_invalid:
            return "replace_import"
        if check_unmapped and latest_run is None:
            return "map_references"
    if unmapped_after_run:
        return "map_references"
    if latest_run is None:
        return "start_sync"
    if latest_run == "failed":
        return "retry_sync"
    return "none"


async def source_detail(
    session: AsyncSession,
    organization_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    limits: ImportLimits,
    store_available: bool,
) -> SourceDetail | None:
    row = (await session.execute(_SOURCE_SQL, {"o": organization_id, "s": source_id})).one_or_none()
    if row is None:
        return None
    imports = (
        await session.execute(
            text(
                f"SELECT {_IMPORT_COLUMNS} FROM source_imports "  # noqa: S608 - fixed columns
                "WHERE organization_id = :o AND source_id = :s "
                "AND (id = :c OR id = (SELECT id FROM source_imports WHERE organization_id = :o "
                "AND source_id = :s ORDER BY created_at DESC, id LIMIT 1))"
            ),
            {"o": organization_id, "s": source_id, "c": row.import_id},
        )
    ).all()
    current_row = next((i for i in imports if i.id == row.import_id), None)
    current = (
        await _import(session, organization_id, source_id, current_row, current=True)
        if current_row is not None
        else None
    )
    latest_row = max(imports, key=lambda i: (i.created_at, str(i.id)), default=None)
    latest = (
        await _import(session, organization_id, source_id, latest_row, current=False)
        if latest_row is not None and latest_row.id != row.import_id
        else None
    )
    job_row = (
        await session.execute(_JOB_SQL, {"o": organization_id, "s": source_id})
    ).one_or_none()
    job = None if job_row is None else SourceJob.model_validate(job_row._asdict())
    run = (
        await session.execute(_LATEST_RUN_SQL, {"o": organization_id, "s": source_id})
    ).one_or_none()
    unmapped = int(
        (
            await session.execute(_RUN_UNMAPPED_SQL, {"o": organization_id, "s": source_id})
        ).scalar_one()
    )
    active = job is not None and job.status in ("queued", "running")
    invalid = (
        current is not None and current.check is not None and current.check.error_code is not None
    )
    blocker = sync_blocker(
        status=row.status,
        managed_by=row.managed_by,
        has_import=current is not None,
        import_invalid=invalid,
        other_job=active and job is not None and job.kind != "source_sync",
    )
    return SourceDetail(
        id=row.id,
        key=str(row.key),
        connector_type=row.connector_type,
        managed_by=row.managed_by,
        status=row.status,
        version=int(row.version),
        settings=await _settings(session, organization_id, row),
        documents=int(row.documents),
        created_at=row.created_at,
        last_synced_at=row.last_synced_at,
        last_succeeded_at=row.last_succeeded_at,
        current_import=current,
        latest_upload=latest,
        job=job,
        next_action=next_action(
            status=row.status,
            managed_by=row.managed_by,
            has_import=current is not None,
            import_invalid=invalid,
            check_unmapped=current is not None
            and current.check is not None
            and any(r.state == "unmapped" for r in current.check.unresolved_references),
            job_active=active,
            latest_run=None if run is None else run.status,
            unmapped_after_run=unmapped,
        ),
        can_sync=blocker is None,
        sync_blocked_by=blocker,
        upload=upload_limits(limits, row.connector_type, store_available),
    )


# --- create and edit ------------------------------------------------------------------------


def _actor(membership_id: uuid.UUID) -> str:
    return f"member:{membership_id}"


async def create_source(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    request: NewSourceRequest,
) -> SourceCreated:
    async with fenced(), db.tenant_transaction(organization_id) as session:
        source_id: uuid.UUID = (
            await session.execute(
                text(
                    "SELECT lattice_create_source(:a, :k, :t, :n, :s, CAST(:d AS text[]), :au, :sc)"
                ),
                {
                    "a": actor_membership_id,
                    "k": request.key,
                    "t": request.connector_type,
                    "n": request.name,
                    "s": request.default_sensitivity,
                    "d": request.default_departments,
                    "au": request.authority,
                    "sc": request.account_scoped,
                },
            )
        ).scalar_one()
        await record_event(
            session,
            organization_id,
            actor=_actor(actor_membership_id),
            action=SOURCE_CREATED,
            target={"source_id": str(source_id)},
            before=None,
            after={
                "key": request.key,
                "name": request.name,
                "connector_type": request.connector_type,
                "default_sensitivity": request.default_sensitivity,
                "default_departments": request.default_departments,
                "authority": request.authority,
                "account_scoped": request.account_scoped,
                "managed_by": "organization",
            },
            reason=request.reason,
        )
    return SourceCreated(id=source_id, version=1)


def _snapshot(row: Any) -> dict[str, Any]:
    config: dict[str, Any] = dict(row.config or {})
    return {
        "name": row.name,
        "default_sensitivity": row.default_sensitivity,
        "default_departments": sorted(str(d) for d in config.get("default_departments") or []),
        "authority": row.authority,
        "account_scoped": bool(row.account_scoped),
        "channel_sensitivities": _channel_levels(config),
    }


def _departments_effect(before: list[str], after: list[str]) -> str:
    old, new = set(before), set(after)
    if old == new:
        return "neutral"
    if not old:
        return "narrows"  # no restriction -> a restriction
    if not new or not new <= old:
        return "widens"  # restriction removed, or another department may now read
    return "narrows"


def _level_effect(before: str, after: str) -> str:
    rank_before, rank_after = _LEVEL_RANK[before], _LEVEL_RANK[after]
    if rank_before == rank_after:
        return "neutral"
    return "widens" if rank_after < rank_before else "narrows"


def _fmt(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(value) or "none"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in sorted(value.items())) or "none"
    return str(value)


def changes(before: dict[str, Any], change: dict[str, Any]) -> list[SettingChange]:
    """Each changed setting and whether it makes records readable to more people at the
    next sync (records that state their own access are unaffected by defaults)."""
    after = {**before, **change}
    found: list[SettingChange] = []
    for field in (
        "name",
        "authority",
        "default_sensitivity",
        "default_departments",
        "account_scoped",
        "channel_sensitivities",
    ):
        if before.get(field) == after.get(field):
            continue
        effect = "neutral"
        if field == "default_sensitivity":
            effect = _level_effect(before[field], after[field])
        elif field == "default_departments":
            effect = _departments_effect(before[field], after[field])
        elif field == "account_scoped":
            effect = "widens" if before[field] and not after[field] else "narrows"
        elif field == "channel_sensitivities":
            effects = set()
            for channel in set(before[field]) | set(after[field]):
                old = before[field].get(channel, before["default_sensitivity"])
                new = after[field].get(channel, after["default_sensitivity"])
                effects.add(_level_effect(old, new))
            effect = (
                "widens"
                if "widens" in effects
                else "narrows"
                if "narrows" in effects
                else "neutral"
            )
        found.append(
            SettingChange(
                field=field,
                before=_fmt(before.get(field)),
                after=_fmt(after.get(field)),
                effect=cast(Any, effect),
            )
        )
    return found


async def _row_for_change(
    session: AsyncSession, organization_id: uuid.UUID, source_id: uuid.UUID
) -> Any:
    row = (await session.execute(_SOURCE_SQL, {"o": organization_id, "s": source_id})).one_or_none()
    if row is None:
        raise refusal("source_not_found")
    return row


async def preview_change(
    session: AsyncSession,
    organization_id: uuid.UUID,
    source_id: uuid.UUID,
    request: SourceChangeRequest,
) -> SourceChangePreview:
    row = await _row_for_change(session, organization_id, source_id)
    if row.managed_by != "organization":
        raise refusal("operator_managed")
    if request.channel_sensitivities is not None and row.connector_type != "chat_export":
        raise refusal("channels_invalid")
    found = changes(_snapshot(row), request.change())
    return SourceChangePreview(changes=found, widens=any(c.effect == "widens" for c in found))


async def update_source(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    source_id: uuid.UUID,
    request: SourceChangeRequest,
) -> SourceVersion:
    change = request.change()
    async with fenced(), db.tenant_transaction(organization_id) as session:
        row = await _row_for_change(session, organization_id, source_id)
        before = _snapshot(row)
        found = changes(before, change)
        if any(c.effect == "widens" for c in found) and not request.acknowledge_widening:
            raise refusal("widening_not_acknowledged")
        version: int = (
            await session.execute(
                text("SELECT lattice_update_source(:a, :s, :v, CAST(:c AS jsonb))"),
                {
                    "a": actor_membership_id,
                    "s": source_id,
                    "v": request.version,
                    "c": _json(change),
                },
            )
        ).scalar_one()
        await record_event(
            session,
            organization_id,
            actor=_actor(actor_membership_id),
            action=SOURCE_UPDATED,
            target={"source_id": str(source_id)},
            before=before,
            after={**before, **change, "widens": any(c.effect == "widens" for c in found)},
            reason=request.reason,
        )
    return SourceVersion(version=version)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


async def set_paused(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    source_id: uuid.UUID,
    request: VersionedRequest,
    paused: bool,
) -> SourceVersion:
    async with fenced(), db.tenant_transaction(organization_id) as session:
        row = await _row_for_change(session, organization_id, source_id)
        if (row.status == "paused") == paused:
            raise refusal("source_paused" if paused else "source_not_paused")
        version: int = (
            await session.execute(
                text("SELECT lattice_set_source_paused(:a, :s, :v, :p)"),
                {"a": actor_membership_id, "s": source_id, "v": request.version, "p": paused},
            )
        ).scalar_one()
        await record_event(
            session,
            organization_id,
            actor=_actor(actor_membership_id),
            action=SOURCE_PAUSED if paused else SOURCE_RESUMED,
            target={"source_id": str(source_id)},
            before={"status": row.status},
            after={"status": "paused" if paused else "active"},
            reason=request.reason,
        )
    return SourceVersion(version=version)


# --- disconnect -----------------------------------------------------------------------------


async def disconnect_preview(
    session: AsyncSession, organization_id: uuid.UUID, source_id: uuid.UUID
) -> DisconnectPreview:
    row = await _row_for_change(session, organization_id, source_id)
    counts = (
        await session.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM documents WHERE organization_id = :o AND source_id = :s), "
                "(SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id "
                " WHERE d.organization_id = :o AND d.source_id = :s), "
                "(SELECT count(*) FROM account_external_refs WHERE organization_id = :o "
                " AND source_id = :s), "
                "(SELECT count(*) FROM source_imports WHERE organization_id = :o "
                " AND source_id = :s AND status <> 'deleted'), "
                "(SELECT count(*) FROM jobs WHERE organization_id = :o AND source_id = :s "
                " AND status = 'queued'), "
                "EXISTS (SELECT 1 FROM jobs WHERE organization_id = :o AND source_id = :s "
                " AND status = 'running')"
            ),
            {"o": organization_id, "s": source_id},
        )
    ).one()
    return DisconnectPreview(
        documents=int(counts[0]),
        chunks=int(counts[1]),
        account_mappings=int(counts[2]),
        imports=int(counts[3]),
        queued_jobs=int(counts[4]),
        sync_running=bool(counts[5]),
        operator_managed=row.managed_by == "operator",
    )


async def disconnect(
    db: Database,
    store: ImportStore | None,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    source_id: uuid.UUID,
    request: DisconnectRequest,
) -> DisconnectResult:
    """Step 1 (one transaction, audited): the source becomes ``deleting`` and every
    document ``deleted`` -- from the next request nothing of it is retrievable. Step 2:
    the purge. A purge that fails leaves the source ``deleting`` (invisible, unsyncable);
    the operator's ``lattice source disconnect`` or the retention resumes it."""
    async with fenced(), db.tenant_transaction(organization_id) as session:
        row = (
            await session.execute(
                text(
                    "SELECT key, name, status FROM sources WHERE organization_id = :o AND id = :s"
                ),
                {"o": organization_id, "s": source_id},
            )
        ).one_or_none()
        if row is None:
            raise refusal("source_not_found")
        if request.confirm_key != str(row.key):
            raise refusal("confirmation_mismatch")
        imports: list[uuid.UUID] = list(
            (
                await session.execute(
                    text(
                        # Every import's files go (removing absent ones is a no-op).
                        "SELECT id FROM source_imports WHERE organization_id = :o "
                        "AND source_id = :s"
                    ),
                    {"o": organization_id, "s": source_id},
                )
            ).scalars()
        )
        counts: dict[str, int] = {
            "documents": 0,
            "chunks": 0,
            "account_mappings": 0,
            "imports": 0,
            "cancelled_jobs": 0,
        }
        if row.status != "deleting":
            found_counts: dict[str, Any] = (
                await session.execute(
                    text("SELECT lattice_disconnect_source(:a, :s, :v)"),
                    {"a": actor_membership_id, "s": source_id, "v": request.version},
                )
            ).scalar_one()
            counts = {str(k): int(v) for k, v in dict(found_counts).items()}
            await record_event(
                session,
                organization_id,
                actor=_actor(actor_membership_id),
                action=SOURCE_DISCONNECTED,
                target={"source_id": str(source_id)},
                before={"status": row.status, "key": str(row.key), "name": row.name},
                after={"status": "deleting", **counts},
                reason=request.reason,
            )
        # A source already ``deleting`` (an interrupted purge): the purge resumes.
    purged = False
    try:
        async with fenced(), db.tenant_transaction(organization_id) as session:
            purged = bool(
                (
                    await session.execute(
                        text("SELECT lattice_purge_source(:a, :s)"),
                        {"a": actor_membership_id, "s": source_id},
                    )
                ).scalar_one()
            )
    except Exception as exc:  # the source stays deleting: invisible, resumable
        log_event(
            _log,
            logging.WARNING,
            "sources.purge_failed",
            source_id=str(source_id),
            error_type=type(exc).__name__,
        )
    if store is not None:
        for import_id in imports:
            store.delete(organization_id, import_id)  # retention removes what this misses
    return DisconnectResult(
        documents=int(counts["documents"]),
        chunks=int(counts["chunks"]),
        account_mappings=int(counts["account_mappings"]),
        imports=int(counts["imports"]),
        cancelled_jobs=int(counts["cancelled_jobs"]),
        purged=purged,
    )
