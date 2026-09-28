"""Lattice administrator grants (decision D-12).

Admin-defined grants are Lattice configuration, not source permissions. They are
applied by a separate, deterministic step *after* ingestion (``lattice
apply-admin-grants``), never by a connector, and a source sync never touches them:
ingestion replaces only ``origin = 'source'`` grants.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.db.engine import Database


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AdminGrant(_Strict):
    # A record key: doc:<front-matter id>, ticket:<ticket id>, chat:<channel id>:<ts>.
    document: str
    # Optional source key, required when the key could name documents of two sources.
    source: str | None = None
    member: str
    reason: str = Field(min_length=1)
    granted_on: date


class AdminGrantsFile(_Strict):
    grants: list[AdminGrant] = Field(default_factory=list)


# --- application --------------------------------------------------------------------------

_PREFIX_TYPES = {
    "doc": ("document_repository",),
    "ticket": ("ticketing",),
    "chat": ("chat_export",),
}


@dataclass(frozen=True)
class GrantProblem:
    """Entry ``index`` (0-based, in file order) cannot be applied. ``code`` is fixed;
    no document title, content or other source metadata is reported."""

    index: int
    code: str


@dataclass
class ApplyReport:
    organization: str
    dry_run: bool
    added: int = 0
    unchanged: int = 0
    removed: int = 0
    problems: list[GrantProblem] = field(default_factory=list)


def load_admin_grants(path: Path) -> AdminGrantsFile:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return AdminGrantsFile.model_validate(raw or {})


async def _resolve_document(
    session: AsyncSession, org: uuid.UUID, grant: AdminGrant
) -> uuid.UUID | str:
    prefix, _, external_id = grant.document.partition(":")
    types = _PREFIX_TYPES.get(prefix)
    if types is None or not external_id:
        return "invalid_document_ref"
    if grant.source is not None:
        found = (
            await session.execute(
                text("SELECT 1 FROM sources WHERE organization_id = :o AND key = :k"),
                {"o": org, "k": grant.source},
            )
        ).first()
        if found is None:
            return "unknown_source"
    rows: list[object] = list(
        (
            await session.execute(
                text(
                    "SELECT d.id FROM documents d JOIN sources s ON s.id = d.source_id "
                    "AND s.organization_id = d.organization_id "
                    "WHERE d.organization_id = :o AND d.external_id = :e "
                    "AND s.connector_type = ANY(CAST(:types AS text[])) AND s.status = 'active' "
                    "AND d.status <> 'deleted' "
                    "AND (CAST(:source AS text) IS NULL OR s.key = :source)"
                ),
                {"o": org, "e": external_id, "types": list(types), "source": grant.source},
            )
        ).scalars()
    )
    if not rows:
        return "unknown_document"
    if len(rows) > 1:
        return "ambiguous_document"
    value = rows[0]
    assert isinstance(value, uuid.UUID)  # noqa: S101 - id column
    return value


async def apply_admin_grants(
    db: Database,
    organization_slug: str,
    grants: AdminGrantsFile,
    *,
    dry_run: bool = False,
    prune: bool = False,
) -> ApplyReport:
    """Make the organization's admin-origin membership grants match the file.

    All-or-nothing: every entry is resolved first -- the document by its record key
    (inside this organization only: the organization is bound, so another tenant's
    documents do not exist here), the member by email among this organization's
    memberships -- and any unknown, ambiguous or duplicate entry fails the whole
    application with nothing written. Idempotent: unchanged input changes nothing.
    Source-origin grants are never read or written here. With ``prune``, admin grants
    not in the file are removed (a revocation); without it they are left alone.
    """
    report = ApplyReport(organization=organization_slug, dry_run=dry_run)
    async with db.unscoped_transaction() as s:
        org = (
            await s.execute(
                text("SELECT id FROM organizations WHERE slug = :s"), {"s": organization_slug}
            )
        ).scalar_one_or_none()
    if not isinstance(org, uuid.UUID):
        report.problems.append(GrantProblem(-1, "unknown_organization"))
        return report

    tx = db.tenant_snapshot if dry_run else db.tenant_transaction
    async with tx(org) as s:
        members = {
            str(email).lower(): membership_id
            for email, membership_id in (
                await s.execute(
                    text(
                        "SELECT u.email, m.id FROM memberships m JOIN users u ON u.id = m.user_id "
                        "WHERE m.organization_id = :o"
                    ),
                    {"o": org},
                )
            ).all()
        }
        wanted: dict[tuple[uuid.UUID, uuid.UUID], int] = {}
        for index, grant in enumerate(grants.grants):
            document = await _resolve_document(s, org, grant)
            member = members.get(grant.member.strip().lower())
            if isinstance(document, str):
                report.problems.append(GrantProblem(index, document))
            elif member is None:
                report.problems.append(GrantProblem(index, "unknown_principal"))
            elif (document, member) in wanted:
                report.problems.append(GrantProblem(index, "duplicate_grant"))
            else:
                wanted[(document, member)] = index
        if report.problems:
            return report  # fail closed: nothing written

        current = {
            (doc, member): grant_id
            for grant_id, doc, member in (
                await s.execute(
                    text(
                        "SELECT id, document_id, membership_id FROM document_grants "
                        "WHERE organization_id = :o AND origin = 'admin' "
                        "AND principal_type = 'membership'"
                    ),
                    {"o": org},
                )
            ).all()
        }
        missing = sorted(set(wanted) - set(current), key=str)
        extra = sorted(set(current) - set(wanted), key=str) if prune else []
        report.added = len(missing)
        report.unchanged = len(set(wanted) & set(current))
        report.removed = len(extra)
        if dry_run:
            return report

        for document_id, membership_id in missing:
            await s.execute(
                text(
                    "INSERT INTO document_grants (organization_id, document_id, principal_type, "
                    "membership_id, origin) VALUES (:o, :d, 'membership', :m, 'admin')"
                ),
                {"o": org, "d": document_id, "m": membership_id},
            )
        if extra:
            await s.execute(
                text(
                    "DELETE FROM document_grants WHERE organization_id = :o "
                    "AND id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"o": org, "ids": [current[key] for key in extra]},
            )
        touched = sorted({doc for doc, _ in (*missing, *extra)}, key=str)
        if touched:
            await s.execute(
                text(
                    "UPDATE documents SET acl_updated_at = now() WHERE organization_id = :o "
                    "AND id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"o": org, "ids": touched},
            )
    return report
