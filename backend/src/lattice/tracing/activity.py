"""Activity: lists of Ask traces (design-system B11; security-model §8, SEC-15).

* ``own_activity`` -- a member's own questions, newest first: time, status and the
  question they typed (their own history). Nothing derived from evidence: no answer, no
  evidence references, no scope. Opening one is ``GET /v1/traces/{id}``, which
  re-authorizes everything for the asker at that moment.
* ``organization_activity`` -- for administrators: every member's Asks as operational
  rows only -- member, time, status, error code, provider/model, duration, token usage
  (the fields of the administrator's trace view). Never the question, answer, evidence,
  counts, scope, accounts or sources (D-5).

Keyset pagination: ``before`` is a trace ID from the previous page; an ID the viewer
could not list is treated as absent (the list starts from the top).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Select, and_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.identity.models import User
from lattice.orgs.models import Membership
from lattice.tracing.models import QueryTrace

MAX_PAGE: Final = 100


@dataclass(frozen=True)
class OwnActivity:
    trace_id: uuid.UUID
    created_at: datetime
    status: str
    status_reason: str | None
    question: str


@dataclass(frozen=True)
class OperationalActivity:
    trace_id: uuid.UUID
    created_at: datetime
    membership_id: uuid.UUID
    member_name: str
    status: str
    status_reason: str | None
    error_code: str | None
    provider: str | None
    model: str | None
    duration_ms: float | None
    generation_calls: int
    input_tokens: int
    output_tokens: int


async def _after(
    session: AsyncSession, query: Select[*tuple[Any, ...]], before: uuid.UUID | None
) -> Select[*tuple[Any, ...]]:
    if before is None:
        return query
    anchor = (
        await session.execute(
            query.with_only_columns(QueryTrace.created_at, QueryTrace.id)
            .where(QueryTrace.id == before)
            .order_by(None)
        )
    ).one_or_none()
    if anchor is None:
        return query
    return query.where(tuple_(QueryTrace.created_at, QueryTrace.id) < tuple_(*anchor))


async def own_activity(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    limit: int,
    before: uuid.UUID | None = None,
) -> list[OwnActivity]:
    query: Select[*tuple[Any, ...]] = select(
        QueryTrace.id,
        QueryTrace.created_at,
        QueryTrace.status,
        QueryTrace.status_reason,
        QueryTrace.question_text,
    ).where(
        QueryTrace.organization_id == organization_id,
        QueryTrace.membership_id == membership_id,
    )
    query = await _after(session, query, before)
    rows = await session.execute(
        query.order_by(QueryTrace.created_at.desc(), QueryTrace.id.desc()).limit(
            min(limit, MAX_PAGE)
        )
    )
    return [OwnActivity(*row) for row in rows]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


async def organization_activity(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    limit: int,
    before: uuid.UUID | None = None,
) -> list[OperationalActivity]:
    """The caller checks the administrator capability."""
    query: Select[*tuple[Any, ...]] = (
        select(
            QueryTrace.id,
            QueryTrace.created_at,
            QueryTrace.membership_id,
            User.display_name,
            QueryTrace.status,
            QueryTrace.status_reason,
            QueryTrace.error_code,
            QueryTrace.egress,
            QueryTrace.validation,
            QueryTrace.timings_ms,
            QueryTrace.usage,
        )
        .join(
            Membership,
            and_(
                Membership.organization_id == QueryTrace.organization_id,
                Membership.id == QueryTrace.membership_id,
            ),
        )
        .join(User, User.id == Membership.user_id)
        .where(QueryTrace.organization_id == organization_id)
    )
    query = await _after(session, query, before)
    rows = await session.execute(
        query.order_by(QueryTrace.created_at.desc(), QueryTrace.id.desc()).limit(
            min(limit, MAX_PAGE)
        )
    )
    result: list[OperationalActivity] = []
    for r in rows:
        egress = r.egress or {}
        usage = r.usage or {}
        result.append(
            OperationalActivity(
                trace_id=r.id,
                created_at=r.created_at,
                membership_id=r.membership_id,
                member_name=r.display_name,
                status=r.status,
                status_reason=r.status_reason,
                error_code=r.error_code,
                provider=egress.get("provider"),
                model=egress.get("model"),
                duration_ms=_number((r.timings_ms or {}).get("total")),
                generation_calls=int((r.validation or {}).get("generation_calls", 0)),
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
            )
        )
    return result
