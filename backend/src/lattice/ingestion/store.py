"""PERSIST (docs/ingestion-design.md §4): every database read and write ingestion makes.

This is the one module outside ``lattice.authz`` that writes access state -- a
document's access columns, its source-origin grants, source-synced groups and their
members -- and it has an explicit, reviewed allowance in
``tests/test_authz_boundary.py``. It never decides who may read anything: it stores
what ``lattice.ingestion.access`` mapped.

Rules kept here:

* Every statement runs inside a transaction with the organization bound (RLS), and
  also filters by ``organization_id`` explicitly.
* One transaction per document: a document and its chunks and source grants change
  together or not at all, so retrieval sees the old version or the new one, never a
  mix.
* Only ``origin = 'source'`` grants are replaced by a sync. Administrator grants
  (``origin = 'admin'``, D-12) are never touched.
* No network call happens inside a transaction (embeddings are computed before).
"""

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.connectors.contract import DocumentKind, ExternalGroup
from lattice.ingestion.access import MappedAccess, OrgDirectory
from lattice.ingestion.canonical import CanonicalDocument
from lattice.sources.config import SourceConfig

_NAMESPACE = uuid.UUID("0b8c5c2e-7c3a-4d5e-9f11-2a6b4c8d9e01")


def document_id_for(source_id: uuid.UUID, external_id: str) -> uuid.UUID:
    """Deterministic document ID. Identity is decided by the idempotency key
    ``(organization_id, source_id, external_id)``, never by this ID; making it
    deterministic keeps chunk IDs reproducible too."""
    return uuid.uuid5(_NAMESPACE, f"{source_id}/{external_id}")


def chunk_id_for(document_id: uuid.UUID, content_hash: bytes, occurrence: int) -> uuid.UUID:
    """``uuid5(document_id, chunk content hash, occurrence)`` (ingestion-design §2):
    an unchanged passage keeps its ID -- and its embedding -- across updates."""
    return uuid.uuid5(document_id, f"{content_hash.hex()}:{occurrence}")


def opaque_ref(key: bytes, value: str) -> str:
    """Keyed reference to a record, shown instead of its identifier to admins who
    cannot read it (security-model §10)."""
    return "rec_" + hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:16]


class SourceConfigurationError(Exception):
    """The source's Lattice configuration does not fit the organization (unknown
    account or department key, a changed connector type, a source being deleted).
    Nothing is written."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- sources ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRow:
    id: uuid.UUID
    key: str
    connector_type: str
    status: str
    opaque_ref_key: bytes


async def organization_id(session: AsyncSession, slug: str) -> uuid.UUID | None:
    """Operator lookup (owner_provisioning policy); works before any org is bound."""
    value: object = (
        await session.execute(text("SELECT id FROM organizations WHERE slug = :s"), {"s": slug})
    ).scalar_one_or_none()
    return value if isinstance(value, uuid.UUID) else None


async def load_directory(session: AsyncSession, org: uuid.UUID) -> OrgDirectory:
    departments = await session.execute(
        text("SELECT key, id FROM departments WHERE organization_id = :o"), {"o": org}
    )
    accounts = await session.execute(
        text("SELECT key, id FROM accounts WHERE organization_id = :o"), {"o": org}
    )
    groups = await session.execute(
        text("SELECT key, id FROM access_groups WHERE organization_id = :o AND origin = 'admin'"),
        {"o": org},
    )
    return OrgDirectory(
        departments={str(k): i for k, i in departments.all()},
        accounts={str(k): i for k, i in accounts.all()},
        admin_groups={str(k): i for k, i in groups.all()},
    )


def check_config(config: SourceConfig, directory: OrgDirectory) -> None:
    if any(k not in directory.accounts for k in config.account_keys()):
        raise SourceConfigurationError("source_config:unknown_account")
    if any(k not in directory.departments for k in config.default_departments):
        raise SourceConfigurationError("source_config:unknown_department")


async def get_source(session: AsyncSession, org: uuid.UUID, key: str) -> SourceRow | None:
    row = (
        await session.execute(
            text(
                "SELECT id, key, connector_type, status, opaque_ref_key FROM sources "
                "WHERE organization_id = :o AND key = :k"
            ),
            {"o": org, "k": key},
        )
    ).first()
    return None if row is None else SourceRow(row[0], str(row[1]), row[2], row[3], bytes(row[4]))


async def upsert_source(
    session: AsyncSession, org: uuid.UUID, config: SourceConfig, export_path: str
) -> SourceRow:
    existing = await get_source(session, org, config.key)
    if existing is not None:
        if existing.connector_type != config.connector_type:
            raise SourceConfigurationError("source_config:connector_type_changed")
        if existing.status != "active":
            raise SourceConfigurationError(f"source_config:source_{existing.status}")
    settings = {
        "path": export_path,
        "default_departments": config.default_departments,
        "account_refs": {str(k): v for k, v in sorted(config.account_refs.items())},
        "channels": {
            k: c.model_dump(exclude_none=True) for k, c in sorted(config.channels.items())
        },
    }
    await session.execute(
        text(
            "INSERT INTO sources (organization_id, key, connector_type, name, config, "
            "default_sensitivity, account_scoped, authority) "
            "VALUES (:o, :k, :t, :n, CAST(:c AS jsonb), :ds, :as, :au) "
            "ON CONFLICT (organization_id, key) DO UPDATE SET "
            "name = EXCLUDED.name, config = EXCLUDED.config, "
            "default_sensitivity = EXCLUDED.default_sensitivity, "
            "account_scoped = EXCLUDED.account_scoped, authority = EXCLUDED.authority "
            "WHERE (sources.name, sources.config, sources.default_sensitivity, "
            "sources.account_scoped, sources.authority) IS DISTINCT FROM "
            "(EXCLUDED.name, EXCLUDED.config, EXCLUDED.default_sensitivity, "
            "EXCLUDED.account_scoped, EXCLUDED.authority)"
        ),
        {
            "o": org,
            "k": config.key,
            "t": config.connector_type,
            "n": config.name,
            "c": json.dumps(settings, sort_keys=True),
            "ds": config.default_sensitivity,
            "as": config.account_scoped,
            "au": config.authority,
        },
    )
    row = await get_source(session, org, config.key)
    assert row is not None  # noqa: S101 - just upserted
    return row


# --- egress settings -------------------------------------------------------------------


async def provider_approved_level(
    session: AsyncSession, org: uuid.UUID, provider: str, model: str
) -> str | None:
    value = (
        await session.execute(
            text(
                "SELECT approved_for_level FROM model_providers WHERE organization_id = :o "
                "AND purpose = 'embedding' AND provider = :p AND model = :m"
            ),
            {"o": org, "p": provider, "m": model},
        )
    ).scalar_one_or_none()
    return None if value is None else str(value)


async def register_provider(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    provider: str,
    model: str,
    deployment: str,
    retention_mode: str,
    training_opt_out: bool,
) -> str:
    """Record the embedding provider in use (security-model §6). A new record is
    approved for ``internal`` only -- the default; raising it is an explicit admin
    action outside ingestion. Returns the approved level."""
    await session.execute(
        text(
            "INSERT INTO model_providers (organization_id, purpose, provider, model, "
            "deployment, retention_mode, training_opt_out) "
            "VALUES (:o, 'embedding', :p, :m, :d, :r, :t) "
            "ON CONFLICT (organization_id, purpose, provider, model) DO NOTHING"
        ),
        {
            "o": org,
            "p": provider,
            "m": model,
            "d": deployment,
            "r": retention_mode,
            "t": training_opt_out,
        },
    )
    level = await provider_approved_level(session, org, provider, model)
    assert level is not None  # noqa: S101 - just inserted
    return level


# --- source-synced groups --------------------------------------------------------------


def group_key(source_key: str, external_ref: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in external_ref.lower()).strip("-")
    return f"src-{source_key}-{slug}"[:63].rstrip("-")


async def sync_groups(
    session: AsyncSession,
    org: uuid.UUID,
    source: SourceRow,
    groups: Sequence[ExternalGroup],
    *,
    remove_stale: bool,
) -> tuple[dict[str, uuid.UUID], int]:
    """Mirror the connector's groups (private channels): upsert by (source,
    external_ref), make each group's members exactly the channel members that are
    memberships of this organization (by email), and bump ``authz_updated_at`` of
    every membership whose group membership changed. Returns the groups by external
    reference and the number of memberships whose access changed."""
    members_by_email = {
        str(email).lower(): membership_id
        for email, membership_id in (
            await session.execute(
                text(
                    "SELECT u.email, m.id FROM memberships m JOIN users u ON u.id = m.user_id "
                    "WHERE m.organization_id = :o"
                ),
                {"o": org},
            )
        ).all()
    }
    existing = {
        ref: (group_id, key, name)
        for ref, group_id, key, name in (
            await session.execute(
                text(
                    "SELECT external_ref, id, key, name FROM access_groups "
                    "WHERE organization_id = :o AND source_id = :s"
                ),
                {"o": org, "s": source.id},
            )
        ).all()
    }
    result: dict[str, uuid.UUID] = {}
    changed: set[uuid.UUID] = set()
    for group in groups:
        key = group_key(source.key, group.external_ref)
        if group.external_ref in existing:
            group_id, old_key, old_name = existing[group.external_ref]
            if (str(old_key), old_name) != (key, group.name):
                await session.execute(
                    text("UPDATE access_groups SET key = :k, name = :n WHERE id = :g"),
                    {"k": key, "n": group.name, "g": group_id},
                )
        else:
            taken = (
                await session.execute(
                    text("SELECT 1 FROM access_groups WHERE organization_id = :o AND key = :k"),
                    {"o": org, "k": key},
                )
            ).first()
            if taken is not None:
                raise SourceConfigurationError("source_config:group_key_conflict")
            group_id = (
                await session.execute(
                    text(
                        "INSERT INTO access_groups (organization_id, key, origin, source_id, "
                        "external_ref, name) VALUES (:o, :k, 'source', :s, :r, :n) RETURNING id"
                    ),
                    {"o": org, "k": key, "s": source.id, "r": group.external_ref, "n": group.name},
                )
            ).scalar_one()
        result[group.external_ref] = group_id
        wanted = {members_by_email[e] for e in group.member_emails if e in members_by_email}
        current: set[uuid.UUID] = set(
            (
                await session.execute(
                    text(
                        "SELECT membership_id FROM access_group_members "
                        "WHERE organization_id = :o AND group_id = :g"
                    ),
                    {"o": org, "g": group_id},
                )
            ).scalars()
        )
        if stale := current - wanted:
            await session.execute(
                text(
                    "DELETE FROM access_group_members WHERE organization_id = :o "
                    "AND group_id = :g AND membership_id = ANY(CAST(:m AS uuid[]))"
                ),
                {"o": org, "g": group_id, "m": list(stale)},
            )
        if added := wanted - current:
            await session.execute(
                text(
                    "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
                    "VALUES (:o, :g, :m)"
                ),
                [{"o": org, "g": group_id, "m": m} for m in sorted(added)],
            )
        changed |= stale | added

    if remove_stale:
        gone = [gid for ref, (gid, _, _) in existing.items() if ref not in result]
        if gone:
            changed |= set(
                (
                    await session.execute(
                        text(
                            "SELECT membership_id FROM access_group_members "
                            "WHERE organization_id = :o AND group_id = ANY(CAST(:g AS uuid[]))"
                        ),
                        {"o": org, "g": gone},
                    )
                ).scalars()
            )
            await session.execute(
                text(
                    "DELETE FROM access_groups WHERE organization_id = :o "
                    "AND id = ANY(CAST(:g AS uuid[]))"
                ),
                {"o": org, "g": gone},
            )
    else:
        for ref, (gid, _, _) in existing.items():
            result.setdefault(ref, gid)

    if changed:
        await session.execute(
            text(
                "UPDATE memberships SET authz_updated_at = now() WHERE organization_id = :o "
                "AND id = ANY(CAST(:m AS uuid[]))"
            ),
            {"o": org, "m": list(changed)},
        )
    return result, len(changed)


async def source_groups(
    session: AsyncSession, org: uuid.UUID, source_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    rows = await session.execute(
        text(
            "SELECT external_ref, id FROM access_groups WHERE organization_id = :o "
            "AND source_id = :s"
        ),
        {"o": org, "s": source_id},
    )
    return {str(ref): gid for ref, gid in rows.all()}


# --- documents -------------------------------------------------------------------------


@dataclass(frozen=True)
class ExistingDocument:
    id: uuid.UUID
    status: str
    content_hash: bytes
    access_hash: bytes
    version: int
    sensitivity: str


async def existing_documents(
    session: AsyncSession, org: uuid.UUID, source_id: uuid.UUID
) -> dict[str, ExistingDocument]:
    """Hashes and status of every document of the source -- metadata only, no content."""
    rows = await session.execute(
        text(
            "SELECT external_id, id, status, content_hash, access_hash, version, sensitivity "
            "FROM documents WHERE organization_id = :o AND source_id = :s"
        ),
        {"o": org, "s": source_id},
    )
    return {
        r[0]: ExistingDocument(r[1], r[2], bytes(r[3]), bytes(r[4]), r[5], r[6]) for r in rows.all()
    }


@dataclass(frozen=True)
class ExistingChunk:
    id: uuid.UUID
    embedding_input_hash: bytes
    embedding_status: str
    embedding_model: str | None
    embedding: list[float] | None


async def existing_chunks(
    session: AsyncSession, org: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[ExistingChunk]]:
    if not document_ids:
        return {}
    rows = await session.execute(
        text(
            "SELECT document_id, id, embedding_input_hash, embedding_status, embedding_model, "
            "embedding::text FROM chunks WHERE organization_id = :o "
            "AND document_id = ANY(CAST(:d AS uuid[]))"
        ),
        {"o": org, "d": list(document_ids)},
    )
    result: dict[uuid.UUID, list[ExistingChunk]] = {}
    for doc_id, chunk_id, input_hash, status, model, vector in rows.all():
        result.setdefault(doc_id, []).append(
            ExistingChunk(chunk_id, bytes(input_hash), status, model, _parse_vector(vector))
        )
    return result


def _parse_vector(value: str | None) -> list[float] | None:
    if value is None:
        return None
    return [float(v) for v in value.strip("[]").split(",")]


def vector_literal(values: Sequence[float] | None) -> str | None:
    return None if values is None else "[" + ",".join(repr(float(v)) for v in values) + "]"


@dataclass
class ChunkRow:
    id: uuid.UUID
    ordinal: int
    text: str
    char_start: int
    char_end: int
    heading_path: tuple[str, ...]
    source_anchors: tuple[str, ...]
    occurred_at: datetime | None
    author_display: str | None
    content_hash: bytes
    embedding_input_hash: bytes
    embedding_input: str
    token_count: int
    lexical_header: str
    embedding: list[float] | None
    embedding_model: str | None
    embedding_status: str


_DOCUMENT_UPSERT = text(
    "INSERT INTO documents (id, organization_id, source_id, external_id, document_kind, "
    "title, display_ref, uri, author_display, authority, source_created_at, "
    "source_updated_at, content_hash, access_hash, version, body, supersedes_external_id, "
    "metadata, account_id, sensitivity, access_mode, department_ids, status, ingested_at, "
    "acl_updated_at) "
    "VALUES (:id, :o, :s, :e, :kind, :title, :ref, :uri, :author, :authority, :created, "
    ":updated, :ch, :ah, :version, :body, :supersedes, CAST(:meta AS jsonb), :account, "
    ":sens, :mode, CAST(:depts AS uuid[]), 'active', now(), now()) "
    "ON CONFLICT (organization_id, source_id, external_id) DO UPDATE SET "
    "document_kind = EXCLUDED.document_kind, title = EXCLUDED.title, "
    "display_ref = EXCLUDED.display_ref, uri = EXCLUDED.uri, "
    "author_display = EXCLUDED.author_display, authority = EXCLUDED.authority, "
    "source_created_at = EXCLUDED.source_created_at, "
    "source_updated_at = EXCLUDED.source_updated_at, content_hash = EXCLUDED.content_hash, "
    "access_hash = EXCLUDED.access_hash, version = EXCLUDED.version, body = EXCLUDED.body, "
    "supersedes_external_id = EXCLUDED.supersedes_external_id, metadata = EXCLUDED.metadata, "
    "account_id = EXCLUDED.account_id, sensitivity = EXCLUDED.sensitivity, "
    "access_mode = EXCLUDED.access_mode, department_ids = EXCLUDED.department_ids, "
    "status = 'active', "
    "ingested_at = CASE WHEN documents.content_hash IS DISTINCT FROM EXCLUDED.content_hash "
    "OR :rechunked THEN now() ELSE documents.ingested_at END, "
    "acl_updated_at = CASE WHEN documents.access_hash IS DISTINCT FROM EXCLUDED.access_hash "
    "OR documents.status <> 'active' THEN now() ELSE documents.acl_updated_at END"
)

_CHUNK_UPSERT = text(
    "INSERT INTO chunks (id, organization_id, document_id, ordinal, text, char_start, "
    "char_end, heading_path, source_anchors, occurred_at, author_display, content_hash, "
    "embedding_input_hash, token_count, tsv, embedding, embedding_model, embedding_status) "
    "VALUES (:id, :o, :d, :ordinal, :text, :cs, :ce, CAST(:hp AS text[]), "
    "CAST(:anchors AS text[]), :occurred, :author, :ch, :ih, :tokens, "
    # D-10: title and heading path (weight A), the document's own identifier in the
    # 'simple' configuration so it is never stemmed (weight A), the text (weight D).
    # No structured account name, ever.
    "setweight(to_tsvector('english', :lex_header), 'A') "
    "|| setweight(to_tsvector('simple', :ident), 'A') "
    "|| setweight(to_tsvector('english', :text), 'D'), "
    "CAST(:vec AS vector), :model, :estatus) "
    "ON CONFLICT (id) DO UPDATE SET ordinal = EXCLUDED.ordinal, "
    "char_start = EXCLUDED.char_start, char_end = EXCLUDED.char_end, "
    "heading_path = EXCLUDED.heading_path, source_anchors = EXCLUDED.source_anchors, "
    "occurred_at = EXCLUDED.occurred_at, author_display = EXCLUDED.author_display, "
    "embedding_input_hash = EXCLUDED.embedding_input_hash, "
    "token_count = EXCLUDED.token_count, tsv = EXCLUDED.tsv, embedding = EXCLUDED.embedding, "
    "embedding_model = EXCLUDED.embedding_model, embedding_status = EXCLUDED.embedding_status"
)


async def write_document(
    session: AsyncSession,
    org: uuid.UUID,
    source_id: uuid.UUID,
    doc: CanonicalDocument,
    access: MappedAccess,
    *,
    document_id: uuid.UUID,
    version: int,
    chunks: Sequence[ChunkRow],
    rechunked: bool,
) -> None:
    """New document or content change: the document row, its complete chunk set and
    its source grants, in the caller's (per-document) transaction."""
    await session.execute(
        _DOCUMENT_UPSERT,
        {
            "id": document_id,
            "o": org,
            "s": source_id,
            "e": doc.external_id,
            "kind": doc.kind,
            "title": doc.title,
            "ref": doc.display_ref,
            "uri": doc.uri,
            "author": doc.author,
            "authority": doc.authority,
            "created": doc.created_at,
            "updated": doc.updated_at,
            "ch": doc.content_hash,
            "ah": access.access_hash,
            "version": version,
            "body": doc.body,
            "supersedes": doc.supersedes_external_id,
            "meta": json.dumps(doc.metadata, sort_keys=True),
            "account": access.account_id,
            "sens": access.sensitivity,
            "mode": access.access_mode,
            "depts": list(access.department_ids),
            "rechunked": rechunked,
        },
    )
    await session.execute(
        text(
            "DELETE FROM chunks WHERE organization_id = :o AND document_id = :d "
            "AND NOT (id = ANY(CAST(:keep AS uuid[])))"
        ),
        {"o": org, "d": document_id, "keep": [c.id for c in chunks]},
    )
    if chunks:
        await session.execute(
            _CHUNK_UPSERT,
            [
                {
                    "id": c.id,
                    "o": org,
                    "d": document_id,
                    "ordinal": c.ordinal,
                    "text": c.text,
                    "cs": c.char_start,
                    "ce": c.char_end,
                    "hp": list(c.heading_path),
                    "anchors": list(c.source_anchors),
                    "occurred": c.occurred_at,
                    "author": c.author_display,
                    "ch": c.content_hash,
                    "ih": c.embedding_input_hash,
                    "tokens": c.token_count,
                    "lex_header": c.lexical_header,
                    "ident": doc.display_ref or "",
                    "vec": vector_literal(c.embedding),
                    "model": c.embedding_model,
                    "estatus": c.embedding_status,
                }
                for c in chunks
            ],
        )
    await replace_source_grants(session, org, document_id, access)


async def replace_source_grants(
    session: AsyncSession, org: uuid.UUID, document_id: uuid.UUID, access: MappedAccess
) -> None:
    """Source-origin grants are replaced wholesale; admin-origin grants are untouched."""
    wanted = set(access.source_grants)
    current: set[tuple[str, uuid.UUID]] = {
        (t, m or d or g)
        for t, m, d, g in (
            await session.execute(
                text(
                    "SELECT principal_type, membership_id, department_id, group_id "
                    "FROM document_grants WHERE organization_id = :o AND document_id = :d "
                    "AND origin = 'source'"
                ),
                {"o": org, "d": document_id},
            )
        ).all()
    }
    if current == wanted:
        return
    await session.execute(
        text(
            "DELETE FROM document_grants WHERE organization_id = :o AND document_id = :d "
            "AND origin = 'source'"
        ),
        {"o": org, "d": document_id},
    )
    column = {"membership": "membership_id", "department": "department_id", "group": "group_id"}
    for principal_type, principal_id in sorted(wanted, key=lambda g: (g[0], str(g[1]))):
        await session.execute(
            text(
                "INSERT INTO document_grants (organization_id, document_id, principal_type, "  # noqa: S608 - column from a fixed map
                f"{column[principal_type]}, origin) VALUES (:o, :d, :t, :p, 'source')"
            ),
            {"o": org, "d": document_id, "t": principal_type, "p": principal_id},
        )


async def update_access(
    session: AsyncSession,
    org: uuid.UUID,
    document_id: uuid.UUID,
    access: MappedAccess,
    *,
    eligible: bool,
) -> int:
    """ACL-only change: access columns, source grants and ``acl_updated_at``; content,
    chunks and embeddings untouched -- except the embedding consequences of a new
    classification (the AI egress policy): chunks that may no longer be sent to the
    provider lose their vectors now; chunks that newly may are marked ``pending`` for
    the embedding backfill. Returns the number of vectors removed."""
    await session.execute(
        text(
            "UPDATE documents SET account_id = :account, sensitivity = :sens, "
            "access_mode = :mode, department_ids = CAST(:depts AS uuid[]), access_hash = :ah, "
            "status = 'active', acl_updated_at = now() "
            "WHERE organization_id = :o AND id = :d"
        ),
        {
            "o": org,
            "d": document_id,
            "account": access.account_id,
            "sens": access.sensitivity,
            "mode": access.access_mode,
            "depts": list(access.department_ids),
            "ah": access.access_hash,
        },
    )
    await replace_source_grants(session, org, document_id, access)
    if eligible:
        await session.execute(
            text(
                "UPDATE chunks SET embedding_status = 'pending' WHERE organization_id = :o "
                "AND document_id = :d AND embedding_status = 'policy_excluded'"
            ),
            {"o": org, "d": document_id},
        )
        return 0
    removed = await session.execute(
        text(
            "UPDATE chunks SET embedding = NULL, embedding_model = NULL, "
            "embedding_status = 'policy_excluded' WHERE organization_id = :o "
            "AND document_id = :d AND embedding_status <> 'policy_excluded' RETURNING id"
        ),
        {"o": org, "d": document_id},
    )
    return len(removed.all())


async def quarantine(session: AsyncSession, org: uuid.UUID, document_id: uuid.UUID) -> bool:
    """Access metadata of an existing document can no longer be mapped: make it
    unreadable (the policy reads only ``active`` documents) rather than leave it on its
    old, possibly wider, access. Its content stays until the next successful sync."""
    result = await session.execute(
        text(
            "UPDATE documents SET status = 'quarantined', acl_updated_at = now() "
            "WHERE organization_id = :o AND id = :d AND status <> 'quarantined' RETURNING id"
        ),
        {"o": org, "d": document_id},
    )
    return result.first() is not None


async def resolve_supersession(session: AsyncSession, org: uuid.UUID, source_id: uuid.UUID) -> int:
    """Link each document to the document of the same source that its source says it
    replaces; clear links whose target is gone. Idempotent."""
    result = await session.execute(
        text(
            "UPDATE documents d SET supersedes_document_id = t.id "
            "FROM (SELECT s.id AS sid, target.id AS id FROM documents s "
            "      LEFT JOIN documents target ON target.organization_id = s.organization_id "
            "      AND target.source_id = s.source_id "
            "      AND target.external_id = s.supersedes_external_id "
            "      AND target.id <> s.id "
            "      WHERE s.organization_id = :o AND s.source_id = :s) t "
            "WHERE d.id = t.sid AND d.supersedes_document_id IS DISTINCT FROM t.id "
            "RETURNING d.id"
        ),
        {"o": org, "s": source_id},
    )
    return len(result.all())


async def delete_documents(
    session: AsyncSession, org: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> int:
    """Hard delete (cascade: chunks with their vectors and lexical entries, and every
    grant on them). Only the current version is ever stored (ingestion-design §5)."""
    if not document_ids:
        return 0
    ids = list(document_ids)
    await session.execute(
        text(
            "UPDATE documents SET supersedes_document_id = NULL WHERE organization_id = :o "
            "AND supersedes_document_id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"o": org, "ids": ids},
    )
    result = await session.execute(
        text(
            "DELETE FROM documents WHERE organization_id = :o AND id = ANY(CAST(:ids AS uuid[])) "
            "RETURNING id"
        ),
        {"o": org, "ids": ids},
    )
    return len(result.all())


# --- embeddings ------------------------------------------------------------------------


@dataclass(frozen=True)
class EmbeddingTarget:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    kind: DocumentKind
    title: str
    heading_path: tuple[str, ...]
    text: str
    embedding_status: str


async def exclude_ineligible(
    session: AsyncSession, org: uuid.UUID, source_id: uuid.UUID, allowed: Sequence[str]
) -> int:
    """Remove every vector of the source's chunks whose document may not be sent to the
    embedding provider at the current effective level (egress policy lowered, or a
    classification raised). Returns the number of chunks newly excluded."""
    result = await session.execute(
        text(
            "UPDATE chunks c SET embedding = NULL, embedding_model = NULL, "
            "embedding_status = 'policy_excluded' FROM documents d "
            "WHERE c.organization_id = :o AND d.organization_id = :o AND d.id = c.document_id "
            "AND d.source_id = :s AND c.embedding_status <> 'policy_excluded' "
            "AND NOT (d.sensitivity = ANY(CAST(:allowed AS text[]))) RETURNING c.id"
        ),
        {"o": org, "s": source_id, "allowed": list(allowed)},
    )
    return len(result.all())


async def embedding_targets(
    session: AsyncSession,
    org: uuid.UUID,
    source_id: uuid.UUID,
    allowed: Sequence[str],
    *,
    reembed: bool,
) -> list[EmbeddingTarget]:
    """Chunks of active documents that may be sent and still need a vector: pending,
    failed, newly eligible -- and with ``reembed``, every eligible chunk (a provider,
    model or input-template change)."""
    statuses = ["pending", "failed", "policy_excluded"]
    rows = await session.execute(
        text(
            "SELECT c.id, d.id, d.document_kind, d.title, c.heading_path, c.text, "
            "c.embedding_status "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            "AND d.organization_id = c.organization_id "
            "WHERE c.organization_id = :o AND d.source_id = :s AND d.status = 'active' "
            "AND d.sensitivity = ANY(CAST(:allowed AS text[])) "
            "AND (c.embedding_status = ANY(CAST(:statuses AS text[])) "
            "     OR CAST(:reembed AS boolean)) "
            "ORDER BY d.id, c.ordinal"
        ),
        {
            "o": org,
            "s": source_id,
            "allowed": list(allowed),
            "statuses": statuses,
            "reembed": reembed,
        },
    )
    return [
        EmbeddingTarget(cid, did, kind, title, tuple(hp), body, status)
        for cid, did, kind, title, hp, body, status in rows.all()
    ]


async def mark_pending(
    session: AsyncSession, org: uuid.UUID, source_id: uuid.UUID, allowed: Sequence[str]
) -> int:
    """No provider configured: eligible chunks without a vector wait as ``pending``."""
    result = await session.execute(
        text(
            "UPDATE chunks c SET embedding_status = 'pending' FROM documents d "
            "WHERE c.organization_id = :o AND d.organization_id = :o AND d.id = c.document_id "
            "AND d.source_id = :s AND c.embedding_status = 'policy_excluded' "
            "AND d.sensitivity = ANY(CAST(:allowed AS text[])) RETURNING c.id"
        ),
        {"o": org, "s": source_id, "allowed": list(allowed)},
    )
    return len(result.all())


async def store_embeddings(
    session: AsyncSession,
    org: uuid.UUID,
    updates: Iterable[tuple[uuid.UUID, list[float] | None, str | None, str]],
) -> None:
    rows = [
        {"o": org, "id": cid, "vec": vector_literal(vec), "model": model, "st": status}
        for cid, vec, model, status in updates
    ]
    if rows:
        await session.execute(
            text(
                "UPDATE chunks SET embedding = CAST(:vec AS vector), embedding_model = :model, "
                "embedding_status = :st WHERE organization_id = :o AND id = :id"
            ),
            rows,
        )


# --- sync runs and failures --------------------------------------------------------------


async def abort_interrupted_runs(
    session: AsyncSession, org: uuid.UUID, source_id: uuid.UUID
) -> int:
    """Runs of this source still ``running`` while the caller holds the source's sync lock
    were interrupted (their process died before it could record the outcome): record
    them ``aborted`` / ``internal_error:interrupted``, so no run stays running forever."""
    result = await session.execute(
        text(
            "UPDATE source_sync_runs SET status = 'aborted', finished_at = now(), "
            "error_code = 'internal_error:interrupted' "
            "WHERE organization_id = :o AND source_id = :s AND status = 'running' "
            "RETURNING id"
        ),
        {"o": org, "s": source_id},
    )
    return len(result.all())


async def open_run(
    session: AsyncSession, org: uuid.UUID, source_id: uuid.UUID, mode: str
) -> uuid.UUID:
    run_id: uuid.UUID = (
        await session.execute(
            text(
                "INSERT INTO source_sync_runs (organization_id, source_id, mode) "
                "VALUES (:o, :s, :m) RETURNING id"
            ),
            {"o": org, "s": source_id, "m": mode},
        )
    ).scalar_one()
    return run_id


async def close_run(
    session: AsyncSession,
    org: uuid.UUID,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    status: str,
    counts: dict[str, Any],
    acl_synced: bool,
) -> None:
    columns = ", ".join(f"{name} = :{name}" for name in sorted(counts))
    await session.execute(
        text(
            f"UPDATE source_sync_runs SET status = :status, finished_at = now(), {columns} "  # noqa: S608 - column names from a fixed set
            "WHERE organization_id = :o AND id = :r"
        ),
        {"o": org, "r": run_id, "status": status, **counts},
    )
    await session.execute(
        text(
            "UPDATE sources SET last_synced_at = now(), "
            "last_succeeded_at = CASE WHEN :ok THEN now() ELSE last_succeeded_at END, "
            "acl_synced_at = CASE WHEN :acl THEN now() ELSE acl_synced_at END "
            "WHERE organization_id = :o AND id = :s"
        ),
        {"o": org, "s": source_id, "ok": status == "succeeded", "acl": acl_synced},
    )


@dataclass(frozen=True)
class Failure:
    stage: str
    code: str
    external_id: str | None = None
    location: str | None = None
    params: tuple[tuple[str, str | int], ...] = ()


async def record_failures(
    session: AsyncSession,
    org: uuid.UUID,
    run_id: uuid.UUID,
    ref_key: bytes,
    failures: Sequence[Failure],
) -> None:
    if not failures:
        return
    await session.execute(
        text(
            "INSERT INTO sync_record_failures (organization_id, sync_run_id, external_id, "
            "location, opaque_ref, stage, error_code, message_params) "
            "VALUES (:o, :r, :e, :loc, :ref, :stage, :code, CAST(:params AS jsonb))"
        ),
        [
            {
                "o": org,
                "r": run_id,
                "e": f.external_id,
                "loc": f.location,
                "ref": opaque_ref(ref_key, f.external_id or f.location or ""),
                "stage": f.stage,
                "code": f.code,
                "params": json.dumps(dict(f.params), sort_keys=True),
            }
            for f in failures
        ],
    )
