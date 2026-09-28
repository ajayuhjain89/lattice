"""Small hand-built worlds for retrieval tests.

Rows are written as lattice_owner through row-level security (organization bound per
transaction), the way the seed and ingestion write them. Chunks get the same lexical
vector expression ingestion uses (title and heading path weight A, display reference
in the ``simple`` configuration weight A, text weight D; never an account name) and,
by default, a real vector from the deterministic fake provider -- so retrieval runs
against realistic rows while each test controls every access attribute.
"""

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from lattice.db.engine import Database
from lattice.providers.embedding import FakeEmbeddingProvider
from lattice.retrieval import RetrievalQuery, RetrievalResult, retrieve
from lattice.retrieval.models import RetrievalConfig

from ..factories import create_member, create_org, create_source

FAKE = FakeEmbeddingProvider()
NOW = datetime(2026, 9, 27, 23, 59, tzinfo=UTC)


def vector_text(values: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


@dataclass
class Org:
    owner: Database
    id: uuid.UUID
    slug: str
    source_id: uuid.UUID
    accounts: dict[str, uuid.UUID] = field(default_factory=dict)
    departments: dict[str, uuid.UUID] = field(default_factory=dict)
    groups: dict[str, uuid.UUID] = field(default_factory=dict)
    members: dict[str, uuid.UUID] = field(default_factory=dict)
    documents: dict[str, uuid.UUID] = field(default_factory=dict)
    chunks: dict[str, list[uuid.UUID]] = field(default_factory=dict)

    async def execute(self, sql: str, **params: Any) -> Any:
        async with self.owner.tenant_transaction(self.id) as s:
            return await s.execute(text(sql), params)

    async def account(self, key: str, name: str, aliases: Sequence[str] = ()) -> uuid.UUID:
        result = await self.execute(
            "INSERT INTO accounts (organization_id, key, name, aliases) "
            "VALUES (:o, :k, :n, CAST(:a AS text[])) RETURNING id",
            o=self.id,
            k=key,
            n=name,
            a=list(aliases),
        )
        self.accounts[key] = result.scalar_one()
        return self.accounts[key]

    async def department(self, key: str) -> uuid.UUID:
        result = await self.execute(
            "INSERT INTO departments (organization_id, key, name) VALUES (:o, :k, :n) RETURNING id",
            o=self.id,
            k=key,
            n=key.title(),
        )
        self.departments[key] = result.scalar_one()
        return self.departments[key]

    async def group(self, key: str) -> uuid.UUID:
        result = await self.execute(
            "INSERT INTO access_groups (organization_id, key, origin, name) "
            "VALUES (:o, :k, 'admin', :n) RETURNING id",
            o=self.id,
            k=key,
            n=key,
        )
        self.groups[key] = result.scalar_one()
        return self.groups[key]

    async def member(
        self,
        name: str,
        *,
        clearance: str = "internal",
        scope: str = "all",
        accounts: Sequence[str] = (),
        departments: Sequence[str] = (),
        groups: Sequence[str] = (),
    ) -> uuid.UUID:
        member = await create_member(self.owner, self.id, f"{name}@{self.slug}.example")
        await self.execute(
            "UPDATE memberships SET clearance = :c, account_scope = :s WHERE id = :m",
            c=clearance,
            s=scope,
            m=member.membership_id,
        )
        for key in accounts:
            await self.execute(
                "INSERT INTO account_assignments (organization_id, membership_id, account_id) "
                "VALUES (:o, :m, :a)",
                o=self.id,
                m=member.membership_id,
                a=self.accounts[key],
            )
        for key in departments:
            await self.execute(
                "INSERT INTO membership_departments (organization_id, membership_id, "
                "department_id) VALUES (:o, :m, :d)",
                o=self.id,
                m=member.membership_id,
                d=self.departments[key],
            )
        for key in groups:
            await self.execute(
                "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
                "VALUES (:o, :g, :m)",
                o=self.id,
                g=self.groups[key],
                m=member.membership_id,
            )
        self.members[name] = member.membership_id
        return member.membership_id

    async def document(
        self,
        key: str,
        title: str,
        chunks: Sequence[str],
        *,
        kind: str = "doc",
        sensitivity: str = "internal",
        access_mode: str = "policy",
        account: str | None = None,
        departments: Sequence[str] = (),
        grants: Sequence[tuple[str, str]] = (),  # (membership|department|group, name)
        status: str = "active",
        display_ref: str | None = None,
        authority: str = "working",
        updated: datetime = NOW,
        embedding: str = "embedded",  # embedded | failed | pending | policy_excluded
        model: str | None = None,
        vector: Sequence[float] | None = None,
        supersedes: str | None = None,
    ) -> uuid.UUID:
        document_id = uuid.uuid4()
        await self.execute(
            "INSERT INTO documents (id, organization_id, source_id, external_id, document_kind, "
            "title, display_ref, authority, source_updated_at, content_hash, access_hash, body, "
            "account_id, sensitivity, access_mode, department_ids, status, "
            "supersedes_document_id) VALUES (:id, :o, :s, :e, :kind, :title, :ref, :auth, :upd, "
            "sha256(convert_to(:e, 'UTF8')), sha256(convert_to(:e, 'UTF8')), :body, :acct, "
            ":sens, :mode, CAST(:depts AS uuid[]), :status, :sup)",
            id=document_id,
            o=self.id,
            s=self.source_id,
            e=key,
            kind=kind,
            title=title,
            ref=display_ref,
            auth=authority,
            upd=updated,
            body="\n\n".join(chunks),
            acct=self.accounts[account] if account else None,
            sens=sensitivity,
            mode=access_mode,
            depts=[self.departments[d] for d in departments],
            status=status,
            sup=self.documents[supersedes] if supersedes else None,
        )
        for principal, name in grants:
            column, value = {
                "membership": ("membership_id", self.members.get(name)),
                "department": ("department_id", self.departments.get(name)),
                "group": ("group_id", self.groups.get(name)),
            }[principal]
            await self.execute(
                "INSERT INTO document_grants (organization_id, document_id, principal_type, "  # noqa: S608
                f"{column}, origin) VALUES (:o, :d, :t, :p, 'admin')",
                o=self.id,
                d=document_id,
                t=principal,
                p=value,
            )
        ids = []
        for ordinal, chunk in enumerate(chunks):
            chunk_id = uuid.uuid4()
            embedded = embedding == "embedded"
            values = vector if vector is not None else FAKE.vector(f"{title}\n\n{chunk}")
            await self.execute(
                "INSERT INTO chunks (id, organization_id, document_id, ordinal, text, "
                "char_start, char_end, heading_path, source_anchors, occurred_at, content_hash, "
                "embedding_input_hash, token_count, tsv, embedding, embedding_model, "
                "embedding_status) VALUES (:id, :o, :d, :n, :t, 0, :len, '{}', "
                "CAST(:anchors AS text[]), :at, :h, :h, 1, "
                "setweight(to_tsvector('english', :title), 'A') "
                "|| setweight(to_tsvector('simple', coalesce(:ref, '')), 'A') "
                "|| setweight(to_tsvector('english', :t), 'D'), "
                "CAST(:vec AS vector), :model, :status)",
                id=chunk_id,
                o=self.id,
                d=document_id,
                n=ordinal,
                t=chunk,
                len=len(chunk),
                anchors=[str(ordinal + 1)],
                at=updated,
                h=hashlib.sha256(f"{key}:{ordinal}:{chunk}".encode()).digest(),
                title=title,
                ref=display_ref,
                vec=vector_text(values) if embedded else None,
                model=(model or FAKE.model) if embedded else None,
                status=embedding,
            )
            ids.append(chunk_id)
        self.documents[key] = document_id
        self.chunks[key] = ids
        return document_id

    async def register_provider(
        self,
        provider: str = "fake",
        model: str | None = None,
        level: str = "internal",
        *,
        deployment: str = "external_api",
        training_opt_out: bool = True,
    ) -> None:
        await self.execute(
            "INSERT INTO model_providers (organization_id, purpose, provider, model, deployment, "
            "approved_for_level, retention_mode, training_opt_out) VALUES (:o, 'embedding', :p, "
            ":m, :d, :l, 'none', :t) ON CONFLICT (organization_id, purpose, "
            "provider, model) DO UPDATE SET approved_for_level = EXCLUDED.approved_for_level",
            o=self.id,
            p=provider,
            m=model or FAKE.model,
            d=deployment,
            l=level,
            t=training_opt_out,
        )

    async def settings(self, **columns: str) -> None:
        """Upsert ``org_settings`` columns (external_egress_level,
        default_query_sensitivity, data_origin)."""
        names = sorted(columns)
        await self.execute(
            f"INSERT INTO org_settings (organization_id, {', '.join(names)}) "  # noqa: S608
            f"VALUES (:o, {', '.join(':' + n for n in names)}) ON CONFLICT (organization_id) "
            f"DO UPDATE SET {', '.join(f'{n} = EXCLUDED.{n}' for n in names)}",
            o=self.id,
            **columns,
        )

    async def set_egress_level(self, level: str) -> None:
        await self.execute(
            "INSERT INTO org_settings (organization_id, external_egress_level) VALUES (:o, :l) "
            "ON CONFLICT (organization_id) DO UPDATE SET external_egress_level = :l",
            o=self.id,
            l=level,
        )

    def document_of(self, chunk_id: uuid.UUID) -> str:
        return next(key for key, ids in self.chunks.items() if chunk_id in ids)


async def new_org(owner: Database, slug: str) -> Org:
    org_id = await create_org(owner, slug)
    source_id = await create_source(owner, org_id)
    org = Org(owner, org_id, slug, source_id)
    await org.register_provider()
    return org


async def search(
    app: Database,
    org: Org,
    member: str,
    question: str,
    *,
    provider: Any = FAKE,
    config: RetrievalConfig | None = None,
    **query: Any,
) -> RetrievalResult:
    kwargs: dict[str, Any] = {"now": NOW}
    if config is not None:
        kwargs["config"] = config
    return await retrieve(
        app,
        organization_id=org.id,
        membership_id=org.members[member],
        query=RetrievalQuery.model_validate({"text": question, **query}),
        provider=provider,
        **kwargs,
    )


def documents_in(org: Org, result: RetrievalResult) -> list[str]:
    """Evidence document keys, in rank order, one per document."""
    seen: dict[str, None] = {}
    for e in result.evidence:
        seen.setdefault(next(k for k, v in org.documents.items() if v == e.document_id))
    return list(seen)


def stage_documents(org: Org, result: RetrievalResult, stage: str) -> set[str]:
    by_chunk = {c: k for k, ids in org.chunks.items() for c in ids}
    return {by_chunk[c.chunk_id] for c in result.trace.stages[stage] if c.chunk_id in by_chunk}
