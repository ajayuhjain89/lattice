"""Accounts and access management as every persona sees them, over the real synthetic
corpus (security-model §10 rows "Account lists" and "Access-change previews"; T20).

Expected values come from the Python reference policy (``authz.account_visible``,
``authz.readable``) -- not from the SQL predicates the endpoints use."""

import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text

from lattice.authz import (
    AccountRef,
    account_visible,
    load_document_access,
    readable,
    resolve_authorization_context,
)
from lattice.db.engine import Database
from lattice.seed import load_manifest

from ...conftest import CSRF, AppFactory
from ...factories import login
from ...ingestion.conftest import DATA, ORGS, Org
from .test_sources_personas import personas


def administrators() -> list[tuple[str, str]]:
    return [
        (slug, m.email)
        for slug in ORGS
        for m in load_manifest(DATA / slug / "org.yaml").members
        if m.is_admin
    ]


@pytest.fixture
async def client(app_client: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client() as c:
        yield c


async def _membership(owner: Database, org: Org, email: str) -> uuid.UUID:
    async with owner.tenant_transaction(org.id) as s:
        found: uuid.UUID = (
            await s.execute(
                text(
                    "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id "
                    "WHERE u.email = :e"
                ),
                {"e": email},
            )
        ).scalar_one()
    return found


@pytest.mark.parametrize(("slug", "email"), personas())
async def test_members_list_exactly_the_accounts_they_may_know(
    client: httpx.AsyncClient,
    owner_db: Database,
    corpus_orgs: dict[str, Org],
    slug: str,
    email: str,
) -> None:
    org = corpus_orgs[slug]
    membership = await _membership(owner_db, org, email)
    async with owner_db.tenant_transaction(org.id) as s:
        ctx = await resolve_authorization_context(
            s, organization_id=org.id, membership_id=membership
        )
        accounts = (await s.execute(text("SELECT id, key, name FROM accounts"))).all()
    expected = sorted(
        (name, str(key)) for i, key, name in accounts if account_visible(ctx, AccountRef(i, org.id))
    )
    hidden = [name for i, _, name in accounts if not account_visible(ctx, AccountRef(i, org.id))]
    assert (await login(client, email)).status_code == 200
    response = await client.get("/v1/accounts")
    assert response.status_code == 200
    body = response.json()
    assert body["account_scope"] == ctx.account_scope
    assert [(a["name"], a["key"]) for a in body["accounts"]] == expected
    for name in hidden:
        assert name not in response.text


@pytest.mark.parametrize(("slug", "email"), administrators())
async def test_administrators_see_access_only_through_what_they_can_read(
    client: httpx.AsyncClient,
    owner_db: Database,
    corpus_orgs: dict[str, Org],
    slug: str,
    email: str,
) -> None:
    """For every member: no title or reference of a document the administrator cannot
    read appears on their access page; grants are listed exactly for readable
    documents; a synced group is named exactly when the administrator is in it; and a
    suspension preview counts exactly the documents both can read."""
    org = corpus_orgs[slug]
    admin = await _membership(owner_db, org, email)
    async with owner_db.tenant_transaction(org.id) as s:
        viewer = await resolve_authorization_context(s, organization_id=org.id, membership_id=admin)
        members: list[uuid.UUID] = list(
            (await s.execute(text("SELECT id FROM memberships WHERE status = 'active'"))).scalars()
        )
        documents: dict[uuid.UUID, str] = dict(
            (await s.execute(text("SELECT id, title FROM documents"))).all()
        )
        access = await load_document_access(s, org.id, documents)
        contexts = {
            m: await resolve_authorization_context(s, organization_id=org.id, membership_id=m)
            for m in members
        }
        source_groups: dict[uuid.UUID, str] = dict(
            (
                await s.execute(text("SELECT id, name FROM access_groups WHERE origin = 'source'"))
            ).all()
        )
        group_members = (
            await s.execute(text("SELECT membership_id, group_id FROM access_group_members"))
        ).all()
        my_groups: set[uuid.UUID] = set(
            (
                await s.execute(
                    text("SELECT group_id FROM access_group_members WHERE membership_id = :m"),
                    {"m": admin},
                )
            ).scalars()
        )
        membership_grants = (
            await s.execute(
                text(
                    "SELECT membership_id, document_id FROM document_grants "
                    "WHERE principal_type = 'membership'"
                )
            )
        ).all()
    hidden_titles = {
        documents[d]
        for d, facts in access.items()
        if not readable(viewer, facts) and len(documents[d]) >= 8
    }
    assert (await login(client, email)).status_code == 200
    for member in members:
        page = await client.get(f"/v1/admin/access/members/{member}")
        assert page.status_code == 200
        body = page.json()
        for title in hidden_titles:
            assert title not in page.text
        expected_grants = {
            str(d) for m, d in membership_grants if m == member and readable(viewer, access[d])
        }
        assert {g["document"]["id"] for g in body["grants"]} == expected_grants
        assert body["has_other_grants"] == any(
            m == member and not readable(viewer, access[d]) for m, d in membership_grants
        )
        theirs = {g for m, g in group_members if m == member}
        hidden = {g for g in theirs if g in source_groups and g not in my_groups}
        assert {uuid.UUID(g["id"]) for g in body["groups"]} == theirs - hidden
        assert body["has_hidden_groups"] == bool(hidden)
        for group_id in hidden:
            assert str(group_id) not in page.text
            assert source_groups[group_id] not in page.text
        if member == admin:
            continue
        preview = await client.post(
            f"/v1/admin/access/members/{member}/preview",
            json={"status": "suspended"},
            headers=CSRF,
        )
        assert preview.status_code == 200
        both = sum(
            1
            for facts in access.values()
            if readable(viewer, facts) and readable(contexts[member], facts)
        )
        assert preview.json() == {
            "effects": [{"kind": "suspended", "before": None, "after": None, "subject": None}],
            "documents_gained": 0,
            "documents_lost": both,
        }
        assert all(title not in json.dumps(preview.json()) for title in hidden_titles)
