"""Property-based differential check of the retrievers against the reference policy.

Hypothesis generates small two-organization worlds -- members across clearance,
account scope, assignments, departments and groups; documents across sensitivity,
access mode, status, account, departments, grants, kind, embedding state and a
display reference drawn from a tiny set so identifiers collide within and across
tenants -- and queries drawn from the same vocabulary as the documents, so most
queries match something in every channel.

Each world is written by a superuser in one transaction that is always rolled back.
For each active member and query, retrieval runs exactly as in production (as
``lattice_app``, RLS on, the member's organization bound, the context resolved in the
same transaction). Then:

1. every chunk each channel's SQL returned (exact, lexical, semantic -- observed before
   any fusion) belongs to a document the **reference** ``readable()`` admits;
2. every evidence item is readable, and its structured account is exactly
   ``visible_document_account()`` (D-8): shown only when visible, never substituted;
3. every account mention is ``account_visible()``;
4. no row of the other organization appears anywhere.

The tripwire would also abort on a violation; this check runs on the raw candidates
first, so a failure names the channel. LATTICE_RETRIEVAL_EXAMPLES sets the number of
worlds (default 200); the test asserts every channel returned rows often enough to
mean something.
"""

import asyncio
import hashlib
import os
import uuid
from collections import Counter
from dataclasses import dataclass

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from lattice.authz import (
    AccountRef,
    AuthorizationContext,
    DocumentAccess,
    GrantRef,
    account_visible,
    readable,
    resolve_authorization_context,
    visible_document_account,
)
from lattice.db.engine import create_engine
from lattice.retrieval import RetrievalQuery, retrieve_with_context
from lattice.retrieval.models import Candidate

from ..conftest import SUPERUSER_URL
from .world import FAKE, NOW, vector_text

EXAMPLES = int(os.environ.get("LATTICE_RETRIEVAL_EXAMPLES", "200"))
WORDS = ("scim", "refund", "renewal", "outage", "credit", "beta", "acme", "kestrel", "orbit")
ACCOUNT_NAMES = ("Acme", "Kestrel", "Orbit")
REFS = (None, "HAL-1", "HAL-2")
HOME, OTHER = 0, 1


@dataclass(frozen=True)
class MemberSpec:
    org: int
    clearance: str
    scope: str
    accounts: frozenset[int]
    departments: frozenset[int]
    groups: frozenset[int]


@dataclass(frozen=True)
class DocSpec:
    org: int
    sensitivity: str
    access_mode: str
    status: str
    account: int | None
    departments: frozenset[int]
    grants: tuple[tuple[str, int], ...]
    kind: str
    ref: str | None
    words: tuple[str, ...]
    embedding: str


members_st = st.builds(
    MemberSpec,
    org=st.sampled_from((HOME,) * 4 + (OTHER,)),
    clearance=st.sampled_from(("internal", "confidential")),
    scope=st.sampled_from(("all", "assigned")),
    accounts=st.frozensets(st.integers(0, 2)),
    departments=st.frozensets(st.integers(0, 2)),
    groups=st.frozensets(st.integers(0, 1)),
)


@st.composite
def docs_st(draw: st.DrawFn) -> DocSpec:
    sensitivity = draw(st.sampled_from(("internal", "confidential", "restricted")))
    return DocSpec(
        org=draw(st.sampled_from((HOME,) * 3 + (OTHER,))),
        sensitivity=sensitivity,
        access_mode="grants_only"
        if sensitivity == "restricted"
        else draw(st.sampled_from(("policy", "policy", "grants_only"))),
        status=draw(st.sampled_from(("active",) * 4 + ("deleted", "quarantined"))),
        account=draw(st.none() | st.integers(0, 2)),
        departments=draw(st.frozensets(st.integers(0, 2), max_size=2)),
        grants=tuple(
            draw(
                st.lists(
                    st.tuples(
                        st.sampled_from(("membership", "department", "group")), st.integers(0, 5)
                    ),
                    max_size=2,
                )
            )
        ),
        kind=draw(st.sampled_from(("doc", "ticket"))),
        ref=draw(st.sampled_from(REFS)),
        words=tuple(draw(st.lists(st.sampled_from(WORDS), min_size=2, max_size=6))),
        embedding=draw(st.sampled_from(("embedded",) * 3 + ("failed", "policy_excluded"))),
    )


@dataclass(frozen=True)
class WorldSpec:
    members: tuple[MemberSpec, ...]
    docs: tuple[DocSpec, ...]
    queries: tuple[str, ...]


worlds_st = st.builds(
    WorldSpec,
    members=st.lists(members_st, min_size=1, max_size=3).map(tuple),
    docs=st.lists(docs_st(), min_size=1, max_size=8).map(tuple),
    queries=st.lists(
        st.lists(st.sampled_from((*WORDS, "HAL-1", "HAL-2")), min_size=1, max_size=4).map(" ".join),
        min_size=1,
        max_size=2,
    ).map(tuple),
)


async def _one(conn: AsyncConnection, sql: str, **params: object) -> uuid.UUID:
    value: object = (await conn.execute(text(sql), params)).scalar_one()
    assert isinstance(value, uuid.UUID)
    return value


@dataclass
class Built:
    org_ids: list[uuid.UUID]
    members: list[uuid.UUID]
    documents: dict[uuid.UUID, DocumentAccess]
    chunk_document: dict[uuid.UUID, uuid.UUID]


async def _build(conn: AsyncConnection, spec: WorldSpec) -> Built:
    orgs: list[dict[str, list[uuid.UUID] | uuid.UUID]] = []
    for i in (HOME, OTHER):
        org = await _one(
            conn,
            "INSERT INTO organizations (slug, name) VALUES (:s, 'x') RETURNING id",
            s=f"p{i}-{uuid.uuid4().hex[:10]}",
        )
        source = await _one(
            conn,
            "INSERT INTO sources (organization_id, key, connector_type, name, "
            "default_sensitivity, authority) VALUES (:o, 'src', 'document_repository', 's', "
            "'internal', 'working') RETURNING id",
            o=org,
        )
        await conn.execute(
            text(
                "INSERT INTO model_providers (organization_id, purpose, provider, model, "
                "deployment, retention_mode, training_opt_out) VALUES (:o, 'embedding', "
                "'fake', :m, 'external_api', 'none', true)"
            ),
            {"o": org, "m": FAKE.model},
        )
        departments = [
            await _one(
                conn,
                "INSERT INTO departments (organization_id, key, name) VALUES (:o, :k, :n) "
                "RETURNING id",
                o=org,
                k=f"d{d}",
                n=f"d{d}",
            )
            for d in range(3)
        ]
        accounts = [
            await _one(
                conn,
                "INSERT INTO accounts (organization_id, key, name) VALUES (:o, :k, :n) "
                "RETURNING id",
                o=org,
                k=name.lower(),
                n=name,
            )
            for name in ACCOUNT_NAMES
        ]
        groups = [
            await _one(
                conn,
                "INSERT INTO access_groups (organization_id, key, origin, name) "
                "VALUES (:o, :k, 'admin', 'g') RETURNING id",
                o=org,
                k=f"g{g}",
            )
            for g in range(2)
        ]
        orgs.append(
            {
                "id": org,
                "source": source,
                "departments": departments,
                "accounts": accounts,
                "groups": groups,
                "members": [],
            }
        )

    def ids(org: int, name: str) -> list[uuid.UUID]:
        value: list[uuid.UUID] | uuid.UUID = orgs[org][name]
        assert isinstance(value, list)
        return value

    def one(org: int, name: str) -> uuid.UUID:
        value: list[uuid.UUID] | uuid.UUID = orgs[org][name]
        assert isinstance(value, uuid.UUID)
        return value

    members: list[uuid.UUID] = []
    for m in spec.members:
        org = one(m.org, "id")
        user = await _one(
            conn,
            "INSERT INTO users (email, display_name) VALUES (:e, 'x') RETURNING id",
            e=f"{uuid.uuid4().hex}@p.example",
        )
        membership = await _one(
            conn,
            "INSERT INTO memberships (organization_id, user_id, clearance, account_scope, "
            "role_label) VALUES (:o, :u, :c, :s, 'x') RETURNING id",
            o=org,
            u=user,
            c=m.clearance,
            s=m.scope,
        )
        for a in m.accounts:
            await conn.execute(
                text(
                    "INSERT INTO account_assignments (organization_id, membership_id, "
                    "account_id) VALUES (:o, :m, :a)"
                ),
                {"o": org, "m": membership, "a": ids(m.org, "accounts")[a]},
            )
        for dep in m.departments:
            await conn.execute(
                text(
                    "INSERT INTO membership_departments (organization_id, membership_id, "
                    "department_id) VALUES (:o, :m, :d)"
                ),
                {"o": org, "m": membership, "d": ids(m.org, "departments")[dep]},
            )
        for g in m.groups:
            await conn.execute(
                text(
                    "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
                    "VALUES (:o, :g, :m)"
                ),
                {"o": org, "g": ids(m.org, "groups")[g], "m": membership},
            )
        ids(m.org, "members").append(membership)
        members.append(membership)

    documents: dict[uuid.UUID, DocumentAccess] = {}
    chunk_document: dict[uuid.UUID, uuid.UUID] = {}
    for n, d in enumerate(spec.docs):
        org = one(d.org, "id")
        body = " ".join(d.words)
        title = f"Record {n}"
        doc = await _one(
            conn,
            "INSERT INTO documents (organization_id, source_id, external_id, document_kind, "
            "title, display_ref, authority, source_updated_at, content_hash, access_hash, body, "
            "account_id, sensitivity, access_mode, department_ids, status) VALUES (:o, :s, :e, "
            ":k, :t, :r, 'working', :u, sha256(convert_to(:e, 'UTF8')), "
            "sha256(convert_to(:e, 'UTF8')), :b, :a, :sens, :mode, CAST(:deps AS uuid[]), :st) "
            "RETURNING id",
            o=org,
            s=one(d.org, "source"),
            e=f"r{n}",
            k=d.kind,
            t=title,
            r=d.ref,
            u=NOW,
            b=body,
            a=None if d.account is None else ids(d.org, "accounts")[d.account],
            sens=d.sensitivity,
            mode=d.access_mode,
            deps=[ids(d.org, "departments")[i] for i in sorted(d.departments)],
            st=d.status,
        )
        grants: list[GrantRef] = []
        for principal, target in d.grants:
            pool = ids(
                d.org,
                {"membership": "members", "department": "departments"}.get(principal, "groups"),
            )
            if not pool:
                continue
            value = pool[target % len(pool)]
            if any(g.principal_id == value for g in grants):
                continue
            column = {"membership": "membership_id", "department": "department_id"}.get(
                principal, "group_id"
            )
            await conn.execute(
                text(
                    "INSERT INTO document_grants (organization_id, document_id, principal_type, "  # noqa: S608
                    f"{column}, origin) VALUES (:o, :d, :t, :p, 'admin')"
                ),
                {"o": org, "d": doc, "t": principal, "p": value},
            )
            grants.append(GrantRef(org, principal, value))
        embedded = d.embedding == "embedded"
        chunk = await _one(
            conn,
            "INSERT INTO chunks (id, organization_id, document_id, ordinal, text, char_start, "
            "char_end, occurred_at, content_hash, embedding_input_hash, token_count, tsv, "
            "embedding, embedding_model, embedding_status) VALUES (gen_random_uuid(), :o, :d, "
            "0, :b, 0, :n, :u, :h, "
            "sha256(convert_to(:b, 'UTF8')), 1, setweight(to_tsvector('english', :t), 'A') "
            "|| setweight(to_tsvector('simple', coalesce(:r, '')), 'A') "
            "|| setweight(to_tsvector('english', :b), 'D'), CAST(:v AS vector), :m, :st) "
            "RETURNING id",
            o=org,
            d=doc,
            b=body,
            n=len(body),
            u=NOW,
            h=hashlib.sha256(f"{doc}:{body}".encode()).digest(),
            t=title,
            r=d.ref,
            v=vector_text(FAKE.vector(f"{title}\n\n{body}")) if embedded else None,
            m=FAKE.model if embedded else None,
            st=d.embedding,
        )
        chunk_document[chunk] = doc
        documents[doc] = DocumentAccess(
            id=doc,
            organization_id=org,
            status=d.status,
            sensitivity=d.sensitivity,
            access_mode=d.access_mode,
            account_id=None if d.account is None else ids(d.org, "accounts")[d.account],
            department_ids=frozenset(ids(d.org, "departments")[i] for i in d.departments),
            grants=tuple(grants),
        )
    return Built([one(HOME, "id"), one(OTHER, "id")], members, documents, chunk_document)


async def _check(conn: AsyncConnection, spec: WorldSpec, stats: Counter[str]) -> None:
    transaction = await conn.begin()
    try:
        built = await _build(conn, spec)
        session = AsyncSession(bind=conn)
        for m_spec, membership in zip(spec.members, built.members, strict=True):
            org = built.org_ids[m_spec.org]
            await conn.execute(text("SET LOCAL ROLE lattice_app"))
            await conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org)})
            ctx: AuthorizationContext = await resolve_authorization_context(
                session, organization_id=org, membership_id=membership
            )
            for question in spec.queries:
                observed: dict[str, list[Candidate]] = {}
                result = await retrieve_with_context(
                    session,
                    ctx,
                    RetrievalQuery(text=question, limit=20),
                    FAKE,
                    now=NOW,
                    on_candidates=observed.update,
                )
                for channel, candidates in observed.items():
                    for c in candidates:
                        document = built.documents[built.chunk_document[c.chunk_id]]
                        assert document.organization_id == org, (channel, "cross-org")
                        assert readable(ctx, document), (channel, document)
                        stats[f"{channel}_rows"] += 1
                for e in result.evidence:
                    document = built.documents[e.document_id]
                    assert readable(ctx, document)
                    expected = visible_document_account(ctx, document)
                    assert (e.account.id if e.account else None) == expected
                    stats["evidence"] += 1
                    stats["evidence_account_shown" if expected else "evidence_account_none"] += 1
                    if document.account_id is not None and expected is None:
                        stats["evidence_account_hidden_d8"] += 1
                for a in result.account_mentions:
                    assert account_visible(ctx, AccountRef(a.id, org))
                    stats["mentions"] += 1
                unreadable = [
                    d
                    for d in built.documents.values()
                    if d.organization_id == org and not readable(ctx, d)
                ]
                stats["unreadable_documents_in_reach"] += len(unreadable)
            await conn.execute(text("RESET ROLE"))
        await session.close()
        stats["examples"] += 1
    finally:
        await transaction.rollback()


def test_every_retrieved_row_is_readable_under_the_reference_policy(
    migrated_schema: None,
) -> None:
    stats: Counter[str] = Counter()
    with asyncio.Runner() as runner:
        engine = create_engine(SUPERUSER_URL, application_name="lattice-tests-retrieval-prop")
        conn = runner.run(engine.connect().start())

        @settings(
            max_examples=EXAMPLES,
            deadline=None,
            suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
        )
        @given(spec=worlds_st)
        def check(spec: WorldSpec) -> None:
            runner.run(_check(conn, spec, stats))

        try:
            check()
        finally:
            runner.run(conn.close())
            runner.run(engine.dispose())
    print(f"\nretrieval property: {dict(sorted(stats.items()))}")
    assert stats["examples"] >= min(EXAMPLES, 20)
    for key in (
        "exact_rows",
        "lexical_rows",
        "semantic_rows",
        "evidence",
        "mentions",
        "evidence_account_shown",
        "evidence_account_none",
        "unreadable_documents_in_reach",
    ):
        assert stats[key] >= 10, f"the generator did not exercise {key}: {stats}"
