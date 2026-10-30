"""What the database returned versus what the application kept (§21).

A listener on the runtime engine records every retrieval statement exactly as sent to
PostgreSQL (the final SQL text and driver parameters, tagged by channel). Each is then
re-executed on its own, as ``lattice_app`` with the reader's organization bound, and:

* every row it returns belongs to a record the dataset oracle says the reader may read
  -- so the SQL itself, not later Python, keeps unauthorized rows out;
* the rows are exactly the candidates the retriever passed on (same chunks, same
  order) -- nothing was dropped or added between the database and fusion.

Removing the predicate from a retriever and "filtering in Python" instead would fail
the first check even if the final evidence were clean.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import event

from lattice.authz import resolve_authorization_context
from lattice.dataset.lint import Dataset
from lattice.dataset.manifests import AS_OF
from lattice.db.engine import Database
from lattice.evals.retrieval import Corpus, Identities, select_items
from lattice.providers.embedding import FakeEmbeddingProvider
from lattice.retrieval import RetrievalQuery, RetrievalScope, retrieve_with_context
from lattice.retrieval.models import Candidate, RetrievalConfig
from lattice.retrieval.permitted import CHANNEL_OPTION

ITEMS = [
    "visibility-commitments-maya",
    "inaccessible-exec-notes-maya",
    "lookup-hal-4821-priority",
    "visibility-kestrel-latest-maya",
    "cross-org-hal-4821-felix",
    "egress-tern-arjun",
]


@contextmanager
def capture(db: Database) -> Iterator[list[tuple[str, str, Any]]]:
    captured: list[tuple[str, str, Any]] = []

    def listener(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        channel = context.execution_options.get(CHANNEL_OPTION) if context is not None else None
        if channel:
            captured.append((channel, statement, parameters))

    target = db.engine.sync_engine
    event.listen(target, "before_cursor_execute", listener)
    try:
        yield captured
    finally:
        event.remove(target, "before_cursor_execute", listener)


@pytest.mark.parametrize("item_id", ITEMS)
async def test_the_database_returns_only_permitted_rows_and_retrieval_keeps_them_all(
    item_id: str,
    app_db: Database,
    dataset: Dataset,
    identities: Identities,
    corpus: Corpus,
) -> None:
    item = next(i for i in select_items(dataset) if i.id == item_id)
    org_id = identities.org_ids[item.organization]
    membership = identities.memberships[(item.organization, item.persona)]
    world = dataset.worlds[item.organization]
    observed: dict[str, list[Candidate]] = {}
    with capture(app_db) as captured:
        async with app_db.tenant_snapshot(org_id) as session:
            ctx = await resolve_authorization_context(
                session, organization_id=org_id, membership_id=membership
            )
            result = await retrieve_with_context(
                session,
                ctx,
                RetrievalQuery(
                    text=item.question,
                    scope=RetrievalScope(
                        account=item.scope.account, source_types=tuple(item.scope.source_types)
                    ),
                    limit=20,
                ),
                FakeEmbeddingProvider(),
                now=AS_OF,
                on_candidates=observed.update,
            )
    channels = [c for c, _, _ in captured]
    assert {"lexical", "semantic", "evidence"} <= set(channels)
    for channel, statement, parameters in captured:
        async with app_db.tenant_snapshot(org_id) as session:
            conn = await session.connection()
            rows = (await conn.exec_driver_sql(statement, parameters)).mappings().all()
        chunk_ids = [row["chunk_id"] for row in rows]
        for chunk_id in chunk_ids:
            ref = corpus.chunks[chunk_id]
            assert ref.organization == item.organization, (channel, ref)
            assert world.can_read(item.persona, world.raw.records[ref.record]), (channel, ref)
        if channel == "evidence":
            assert set(chunk_ids) == {e.chunk_id for e in result.evidence}
        else:
            assert chunk_ids == [c.chunk_id for c in observed[channel]], channel


RECORD_AWARE = RetrievalConfig(record_mode="soft", record_weight=0.5, record_within=True)


@pytest.mark.parametrize("item_id", ITEMS)
async def test_record_coverage_reads_only_the_pooled_permitted_chunks(
    item_id: str,
    app_db: Database,
    dataset: Dataset,
    identities: Identities,
    corpus: Corpus,
) -> None:
    """F5.2 rerank_v2: its coverage statement, re-executed alone, returns only chunks the
    reader may read, and exactly the reranked pool's."""
    item = next(i for i in select_items(dataset) if i.id == item_id)
    org_id = identities.org_ids[item.organization]
    membership = identities.memberships[(item.organization, item.persona)]
    world = dataset.worlds[item.organization]
    with capture(app_db) as captured:
        async with app_db.tenant_snapshot(org_id) as session:
            ctx = await resolve_authorization_context(
                session, organization_id=org_id, membership_id=membership
            )
            result = await retrieve_with_context(
                session,
                ctx,
                RetrievalQuery(
                    text=item.question,
                    scope=RetrievalScope(
                        account=item.scope.account, source_types=tuple(item.scope.source_types)
                    ),
                    limit=20,
                ),
                FakeEmbeddingProvider(),
                config=RECORD_AWARE,
                now=AS_OF,
            )
    coverage = [(st, params) for channel, st, params in captured if channel == "record_coverage"]
    assert len(coverage) == 1
    statement, parameters = coverage[0]
    async with app_db.tenant_snapshot(org_id) as session:
        conn = await session.connection()
        rows = (await conn.exec_driver_sql(statement, parameters)).mappings().all()
    for row in rows:
        ref = corpus.chunks[row["chunk_id"]]
        assert ref.organization == item.organization, ref
        assert world.can_read(item.persona, world.raw.records[ref.record]), ref
    assert {row["chunk_id"] for row in rows} == {r.chunk_id for r in result.trace.reranked}
