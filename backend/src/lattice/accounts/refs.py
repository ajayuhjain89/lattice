"""External account references: what a connector reports, and how it is mapped
(phase-12-plan C10; migration 0018).

A ticketing record names the ticketing tool's organization ID (``ticketing_organization``,
canonical decimal text, as ``connectors.tickets`` reports it); a chat record names its
channel ID (``chat_channel``). Each is matched **exactly** -- byte for byte after the
connector's own normalisation -- against ``account_external_refs`` of the record's source:
never fuzzily, never by name, never from content. A document names an account *key*
(front matter), which is the canonical account itself and needs no mapping.

A row with no account is an **unresolved** reference (0019): it was mapped and an
administrator unmapped it. Its records fail closed at sync (``lattice.ingestion.access``)
-- a missing mapping never widens who can read them -- until it is mapped again.

Writers: the API through the fenced ``lattice_set_account_ref`` (``lattice.accounts.service``)
and operators through ``lattice sync --sources`` (``apply_file_mappings``): a sources file
makes the references it names match it again -- audited as ``operator:sync``, never
silently; references it does not name are left as administrators set them.
"""

import uuid
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import null

from lattice.sources.config import SourceConfig
from lattice.tracing.models import AuditEvent

# Connector type -> the reference kind its records carry. Others carry no reference.
REF_KINDS: Final[dict[str, str]] = {
    "ticketing": "ticketing_organization",
    "chat_export": "chat_channel",
}
MAPPING_CHANGED: Final = "account.mapping_changed"
# The fixed reason of an operator file's mapping events.
FILE_REASON: Final = "Mapped by the operator's sources file"


class MappingRefused(Exception):
    """A mapping the database refused (a fenced check: archived account, wrong kind)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def ref_kind_for(connector_type: str) -> str | None:
    return REF_KINDS.get(connector_type)


def file_mappings(config: SourceConfig) -> dict[str, str]:
    """The references a sources file maps: external reference -> account key."""
    if config.connector_type == "ticketing":
        return {str(k): v for k, v in config.account_refs.items()}
    if config.connector_type == "chat_export":
        return {cid: c.account for cid, c in config.channels.items() if c.account}
    return {}


def mapping_target(source_id: uuid.UUID, ref_kind: str, external_ref: str) -> dict[str, Any]:
    """The audit target of a mapping change (the coupling trigger matches it)."""
    return {"source_id": str(source_id), "ref_kind": ref_kind, "external_ref": external_ref}


async def load_mappings(
    session: AsyncSession, organization_id: uuid.UUID, source_id: uuid.UUID, connector_type: str
) -> dict[str, uuid.UUID | None]:
    """The source's mappings: external reference -> account ID, or None when the reference
    is unresolved (unmapped by an administrator; its records fail closed)."""
    kind = ref_kind_for(connector_type)
    if kind is None:
        return {}
    rows = await session.execute(
        text(
            "SELECT external_ref, account_id FROM account_external_refs "
            "WHERE organization_id = :o AND source_id = :s AND ref_kind = :k"
        ),
        {"o": organization_id, "s": source_id, "k": kind},
    )
    return {str(ref): account for ref, account in rows}


async def apply_file_mappings(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    source_id: uuid.UUID,
    config: SourceConfig,
    accounts: dict[str, uuid.UUID],
) -> int:
    """Make the references the file names map as it says (operator writes, audited as
    ``operator:sync``); leave every other reference alone. Returns the number changed.
    The caller has checked every key names an account (``store.check_config``)."""
    kind = ref_kind_for(config.connector_type)
    wanted = file_mappings(config)
    if kind is None or not wanted:
        return 0
    current = await load_mappings(session, organization_id, source_id, config.connector_type)
    changed = 0
    for ref, key in sorted(wanted.items()):
        account_id = accounts[key]
        before = current.get(ref)
        if before == account_id:
            continue
        if ref not in current:
            statement = text(
                "INSERT INTO account_external_refs (organization_id, source_id, ref_kind, "
                "external_ref, account_id, created_by) VALUES (:o, :s, :k, :r, :a, 'operator:sync')"
            )
        else:
            statement = text(
                "UPDATE account_external_refs SET account_id = :a WHERE organization_id = :o "
                "AND source_id = :s AND ref_kind = :k AND external_ref = :r"
            )
        params = {"o": organization_id, "s": source_id, "k": kind, "r": ref, "a": account_id}
        try:
            async with session.begin_nested():
                await session.execute(statement, params)
        except DBAPIError as exc:
            code = _refusal(exc)
            if code is None:
                raise
            raise MappingRefused(code) from None
        session.add(
            AuditEvent(
                organization_id=organization_id,
                actor="operator:sync",
                action=MAPPING_CHANGED,
                target=mapping_target(source_id, kind, ref),
                before=null() if before is None else {"account_id": str(before)},
                after={"account_id": str(account_id)},
                reason=FILE_REASON,
            )
        )
        changed += 1
    await session.flush()
    return changed


def _refusal(exc: BaseException) -> str | None:
    """The fixed reason of a 0018 check-trigger refusal (SQLSTATE LA001), else None."""
    orig = getattr(exc, "orig", None)
    for candidate in (orig, getattr(orig, "__cause__", None)):
        if candidate is not None and getattr(candidate, "sqlstate", None) == "LA001":
            message = getattr(candidate, "message", None) or str(candidate)
            return str(message).strip().splitlines()[0]
    return None
