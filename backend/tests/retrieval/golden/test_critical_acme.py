"""The permanent critical scenario, retrieval half (authorization-model §10).

Doc A: *Acme -- Q3 success plan* (internal, account Acme Corp). Doc B: *Acme --
executive escalation notes* (confidential, Leadership, canary B). Maya (AM, Acme,
internal) and Grace (Leadership, confidential) ask "What commitments have we made to
Acme?" (Grace with the Acme Corp scope, as the golden set does).

1. For Maya, Doc B's chunks are absent from exact, lexical, semantic candidates, the
   fused pool and the evidence; canary B is nowhere in her result or trace.
3. Grace retrieves Doc B (with the question that targets it) -- authorization gives it
   to her; what may reach a model is Phase 6's egress decision.
5. After Maya is un-assigned from Acme, Doc A disappears for her; after Grace's
   clearance is lowered, Doc B disappears for her -- without re-ingestion, on the very
   next request. (Both changes are restored afterwards.)
"""

import uuid

from sqlalchemy import text

from lattice.db.engine import Database
from lattice.providers.embedding import FakeEmbeddingProvider
from lattice.retrieval import RetrievalQuery, RetrievalResult, retrieve

from ...ingestion.conftest import Org

CANARY_B = "LATTICE-CANARY-B-7f3a9c"
QUESTION = "What commitments have we made to Acme?"
TARGETED = "What did we offer Acme on the executive escalation call?"
FAKE = FakeEmbeddingProvider()
DOC_A_QUESTION = "What is in the Acme Q3 success plan?"
_MEMBER = "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id WHERE u.email = :e"
_CHUNKS = (
    "SELECT c.id FROM chunks c JOIN documents d ON d.id = c.document_id WHERE d.external_id = :e"
)


async def _ids(
    owner_db: Database, org: Org, email: str
) -> tuple[uuid.UUID, set[uuid.UUID], set[uuid.UUID]]:
    async with owner_db.tenant_transaction(org.id) as s:
        member: uuid.UUID = (await s.execute(text(_MEMBER), {"e": email})).scalar_one()
        doc_a: set[uuid.UUID] = set(
            (await s.execute(text(_CHUNKS), {"e": "acme-q3-success-plan"})).scalars()
        )
        doc_b: set[uuid.UUID] = set(
            (await s.execute(text(_CHUNKS), {"e": "acme-exec-escalation-notes"})).scalars()
        )
    return member, doc_a, doc_b


def _everywhere(result: RetrievalResult) -> set[uuid.UUID]:
    return (
        {c.chunk_id for stage in result.trace.stages.values() for c in stage}
        | {r.chunk_id for r in result.trace.reranked}
        | {e.chunk_id for e in result.evidence}
    )


async def _ask(
    app_db: Database,
    org: Org,
    member: uuid.UUID,
    question: str,
    *,
    account: str | None = None,
    provider: FakeEmbeddingProvider | None = FAKE,
) -> RetrievalResult:
    return await retrieve(
        app_db,
        organization_id=org.id,
        membership_id=member,
        query=RetrievalQuery.model_validate(
            {"text": question, "scope": {"account": account}, "limit": 20}
        ),
        provider=provider,
    )


async def test_critical_acme_scenario(
    owner_db: Database, app_db: Database, corpus_orgs: dict[str, Org]
) -> None:
    halden = corpus_orgs["halden"]
    maya, doc_a, doc_b = await _ids(owner_db, halden, "maya.okafor@halden.example")
    grace, _, _ = await _ids(owner_db, halden, "grace.whitfield@halden.example")
    assert doc_a
    assert doc_b

    # 1. Maya: Doc B nowhere, in any stage, for either question.
    for question in (QUESTION, TARGETED):
        result = await _ask(app_db, halden, maya, question)
        assert _everywhere(result).isdisjoint(doc_b)
        assert CANARY_B not in result.model_dump_json()
    maya_result = await _ask(app_db, halden, maya, DOC_A_QUESTION)
    assert {e.chunk_id for e in maya_result.evidence} & doc_a, "Doc A is Maya's evidence"

    # 3. Grace retrieves Doc B.
    # Doc B is confidential: under the default egress policy it has no vector and is
    # found lexically; semantic candidates of internal documents would only dilute it.
    grace_result = await _ask(app_db, halden, grace, TARGETED, account="acme-corp", provider=None)
    assert {e.chunk_id for e in grace_result.evidence} & doc_b

    # 5. Revocation applies on the next request, with no re-ingestion.
    try:
        async with owner_db.tenant_transaction(halden.id) as s:
            await s.execute(
                text(
                    "DELETE FROM account_assignments WHERE membership_id = :m AND account_id = "
                    "(SELECT id FROM accounts WHERE key = 'acme-corp')"
                ),
                {"m": maya},
            )
            await s.execute(
                text("UPDATE memberships SET clearance = 'internal' WHERE id = :g"), {"g": grace}
            )
        after_maya = await _ask(app_db, halden, maya, DOC_A_QUESTION)
        assert _everywhere(after_maya).isdisjoint(doc_a)
        after_grace = await _ask(
            app_db, halden, grace, TARGETED, account="acme-corp", provider=None
        )
        assert _everywhere(after_grace).isdisjoint(doc_b)
    finally:
        async with owner_db.tenant_transaction(halden.id) as s:
            await s.execute(
                text(
                    "INSERT INTO account_assignments (organization_id, membership_id, account_id) "
                    "SELECT :o, :m, id FROM accounts WHERE key = 'acme-corp' ON CONFLICT DO NOTHING"
                ),
                {"o": halden.id, "m": maya},
            )
            await s.execute(
                text("UPDATE memberships SET clearance = 'confidential' WHERE id = :g"),
                {"g": grace},
            )
    restored = await _ask(app_db, halden, maya, DOC_A_QUESTION)
    assert {e.chunk_id for e in restored.evidence} & doc_a
