"""SCIM 2.0 provisioning (Phase 10; D-20, ADR-015): contract, authentication,
idempotency, attribute ownership and organization boundaries."""

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from lattice.db.engine import Database

from ..conftest import AppFactory, FreshWindow
from ..factories import create_member, create_oidc_connection, create_scim_token, login
from .conftest import World, patch, user

BASE = "/scim/v2"
SCIM_JSON = "application/scim+json"


async def _create(scim: httpx.AsyncClient, world: World, body: dict[str, Any]) -> dict[str, Any]:
    response = await scim.post(f"{BASE}/Users", json=body, headers=world.halden.auth)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def _membership(owner_db: Database, org: uuid.UUID, resource_id: str) -> dict[str, Any]:
    async with owner_db.tenant_transaction(org) as s:
        row = (
            await s.execute(
                text(
                    "SELECT m.id, m.status, m.is_admin, m.clearance, m.account_scope, "
                    "m.role_label, u.id AS user_id, u.email, u.display_name, "
                    "u.home_organization_id FROM directory_identities d "
                    "JOIN memberships m ON m.organization_id = d.organization_id "
                    "AND m.id = d.membership_id JOIN users u ON u.id = m.user_id WHERE d.id = :d"
                ),
                {"d": uuid.UUID(resource_id)},
            )
        ).one()
    return dict(row._mapping)


async def _events(owner_db: Database, org: uuid.UUID) -> list[dict[str, Any]]:
    async with owner_db.tenant_transaction(org) as s:
        rows = await s.execute(
            text(
                "SELECT actor, action, target, before, after, reason FROM audit_events "
                "WHERE action LIKE 'access.%' ORDER BY created_at, id"
            )
        )
        return [dict(r._mapping) for r in rows]


# --- authentication -------------------------------------------------------------------------


async def test_every_bad_token_is_one_401(
    scim: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    revoked = await create_scim_token(
        owner_db, world.halden.org, world.halden.connection, revoked=True
    )
    bodies = []
    for header in (
        None,
        "Bearer",
        "Basic dXNlcjpwYXNz",
        "Bearer not-a-token",
        f"Bearer lsc_{'A' * 43}",
        f"Bearer {revoked}",
    ):
        headers = {"Authorization": header} if header else {}
        response = await scim.get(f"{BASE}/Users", headers=headers)
        assert response.status_code == 401
        assert response.headers["content-type"].startswith(SCIM_JSON)
        assert response.headers["www-authenticate"] == 'Bearer realm="lattice-scim"'
        bodies.append(response.json())
    assert all(b == bodies[0] for b in bodies)
    assert bodies[0]["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]


@pytest.mark.parametrize("what", ["connection", "organization"])
async def test_a_disabled_connection_or_suspended_organization_refuses_its_token(
    scim: httpx.AsyncClient, world: World, owner_db: Database, what: str
) -> None:
    sql = (
        "UPDATE oidc_connections SET status = 'disabled'"
        if what == "connection"
        else "UPDATE organizations SET status = 'suspended'"
    )
    async with owner_db.tenant_transaction(world.halden.org) as s:
        await s.execute(text(sql))
    response = await scim.get(f"{BASE}/Users", headers=world.halden.auth)
    assert response.status_code == 401


async def test_the_session_cookie_is_never_scim_authentication(
    scim: httpx.AsyncClient, world: World
) -> None:
    assert (await login(scim, world.admin.email)).status_code == 200
    response = await scim.get(f"{BASE}/Users")
    assert response.status_code == 401


async def test_scim_needs_no_csrf_header_but_cross_site_requests_are_refused(
    scim: httpx.AsyncClient, world: World
) -> None:
    body = user("e-1", "pat@halden.example")
    response = await scim.post(
        f"{BASE}/Users", json=body, headers={**world.halden.auth, "Sec-Fetch-Site": "cross-site"}
    )
    assert response.status_code == 403
    assert (
        await scim.post(f"{BASE}/Users", json=body, headers=world.halden.auth)
    ).status_code == 201


async def test_repeated_authentication_failures_are_refused_before_the_token_is_checked(
    app_client: AppFactory, world: World, fresh_window: FreshWindow
) -> None:
    async with app_client(rate_limit_sign_in_per_minute=2) as client:
        await fresh_window()
        for _ in range(2):
            bad = await client.get(f"{BASE}/Users", headers={"Authorization": "Bearer x"})
            assert bad.status_code == 401
        refused = await client.get(f"{BASE}/Users", headers={"Authorization": "Bearer x"})
        assert refused.status_code == 429
        # Even a valid token is not checked while this client is over the limit.
        limited = await client.get(f"{BASE}/Users", headers=world.halden.auth)
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) >= 1


async def test_each_token_has_its_own_request_limit(
    app_client: AppFactory, world: World, fresh_window: FreshWindow
) -> None:
    async with app_client(rate_limit_scim_per_minute=2) as client:
        await fresh_window()
        for _ in range(2):
            assert (await client.get(f"{BASE}/Users", headers=world.halden.auth)).status_code == 200
        refused = await client.get(f"{BASE}/Users", headers=world.halden.auth)
        assert refused.status_code == 429
        assert refused.json()["status"] == "429"
        # Another organization's token is unaffected.
        assert (await client.get(f"{BASE}/Users", headers=world.veridian.auth)).status_code == 200


async def test_discovery_documents(scim: httpx.AsyncClient, world: World) -> None:
    config = (await scim.get(f"{BASE}/ServiceProviderConfig", headers=world.halden.auth)).json()
    assert config["patch"]["supported"] is True
    assert config["bulk"]["supported"] is False
    assert config["etag"]["supported"] is True
    types = (await scim.get(f"{BASE}/ResourceTypes", headers=world.halden.auth)).json()
    assert [r["id"] for r in types["Resources"]] == ["User"]
    schemas = (await scim.get(f"{BASE}/Schemas", headers=world.halden.auth)).json()
    names = {a["name"] for a in schemas["Resources"][0]["attributes"]}
    assert names == {"userName", "externalId", "name", "displayName", "emails", "active"}
    assert (await scim.get(f"{BASE}/Schemas")).status_code == 401


# --- create -------------------------------------------------------------------------------


async def test_create_provisions_a_least_access_member_owned_by_the_organization(
    scim: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    response = await scim.post(
        f"{BASE}/Users", json=user("okta-1", "pat@halden.example"), headers=world.halden.auth
    )
    assert response.status_code == 201
    assert response.headers["content-type"].startswith(SCIM_JSON)
    body = response.json()
    assert response.headers["location"] == body["meta"]["location"]
    assert response.headers["etag"] == body["meta"]["version"] == 'W/"1"'
    assert body["meta"]["location"].endswith(f"/api/scim/v2/Users/{body['id']}")
    assert (body["externalId"], body["userName"], body["active"]) == (
        "okta-1",
        "pat@halden.example",
        True,
    )
    assert body["displayName"] == "Pat Doe"
    row = await _membership(owner_db, world.halden.org, body["id"])
    assert (
        row["status"],
        row["is_admin"],
        row["clearance"],
        row["account_scope"],
        row["role_label"],
        row["home_organization_id"],
    ) == ("active", False, "internal", "assigned", None, world.halden.org)
    events = await _events(owner_db, world.halden.org)
    assert [(e["actor"].split(":")[0], e["action"], e["before"]) for e in events] == [
        ("scim", "access.member_changed", None)
    ]
    assert events[0]["after"] == {
        "status": "active",
        "is_admin": False,
        "clearance": "internal",
        "account_scope": "assigned",
        "accounts": [],
        "departments": [],
        "groups": [],
    }


async def test_access_attributes_in_a_request_are_ignored(
    scim: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    body = user(
        "okta-2",
        "pat@halden.example",
        roles=[{"value": "admin"}],
        groups=[{"value": "exec-staff"}],
        entitlements=[{"value": "restricted"}],
        clearance="confidential",
        is_admin=True,
    )
    body["urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"] = {
        "department": "Leadership"
    }
    created = await _create(scim, world, body)
    row = await _membership(owner_db, world.halden.org, created["id"])
    assert (row["is_admin"], row["clearance"], row["account_scope"]) == (
        False,
        "internal",
        "assigned",
    )
    async with owner_db.tenant_transaction(world.halden.org) as s:
        links: int = (
            await s.execute(
                text(
                    "SELECT (SELECT count(*) FROM membership_departments WHERE membership_id = :m)"
                    " + (SELECT count(*) FROM access_group_members WHERE membership_id = :m)"
                    " + (SELECT count(*) FROM account_assignments WHERE membership_id = :m)"
                    " + (SELECT count(*) FROM document_grants WHERE membership_id = :m)"
                ),
                {"m": row["id"]},
            )
        ).scalar_one()
    assert links == 0


async def test_an_inactive_user_is_provisioned_suspended(
    scim: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    created = await _create(scim, world, user("okta-3", "pat@halden.example", active=False))
    assert created["active"] is False
    assert (await _membership(owner_db, world.halden.org, created["id"]))["status"] == "suspended"


@pytest.mark.parametrize(
    ("body", "scim_type"),
    [
        ({"userName": "pat@halden.example"}, "invalidSyntax"),
        (user("", "pat@halden.example"), "invalidValue"),
        (user("x", "pat", email="not-an-address"), "invalidValue"),
        (user("x", "pat\x00@halden.example"), "invalidValue"),
        (user("x", "pat@halden.example", active="maybe"), "invalidValue"),
        ([], "invalidSyntax"),
    ],
)
async def test_invalid_users_are_refused_without_echoing_the_value(
    scim: httpx.AsyncClient, world: World, body: Any, scim_type: str
) -> None:
    response = await scim.post(f"{BASE}/Users", json=body, headers=world.halden.auth)
    assert response.status_code == 400
    assert response.json()["scimType"] == scim_type
    assert "halden.example" not in response.text
    assert "not-an-address" not in response.text


async def test_malformed_json_is_invalid_syntax(scim: httpx.AsyncClient, world: World) -> None:
    response = await scim.post(
        f"{BASE}/Users",
        content=b"{not json",
        headers={**world.halden.auth, "Content-Type": SCIM_JSON},
    )
    assert response.status_code == 400
    assert response.json()["scimType"] == "invalidSyntax"


@pytest.mark.parametrize(
    "duplicate",
    [
        user("okta-1", "someone.else@halden.example"),  # same externalId
        user("okta-9", "PAT@halden.example", email="pat2@halden.example"),  # same userName
        user("okta-9", "pat2", email="maya@halden.example"),  # a member's e-mail
    ],
)
async def test_duplicates_inside_the_organization_are_conflicts(
    scim: httpx.AsyncClient, world: World, duplicate: dict[str, Any]
) -> None:
    await _create(scim, world, user("okta-1", "pat@halden.example"))
    response = await scim.post(f"{BASE}/Users", json=duplicate, headers=world.halden.auth)
    assert response.status_code == 409
    assert response.json()["scimType"] == "uniqueness"
    assert "halden.example" not in response.text


async def test_another_organizations_user_with_the_same_email_is_invisible(
    scim: httpx.AsyncClient, world: World, owner_db: Database, app_db: Database
) -> None:
    """Veridian's provisioning of maya@halden.example neither collides with, links to,
    modifies nor reveals Halden's Maya."""
    response = await scim.post(
        f"{BASE}/Users",
        json=user("v-1", "maya@halden.example", given="Imposter", family="Name"),
        headers=world.veridian.auth,
    )
    assert response.status_code == 201
    row = await _membership(owner_db, world.veridian.org, response.json()["id"])
    assert row["user_id"] != world.maya.user_id
    assert row["home_organization_id"] == world.veridian.org
    async with owner_db.tenant_transaction(world.halden.org) as s:
        name: str = (
            await s.execute(
                text("SELECT display_name FROM users WHERE id = :u"), {"u": world.maya.user_id}
            )
        ).scalar_one()
    assert name == "maya"
    # Maya can still sign in to Halden only; the Veridian person is a different user.
    async with app_db.unscoped_transaction() as s:
        orgs: list[str] = list(
            (
                await s.execute(
                    text("SELECT organization_slug FROM lattice_identity_memberships('dev', :e)"),
                    {"e": "maya@halden.example"},
                )
            )
            .scalars()
            .all()
        )
    assert list(orgs) == ["halden"]


async def test_parallel_creates_of_one_person_make_one_member(
    scim: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    body = user("okta-race", "race@halden.example")
    responses = await asyncio.gather(
        *(scim.post(f"{BASE}/Users", json=body, headers=world.halden.auth) for _ in range(6))
    )
    assert sorted(r.status_code for r in responses) == [201] + [409] * 5
    async with owner_db.tenant_transaction(world.halden.org) as s:
        count: int = (
            await s.execute(text("SELECT count(*) FROM directory_identities"))
        ).scalar_one()
    assert count == 1


# --- read and list ------------------------------------------------------------------------


async def test_read_and_filter(scim: httpx.AsyncClient, world: World) -> None:
    a = await _create(scim, world, user("okta-a", "ann@halden.example"))
    await _create(scim, world, user("okta-b", "bob@halden.example"))
    got = await scim.get(f"{BASE}/Users/{a['id']}", headers=world.halden.auth)
    assert got.json() == a
    for query in ('userName eq "ANN@halden.example"', 'externalId eq "okta-a"'):
        listed = (
            await scim.get(f"{BASE}/Users", params={"filter": query}, headers=world.halden.auth)
        ).json()
        assert (listed["totalResults"], [r["id"] for r in listed["Resources"]]) == (1, [a["id"]])
    page = (
        await scim.get(
            f"{BASE}/Users", params={"startIndex": 2, "count": 1}, headers=world.halden.auth
        )
    ).json()
    assert (page["totalResults"], page["startIndex"], page["itemsPerPage"]) == (2, 2, 1)
    none = (
        await scim.get(
            f"{BASE}/Users", params={"filter": 'userName eq "x"'}, headers=world.halden.auth
        )
    ).json()
    assert (none["totalResults"], none["Resources"]) == (0, [])


@pytest.mark.parametrize(
    "query", ['displayName eq "x"', 'userName co "x"', "userName eq x", 'userName eq "a" or 1']
)
async def test_unsupported_filters_are_refused(
    scim: httpx.AsyncClient, world: World, query: str
) -> None:
    response = await scim.get(f"{BASE}/Users", params={"filter": query}, headers=world.halden.auth)
    assert response.status_code == 400
    assert response.json()["scimType"] == "invalidFilter"


async def test_organizations_never_see_each_others_users(
    scim: httpx.AsyncClient, world: World
) -> None:
    halden = await _create(scim, world, user("same-id", "pat@halden.example"))
    veridian = await scim.post(
        f"{BASE}/Users", json=user("same-id", "pat@veridian.example"), headers=world.veridian.auth
    )
    assert veridian.status_code == 201  # the same external ID in another organization
    other = veridian.json()["id"]
    assert other != halden["id"]
    requests: list[tuple[str, dict[str, Any] | None]] = [
        ("GET", None),
        ("PUT", user("same-id", "x@veridian.example")),
        ("PATCH", patch({"op": "replace", "path": "active", "value": False})),
        ("DELETE", None),
    ]
    for method, payload in requests:
        foreign = await scim.request(
            method, f"{BASE}/Users/{other}", headers=world.halden.auth, json=payload
        )
        missing = await scim.request(
            method, f"{BASE}/Users/{uuid.uuid4()}", headers=world.halden.auth, json=payload
        )
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()
    listed = (await scim.get(f"{BASE}/Users", headers=world.halden.auth)).json()
    assert [r["id"] for r in listed["Resources"]] == [halden["id"]]
    assert (
        await scim.get(f"{BASE}/Users/not-a-uuid", headers=world.halden.auth)
    ).status_code == 404


# --- update -------------------------------------------------------------------------------


async def test_an_identical_replace_changes_and_records_nothing(
    scim: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    created = await _create(scim, world, user("okta-1", "pat@halden.example"))
    events = len(await _events(owner_db, world.halden.org))
    for _ in range(2):
        again = await scim.put(
            f"{BASE}/Users/{created['id']}",
            json=user("okta-1", "pat@halden.example"),
            headers=world.halden.auth,
        )
        assert again.status_code == 200
        assert again.json() == created
    assert len(await _events(owner_db, world.halden.org)) == events


async def test_replace_updates_the_identity_and_records_it(
    scim: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    created = await _create(scim, world, user("okta-1", "pat@halden.example"))
    response = await scim.put(
        f"{BASE}/Users/{created['id']}",
        json=user("okta-1", "patricia@halden.example", given="Patricia"),
        headers=world.halden.auth,
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["userName"], body["displayName"], body["meta"]["version"]) == (
        "patricia@halden.example",
        "Patricia Doe",
        'W/"2"',
    )
    row = await _membership(owner_db, world.halden.org, created["id"])
    assert (row["email"], row["display_name"]) == ("patricia@halden.example", "Patricia Doe")
    last = (await _events(owner_db, world.halden.org))[-1]
    assert last["action"] == "access.identity_changed"
    assert last["before"]["email"] == "pat@halden.example"
    assert last["after"]["email"] == "patricia@halden.example"


async def test_external_id_is_immutable(scim: httpx.AsyncClient, world: World) -> None:
    created = await _create(scim, world, user("okta-1", "pat@halden.example"))
    for method, body in (
        ("PUT", user("okta-2", "pat@halden.example")),
        ("PATCH", patch({"op": "replace", "path": "externalId", "value": "okta-2"})),
    ):
        response = await scim.request(
            method, f"{BASE}/Users/{created['id']}", json=body, headers=world.halden.auth
        )
        assert response.status_code == 400
        assert response.json()["scimType"] == "mutability"


async def test_an_update_cannot_take_another_members_email(
    scim: httpx.AsyncClient, world: World
) -> None:
    created = await _create(scim, world, user("okta-1", "pat@halden.example"))
    response = await scim.patch(
        f"{BASE}/Users/{created['id']}",
        json=patch(
            {
                "op": "replace",
                "path": 'emails[type eq "work"].value',
                "value": "maya@halden.example",
            }
        ),
        headers=world.halden.auth,
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "replace", "value": {"active": False}},  # path-less (Okta)
        {"op": "Replace", "path": "active", "value": "False"},  # string boolean (Entra)
    ],
)
async def test_deactivation_is_idempotent_and_audited_once(
    scim: httpx.AsyncClient, world: World, owner_db: Database, operation: dict[str, Any]
) -> None:
    created = await _create(scim, world, user("okta-1", "pat@halden.example"))
    for _ in range(2):
        response = await scim.patch(
            f"{BASE}/Users/{created['id']}", json=patch(operation), headers=world.halden.auth
        )
        assert response.status_code == 200
        assert response.json()["active"] is False
    events = [e for e in await _events(owner_db, world.halden.org) if e["after"]]
    statuses = [e["after"].get("status") for e in events]
    assert statuses == ["active", "suspended"]
    assert events[-1]["reason"] == "Deactivated by the identity provider (SCIM)"
    reactivated = await scim.patch(
        f"{BASE}/Users/{created['id']}",
        json=patch({"op": "replace", "path": "active", "value": True}),
        headers=world.halden.auth,
    )
    assert reactivated.json()["active"] is True
    assert (await _membership(owner_db, world.halden.org, created["id"]))["status"] == "active"


async def test_if_match(scim: httpx.AsyncClient, world: World) -> None:
    created = await _create(scim, world, user("okta-1", "pat@halden.example"))
    url = f"{BASE}/Users/{created['id']}"
    change = patch({"op": "replace", "path": "displayName", "value": "P"})
    stale = await scim.patch(url, json=change, headers={**world.halden.auth, "If-Match": 'W/"7"'})
    assert stale.status_code == 412
    fresh = await scim.patch(url, json=change, headers={**world.halden.auth, "If-Match": 'W/"1"'})
    assert fresh.status_code == 200
    assert fresh.headers["etag"] == 'W/"2"'


@pytest.mark.parametrize(
    ("operation", "scim_type"),
    [
        ({"op": "move", "path": "active", "value": True}, "invalidSyntax"),
        ({"op": "remove", "path": "userName"}, "mutability"),
        ({"op": "remove"}, "noTarget"),
        ({"op": "replace", "path": "active", "value": "yes"}, "invalidValue"),
    ],
)
async def test_invalid_patches(
    scim: httpx.AsyncClient, world: World, operation: dict[str, Any], scim_type: str
) -> None:
    created = await _create(scim, world, user("okta-1", "pat@halden.example"))
    response = await scim.patch(
        f"{BASE}/Users/{created['id']}", json=patch(operation), headers=world.halden.auth
    )
    assert response.status_code == 400
    assert response.json()["scimType"] == scim_type


async def test_attributes_lattice_does_not_keep_are_ignored_by_patch(
    scim: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    created = await _create(scim, world, user("okta-1", "pat@halden.example"))
    events = len(await _events(owner_db, world.halden.org))
    response = await scim.patch(
        f"{BASE}/Users/{created['id']}",
        json=patch(
            {"op": "add", "path": "title", "value": "CFO"},
            {"op": "replace", "path": "roles", "value": [{"value": "admin"}]},
            {
                "op": "add",
                "path": "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department",
                "value": "Leadership",
            },
        ),
        headers=world.halden.auth,
    )
    assert response.status_code == 200
    assert response.json() == created
    assert len(await _events(owner_db, world.halden.org)) == events


# --- delete -------------------------------------------------------------------------------


async def test_delete_removes_for_good_and_is_idempotent(
    scim: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    created = await _create(scim, world, user("okta-1", "pat@halden.example"))
    url = f"{BASE}/Users/{created['id']}"
    assert (await scim.delete(url, headers=world.halden.auth)).status_code == 204
    assert (await _membership(owner_db, world.halden.org, created["id"]))["status"] == "removed"
    events = len(await _events(owner_db, world.halden.org))
    assert (await scim.delete(url, headers=world.halden.auth)).status_code == 204
    assert len(await _events(owner_db, world.halden.org)) == events
    assert (await scim.get(url, headers=world.halden.auth)).status_code == 404
    reactivate = patch({"op": "replace", "path": "active", "value": True})
    assert (await scim.patch(url, json=reactivate, headers=world.halden.auth)).status_code == 404
    listed = (await scim.get(f"{BASE}/Users", headers=world.halden.auth)).json()
    assert listed["totalResults"] == 0
    # Removal is terminal: the same person cannot be provisioned again over it.
    again = await scim.post(
        f"{BASE}/Users", json=user("okta-1", "pat@halden.example"), headers=world.halden.auth
    )
    assert again.status_code == 409


async def test_a_token_sees_only_its_own_connections_people(
    scim: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    """After a switch of identity provider, the new connection's token neither lists,
    reads, changes nor collides with people the old connection provisioned."""
    old = await _create(scim, world, user("okta-1", "pat@halden.example"))
    async with owner_db.tenant_transaction(world.halden.org) as s:
        await s.execute(text("UPDATE oidc_connections SET status = 'disabled'"))
    new_connection = await create_oidc_connection(
        owner_db, world.halden.org, issuer="https://login.halden.example"
    )
    token = await create_scim_token(owner_db, world.halden.org, new_connection)
    auth = {"Authorization": f"Bearer {token}"}
    assert (await scim.get(f"{BASE}/Users", headers=auth)).json()["totalResults"] == 0
    assert (await scim.get(f"{BASE}/Users/{old['id']}", headers=auth)).status_code == 404
    gone = await scim.delete(f"{BASE}/Users/{old['id']}", headers=auth)
    assert gone.status_code == 404
    # The same externalId is free in the new provider's namespace (the e-mail is not).
    fresh = await scim.post(
        f"{BASE}/Users", json=user("okta-1", "pat.new@halden.example"), headers=auth
    )
    assert fresh.status_code == 201
    assert fresh.json()["id"] != old["id"]


async def test_scim_never_touches_members_it_did_not_provision(
    scim: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    """Manifest members have no directory identity: nothing in SCIM can name them."""
    listed = (await scim.get(f"{BASE}/Users", headers=world.halden.auth)).json()
    assert listed["totalResults"] == 0
    response = await scim.delete(
        f"{BASE}/Users/{world.maya.membership_id}", headers=world.halden.auth
    )
    assert response.status_code == 404
    await create_member(owner_db, world.halden.org, "omar@halden.example")
    assert (await scim.get(f"{BASE}/Users", headers=world.halden.auth)).json()["totalResults"] == 0
