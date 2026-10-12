"""Accounts administered in the product (Phase 12, P12.3; phase-12-plan C10; ADR-017;
migration 0018).

Proves: an administrator creates, edits, archives and reactivates their organization's
accounts -- each with a reason, a version check and an audit event -- and nothing else:
the key never changes, creating grants nobody access, archiving deletes and hides nothing
(documents, assignments and readability stay) and reactivating keeps the same account;
another organization's accounts are invisible and indistinguishable from random IDs; and
the database refuses what the code refuses, for a statement that bypasses the code."""

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice.db.engine import Database

from ..conftest import CSRF
from .conftest import World, as_member, events, owner_rows, patch, post, readable


def _new(key: str = "fernhill-studios", **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "name": "Fernhill Studios",
        "aliases": ["Fernhill"],
        "status": "prospect",
        "reason": "Signed up on 24 September",
        **extra,
    }


async def _create(client: httpx.AsyncClient, **body: Any) -> httpx.Response:
    return await post(client, "accounts", _new(**body))


# --- create -----------------------------------------------------------------------------


async def test_an_administrator_creates_an_account_that_grants_nobody_anything(
    client: httpx.AsyncClient, world: World, owner_db: Database, app_db: Database
) -> None:
    halden = world.halden
    lena_before = await readable(app_db, halden.id, halden.members["lena"])
    await as_member(client, halden, "lena")
    created = await _create(client, aliases=[" Fernhill ", "fernhill", "FH  Studios"])
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["key"], body["name"], body["status"], body["lifecycle"]) == (
        "fernhill-studios",
        "Fernhill Studios",
        "prospect",
        "active",
    )
    # Trimmed, inner whitespace collapsed, duplicates (ignoring case) dropped.
    assert body["aliases"] == ["Fernhill", "FH Studios"]
    assert (body["references"], body["assigned_members"], body["mappings"]) == (0, 0, [])
    # Nobody is assigned it; the administrator reads exactly what they read before.
    assigned = await owner_rows(
        owner_db,
        halden.id,
        "SELECT count(*) FROM account_assignments WHERE account_id = :a",
        a=uuid.UUID(body["id"]),
    )
    assert assigned[0][0] == 0
    assert await readable(app_db, halden.id, halden.members["lena"]) == lena_before
    [event] = await events(owner_db, halden.id)
    assert event.actor == f"member:{halden.members['lena']}"
    assert event.action == "account.created"
    assert event.target == {"account_id": body["id"]}
    assert event.before is None
    assert event.after == {
        "key": "fernhill-studios",
        "name": "Fernhill Studios",
        "aliases": ["Fernhill", "FH Studios"],
        "status": "prospect",
        "lifecycle": "active",
    }
    assert event.reason == "Signed up on 24 September"


@pytest.mark.parametrize(
    "body",
    [
        {"key": "Fernhill"},  # uppercase
        {"key": "-fernhill"},
        {"key": "fern hill"},
        {"key": "x" * 64},
        {"name": "   "},
        {"name": "Fern\x00hill"},
        {"aliases": ["ok", "x" * 101]},
        {"aliases": [f"a{i}" for i in range(21)]},
        {"reason": "  "},
        {"status": "archived"},  # the lifecycle is not a business status
        {"archived_at": "2026-10-01T00:00:00Z"},  # unknown fields are refused
        {"organization_id": str(uuid.uuid4())},
    ],
)
async def test_invalid_accounts_are_refused(
    client: httpx.AsyncClient, world: World, owner_db: Database, body: dict[str, Any]
) -> None:
    await as_member(client, world.halden, "lena")
    response = await _create(client, **body)
    assert response.status_code == 422
    assert await events(owner_db, world.halden.id) == []


async def test_keys_are_unique_within_the_organization_only(
    client: httpx.AsyncClient, world: World
) -> None:
    await as_member(client, world.halden, "lena")
    taken = await _create(client, key="acme")
    assert taken.status_code == 409
    assert taken.json()["error"]["details"] == [{"field": "key", "issue": "taken"}]
    # Veridian's own key does not collide with Halden's, and is not revealed by it.
    await as_member(client, world.veridian, "victor")
    assert (await _create(client, key="kestrel")).status_code == 201
    assert (await _create(client, key="quarry")).status_code == 201
    await as_member(client, world.halden, "lena")
    assert (await _create(client, key="quarry")).status_code == 201


async def test_concurrent_creates_of_one_key_yield_one_account(
    app_client: Any, world: World, owner_db: Database
) -> None:
    async def attempt() -> int:
        async with app_client() as c:
            await as_member(c, world.halden, "lena")
            return (await _create(c)).status_code

    results = sorted(await asyncio.gather(attempt(), attempt()))
    assert results == [201, 409]
    count = await owner_rows(
        owner_db, world.halden.id, "SELECT count(*) FROM accounts WHERE key = 'fernhill-studios'"
    )
    assert count[0][0] == 1
    assert len(await events(owner_db, world.halden.id)) == 1


# --- read --------------------------------------------------------------------------------


async def test_the_list_finds_by_name_key_and_alias_and_filters_by_lifecycle(
    client: httpx.AsyncClient, world: World
) -> None:
    halden = world.halden
    await as_member(client, halden, "lena")
    created = (await _create(client, aliases=["Greenhouse"])).json()
    listed = (await client.get("/v1/admin/access/accounts")).json()
    assert [a["key"] for a in listed["items"]] == ["acme", "fernhill-studios", "kestrel"]
    for q in ("fernhill", "FERNHILL-stud", "greenhouse"):
        found = (await client.get("/v1/admin/access/accounts", params={"q": q})).json()
        assert [a["key"] for a in found["items"]] == ["fernhill-studios"], q
    page = (await client.get("/v1/admin/access/accounts", params={"limit": 2})).json()
    rest = (
        await client.get(
            "/v1/admin/access/accounts", params={"limit": 2, "after": page["next_after"]}
        )
    ).json()
    assert [a["key"] for a in page["items"] + rest["items"]] == [
        "acme",
        "fernhill-studios",
        "kestrel",
    ]
    assert rest["next_after"] is None
    await post(
        client,
        f"accounts/{created['id']}/archive",
        {"version": created["version"], "reason": "Never signed"},
    )
    archived = (
        await client.get("/v1/admin/access/accounts", params={"lifecycle": "archived"})
    ).json()
    active = (await client.get("/v1/admin/access/accounts", params={"lifecycle": "active"})).json()
    assert [a["key"] for a in archived["items"]] == ["fernhill-studios"]
    assert [a["key"] for a in active["items"]] == ["acme", "kestrel"]
    # Veridian's accounts never appear in Halden's list.
    assert all(a["id"] != str(world.veridian.accounts["acme"]) for a in listed["items"])


# --- edit ---------------------------------------------------------------------------------


async def test_an_edit_changes_only_what_is_sent_with_a_version_and_a_reason(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    halden = world.halden
    await as_member(client, halden, "lena")
    acme = (await client.get(f"/v1/admin/access/accounts/{halden.accounts['acme']}")).json()
    changed = await patch(
        client,
        f"accounts/{acme['id']}",
        {"version": acme["version"], "aliases": ["Acme", "ACME Co"], "reason": "Common names"},
    )
    assert changed.status_code == 200, changed.text
    after = changed.json()
    assert (after["name"], after["aliases"], after["key"]) == (
        "Acme Corp",
        ["Acme", "ACME Co"],
        "acme",
    )
    assert after["version"] != acme["version"]
    [event] = await events(owner_db, halden.id)
    assert event.action == "account.updated"
    assert event.before["aliases"] == []
    assert event.after["aliases"] == ["Acme", "ACME Co"]
    assert event.before["name"] == event.after["name"] == "Acme Corp"
    # The reviewed version moved on: 409, nothing written.
    stale = await patch(
        client,
        f"accounts/{acme['id']}",
        {"version": acme["version"], "name": "Acme Corporation", "reason": "Rename"},
    )
    assert stale.status_code == 409
    same = await patch(
        client,
        f"accounts/{acme['id']}",
        {"version": after["version"], "status": "active", "reason": "No-op"},
    )
    assert same.status_code == 422
    assert same.json()["error"]["details"] == [{"field": "change", "issue": "no_change"}]
    # The key, the lifecycle and the organization are not editable here.
    for field, value in (("key", "acme-2"), ("archived_at", None), ("lifecycle", "archived")):
        refused = await patch(
            client,
            f"accounts/{acme['id']}",
            {"version": after["version"], field: value, "reason": "Try"},
        )
        assert refused.status_code == 422, field
    assert len(await events(owner_db, halden.id)) == 1


# --- lifecycle ------------------------------------------------------------------------------


async def test_archiving_keeps_everything_and_reactivating_keeps_the_same_account(
    client: httpx.AsyncClient, world: World, owner_db: Database, app_db: Database
) -> None:
    halden = world.halden
    maya = halden.members["maya"]
    maya_before = await readable(app_db, halden.id, maya)
    assert halden.documents["acme-plan"] in maya_before
    await as_member(client, halden, "lena")
    acme = (await client.get(f"/v1/admin/access/accounts/{halden.accounts['acme']}")).json()
    archived = await post(
        client, f"accounts/{acme['id']}/archive", {"version": acme["version"], "reason": "Churned"}
    )
    assert archived.status_code == 200, archived.text
    body = archived.json()
    assert (body["lifecycle"], body["id"], body["key"]) == ("archived", acme["id"], "acme")
    assert body["archived_at"] is not None
    assert body["assigned_members"] == acme["assigned_members"] == 1
    # Documents keep their account; Maya reads exactly what she read before.
    linked = await owner_rows(
        owner_db,
        halden.id,
        "SELECT count(*) FROM documents WHERE account_id = :a",
        a=halden.accounts["acme"],
    )
    assert linked[0][0] == 1
    assert await readable(app_db, halden.id, maya) == maya_before
    # Ask's account list still shows it to Maya, marked archived.
    await as_member(client, halden, "maya")
    accounts = (await client.get("/v1/accounts")).json()["accounts"]
    assert accounts == [{"key": "acme", "name": "Acme Corp", "archived": True}]

    await as_member(client, halden, "lena")
    # Read-only while archived; archiving twice changes nothing.
    edit = await patch(
        client, f"accounts/{acme['id']}", {"version": body["version"], "name": "X", "reason": "x"}
    )
    assert edit.status_code == 422
    assert edit.json()["error"]["details"] == [{"field": "account", "issue": "archived"}]
    again = await post(
        client, f"accounts/{acme['id']}/archive", {"version": body["version"], "reason": "x"}
    )
    assert again.status_code == 422
    stale = await post(
        client, f"accounts/{acme['id']}/reactivate", {"version": acme["version"], "reason": "x"}
    )
    assert stale.status_code == 409
    reactivated = await post(
        client,
        f"accounts/{acme['id']}/reactivate",
        {"version": body["version"], "reason": "Renewed"},
    )
    assert reactivated.status_code == 200
    assert (reactivated.json()["lifecycle"], reactivated.json()["id"]) == ("active", acme["id"])
    count = await owner_rows(owner_db, halden.id, "SELECT count(*) FROM accounts")
    assert count[0][0] == 2  # no duplicate account
    assert [(e.action, e.before, e.after) for e in await events(owner_db, halden.id)] == [
        ("account.archived", {"lifecycle": "active"}, {"lifecycle": "archived"}),
        ("account.reactivated", {"lifecycle": "archived"}, {"lifecycle": "active"}),
    ]


async def test_the_audit_view_names_the_account(client: httpx.AsyncClient, world: World) -> None:
    await as_member(client, world.halden, "lena")
    await _create(client)
    [entry] = (await client.get("/v1/admin/audit-events")).json()["items"]
    assert entry["action"] == "account.created"
    details = {d["field"]: (d["before"], d["after"]) for d in entry["details"]}
    assert details["account"] == (None, "Fernhill Studios")
    assert details["aliases"] == (None, "Fernhill")
    assert details["status"] == (None, "prospect")


# --- other organizations ------------------------------------------------------------------


async def test_another_organizations_account_is_unknown_for_every_operation(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    await as_member(client, world.veridian, "victor")
    foreign = str(world.halden.accounts["acme"])
    missing = str(uuid.uuid4())
    version = "2026-10-01T00:00:00Z"
    for account in (foreign, missing):
        responses = [
            await client.get(f"/v1/admin/access/accounts/{account}"),
            await patch(
                client, f"accounts/{account}", {"version": version, "name": "X", "reason": "x"}
            ),
            await post(client, f"accounts/{account}/archive", {"version": version, "reason": "x"}),
            await post(
                client, f"accounts/{account}/reactivate", {"version": version, "reason": "x"}
            ),
        ]
        assert [r.status_code for r in responses] == [404] * 4
        bodies = [r.json()["error"]["code"] for r in responses]
        assert bodies == ["not_found"] * 4
    assert await events(owner_db, world.halden.id) == []


# --- the database's own refusals --------------------------------------------------------------


async def _as_app(
    app_db: Database, org: uuid.UUID, *statements: tuple[str, dict[str, Any]]
) -> None:
    async with app_db.tenant_transaction(org) as s:
        for sql, params in statements:
            await s.execute(text(sql), params)


async def test_the_api_role_cannot_bypass_the_account_fences(
    world: World, app_db: Database, owner_db: Database
) -> None:
    halden = world.halden
    org, acme = halden.id, halden.accounts["acme"]
    lena, maya = halden.members["lena"], halden.members["maya"]
    event = (
        "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
        "VALUES (:o, :actor, :action, CAST(:t AS jsonb), 'probe')"
    )
    # Columns without a grant: the lifecycle, the key, the organization; no DELETE.
    for sql in (
        "UPDATE accounts SET archived_at = now() WHERE id = :a",
        "UPDATE accounts SET key = 'other' WHERE id = :a",
        "UPDATE accounts SET organization_id = gen_random_uuid() WHERE id = :a",
        "DELETE FROM accounts WHERE id = :a",
        "INSERT INTO accounts (organization_id, key, name, archived_at) "
        "VALUES (:o, 'x', 'X', now())",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            await _as_app(app_db, org, (sql, {"a": acme, "o": org}))
    # A direct write without its event does not commit.
    for sql in (
        "UPDATE accounts SET name = 'Renamed' WHERE id = :a",
        "INSERT INTO accounts (organization_id, key, name) VALUES (:o, 'sneaky', 'Sneaky')",
    ):
        with pytest.raises(DBAPIError, match="without an audit event"):
            await _as_app(app_db, org, (sql, {"a": acme, "o": org}))
    # Nor with an event of another action or for another account.
    target = f'{{"account_id": "{acme}"}}'
    with pytest.raises(DBAPIError, match="without an audit event"):
        await _as_app(
            app_db,
            org,
            (
                event,
                {"o": org, "actor": f"member:{lena}", "action": "account.archived", "t": target},
            ),
            ("UPDATE accounts SET name = 'Renamed' WHERE id = :a", {"a": acme}),
        )
    # The lifecycle only through the fenced function, only for an active administrator.
    for actor in (maya, uuid.uuid4(), world.veridian.members["victor"]):
        with pytest.raises(DBAPIError, match="not_administrator"):
            await _as_app(
                app_db,
                org,
                (
                    "SELECT lattice_set_account_archived(:actor, :a, true)",
                    {"actor": actor, "a": acme},
                ),
            )
    # The function's write needs its event too.
    with pytest.raises(DBAPIError, match="without an audit event"):
        await _as_app(
            app_db,
            org,
            ("SELECT lattice_set_account_archived(:actor, :a, true)", {"actor": lena, "a": acme}),
        )
    # An archived account is read-only to the API role even with an event.
    await _as_app(
        app_db,
        org,
        ("SELECT lattice_set_account_archived(:actor, :a, true)", {"actor": lena, "a": acme}),
        (event, {"o": org, "actor": f"member:{lena}", "action": "account.archived", "t": target}),
    )
    async with app_db.tenant_transaction(org) as s:
        await s.execute(
            text(event),
            {"o": org, "actor": f"member:{lena}", "action": "account.updated", "t": target},
        )
        updated = await s.execute(
            text("UPDATE accounts SET name = 'Renamed' WHERE id = :a"), {"a": acme}
        )
        assert getattr(updated, "rowcount", None) == 0
    # Another organization's account is invisible to the bound organization.
    veridian_acme = world.veridian.accounts["acme"]
    async with app_db.tenant_transaction(org) as s:
        seen = await s.execute(
            text("SELECT count(*) FROM accounts WHERE id = :v"), {"v": veridian_acme}
        )
        assert seen.scalar_one() == 0
    names = await owner_rows(owner_db, org, "SELECT name FROM accounts ORDER BY key")
    assert [n[0] for n in names] == ["Acme Corp", "Kestrel Bank"]


async def test_checks_hold_for_every_writer(world: World, owner_db: Database) -> None:
    for sql in (
        "INSERT INTO accounts (organization_id, key, name) VALUES (:o, 'Bad Key', 'X')",
        "INSERT INTO accounts (organization_id, key, name) VALUES (:o, 'ok', ' padded ')",
        "INSERT INTO accounts (organization_id, key, name, aliases) "
        "VALUES (:o, 'ok', 'X', ARRAY['a', 'A'])",
        "INSERT INTO accounts (organization_id, key, name, aliases) "
        "VALUES (:o, 'ok', 'X', ARRAY[' a'])",
    ):
        with pytest.raises(DBAPIError, match="check constraint"):
            async with owner_db.tenant_transaction(world.halden.id) as s:
                await s.execute(text(sql), {"o": world.halden.id})


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_members_cannot_list_or_create_accounts(
    client: httpx.AsyncClient, world: World, method: str
) -> None:
    await as_member(client, world.halden, "maya")
    response = await client.request(
        method, "/v1/admin/access/accounts", json=_new() if method == "POST" else None, headers=CSRF
    )
    assert response.status_code == 403
