"""The organization's settings an administrator changes in the product (P12.1: its
display name; ADR-017's first fenced setup write).

The API role may update ``organizations.name`` of its own, active organization only
(column grant and restrictive policy, migration 0016), and the change cannot commit
without an ``organization.updated`` event in the same transaction (a deferred
constraint trigger): the event and the change commit together or not at all. Slug,
status, data origin and creation stay operator-only.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text

from lattice.access.audit import record_event
from lattice.db.engine import Database
from lattice.errors import ErrorCode, LatticeError


@dataclass(frozen=True)
class OrganizationRecord:
    id: uuid.UUID
    slug: str
    name: str
    status: str
    data_origin: str
    version: datetime


_RECORD_SQL = text(
    "SELECT o.id, o.slug, o.name, o.status, o.updated_at AS version, "
    "coalesce(s.data_origin, 'customer') AS data_origin FROM organizations o "
    "LEFT JOIN org_settings s ON s.organization_id = o.id WHERE o.id = :o"
)


async def organization_record(db: Database, org: uuid.UUID) -> OrganizationRecord:
    async with db.tenant_snapshot(org) as session:
        row = (await session.execute(_RECORD_SQL, {"o": org})).one()
    return OrganizationRecord(
        row.id, str(row.slug), row.name, row.status, row.data_origin, row.version
    )


async def rename_organization(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    name: str,
    version: datetime,
    reason: str,
) -> OrganizationRecord:
    """Rename; 409 when ``version`` is not the current one. An unchanged name writes
    nothing."""
    async with db.tenant_transaction(organization_id) as session:
        current = (
            await session.execute(
                text("SELECT name, updated_at FROM organizations WHERE id = :o FOR UPDATE"),
                {"o": organization_id},
            )
        ).one()
        if current.updated_at != version:
            raise LatticeError(
                ErrorCode.CONFLICT, "The organization changed meanwhile. Reload and try again."
            )
        if current.name != name:
            await session.execute(
                text("UPDATE organizations SET name = :n WHERE id = :o"),
                {"n": name, "o": organization_id},
            )
            await record_event(
                session,
                organization_id,
                actor=f"member:{actor_membership_id}",
                action="organization.updated",
                target={"organization_id": str(organization_id)},
                before={"name": current.name},
                after={"name": name},
                reason=reason,
            )
    return await organization_record(db, organization_id)
