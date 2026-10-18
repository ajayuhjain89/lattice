"""Source disconnect (docs/data-model.md §10, docs/ingestion-design.md §9).

``lattice source disconnect`` takes a source's content out of Lattice in two steps:

1. **Immediately** (one transaction): the source becomes ``deleting`` and every one of
   its documents ``deleted``. The policy reads only ``active`` documents, so from the
   next request on nothing of the source is retrievable, citable or countable -- even if
   step 2 never runs. A ``deleting`` source refuses syncs.
2. **Purge** (one transaction): the source row is deleted, and with it, by cascade:
   its documents; their chunks (vectors and lexical entries are chunk columns, so no
   orphaned index state can remain); every grant on those documents -- source-origin
   *and* admin-origin, because an admin grant is a grant on a document and ends with
   it (re-applying ``admin-grants.yaml`` after a reconnect restores them); its
   source-synced groups and their members; its sync runs and record failures (which
   hold its external identifiers); its account mappings (``account_external_refs``,
   P12.3) -- the accounts themselves, and other sources' mappings, stay. Its imports
   (P12.6) are marked deleted in step 1 and their files removed by the worker's import
   retention; queued jobs end ``source_disconnected``. Step 1 records
   ``source.disconnected`` (C11). Query traces
   do not exist yet (Phase 6); when they do, their references to purged chunks stop
   resolving and are re-authorized on read.

If the purge fails, the source stays ``deleting`` (invisible, unsyncable) and
re-running the command resumes it.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text

from lattice.db.engine import Database
from lattice.ingestion.store import get_source
from lattice.tracing.models import AuditEvent


@dataclass(frozen=True)
class DisconnectReport:
    documents: int
    chunks: int
    vectors: int
    source_grants: int
    admin_grants: int
    groups: int
    sync_runs: int
    account_mappings: int


class SourceNotFound(Exception):
    pass


async def disconnect_source(
    db: Database, org: uuid.UUID, key: str, *, actor: str = "operator:cli"
) -> DisconnectReport:
    async with db.tenant_transaction(org) as s:
        source = await get_source(s, org, key)
        if source is None:
            raise SourceNotFound(key)
        if source.status != "deleting":
            # Recorded once, by the request that disconnects (C11); a resumed purge is not.
            s.add(
                AuditEvent(
                    organization_id=org,
                    actor=actor,
                    action="source.disconnected",
                    target={"source_id": str(source.id)},
                    before={"status": source.status, "key": source.key},
                    after={"status": "deleting"},
                    reason="Disconnected by the operator (lattice source disconnect)",
                )
            )
        await s.execute(
            text(
                "UPDATE sources SET status = 'deleting', import_id = NULL "
                "WHERE organization_id = :o AND id = :s"
            ),
            {"o": org, "s": source.id},
        )
        # Its imports' files are removed by the worker's import retention (P12.6).
        await s.execute(
            text(
                "UPDATE source_imports SET status = 'deleted', deleted_at = now() "
                "WHERE organization_id = :o AND source_id = :s AND status <> 'deleted'"
            ),
            {"o": org, "s": source.id},
        )
        await s.execute(
            text(
                "UPDATE jobs SET status = 'failed', error_code = 'source_disconnected', "
                "finished_at = now(), lease_expires_at = NULL "
                "WHERE organization_id = :o AND source_id = :s AND status = 'queued'"
            ),
            {"o": org, "s": source.id},
        )
        await s.execute(
            text(
                "UPDATE documents SET status = 'deleted' WHERE organization_id = :o "
                "AND source_id = :s AND status <> 'deleted'"
            ),
            {"o": org, "s": source.id},
        )

    async with db.tenant_transaction(org) as s:
        params = {"o": org, "s": source.id}
        counts = (
            await s.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM documents WHERE organization_id = :o "
                    " AND source_id = :s), "
                    "(SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id "
                    " WHERE d.organization_id = :o AND d.source_id = :s), "
                    "(SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id "
                    " WHERE d.organization_id = :o AND d.source_id = :s "
                    " AND c.embedding IS NOT NULL), "
                    "(SELECT count(*) FROM document_grants g JOIN documents d "
                    " ON d.id = g.document_id WHERE d.organization_id = :o "
                    " AND d.source_id = :s AND g.origin = 'source'), "
                    "(SELECT count(*) FROM document_grants g JOIN documents d "
                    " ON d.id = g.document_id WHERE d.organization_id = :o "
                    " AND d.source_id = :s AND g.origin = 'admin'), "
                    "(SELECT count(*) FROM access_groups WHERE organization_id = :o "
                    " AND source_id = :s), "
                    "(SELECT count(*) FROM source_sync_runs WHERE organization_id = :o "
                    " AND source_id = :s), "
                    "(SELECT count(*) FROM account_external_refs WHERE organization_id = :o "
                    " AND source_id = :s)"
                ),
                params,
            )
        ).one()
        # Losing a group membership is an access change for those members.
        await s.execute(
            text(
                "UPDATE memberships SET authz_updated_at = now() WHERE organization_id = :o "
                "AND id IN (SELECT m.membership_id FROM access_group_members m "
                "JOIN access_groups g ON g.id = m.group_id "
                "WHERE g.organization_id = :o AND g.source_id = :s)"
            ),
            params,
        )
        await s.execute(text("DELETE FROM sources WHERE organization_id = :o AND id = :s"), params)
    return DisconnectReport(*(int(c) for c in counts))
