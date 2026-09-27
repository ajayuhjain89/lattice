"""Load the access-relevant facts of documents for the reference policy.

Used by the pre-generation tripwire (E4), which must re-read access columns from the
database rather than trust whatever retrieval returned, and by explanations.
"""

import uuid
from collections.abc import Collection

from sqlalchemy import any_, literal, select
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz.models import Document, DocumentGrant
from lattice.authz.policy import DocumentAccess, GrantRef

_UUID_ARRAY = ARRAY(UUID(as_uuid=True))


async def load_document_access(
    session: AsyncSession, organization_id: uuid.UUID, document_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, DocumentAccess]:
    """Access facts for those of ``document_ids`` that exist in ``organization_id``.

    The session must be bound to the same organization (RLS); the explicit
    organization filter is the second, independent layer. Missing IDs are absent from
    the result -- callers decide what absence means.
    """
    ids = literal(sorted(set(document_ids)), _UUID_ARRAY)
    documents = (
        await session.execute(
            select(
                Document.id,
                Document.organization_id,
                Document.status,
                Document.sensitivity,
                Document.access_mode,
                Document.account_id,
                Document.department_ids,
            ).where(Document.organization_id == organization_id, Document.id == any_(ids))
        )
    ).all()
    grant_rows = (
        await session.execute(
            select(
                DocumentGrant.document_id,
                DocumentGrant.organization_id,
                DocumentGrant.principal_type,
                DocumentGrant.membership_id,
                DocumentGrant.department_id,
                DocumentGrant.group_id,
            ).where(
                DocumentGrant.organization_id == organization_id,
                DocumentGrant.document_id == any_(ids),
            )
        )
    ).all()

    grants: dict[uuid.UUID, list[GrantRef]] = {}
    for g in grant_rows:
        principal_id = {
            "membership": g.membership_id,
            "department": g.department_id,
            "group": g.group_id,
        }.get(g.principal_type)
        if principal_id is None:  # malformed row: contributes no access
            continue
        grants.setdefault(g.document_id, []).append(
            GrantRef(g.organization_id, g.principal_type, principal_id)
        )

    return {
        d.id: DocumentAccess(
            id=d.id,
            organization_id=d.organization_id,
            status=d.status,
            sensitivity=d.sensitivity,
            access_mode=d.access_mode,
            account_id=d.account_id,
            department_ids=frozenset(d.department_ids),
            grants=tuple(
                sorted(grants.get(d.id, ()), key=lambda g: (g.principal_type, g.principal_id))
            ),
        )
        for d in documents
    }
