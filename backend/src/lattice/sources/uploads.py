"""Receiving an export (Phase 12, P12.6; phase-12-plan C8.1-C8.4).

``POST /v1/admin/sources/{id}/imports`` streams the request body here:

1. **Refuse early**, before any row or file exists: no import area configured
   (``storage_unavailable``), not a zip (and, for Tickets, not a JSON Lines page) by its
   first bytes, the organization's quota or the area's free space could not hold one
   more worst-case upload (``storage_full``). The format is decided by content, never by
   a file name or a client ``Content-Type``.
2. **Register** (``lattice_begin_import``): a ``receiving`` import with a generated ID --
   one upload per source at a time.
3. **Receive**: the body streams to ``archive.partial`` in 1 MiB writes off the event
   loop, size-capped and hashed as it arrives, within the upload deadline; a body that
   ends early or differs from its declared length is ``upload_incomplete``.
4. **Seal and validate** (``validating``): the archive is checked and extracted entry by
   entry (``lattice.imports.archive``) into a staging directory published by one rename.
5. **Finish**: ``ready`` and the source's current import (the previous one superseded),
   ``source.import_accepted``, a check queued; or ``rejected`` with a fixed code,
   ``source.import_rejected``, its files removed at once.

Memory stays bounded by the write buffer; disk by the limits; nothing of a refused upload
remains available, and the worker can never read an import that is not ``ready``.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text

from lattice import metrics
from lattice.access.audit import record_event
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorDetail, LatticeError
from lattice.imports.archive import is_zip
from lattice.imports.limits import ImportLimits, ImportRejected, RejectCode
from lattice.imports.store import ImportStore, ImportUnavailable, UploadWriter
from lattice.log import log_event
from lattice.sources.admin import fenced, refusal, source_detail
from lattice.sources.admin_models import ImportOut, UploadResult

IMPORT_ACCEPTED: Final = "source.import_accepted"
IMPORT_REJECTED: Final = "source.import_rejected"
_BUFFER: Final = 1024 * 1024
_log = logging.getLogger("lattice.sources")

# Fixed explanations of each code (shown by the API; the web app has its own wording).
EXPLANATIONS: Final[dict[str, str]] = {
    "archive_invalid": "The file is not a readable zip archive (or uses encryption or an "
    "unsupported compression method).",
    "too_large": "The upload, its expanded contents or one of its files is over the limit.",
    "too_many_entries": "The archive has more entries than the limit.",
    "ratio_exceeded": "An entry expands far more than real exports do (a compression bomb).",
    "unsafe_path": "An entry has an unsafe or invalid path.",
    "link_refused": "The archive contains a link or a special file.",
    "duplicate_name": "Two entries have the same path.",
    "unsupported_layout": "The archive does not have the structure this source type reads.",
    "upload_incomplete": "The upload ended before the whole file arrived.",
    "upload_timeout": "The upload took longer than allowed.",
    "storage_full": "The import area cannot hold this upload now.",
    "upload_failed": "The server could not finish receiving the upload. Try again.",
}


@dataclass(frozen=True)
class UploadContext:
    db: Database
    store: ImportStore | None
    limits: ImportLimits
    timeout_seconds: float


def _rejected(code: RejectCode) -> LatticeError:
    if code == "storage_full":
        return LatticeError(
            ErrorCode.SERVICE_UNAVAILABLE,
            EXPLANATIONS[code],
            details=[ErrorDetail(field="import", issue=code)],
        )
    return LatticeError(
        ErrorCode.REQUEST_TOO_LARGE if code == "too_large" else ErrorCode.VALIDATION_FAILED,
        f"The upload was refused: {EXPLANATIONS.get(code, code)}",
        details=[ErrorDetail(field="import", issue=code)],
    )


def _unavailable() -> LatticeError:
    return LatticeError(
        ErrorCode.SERVICE_UNAVAILABLE,
        "This deployment has no import area configured.",
        details=[ErrorDetail(field="import", issue="storage_unavailable")],
    )


async def _head(body: AsyncIterator[bytes]) -> bytes:
    """At least the first four bytes of the body (or all of a shorter one); the rest of
    the body stays in the iterator."""
    head = b""
    while len(head) < 4:
        try:
            head += await anext(body)
        except StopAsyncIteration:
            break
    return head


_USAGE_SQL = text(
    "SELECT coalesce(sum(coalesce(size_bytes, 0) + coalesce(expanded_bytes, 0)), 0) "
    "FROM source_imports WHERE organization_id = :o AND status <> 'deleted'"
)
_SOURCE_SQL = text(
    "SELECT connector_type, managed_by, status FROM sources "
    "WHERE organization_id = :o AND id = :s AND status <> 'deleting'"
)


async def receive_upload(
    ctx: UploadContext,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    source_id: uuid.UUID,
    body: AsyncIterator[bytes],
    declared_length: int | None,
) -> UploadResult:
    store, limits = ctx.store, ctx.limits
    async with ctx.db.tenant_snapshot(organization_id) as session:
        source = (
            await session.execute(_SOURCE_SQL, {"o": organization_id, "s": source_id})
        ).one_or_none()
        usage = int((await session.execute(_USAGE_SQL, {"o": organization_id})).scalar_one())
    if source is None:
        raise refusal("source_not_found")
    if source.managed_by != "organization":
        raise refusal("operator_managed")
    # After the target: an unknown source answers the same whatever the deployment.
    if store is None:
        raise _unavailable()
    early = time.perf_counter()
    deadline = time.monotonic() + ctx.timeout_seconds
    body = aiter(body)
    try:
        async with asyncio.timeout(ctx.timeout_seconds):
            head = await _head(body)
    except TimeoutError:
        raise _refused_early(source_id, "upload_timeout", early) from None
    if not head:
        raise _refused_early(source_id, "archive_invalid", early)
    if is_zip(head):
        fmt = "zip"
    elif source.connector_type == "ticketing":
        fmt = "jsonl"
    else:
        raise _refused_early(source_id, "archive_invalid", early)
    if usage + limits.worst_case_bytes > limits.organization_quota_bytes:
        raise _refused_early(source_id, "storage_full", early)
    try:
        free = await asyncio.to_thread(store.free_bytes)
    except ImportUnavailable:
        raise _unavailable() from None
    if free < limits.worst_case_bytes + limits.reserve_free_bytes:
        raise _refused_early(source_id, "storage_full", early)

    async with fenced(), ctx.db.tenant_transaction(organization_id) as session:
        import_id: uuid.UUID = (
            await session.execute(
                text("SELECT lattice_begin_import(:a, :s, :f)"),
                {"a": actor_membership_id, "s": source_id, "f": fmt},
            )
        ).scalar_one()
    started = time.perf_counter()
    size: int | None = None
    digest: bytes | None = None
    try:
        writer = await asyncio.to_thread(store.begin, organization_id, import_id)
        try:
            size, digest = await _receive(
                writer, head, body, deadline=deadline, declared=declared_length
            )
        except BaseException:
            await asyncio.to_thread(writer.abort)
            raise
        async with fenced(), ctx.db.tenant_transaction(organization_id) as session:
            await session.execute(
                text("SELECT lattice_import_received(:a, :i, :n, :h)"),
                {"a": actor_membership_id, "i": import_id, "n": size, "h": digest},
            )
        extracted = await asyncio.to_thread(
            store.extract,
            organization_id,
            import_id,
            connector_type=source.connector_type,
            fmt=fmt,
            limits=limits,
        )
    except ImportRejected as exc:
        await _reject(
            ctx, organization_id, actor_membership_id, source_id, import_id, exc.code, size, digest
        )
        _observe("rejected", exc.code, started)
        raise _rejected(exc.code) from None
    except (OSError, ImportUnavailable):
        await _reject(
            ctx,
            organization_id,
            actor_membership_id,
            source_id,
            import_id,
            "upload_failed",
            size,
            digest,
        )
        _observe("rejected", "upload_failed", started)
        raise _rejected("upload_failed") from None
    except BaseException:
        # Cancelled (the client went away mid-validation) or a bug: never leave it usable.
        await asyncio.shield(
            _reject(
                ctx,
                organization_id,
                actor_membership_id,
                source_id,
                import_id,
                "upload_failed",
                size,
                digest,
            )
        )
        raise

    try:
        async with fenced(), ctx.db.tenant_transaction(organization_id) as session:
            finished: dict[str, Any] = (
                await session.execute(
                    text("SELECT lattice_finish_import(:a, :i, :e, :g, :x)"),
                    {
                        "a": actor_membership_id,
                        "i": import_id,
                        "e": extracted.entries,
                        "g": extracted.ignored,
                        "x": extracted.expanded_bytes,
                    },
                )
            ).scalar_one()
            await record_event(
                session,
                organization_id,
                actor=f"member:{actor_membership_id}",
                action=IMPORT_ACCEPTED,
                target={"source_id": str(source_id), "import_id": str(import_id)},
                before=(
                    None
                    if finished.get("superseded") is None
                    else {"import_id": str(finished["superseded"])}
                ),
                after={
                    "format": fmt,
                    "size_bytes": size,
                    "sha256": None if digest is None else digest.hex(),
                    "entries": extracted.entries,
                    "ignored_entries": extracted.ignored,
                    "expanded_bytes": extracted.expanded_bytes,
                },
                reason="Export uploaded in the product",
            )
    except BaseException:
        # The source went away (disconnected) or the database refused: nothing stays.
        await asyncio.to_thread(store.delete, organization_id, import_id)
        raise
    _observe("accepted", None, started)
    log_event(
        _log,
        logging.INFO,
        "sources.import_accepted",
        source_id=str(source_id),
        import_id=str(import_id),
        size_bytes=size,
        entries=extracted.entries,
    )
    async with ctx.db.tenant_snapshot(organization_id) as session:
        detail = await source_detail(
            session, organization_id, source_id, limits=limits, store_available=True
        )
    current = detail.current_import if detail is not None else None
    if current is None or current.id != import_id:  # superseded at once by a newer upload
        raise refusal("source_changed")
    return UploadResult(import_=current, check_queued=bool(finished.get("check_queued")))


async def _receive(
    writer: UploadWriter,
    head: bytes,
    body: AsyncIterator[bytes],
    *,
    deadline: float,
    declared: int | None,
) -> tuple[int, bytes]:
    buffer = bytearray(head)
    try:
        async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
            async for chunk in body:
                buffer += chunk
                if len(buffer) >= _BUFFER:
                    await asyncio.to_thread(writer.append, bytes(buffer))
                    buffer.clear()
    except TimeoutError:
        raise ImportRejected("upload_timeout") from None
    except ImportRejected:
        raise
    except Exception as exc:  # starlette's ClientDisconnect, a reset connection
        if type(exc).__name__ in ("ClientDisconnect", "ConnectionResetError"):
            raise ImportRejected("upload_incomplete") from None
        raise
    if buffer:
        await asyncio.to_thread(writer.append, bytes(buffer))
    if declared is not None and writer.size != declared:
        raise ImportRejected("upload_incomplete")
    return await asyncio.to_thread(writer.seal)


async def _reject(
    ctx: UploadContext,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    source_id: uuid.UUID,
    import_id: uuid.UUID,
    code: str,
    size: int | None,
    digest: bytes | None,
) -> None:
    """``rejected`` with its code and the audit event; then its files go and it is marked
    deleted. Failures here are left to the worker's import retention."""
    try:
        async with ctx.db.tenant_transaction(organization_id) as session:
            await session.execute(
                text("SELECT lattice_reject_import(:a, :i, :c, :n, :h)"),
                {"a": actor_membership_id, "i": import_id, "c": code, "n": size, "h": digest},
            )
            await record_event(
                session,
                organization_id,
                actor=f"member:{actor_membership_id}",
                action=IMPORT_REJECTED,
                target={"source_id": str(source_id), "import_id": str(import_id)},
                before=None,
                after={
                    "code": code,
                    "size_bytes": size,
                    "sha256": None if digest is None else digest.hex(),
                },
                reason="Export upload refused by validation",
            )
        if ctx.store is not None:
            await asyncio.to_thread(ctx.store.delete, organization_id, import_id)
        async with ctx.db.tenant_transaction(organization_id) as session:
            await session.execute(
                text("SELECT lattice_mark_import_deleted(:a, :i)"),
                {"a": actor_membership_id, "i": import_id},
            )
    except Exception as exc:
        log_event(
            _log,
            logging.WARNING,
            "sources.import_reject_failed",
            import_id=str(import_id),
            error_type=type(exc).__name__,
        )
    log_event(
        _log,
        logging.INFO,
        "sources.import_rejected",
        source_id=str(source_id),
        import_id=str(import_id),
        code=code,
    )


def _refused_early(source_id: uuid.UUID, code: RejectCode, started: float) -> LatticeError:
    """An upload refused before it was registered (no import row, no file): counted and
    logged like any refused upload (P12.7: these were invisible to operators), with no
    audit event -- nothing was received."""
    _observe("rejected", code, started)
    log_event(_log, logging.INFO, "sources.import_refused", source_id=str(source_id), code=code)
    return _rejected(code)


def _observe(outcome: str, code: str | None, started: float) -> None:
    metrics.IMPORTS.labels(outcome=outcome, code=metrics.bounded(code, metrics.IMPORT_CODES)).inc()
    metrics.IMPORT_DURATION.observe(time.perf_counter() - started)


__all__ = ["EXPLANATIONS", "ImportOut", "UploadContext", "receive_upload"]
