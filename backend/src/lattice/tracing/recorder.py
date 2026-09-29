"""Query traces: record one row per Ask, read it back re-authorized (security-model §8,
SEC-9, SEC-15; data-model §7).

What a trace holds: identifiers, versions (policy, retrieval config, rerank, prompt),
provider/model/trust class, the egress decision and its reason code, counts and codes
of claim validation, statuses, timings and token usage; the retrieval trace (chunk IDs
and scores of permitted rows only); the validated answer; and the asker's question
(for the asker's own history, retention-limited). What it never holds: the prompt,
evidence text, rejected claim text, provider payloads or error bodies, secrets or
secret references.

Reading (``read_trace``) re-authorizes for the *viewer*, now:

* **the asker** sees their question, status, the answer and the evidence references
  that are still readable to them. If any evidence of that Ask -- cited or not -- is no
  longer readable, the stored answer is hidden (``answer_hidden``): a trace must not let
  a revoked user re-read content derived from it, and the answer carries metadata of
  uncited evidence too (supersession items and notices name documents, their IDs and
  dates). Likewise for accounts (D-8): the trace records, as IDs, the accounts whose
  metadata the answer may carry (``answer_account_ids``: clarification choices, the
  account attribute of prompt evidence); if any of them -- or the account the request
  was scoped to -- is no longer visible to the asker, or no longer exists, the answer is
  hidden and the scope's account withheld. Unknown dependencies (NULL) hide the answer;
* **an administrator** looking at another member's trace sees operational structure
  only: member, time, status, error code, stage durations, provider/model, prompt and
  policy versions, token usage. Never the question, answer, evidence references,
  counts, scope, accounts or sources (D-5);
* anyone else gets nothing, exactly as for a trace that does not exist (404).
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import insert, select

from lattice.authz import resolve_authorization_context
from lattice.db.engine import Database
from lattice.retrieval import lookup_evidence
from lattice.retrieval.service import visible_accounts
from lattice.tracing.models import QueryTrace


async def record_trace(db: Database, trace: dict[str, Any]) -> None:
    """One INSERT as the runtime role, bound to the trace's organization."""
    async with db.tenant_transaction(trace["organization_id"]) as session:
        await session.execute(insert(QueryTrace).values(**trace))


class OperationalTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    trace_id: uuid.UUID
    membership_id: uuid.UUID
    created_at: datetime
    status: str
    status_reason: str | None
    error_code: str | None
    provider: str | None
    model: str | None
    deployment: str | None
    prompt_version: str | None
    policy_version: str | None
    retrieval_config_version: str | None
    generation_calls: int
    timings_ms: dict[str, Any]
    usage: dict[str, Any]


class AskerTrace(OperationalTrace):
    view: Literal["asker"] = "asker"
    question: str
    question_sensitivity: str
    scope: dict[str, Any]
    answer: dict[str, Any] | None
    answer_hidden: bool
    evidence_ids: list[uuid.UUID]  # still readable by the asker, now
    decision_reason: str | None
    validation: dict[str, Any]


class AdminTrace(OperationalTrace):
    view: Literal["operational"] = "operational"


def _operational(row: QueryTrace) -> dict[str, Any]:
    egress = row.egress or {}
    retrieval = row.retrieval or {}
    validation = row.validation or {}
    timings = row.timings_ms or {}
    return {
        "trace_id": row.id,
        "membership_id": row.membership_id,
        "created_at": row.created_at,
        "status": row.status,
        "status_reason": row.status_reason,
        "error_code": row.error_code,
        "provider": egress.get("provider"),
        "model": egress.get("model"),
        "deployment": egress.get("deployment"),
        "prompt_version": egress.get("prompt_version"),
        "policy_version": retrieval.get("policy_version"),
        "retrieval_config_version": retrieval.get("config_version"),
        "generation_calls": int(validation.get("generation_calls", 0)),
        # Stage durations only: the retrieval stage map is kept, its counts are not.
        "timings_ms": {k: v for k, v in timings.items() if k in ("policy", "generation", "total")}
        | {
            "retrieval_total": (timings.get("retrieval") or {}).get("total"),
        },
        "usage": row.usage or {},
    }


async def read_trace(
    db: Database,
    *,
    organization_id: uuid.UUID,
    viewer_membership_id: uuid.UUID,
    viewer_is_admin: bool,
    trace_id: uuid.UUID,
) -> AskerTrace | AdminTrace | None:
    async with db.tenant_snapshot(organization_id) as session:
        row = (
            await session.execute(
                select(QueryTrace).where(
                    QueryTrace.organization_id == organization_id, QueryTrace.id == trace_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.membership_id != viewer_membership_id:
            return AdminTrace(**_operational(row)) if viewer_is_admin else None
        ctx = await resolve_authorization_context(
            session, organization_id=organization_id, membership_id=viewer_membership_id
        )
        wanted = sorted(set(row.evidence_chunk_ids) | set(row.cited_chunk_ids))
        readable = await lookup_evidence(session, ctx, wanted)
        visible = await visible_accounts(session, ctx)
    scope = dict(row.scope or {})
    scope_key = scope.get("account")
    # Account keys are case-insensitive (citext), as when the scope was resolved.
    scope_lost = scope_key is not None and str(scope_key).casefold() not in {
        a.key.casefold() for a in visible
    }
    if scope_lost:
        del scope["account"]
    accounts_lost = row.answer_account_ids is None or any(
        a not in {v.id for v in visible} for a in row.answer_account_ids
    )
    hidden = any(c not in readable for c in wanted) or accounts_lost or scope_lost
    return AskerTrace(
        **_operational(row),
        question=row.question_text,
        question_sensitivity=row.question_sensitivity,
        scope=scope,
        answer=None if hidden else row.answer,
        answer_hidden=hidden,
        evidence_ids=[c for c in row.evidence_chunk_ids if c in readable],
        decision_reason=(row.egress or {}).get("decision_reason"),
        validation=row.validation,
    )
