"""Phase 9 privacy closure: private groups and audit events an administrator may not
know about (security-model §10 "Access groups" and "Audit log"; D-18; SEC-14, SEC-15).

* A private (source-synced) group the administrator is not in is acknowledged only as
  "the member is in private groups you can't see": no name, member list, member count
  or identifier anywhere -- member pages, explanations, the directory, refusals.
* An audit event that targets a document the administrator cannot read now is not
  listed at all: such events change neither the audit result set nor its paging. They
  stay in the database, and an administrator who can read the document sees them.
* Being an administrator reveals none of this; organizations stay separate.
"""

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from lattice.db.engine import Database

from ..access.conftest import CANARY, add_to_group, build, sign_in, source_group
from ..conftest import CSRF, AppFactory
from ..retrieval.world import Org


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await build(owner_db)
    # Olga is the administrator who can read the confidential pricing sheet.
    await org.execute(
        "UPDATE memberships SET clearance = 'confidential' WHERE id = :m", m=org.members["olga"]
    )
    return org


@pytest.fixture
async def client(app_client: AppFactory) -> Any:
    async with app_client() as c:
        yield c


def _error(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()["error"]
    return {k: v for k, v in body.items() if k != "request_id"}


async def _member_page(client: httpx.AsyncClient, org: Org, name: str) -> httpx.Response:
    response = await client.get(f"/v1/admin/access/members/{org.members[name]}")
    assert response.status_code == 200, response.text
    return response


async def _channel_identifiers(org: Org) -> list[str]:
    rows = await org.execute(
        "SELECT id::text, key::text, external_ref, name FROM access_groups WHERE origin = 'source'"
    )
    return [value for row in rows for value in row if value]


# --- private groups ---------------------------------------------------------------------


async def test_an_administrator_outside_a_private_channel_cannot_infer_anything_about_it(
    client: httpx.AsyncClient, org: Org
) -> None:
    await sign_in(client, org, "lena")
    before = (await _member_page(client, org, "maya")).json()
    assert before["groups"] == []
    assert before["has_hidden_groups"] is True
    directory = (await client.get("/v1/admin/access/directory")).json()
    members = (await client.get("/v1/admin/access/members")).json()

    # More members in the channel, a second private channel, a renamed first one:
    # nothing Lena receives changes -- no count, no identifier to correlate.
    await add_to_group(org, "acct-acme-private", "grace")
    await add_to_group(org, "acct-acme-private", "tom")
    await source_group(org, "acct-kestrel-private", ["maya", "grace"])
    await org.execute(
        "UPDATE access_groups SET name = :n WHERE id = :g",
        n=f"#renamed {CANARY}",
        g=org.groups["acct-acme-private"],
    )
    after = await _member_page(client, org, "maya")
    assert after.json() == before
    assert (await client.get("/v1/admin/access/directory")).json() == directory
    assert (await client.get("/v1/admin/access/members")).json() == members
    for identifier in await _channel_identifiers(org):
        assert identifier not in after.text
    assert CANARY not in after.text


async def test_explanations_acknowledge_a_private_channel_without_identifying_it(
    client: httpx.AsyncClient, org: Org
) -> None:
    await source_group(org, "acct-kestrel-private", ["maya"])

    async def via(channel: str) -> list[dict[str, Any]]:
        await org.execute(
            "DELETE FROM document_grants WHERE document_id = :d", d=org.documents["handbook"]
        )
        await org.execute(
            "INSERT INTO document_grants (organization_id, document_id, principal_type, "
            "group_id, origin) VALUES (:o, :d, 'group', :g, 'source')",
            o=org.id,
            d=org.documents["handbook"],
            g=org.groups[channel],
        )
        response = await client.get(
            f"/v1/admin/access/members/{org.members['maya']}/documents/{org.documents['handbook']}"
        )
        for identifier in await _channel_identifiers(org):
            assert identifier not in response.text
        found: list[dict[str, Any]] = response.json()["via"]
        return found

    await sign_in(client, org, "lena")
    first = await via("acct-acme-private")
    assert first == [{"principal_type": "group", "name": None}]
    # Two different channels are indistinguishable.
    assert await via("acct-kestrel-private") == first


@pytest.mark.parametrize("endpoint", ["preview", "change"])
async def test_a_private_group_id_is_refused_exactly_like_an_unknown_one(
    client: httpx.AsyncClient, org: Org, owner_db: Database, endpoint: str
) -> None:
    await sign_in(client, org, "lena")
    url = f"/v1/admin/access/members/{org.members['maya']}"
    version = (await _member_page(client, org, "maya")).json()["version"]

    async def send(group: uuid.UUID) -> httpx.Response:
        body = {"groups": [str(org.groups["exec"]), str(group)]}
        if endpoint == "preview":
            return await client.post(f"{url}/preview", json=body, headers=CSRF)
        return await client.patch(
            url, json={**body, "version": version, "reason": "x"}, headers=CSRF
        )

    private = await send(org.groups["acct-acme-private"])
    unknown = await send(uuid.uuid4())
    assert private.status_code == unknown.status_code == 422
    assert _error(private) == _error(unknown)
    assert _error(private)["details"] == [{"field": "groups", "issue": "unknown"}]
    async with owner_db.tenant_transaction(org.id) as s:
        members: int = (
            await s.execute(
                text("SELECT count(*) FROM access_group_members WHERE group_id = :g"),
                {"g": org.groups["acct-acme-private"]},
            )
        ).scalar_one()
    assert members == 1  # untouched


async def test_a_change_keeps_hidden_private_membership_untouched(
    client: httpx.AsyncClient, org: Org
) -> None:
    await sign_in(client, org, "lena")
    page = (await _member_page(client, org, "maya")).json()
    changed = await client.patch(
        f"/v1/admin/access/members/{org.members['maya']}",
        json={"version": page["version"], "groups": [], "clearance": "confidential", "reason": "x"},
        headers=CSRF,
    )
    assert changed.status_code == 200
    rows = await org.execute(
        "SELECT 1 FROM access_group_members WHERE group_id = :g AND membership_id = :m",
        g=org.groups["acct-acme-private"],
        m=org.members["maya"],
    )
    assert rows.first() is not None


# --- audit events about unreadable documents --------------------------------------------


async def _audit(client: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get("/v1/admin/audit-events", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _grant_and_revoke(client: httpx.AsyncClient, org: Org, document: str) -> list[str]:
    created = await client.post(
        f"/v1/admin/access/members/{org.members['maya']}/grants",
        json={"document_id": str(org.documents[document]), "reason": f"Grant {document}"},
        headers=CSRF,
    )
    assert created.status_code == 201, created.text
    revoked = await client.post(
        f"/v1/admin/access/grants/{created.json()['id']}/revoke",
        json={"reason": "Done"},
        headers=CSRF,
    )
    assert revoked.status_code == 204
    return [created.json()["id"]]


async def _event_ids(owner: Database, org: Org, action: str) -> list[str]:
    async with owner.tenant_transaction(org.id) as s:
        rows = await s.execute(
            text("SELECT id::text FROM audit_events WHERE action = :a ORDER BY created_at"),
            {"a": action},
        )
        found: list[str] = list(rows.scalars())
    return found


async def test_grants_on_an_unreadable_document_leave_the_viewers_audit_view_unchanged(
    client: httpx.AsyncClient, app_client: AppFactory, org: Org, owner_db: Database
) -> None:
    # A readable event first, so the view is not empty and paging is exercised.
    await sign_in(client, org, "lena")
    await _grant_and_revoke(client, org, "kestrel-notes")
    full = await _audit(client)
    pages = [await _audit(client, limit=1)]
    while pages[-1]["next_before"]:
        pages.append(await _audit(client, limit=1, before=pages[-1]["next_before"]))

    # Olga can read the pricing sheet; she grants it to Maya and revokes it.
    async with app_client() as olga:
        await sign_in(olga, org, "olga")
        await _grant_and_revoke(olga, org, "pricing")
        seen_by_olga = await _audit(olga)
    titles = [e["document"]["title"] for e in seen_by_olga["items"] if e["document"]]
    assert titles.count(f"Pricing sheet {CANARY}") == 2  # readable events appear normally

    # Lena's view is identical: same events, same paging, nothing about the new ones.
    assert await _audit(client) == full
    again = [await _audit(client, limit=1)]
    while again[-1]["next_before"]:
        again.append(await _audit(client, limit=1, before=again[-1]["next_before"]))
    assert again == pages
    shown = json.dumps(await _audit(client))
    assert CANARY not in shown
    assert "Grant pricing" not in shown
    assert str(org.documents["pricing"]) not in shown

    # A hidden event's ID used as a keyset anchor behaves like a missing one.
    hidden = (await _event_ids(owner_db, org, "access.grant_created"))[-1]
    as_anchor = await _audit(client, before=hidden)
    assert as_anchor == await _audit(client, before=str(uuid.uuid4())) == full
    # The events themselves are kept.
    assert len(await _event_ids(owner_db, org, "access.grant_created")) == 2


async def test_becoming_an_administrator_reveals_neither_private_groups_nor_hidden_events(
    client: httpx.AsyncClient, app_client: AppFactory, org: Org
) -> None:
    async with app_client() as olga:
        await sign_in(olga, org, "olga")
        await _grant_and_revoke(olga, org, "pricing")
    # Tom (not in the channel, Internal) only gains the capability.
    await sign_in(client, org, "tom")
    assert (await client.get("/v1/admin/access/members")).status_code == 403
    await org.execute("UPDATE memberships SET is_admin = true WHERE id = :m", m=org.members["tom"])
    maya = await _member_page(client, org, "maya")
    assert maya.json()["groups"] == []
    assert maya.json()["has_hidden_groups"] is True
    for identifier in await _channel_identifiers(org):
        assert identifier not in maya.text
    assert await _audit(client) == {"items": [], "next_before": None}


async def test_another_organizations_events_and_groups_stay_invisible(
    client: httpx.AsyncClient, app_client: AppFactory, org: Org, owner_db: Database
) -> None:
    other = await build(owner_db, "veridian")
    await other.execute(
        "UPDATE memberships SET clearance = 'confidential' WHERE id = :m", m=other.members["olga"]
    )
    await sign_in(client, org, "lena")
    before = await _audit(client)
    async with app_client() as theirs:
        await sign_in(theirs, other, "lena")
        await _grant_and_revoke(theirs, other, "kestrel-notes")  # readable to them
        assert len((await _audit(theirs))["items"]) == 2
    assert await _audit(client) == before
    foreign = await client.get(f"/v1/admin/access/members/{other.members['maya']}")
    assert foreign.status_code == 404
