"""The conditions every retrieval statement carries (retrieval-design §3).

Each retriever -- exact, lexical, semantic -- and the evidence hydration builds ONE SQL
statement whose WHERE clause contains all of:

1. **Tenant**: the chunk and its document belong to the reader's organization (from the
   server-side session). Row-level security is the second, independent layer.
2. **Lifecycle**: only ``active`` documents are searchable; ``deleted`` and
   ``quarantined`` never are.
3. **Authorization**: ``authz.readable_documents_predicate(ctx, documents)`` -- the one
   policy, compiled by ``lattice.authz``. Retrieval never writes an access condition.
4. **Scope** (optional): account, source types, time range -- ANDed, so they can only
   narrow.

The retrieval-specific condition (identifier, text match, vector) is added by the
retriever. Nothing unreadable leaves PostgreSQL, so there is nothing to filter later.

The structured account is joined **only through** ``authz.visible_accounts_predicate``
(LEFT JOIN): a readable document whose account is outside the reader's scope yields no
account at all (decision D-8), so hidden account metadata never reaches Python -- it
cannot be displayed, boosted on or matched (D-10).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, FromClause, Join, Row, Table, and_, func

from lattice.authz import (
    AuthorizationContext,
    Document,
    readable_documents_predicate,
    visible_accounts_predicate,
)
from lattice.ingestion.models import Chunk
from lattice.orgs.models import Account
from lattice.retrieval.models import Candidate, Channel


def _table(model: type[Chunk] | type[Document] | type[Account]) -> Table:
    table = model.__table__
    if not isinstance(table, Table):  # pragma: no cover - declarative models map to Tables
        raise TypeError(model)
    return table


CHUNKS = _table(Chunk)
DOCUMENTS = _table(Document)
ACCOUNTS = _table(Account)
VISIBLE_ACCOUNT = ACCOUNTS.alias("visible_account")

# Execution option naming the retrieval path of a statement (tests capture and
# re-execute statements by it; the value never affects the SQL).
CHANNEL_OPTION = "lattice_retrieval_channel"


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    """The client's scope after validation: the account is one the reader may see."""

    account_id: uuid.UUID | None = None
    document_kinds: tuple[str, ...] = ()
    since: datetime | None = None  # inclusive
    until: datetime | None = None  # exclusive


def evidence_time(chunks: FromClause = CHUNKS, documents: FromClause = DOCUMENTS) -> Any:
    """When this content happened: the chunk's own time, else its document's."""
    return func.coalesce(chunks.c.occurred_at, documents.c.source_updated_at)


def permitted_chunk_conditions(
    ctx: AuthorizationContext,
    scope: ResolvedScope,
    chunks: FromClause = CHUNKS,
    documents: FromClause = DOCUMENTS,
) -> list[ColumnElement[bool]]:
    """Tenant, lifecycle, authorization and scope -- for one statement's WHERE."""
    conditions: list[ColumnElement[bool]] = [
        # 1. Tenant.
        chunks.c.organization_id == ctx.organization_id,
        documents.c.organization_id == ctx.organization_id,
        # 2. Lifecycle.
        documents.c.status == "active",
        # 3. Authorization (lattice.authz).
        readable_documents_predicate(ctx, documents),
    ]
    # 4. Scope: narrowing only.
    if scope.account_id is not None:
        conditions.append(documents.c.account_id == scope.account_id)
    if scope.document_kinds:
        conditions.append(documents.c.document_kind.in_(scope.document_kinds))
    if scope.since is not None:
        conditions.append(evidence_time(chunks, documents) >= scope.since)
    if scope.until is not None:
        conditions.append(evidence_time(chunks, documents) < scope.until)
    return conditions


def chunks_with_documents(ctx: AuthorizationContext) -> Join:
    """chunks ⋈ documents ⟕ visible account."""
    return CHUNKS.join(
        DOCUMENTS,
        and_(
            DOCUMENTS.c.id == CHUNKS.c.document_id,
            DOCUMENTS.c.organization_id == CHUNKS.c.organization_id,
        ),
    ).outerjoin(
        VISIBLE_ACCOUNT,
        and_(
            VISIBLE_ACCOUNT.c.id == DOCUMENTS.c.account_id,
            VISIBLE_ACCOUNT.c.organization_id == ctx.organization_id,
            visible_accounts_predicate(ctx, VISIBLE_ACCOUNT),
        ),
    )


def candidate_columns() -> list[Any]:
    """What fusion and rerank need; no text, no access attributes."""
    return [
        CHUNKS.c.organization_id,
        DOCUMENTS.c.id.label("document_id"),
        CHUNKS.c.id.label("chunk_id"),
        DOCUMENTS.c.document_kind,
        DOCUMENTS.c.authority,
        evidence_time().label("occurred_at"),
        CHUNKS.c.content_hash,
        VISIBLE_ACCOUNT.c.id.label("visible_account_id"),
        DOCUMENTS.c.external_id,
        CHUNKS.c.ordinal,
    ]


def stable_order() -> list[Any]:
    """Deterministic tie-breaks after a channel's own ordering, identical across
    databases (document IDs derive from randomly generated source IDs)."""
    return [DOCUMENTS.c.external_id, CHUNKS.c.ordinal, CHUNKS.c.id]


def to_candidates(rows: Sequence[Row[Any]], channel: Channel) -> list[Candidate]:
    """Every row, in the order the database returned it. No filtering happens here --
    or anywhere after the SQL: the statement already selected only permitted rows."""
    return [
        Candidate(
            organization_id=row.organization_id,
            document_id=row.document_id,
            chunk_id=row.chunk_id,
            channel=channel,
            rank=rank,
            score=float(row.score),
            document_kind=row.document_kind,
            authority=row.authority,
            occurred_at=row.occurred_at,
            content_hash=bytes(row.content_hash),
            visible_account_id=row.visible_account_id,
            sort_key=(row.external_id, row.ordinal),
        )
        for rank, row in enumerate(rows, start=1)
    ]
