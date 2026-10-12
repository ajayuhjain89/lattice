"""Accounts and external-reference mappings operated in the product (Phase 12, P12.3;
phase-12-plan C10; ADR-017).

Every function reads through row-level security bound to the administrator's
organization; the caller checks the administrator capability first (E7). An account or
source of another organization is invisible, so it answers exactly as a random ID does.

* **Accounts**: create (key, name, aliases, business status), edit the name, aliases and
  business status, archive and reactivate. Direct writes of named columns by the API
  role (migration 0018) except the lifecycle, which only the fenced
  ``lattice_set_account_archived`` sets. Archiving keeps everything -- documents,
  assignments, traces, mappings -- and refuses new mappings; an archived record is
  read-only until reactivated. Creating an account grants nobody anything.
* **Mappings**: map, remap and unmap one reference through the fenced
  ``lattice_set_account_ref`` with the mapping the administrator reviewed as
  ``expected`` (409 when it moved on). Nothing indexed changes until the source syncs:
  then mapped records are indexed under (or move to) the account, and unmapped ones are
  quarantined (account-scoped source) or lose the account (otherwise) -- the Phase 4
  rules, deterministic, never an ad-hoc rewrite of stored evidence.
* **Unmapped references**: from the latest run's ``access_unmappable:account`` failures,
  grouped by reference -- the exact reference and a count, nothing from the records.

Each write and its audit event share a transaction; the 0018 triggers refuse to commit
one without the other. Changing a mapping changes which account a record belongs to; it
never bypasses the document's own access (ACLs, groups, sensitivity, the tenant).
"""

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final, cast

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.access.audit import record_event
from lattice.accounts.models import (
    AccountChangeRequest,
    AccountDetail,
    AccountLifecycleRequest,
    AccountMapping,
    AccountRef,
    AccountsPage,
    AccountSummary,
    MappingEffect,
    MappingOperation,
    MappingPreview,
    MappingPreviewRequest,
    MappingResult,
    MapRequest,
    NewAccountRequest,
    ReferenceKey,
    RefKind,
    RemapRequest,
    SourceRef,
    UnmappedReference,
    UnmappedReferences,
    UnmapRequest,
)
from lattice.accounts.refs import MAPPING_CHANGED, mapping_target, ref_kind_for
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorDetail, LatticeError

MAX_ACCOUNTS_PAGE: Final = 100
MAX_UNMAPPED: Final = 500
ACCOUNT_CREATED: Final = "account.created"
ACCOUNT_UPDATED: Final = "account.updated"
ACCOUNT_ARCHIVED: Final = "account.archived"
ACCOUNT_REACTIVATED: Final = "account.reactivated"
_FENCED_SQLSTATE: Final = "LA001"
_TICKETING_REF: Final = re.compile(r"0|[1-9][0-9]{0,18}")


# --- refusals ---------------------------------------------------------------------------


def _invalid(field: str, issue: str) -> LatticeError:
    return LatticeError(
        ErrorCode.VALIDATION_FAILED, details=[ErrorDetail(field=field, issue=issue)]
    )


def refusal(reason: str) -> LatticeError:
    """The API's answer to a fixed refusal reason (this module's or the database's)."""
    if reason == "not_administrator":
        return LatticeError(ErrorCode.PERMISSION_DENIED)
    if reason == "account_not_found":
        return LatticeError(ErrorCode.NOT_FOUND)
    if reason in ("mapping_changed", "already_mapped"):
        return LatticeError(
            ErrorCode.CONFLICT,
            "This reference's mapping changed after you reviewed it.",
            details=[ErrorDetail(field="mapping", issue=reason)],
        )
    if reason == "key_taken":
        return LatticeError(
            ErrorCode.CONFLICT,
            "Another account of this organization already has this key.",
            details=[ErrorDetail(field="key", issue="taken")],
        )
    field = {
        "unknown_account": "account_id",
        "account_archived": "account_id",
        "unknown_source": "source_id",
        "source_not_found": "source_id",
        "ref_kind_mismatch": "ref_kind",
        "reference_invalid": "external_ref",
        "reference_immutable": "external_ref",
        "archived": "account",
        "no_change": "change",
        "change_invalid": "change",
    }.get(reason, "change")
    issue = {"source_not_found": "unknown_source"}.get(reason, reason)
    return _invalid(field, issue)


def _sqlstate_reason(exc: DBAPIError, sqlstate: str) -> tuple[str, str] | None:
    """(message, constraint) of a database error with ``sqlstate``, else None."""
    orig = exc.orig
    candidates = [c for c in (orig, getattr(orig, "__cause__", None)) if c is not None]
    if not any(getattr(c, "sqlstate", None) == sqlstate for c in candidates):
        return None
    message = next(
        (getattr(c, "message", None) for c in candidates if getattr(c, "message", None)), None
    )
    constraint = next(
        (
            str(getattr(c, "constraint_name", ""))
            for c in candidates
            if getattr(c, "constraint_name", None)
        ),
        "",
    )
    return str(message or orig).strip().splitlines()[0], constraint


@asynccontextmanager
async def _fenced(*, body_account: bool = False) -> AsyncIterator[None]:
    """Translate the database's refusals into the API's errors. ``body_account``: the
    account was named in the body (an unknown one is a validation issue, not a 404)."""
    try:
        yield
    except DBAPIError as exc:
        fenced = _sqlstate_reason(exc, _FENCED_SQLSTATE)
        if fenced is not None:
            reason = fenced[0]
            if reason == "account_not_found" and body_account:
                reason = "unknown_account"
            raise refusal(reason) from None
        unique = _sqlstate_reason(exc, "23505")
        if unique is not None and "key" in unique[1]:
            raise refusal("key_taken") from None
        raise


# --- reads ------------------------------------------------------------------------------

_ACCOUNT_COLUMNS = """
    a.id, a.key, a.name, a.aliases, a.status, a.archived_at, a.created_at, a.updated_at,
    (SELECT count(*) FROM account_external_refs r
      WHERE r.organization_id = a.organization_id AND r.account_id = a.id) AS refs,
    (SELECT count(*) FROM account_assignments aa
      WHERE aa.organization_id = a.organization_id AND aa.account_id = a.id) AS assigned
"""


def _summary_values(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "key": str(row.key),
        "name": row.name,
        "aliases": list(row.aliases),
        "status": row.status,
        "lifecycle": "archived" if row.archived_at is not None else "active",
        "archived_at": row.archived_at,
        "references": int(row.refs),
        "assigned_members": int(row.assigned),
    }


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def accounts_page(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    q: str | None,
    lifecycle: str | None,
    limit: int,
    after: uuid.UUID | None,
) -> AccountsPage:
    """Accounts by name (case-insensitive), then ID; ``q`` matches the name, key or an
    alias. ``after``: the last ID of the previous page (unknown here: from the top)."""
    where = ["a.organization_id = :o"]
    params: dict[str, Any] = {"o": organization_id, "n": min(limit, MAX_ACCOUNTS_PAGE) + 1}
    if q:
        where.append(
            "(a.name ILIKE :q OR a.key::text ILIKE :q OR array_to_string(a.aliases, ' ') ILIKE :q)"
        )
        params["q"] = _like(q)
    if lifecycle == "active":
        where.append("a.archived_at IS NULL")
    elif lifecycle == "archived":
        where.append("a.archived_at IS NOT NULL")
    if (
        after is not None
        and (
            await session.execute(
                text("SELECT 1 FROM accounts WHERE organization_id = :o AND id = :after"),
                {"o": organization_id, "after": after},
            )
        ).first()
    ):
        where.append(
            "(lower(a.name), a.id) > (SELECT lower(b.name), b.id FROM accounts b "
            "WHERE b.organization_id = :o AND b.id = :after)"
        )
        params["after"] = after
    rows = (
        await session.execute(
            text(
                f"SELECT {_ACCOUNT_COLUMNS} FROM accounts a WHERE {' AND '.join(where)} "  # noqa: S608 - fixed fragments
                "ORDER BY lower(a.name), a.id LIMIT :n"
            ),
            params,
        )
    ).all()
    size = params["n"] - 1
    more = len(rows) > size
    rows = rows[:size]
    return AccountsPage(
        items=[AccountSummary(**_summary_values(r)) for r in rows],
        next_after=rows[-1].id if more and rows else None,
    )


async def _account_row(
    session: AsyncSession, organization_id: uuid.UUID, account_id: uuid.UUID
) -> Any:
    return (
        await session.execute(
            text(
                f"SELECT {_ACCOUNT_COLUMNS} FROM accounts a "  # noqa: S608 - fixed fragments
                "WHERE a.organization_id = :o AND a.id = :a"
            ),
            {"o": organization_id, "a": account_id},
        )
    ).one_or_none()


async def account_detail(
    session: AsyncSession, organization_id: uuid.UUID, account_id: uuid.UUID
) -> AccountDetail | None:
    row = await _account_row(session, organization_id, account_id)
    if row is None:
        return None
    mappings = await session.execute(
        text(
            "SELECT s.id AS source_id, s.name AS source_name, s.connector_type, r.ref_kind, "
            "r.external_ref, r.created_at, r.updated_at FROM account_external_refs r "
            "JOIN sources s ON s.organization_id = r.organization_id AND s.id = r.source_id "
            "WHERE r.organization_id = :o AND r.account_id = :a "
            "ORDER BY s.name, s.id, r.ref_kind, r.external_ref"
        ),
        {"o": organization_id, "a": account_id},
    )
    return AccountDetail(
        **_summary_values(row),
        version=row.updated_at,
        created_at=row.created_at,
        mappings=[
            AccountMapping(
                source=SourceRef(
                    id=m.source_id, name=m.source_name, connector_type=m.connector_type
                ),
                ref_kind=m.ref_kind,
                external_ref=m.external_ref,
                created_at=m.created_at,
                updated_at=m.updated_at,
            )
            for m in mappings
        ],
    )


def _snapshot(row: Any) -> dict[str, Any]:
    """An account as audited: its administrable metadata, never documents or members."""
    return {
        "key": str(row.key),
        "name": row.name,
        "aliases": list(row.aliases),
        "status": row.status,
        "lifecycle": "archived" if row.archived_at is not None else "active",
    }


async def _account_ref(
    session: AsyncSession, organization_id: uuid.UUID, account_id: uuid.UUID | None
) -> AccountRef | None:
    if account_id is None:
        return None
    row = (
        await session.execute(
            text(
                "SELECT id, key, name, archived_at FROM accounts "
                "WHERE organization_id = :o AND id = :a"
            ),
            {"o": organization_id, "a": account_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return AccountRef(
        id=row.id,
        key=str(row.key),
        name=row.name,
        lifecycle="archived" if row.archived_at is not None else "active",
    )


# --- account writes ------------------------------------------------------------------------


async def create_account(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    request: NewAccountRequest,
) -> uuid.UUID:
    """A new canonical account. Nobody is assigned it: creating one grants nothing."""
    async with _fenced(), db.tenant_transaction(organization_id) as session:
        account_id: uuid.UUID = (
            await session.execute(
                text(
                    "INSERT INTO accounts (organization_id, key, name, aliases, status) "
                    "VALUES (:o, :k, :n, :al, :st) RETURNING id"
                ),
                {
                    "o": organization_id,
                    "k": request.key,
                    "n": request.name,
                    "al": request.aliases,
                    "st": request.status,
                },
            )
        ).scalar_one()
        await record_event(
            session,
            organization_id,
            actor=f"member:{actor_membership_id}",
            action=ACCOUNT_CREATED,
            target={"account_id": str(account_id)},
            before=None,
            after={
                "key": request.key,
                "name": request.name,
                "aliases": request.aliases,
                "status": request.status,
                "lifecycle": "active",
            },
            reason=request.reason,
        )
    return account_id


async def _locked_for_change(
    session: AsyncSession, organization_id: uuid.UUID, account_id: uuid.UUID, version: Any
) -> Any:
    row = await _account_row(session, organization_id, account_id)
    if row is None:
        raise refusal("account_not_found")
    if row.updated_at != version:
        raise LatticeError(ErrorCode.CONFLICT)
    return row


async def update_account(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    account_id: uuid.UUID,
    request: AccountChangeRequest,
) -> None:
    async with _fenced(), db.tenant_transaction(organization_id) as session:
        row = await _account_row(session, organization_id, account_id)
        if row is None:
            raise refusal("account_not_found")
        if row.archived_at is not None:
            raise refusal("archived")
        # The archived check above, then the row lock (the restrictive update policy
        # hides archived rows from FOR UPDATE).
        await session.execute(
            text("SELECT 1 FROM accounts WHERE organization_id = :o AND id = :a FOR UPDATE"),
            {"o": organization_id, "a": account_id},
        )
        row = await _locked_for_change(session, organization_id, account_id, request.version)
        if row.archived_at is not None:
            raise refusal("archived")
        before = _snapshot(row)
        after = {
            **before,
            **{
                k: v
                for k, v in (
                    ("name", request.name),
                    ("aliases", request.aliases),
                    ("status", request.status),
                )
                if v is not None
            },
        }
        if after == before:
            raise refusal("no_change")
        await session.execute(
            text(
                "UPDATE accounts SET name = :n, aliases = :al, status = :st "
                "WHERE organization_id = :o AND id = :a"
            ),
            {
                "o": organization_id,
                "a": account_id,
                "n": after["name"],
                "al": after["aliases"],
                "st": after["status"],
            },
        )
        await record_event(
            session,
            organization_id,
            actor=f"member:{actor_membership_id}",
            action=ACCOUNT_UPDATED,
            target={"account_id": str(account_id)},
            before=before,
            after=after,
            reason=request.reason,
        )


async def set_archived(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    account_id: uuid.UUID,
    archived: bool,
    request: AccountLifecycleRequest,
) -> None:
    """Archive or reactivate the same account (never a new one). Its documents,
    assignments, traces and mappings are untouched either way."""
    async with _fenced(), db.tenant_transaction(organization_id) as session:
        row = await _locked_for_change(session, organization_id, account_id, request.version)
        if (row.archived_at is not None) == archived:
            raise refusal("no_change")
        changed: bool = (
            await session.execute(
                text("SELECT lattice_set_account_archived(:actor, :a, :archived)"),
                {"actor": actor_membership_id, "a": account_id, "archived": archived},
            )
        ).scalar_one()
        if not changed:  # pragma: no cover - a concurrent change; the function locked it
            raise LatticeError(ErrorCode.CONFLICT)
        await record_event(
            session,
            organization_id,
            actor=f"member:{actor_membership_id}",
            action=ACCOUNT_ARCHIVED if archived else ACCOUNT_REACTIVATED,
            target={"account_id": str(account_id)},
            before={"lifecycle": "archived" if not archived else "active"},
            after={"lifecycle": "archived" if archived else "active"},
            reason=request.reason,
        )


# --- references ----------------------------------------------------------------------------


async def _source(
    session: AsyncSession, organization_id: uuid.UUID, key: ReferenceKey
) -> tuple[SourceRef, bool]:
    row = (
        await session.execute(
            text(
                "SELECT id, name, connector_type, account_scoped FROM sources "
                "WHERE organization_id = :o AND id = :s AND status <> 'deleting'"
            ),
            {"o": organization_id, "s": key.source_id},
        )
    ).one_or_none()
    if row is None:
        raise refusal("unknown_source")
    if ref_kind_for(row.connector_type) != key.ref_kind:
        raise refusal("ref_kind_mismatch")
    # The connector reports a ticketing organization ID in canonical decimal (0018 checks
    # it too): "0360" or "acme" could never match a record.
    if key.ref_kind == "ticketing_organization" and not _TICKETING_REF.fullmatch(key.external_ref):
        raise refusal("reference_invalid")
    source = SourceRef(id=row.id, name=row.name, connector_type=row.connector_type)
    return source, bool(row.account_scoped)


async def _current(
    session: AsyncSession, organization_id: uuid.UUID, key: ReferenceKey
) -> uuid.UUID | None:
    value: uuid.UUID | None = (
        await session.execute(
            text(
                "SELECT account_id FROM account_external_refs WHERE organization_id = :o "
                "AND source_id = :s AND ref_kind = :k AND external_ref = :r"
            ),
            {"o": organization_id, "s": key.source_id, "k": key.ref_kind, "r": key.external_ref},
        )
    ).scalar_one_or_none()
    return value


_LATEST_RUNS = """
    WITH latest AS (
      SELECT DISTINCT ON (r.source_id) r.source_id, r.id, r.started_at
      FROM source_sync_runs r JOIN sources s
        ON s.organization_id = r.organization_id AND s.id = r.source_id
      WHERE r.organization_id = :o AND s.status <> 'deleting'
        AND s.connector_type IN ('ticketing', 'chat_export')
      ORDER BY r.source_id, r.started_at DESC, r.id
    ), waiting AS (
      SELECT l.source_id, l.started_at, f.message_params ->> 'external_account_ref' AS ref,
             count(*) AS records
      FROM sync_record_failures f JOIN latest l ON l.id = f.sync_run_id
      WHERE f.organization_id = :o AND f.error_code = 'access_unmappable:account'
        AND f.message_params ? 'external_account_ref'
      GROUP BY l.source_id, l.started_at, f.message_params ->> 'external_account_ref'
    )
"""


async def _records_waiting(
    session: AsyncSession, organization_id: uuid.UUID, key: ReferenceKey
) -> int:
    found: int = (
        await session.execute(
            text(
                f"{_LATEST_RUNS} SELECT coalesce(sum(records), 0) FROM waiting "  # noqa: S608 - fixed
                "WHERE source_id = :s AND ref = :r"
            ),
            {"o": organization_id, "s": key.source_id, "r": key.external_ref},
        )
    ).scalar_one()
    return int(found)


async def unmapped_references(
    session: AsyncSession, organization_id: uuid.UUID
) -> UnmappedReferences:
    """References the latest run of each source could not map, with the count of records
    waiting -- and whether one has been mapped since (it applies at the next sync)."""
    rows = await session.execute(
        text(
            f"""{_LATEST_RUNS}
            SELECT w.source_id, s.name AS source_name, s.connector_type, w.ref, w.records,
                   w.started_at, a.id AS account_id, a.key, a.name, a.archived_at
            FROM waiting w
            JOIN sources s ON s.organization_id = :o AND s.id = w.source_id
            LEFT JOIN account_external_refs m
              ON m.organization_id = :o AND m.source_id = w.source_id
             AND m.ref_kind = CASE s.connector_type WHEN 'ticketing' THEN
                 'ticketing_organization' ELSE 'chat_channel' END
             AND m.external_ref = w.ref
            LEFT JOIN accounts a ON a.organization_id = :o AND a.id = m.account_id
            ORDER BY (m.account_id IS NULL) DESC, w.records DESC, s.name, w.ref
            LIMIT :n
            """  # noqa: S608 - fixed fragments
        ),
        {"o": organization_id, "n": MAX_UNMAPPED},
    )
    items = []
    for r in rows:
        kind = ref_kind_for(r.connector_type)
        assert kind is not None  # noqa: S101 - filtered to mappable connectors
        account = (
            None
            if r.account_id is None
            else AccountRef(
                id=r.account_id,
                key=str(r.key),
                name=r.name,
                lifecycle="archived" if r.archived_at is not None else "active",
            )
        )
        items.append(
            UnmappedReference(
                source=SourceRef(
                    id=r.source_id, name=r.source_name, connector_type=r.connector_type
                ),
                ref_kind=cast(RefKind, kind),
                external_ref=r.ref,
                records_waiting=int(r.records),
                seen_at=r.started_at,
                state="unmapped" if account is None else "mapped_pending_sync",
                account=account,
            )
        )
    return UnmappedReferences(items=items)


def _operation(now: uuid.UUID | None, after: uuid.UUID | None) -> MappingOperation:
    if (now is None and after is None) or now == after:
        raise refusal("no_change")
    if now is None:
        return "map"
    return "unmap" if after is None else "remap"


async def preview_mapping(
    session: AsyncSession, organization_id: uuid.UUID, request: MappingPreviewRequest
) -> MappingPreview:
    """Now → After, with the refusals the change would give (without its lock)."""
    source, account_scoped = await _source(session, organization_id, request)
    now = await _current(session, organization_id, request)
    operation = _operation(now, request.account_id)
    after = await _account_ref(session, organization_id, request.account_id)
    if request.account_id is not None:
        if after is None:
            raise refusal("unknown_account")
        if after.lifecycle == "archived":
            raise refusal("account_archived")
    effect = {
        "map": "indexed" if account_scoped else "scoped",
        "remap": "moved",
        "unmap": "quarantined" if account_scoped else "unscoped",
    }[operation]
    return MappingPreview(
        operation=operation,
        source=source,
        ref_kind=request.ref_kind,
        external_ref=request.external_ref,
        account_scoped=account_scoped,
        now=await _account_ref(session, organization_id, now),
        after=after,
        effect=cast(MappingEffect, effect),
        records_waiting=await _records_waiting(session, organization_id, request),
    )


async def _change(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    key: ReferenceKey,
    expected: uuid.UUID | None,
    account_id: uuid.UUID | None,
    reason: str,
    operation: MappingOperation,
) -> MappingResult:
    async with _fenced(body_account=True), db.tenant_transaction(organization_id) as session:
        source, _ = await _source(session, organization_id, key)
        try:
            async with session.begin_nested():
                done: str = (
                    await session.execute(
                        text("SELECT lattice_set_account_ref(:actor, :s, :k, :r, :e, :a)"),
                        {
                            "actor": actor_membership_id,
                            "s": key.source_id,
                            "k": key.ref_kind,
                            "r": key.external_ref,
                            "e": expected,
                            "a": account_id,
                        },
                    )
                ).scalar_one()
        except DBAPIError as exc:
            fenced = _sqlstate_reason(exc, _FENCED_SQLSTATE)
            if fenced is not None and fenced[0] == "mapping_changed" and operation == "map":
                raise refusal("already_mapped") from None
            raise
        assert done == {"map": "created", "remap": "changed", "unmap": "removed"}[operation]  # noqa: S101
        await record_event(
            session,
            organization_id,
            actor=f"member:{actor_membership_id}",
            action=MAPPING_CHANGED,
            target=mapping_target(key.source_id, key.ref_kind, key.external_ref),
            before=None if expected is None else {"account_id": str(expected)},
            after=None if account_id is None else {"account_id": str(account_id)},
            reason=reason,
        )
        account = await _account_ref(session, organization_id, account_id)
    return MappingResult(
        operation=operation,
        source=source,
        ref_kind=key.ref_kind,
        external_ref=key.external_ref,
        account=account,
    )


async def map_reference(
    db: Database, *, organization_id: uuid.UUID, actor_membership_id: uuid.UUID, request: MapRequest
) -> MappingResult:
    """A reference that maps to nothing now → one active account (409 if mapped)."""
    return await _change(
        db,
        organization_id=organization_id,
        actor_membership_id=actor_membership_id,
        key=request,
        expected=None,
        account_id=request.account_id,
        reason=request.reason,
        operation="map",
    )


async def remap_reference(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    request: RemapRequest,
) -> MappingResult:
    """Move a reference from the account reviewed to another active account."""
    if request.current_account_id == request.account_id:
        raise refusal("no_change")
    return await _change(
        db,
        organization_id=organization_id,
        actor_membership_id=actor_membership_id,
        key=request,
        expected=request.current_account_id,
        account_id=request.account_id,
        reason=request.reason,
        operation="remap",
    )


async def unmap_reference(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    request: UnmapRequest,
) -> MappingResult:
    """Remove a reference's mapping. Indexed records keep their account until the next
    sync; then they are quarantined or lose the account (``MappingPreview.effect``)."""
    return await _change(
        db,
        organization_id=organization_id,
        actor_membership_id=actor_membership_id,
        key=request,
        expected=request.current_account_id,
        account_id=None,
        reason=request.reason,
        operation="unmap",
    )
