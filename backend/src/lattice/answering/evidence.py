"""One evidence item by ID (enforcement point E5; the inspector's data).

The lookup runs through the same authorization predicate as retrieval, in one
snapshot: a chunk that is unreadable, another organization's, deleted or nonexistent is
``None`` -- the route answers 404 for all of them identically (SEC-5)."""

import uuid

from lattice.answering.models import EvidenceAccount, EvidenceDetail
from lattice.authz import readable_document_classifications, resolve_authorization_context
from lattice.db.engine import Database
from lattice.retrieval import lookup_evidence


async def get_evidence(
    db: Database, *, organization_id: uuid.UUID, membership_id: uuid.UUID, chunk_id: uuid.UUID
) -> EvidenceDetail | None:
    async with db.tenant_snapshot(organization_id) as session:
        ctx = await resolve_authorization_context(
            session, organization_id=organization_id, membership_id=membership_id
        )
        found = (await lookup_evidence(session, ctx, [chunk_id])).get(chunk_id)
        if found is None:
            return None
        classification = (
            await readable_document_classifications(session, ctx, [found.document_id])
        )[found.document_id]
    return EvidenceDetail.model_validate(
        {
            "evidence_id": found.chunk_id,
            "document_id": found.document_id,
            "source_type": found.source_type,
            "document_kind": found.document_kind,
            "source_name": found.source_name,
            "title": found.title,
            "display_ref": found.display_ref,
            "uri": found.uri,
            "heading_path": list(found.heading_path),
            "author": found.author,
            "occurred_at": found.occurred_at,
            "document_updated_at": found.document_updated_at,
            "version": found.version,
            "authority": found.authority,
            "classification": classification,
            "account": (
                EvidenceAccount(key=found.account.key, name=found.account.name)
                if found.account
                else None
            ),
            "text": found.text,
            "char_start": found.char_start,
            "char_end": found.char_end,
            "supersedes_document_id": found.supersedes_document_id,
            "superseded_by_document_ids": list(found.superseded_by_document_ids),
        }
    )
