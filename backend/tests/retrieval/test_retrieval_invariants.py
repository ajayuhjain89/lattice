"""The E4 tripwire, the trace, one consistent snapshot, and no existence oracle.

* Tripwire: ``assert_all_readable`` runs over every candidate the database returned and
  over the final evidence. If a retriever ever hands over an unreadable row, the whole
  request aborts -- the row is never silently dropped and the rest returned.
* Trace: counts per stage and the degradation state are recorded; nothing in the trace
  or the log line carries the question, content of unreadable documents, hidden
  account metadata or a credential.
* Snapshot: a permission change committed while a request runs applies to the next
  request; the running one never mixes two permission states (and the tripwire never
  fires falsely).
* Existence oracle: the reader's result -- evidence, counts, trace structure -- is the
  same whether a strongly matching document they cannot read exists or not.
"""

import contextlib
import json
import logging
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import lattice.retrieval.service as service
from lattice.authz import AuthorizationContext, AuthorizationInvariantViolation
from lattice.db.engine import Database
from lattice.providers.embedding import FakeEmbeddingProvider, Purpose
from lattice.retrieval import RetrievalConfig, RetrievalResult
from lattice.retrieval.lexical import lexical_candidates
from lattice.retrieval.models import Candidate
from lattice.retrieval.permitted import ResolvedScope

from .world import FAKE, Org, documents_in, new_org, search


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.department("leadership")
    await org.account("acme-corp", "Acme Corp", ["Acme"])
    await org.account("kestrel-bank", "Kestrel Bank", ["Kestrel"])
    await org.member("maya", scope="assigned", accounts=["acme-corp"])
    await org.member("grace", clearance="confidential", departments=["leadership"])
    await org.document(
        "plan", "Acme Q3 success plan", ["SCIM planned for Q3."], account="acme-corp"
    )
    await org.document(
        "exec",
        "Acme executive escalation notes SCIM",
        ["SCIM credit if GA slips. LATTICE-CANARY-B-7f3a9c"],
        sensitivity="confidential",
        account="acme-corp",
        departments=["leadership"],
    )
    await org.document(
        "kestrel", "Kestrel renewal", ["Kestrel SCIM renewal risk"], account="kestrel-bank"
    )
    return org


# --- E4 tripwire ---------------------------------------------------------------------------


async def test_all_readable_evidence_passes_the_tripwire(app_db: Database, org: Org) -> None:
    result = await search(app_db, org, "maya", "SCIM")
    assert documents_in(org, result) == ["plan"]


def _leaky(org: Org, leaked: str) -> Any:
    """A lexical retriever with a bug: it also returns a chunk the reader cannot read."""
    real = lexical_candidates

    async def lexical(
        session: AsyncSession,
        ctx: AuthorizationContext,
        scope: ResolvedScope,
        text: str,
        config: RetrievalConfig,
        **kwargs: Any,
    ) -> list[Candidate]:
        found = await real(session, ctx, scope, text, config, **kwargs)
        chunk = org.chunks[leaked][0]
        return [
            *found,
            Candidate(
                organization_id=org.id,
                document_id=org.documents[leaked],
                chunk_id=chunk,
                channel="lexical",
                rank=len(found) + 1,
                score=0.0,
                document_kind="doc",
                authority="working",
                occurred_at=None,
                content_hash=chunk.bytes,
                visible_account_id=None,
                sort_key=(leaked, 0),
            ),
        ]

    return lexical


async def test_an_unreadable_candidate_aborts_the_whole_request(
    app_db: Database, org: Org, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(service, "lexical_candidates", _leaky(org, "exec"))
    with caplog.at_level(logging.ERROR), pytest.raises(AuthorizationInvariantViolation):
        await search(app_db, org, "maya", "SCIM")
    events = [r for r in caplog.records if r.getMessage() == "security.invariant_violated"]
    assert len(events) == 1
    fields = events[0].__dict__["fields"]
    assert fields["document_ids"] == [str(org.documents["exec"])]
    assert "LATTICE-CANARY" not in caplog.text
    assert "escalation" not in caplog.text  # no titles or text in the security event


async def test_a_foreign_organizations_candidate_aborts_too(
    owner_db: Database, app_db: Database, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = await new_org(owner_db, "veridian")
    await other.document("theirs", "Acme SCIM", ["SCIM"])
    real = lexical_candidates

    async def lexical(*args: Any, **kwargs: Any) -> list[Candidate]:
        found = await real(*args, **kwargs)
        chunk = other.chunks["theirs"][0]
        foreign = Candidate(
            organization_id=other.id,
            document_id=other.documents["theirs"],
            chunk_id=chunk,
            channel="lexical",
            rank=1,
            score=1.0,
            document_kind="doc",
            authority="working",
            occurred_at=None,
            content_hash=chunk.bytes,
            visible_account_id=None,
            sort_key=("theirs", 0),
        )
        return [foreign, *found]

    monkeypatch.setattr(service, "lexical_candidates", lexical)
    with pytest.raises(AuthorizationInvariantViolation):
        await search(app_db, org, "grace", "SCIM")


async def test_the_tripwire_never_filters(
    app_db: Database, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even when the readable part of the answer would be useful, nothing is returned."""
    monkeypatch.setattr(service, "lexical_candidates", _leaky(org, "exec"))
    returned: list[RetrievalResult] = []
    with contextlib.suppress(AuthorizationInvariantViolation):
        returned.append(await search(app_db, org, "maya", "SCIM plan"))
    assert returned == []


# --- trace ------------------------------------------------------------------------------------


async def test_the_trace_records_stages_and_degradation_without_content(
    app_db: Database, org: Org, caplog: pytest.LogCaptureFixture
) -> None:
    question = "Acme SCIM plan secret-question-marker"
    with caplog.at_level(logging.INFO):
        result = await search(app_db, org, "maya", question)
    trace = result.trace
    assert set(trace.counts) == {"exact", "lexical", "semantic", "merged", "reranked", "final"}
    assert trace.counts["final"] == len(result.evidence)
    assert trace.counts["lexical"] == len(trace.stages["lexical"])
    assert trace.semantic_status == "matched"
    assert trace.degraded is False
    assert trace.config_version == RetrievalConfig().version
    assert trace.rrf_k == 60
    assert trace.policy_version
    assert {"exact", "lexical", "semantic", "fuse_rerank", "tripwire", "total"} <= set(
        trace.timings_ms
    )
    dumped = trace.model_dump_json()
    for forbidden in (question, "secret-question-marker", "LATTICE-CANARY", "Acme", "SCIM"):
        assert forbidden not in dumped
    # Every chunk in the trace is one the reader may read.
    readable = set(org.chunks["plan"])
    traced = {c.chunk_id for s in trace.stages.values() for c in s} | {
        r.chunk_id for r in trace.reranked
    }
    assert traced <= readable
    line = next(r for r in caplog.records if r.getMessage() == "retrieval.completed")
    logged = json.dumps(line.__dict__["fields"])
    assert "secret-question-marker" not in logged
    assert "SCIM" not in logged


async def test_the_trace_marks_degradation(app_db: Database, org: Org) -> None:
    result = await search(app_db, org, "maya", "SCIM", classification="restricted")
    assert (result.trace.semantic_status, result.trace.semantic_reason) == (
        "policy_excluded",
        "query_classification",
    )
    assert result.trace.degraded is True


async def test_hidden_account_metadata_is_nowhere_in_the_result(app_db: Database, org: Org) -> None:
    await org.document(
        "granted",
        "Kestrel incident RCA",
        ["Kestrel Bank users could not sign in (SCIM unrelated)."],
        account="kestrel-bank",
        grants=[("membership", "maya")],
    )
    result = await search(app_db, org, "maya", "Kestrel incident SCIM")
    assert "granted" in documents_in(org, result)
    dumped = result.model_dump_json()
    assert str(org.accounts["kestrel-bank"]) not in dumped
    assert "kestrel-bank" not in dumped
    for evidence in result.evidence:
        assert evidence.account is None or evidence.account.key == "acme-corp"


# --- one snapshot per request -----------------------------------------------------------------


class RevokingProvider(FakeEmbeddingProvider):
    """Commits a revocation while the request is running (between the lexical query
    and the semantic query), from another connection."""

    def __init__(self, org: Org, member: str) -> None:
        super().__init__()
        self.org, self.member = org, member
        self.model = FAKE.model

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        await self.org.execute(
            "DELETE FROM account_assignments WHERE membership_id = :m",
            m=self.org.members[self.member],
        )
        await self.org.execute(
            "UPDATE memberships SET authz_updated_at = now() WHERE id = :m",
            m=self.org.members[self.member],
        )
        return await super().embed(texts, purpose)


async def test_a_permission_change_mid_request_applies_to_the_next_request(
    app_db: Database, org: Org
) -> None:
    revoking = RevokingProvider(org, "maya")
    during = await search(app_db, org, "maya", "SCIM plan", provider=revoking)
    # The running request finished under its snapshot: lexical and semantic both saw
    # the assignment, and the tripwire (same snapshot) did not fire.
    assert documents_in(org, during) == ["plan"]
    assert during.trace.counts["semantic"] >= 1
    semantic_docs = {org.document_of(c.chunk_id) for c in during.trace.stages["semantic"]}
    assert "plan" in semantic_docs
    after = await search(app_db, org, "maya", "SCIM plan")
    assert "plan" not in documents_in(org, after)


# --- no existence oracle ------------------------------------------------------------------------


def _observable(org: Org, result: RetrievalResult) -> dict[str, Any]:
    """Everything a reader could learn from a result, minus IDs and timings."""
    return {
        "evidence": [(e.rank, e.title, e.text, e.matched_by) for e in result.evidence],
        "semantic": result.semantic.model_dump(),
        "counts": result.trace.counts,
        "stages": {k: [(c.rank, c.score) for c in v] for k, v in result.trace.stages.items()},
        "mentions": [a.key for a in result.account_mentions],
    }


async def test_results_are_identical_whether_an_unreadable_match_exists_or_not(
    app_db: Database, org: Org
) -> None:
    question = "Acme executive escalation SCIM credit"
    with_secret = _observable(org, await search(app_db, org, "maya", question))
    await org.execute("DELETE FROM documents WHERE id = :d", d=org.documents["exec"])
    without_secret = _observable(org, await search(app_db, org, "maya", question))
    assert with_secret == without_secret
