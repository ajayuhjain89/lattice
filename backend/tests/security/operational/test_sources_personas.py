"""Sources and failures as every persona sees them, over the real synthetic corpus
(security-model §10, SEC-14; T20).

The expected values come from the Python reference policy (``authz.readable``) over
each document's access facts -- not from the SQL predicate the endpoint uses -- so a
defect in either shows up as a difference."""

import uuid
from collections import Counter
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text

from lattice.authz import load_document_access, readable, resolve_authorization_context
from lattice.db.engine import Database
from lattice.seed import load_manifest

from ...conftest import AppFactory
from ...factories import login
from ...ingestion.conftest import DATA, ORGS, Org


def personas() -> list[tuple[str, str]]:
    """(organization, email) of every seeded member of both organizations."""
    return [
        (slug, member.email)
        for slug in ORGS
        for member in load_manifest(DATA / slug / "org.yaml").members
    ]


ADMIN_SOURCE_KEYS = {
    "id",
    "key",
    "name",
    "connector_type",
    "status",
    "account_scoped",
    "default_sensitivity",
    "authority",
    "documents",
    "last_synced_at",
    "last_succeeded_at",
    "acl_synced_at",
    "last_run",
    "failure_codes",
    "job",
}


@pytest.fixture
async def client(app_client: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client() as c:
        yield c


async def _expected(owner: Database, org: Org, email: str) -> dict[uuid.UUID, int]:
    """Readable active documents per source for this member, by the reference policy."""
    async with owner.tenant_transaction(org.id) as s:
        membership: uuid.UUID = (
            await s.execute(
                text(
                    "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id "
                    "WHERE u.email = :e"
                ),
                {"e": email},
            )
        ).scalar_one()
        documents: dict[uuid.UUID, uuid.UUID] = {
            d: src for d, src in await s.execute(text("SELECT id, source_id FROM documents"))
        }
        access = await load_document_access(s, org.id, documents)
        ctx = await resolve_authorization_context(
            s, organization_id=org.id, membership_id=membership
        )
    return dict(Counter(documents[d] for d, facts in access.items() if readable(ctx, facts)))


async def _titles(owner: Database, org: Org) -> set[str]:
    async with owner.tenant_transaction(org.id) as s:
        rows = await s.execute(text("SELECT title, display_ref FROM documents"))
        return {v for row in rows for v in row if v and len(v) >= 8}


@pytest.mark.parametrize(("slug", "email"), personas())
async def test_members_see_only_sources_they_read_from_with_their_own_counts(
    client: httpx.AsyncClient,
    owner_db: Database,
    corpus_orgs: dict[str, Org],
    slug: str,
    email: str,
) -> None:
    org = corpus_orgs[slug]
    assert (await login(client, email)).status_code == 200
    response = await client.get("/v1/sources")
    assert response.status_code == 200
    got = {uuid.UUID(s["id"]): s["readable_documents"] for s in response.json()["sources"]}
    expected = {k: v for k, v in (await _expected(owner_db, org, email)).items() if v > 0}
    assert got == expected
    for title in await _titles(owner_db, org):
        assert title not in response.text


@pytest.mark.parametrize(("slug", "email"), personas())
async def test_the_admin_views_are_administrators_only(
    client: httpx.AsyncClient, corpus_orgs: dict[str, Org], slug: str, email: str
) -> None:
    assert (await login(client, email)).status_code == 200
    me = (await client.get("/v1/me")).json()
    admin = "admin" in me["capabilities"]
    listing = await client.get("/v1/admin/sources")
    unknown = await client.get(f"/v1/admin/sources/{uuid.uuid4()}/failures")
    activity = await client.get("/v1/admin/activity")
    if admin:
        assert listing.status_code == 200
        assert unknown.status_code == 404
        assert activity.status_code == 200
    else:
        # Identical for existing and nonexistent sources: no oracle for non-admins.
        for response in (listing, unknown, activity):
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "permission_denied"


async def test_the_administrator_sees_aggregates_and_opaque_failures(
    client: httpx.AsyncClient, owner_db: Database, corpus_orgs: dict[str, Org]
) -> None:
    org = corpus_orgs["halden"]
    assert (await login(client, "lena.brandt@halden.example")).status_code == 200
    response = await client.get("/v1/admin/sources")
    assert response.status_code == 200
    sources = {s["key"]: s for s in response.json()["sources"]}
    assert set(sources) == {c.key for c in org.sources.sources}
    for source in sources.values():
        assert set(source) == ADMIN_SOURCE_KEYS
    async with owner_db.tenant_transaction(org.id) as s:
        totals: dict[str, int] = dict(
            (
                await s.execute(
                    text(
                        "SELECT s.key, count(d.id) FROM sources s LEFT JOIN documents d "
                        "ON d.source_id = s.id AND d.status = 'active' GROUP BY s.key"
                    )
                )
            ).all()
        )
    assert {k: s["documents"] for k, s in sources.items()} == totals
    for title in await _titles(owner_db, org):
        assert title not in response.text

    # HAL-4951: the ticket whose organization is deliberately unmapped (fail closed).
    support = sources["support"]
    assert support["last_run"]["status"] == "partial"
    assert {"stage": "map_access", "code": "access_unmappable:account", "count": 1} in support[
        "failure_codes"
    ]
    failures = (await client.get(f"/v1/admin/sources/{support['id']}/failures")).json()
    [failure] = failures["failures"]
    assert failure["code"] == "access_unmappable:account"
    assert failure["record_kind"] == "opaque"  # no document exists for it to read
    assert failure["record"].startswith("rec_")
    assert failure["params"] == {"external_account_ref": "360009913"}
    for leaked in ("4951", "tickets", ".jsonl", "Fernhill"):
        assert leaked not in str(failures)


async def test_members_of_another_organization_see_none_of_it(
    client: httpx.AsyncClient, corpus_orgs: dict[str, Org]
) -> None:
    halden = corpus_orgs["halden"]
    assert (await login(client, "noor.haddad@veridian.example")).status_code == 200
    listing = (await client.get("/v1/sources")).json()["sources"]
    names = {c.name for c in halden.sources.sources}
    assert not names & {s["name"] for s in listing}
