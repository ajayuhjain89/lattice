"""External-reference mappings operated in the product (Phase 12, P12.3; phase-12-plan
C10; ADR-017; migration 0018).

Proves: a reference maps to at most one account and an account takes many; map, remap
and unmap are explicit operations, each previewed as Now → After, given a reason,
checked against the mapping the administrator reviewed (409) and audited; an archived
account receives no new mapping until reactivated; the kind must fit the source's
connector and a ticketing reference is canonical; another organization's sources and
accounts answer exactly as random IDs; concurrent requests cannot map one reference
twice; and the database refuses what the code refuses, for any writer."""

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice.db.engine import Database

from .conftest import World, as_member, events, owner_rows, post, readable


def _ref(world: World, **extra: Any) -> dict[str, Any]:
    return {
        "source_id": str(world.halden_sources["support"]),
        "ref_kind": "ticketing_organization",
        "external_ref": "360009913",
        **extra,
    }


def _channel(world: World, **extra: Any) -> dict[str, Any]:
    return {
        "source_id": str(world.halden_sources["slack"]),
        "ref_kind": "chat_channel",
        "external_ref": "C0GENERAL01",
        **extra,
    }


async def _preview(client: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
    return await post(client, "account-references/preview", body)


async def _map(client: httpx.AsyncClient, body: dict[str, Any], reason: str = "Signed") -> Any:
    return await post(client, "account-references", {**body, "reason": reason})


async def _mappings(owner: Database, org: uuid.UUID) -> list[tuple[str, str, uuid.UUID | None]]:
    rows = await owner_rows(
        owner,
        org,
        "SELECT ref_kind, external_ref, account_id FROM account_external_refs "
        "ORDER BY ref_kind, external_ref",
    )
    return [tuple(r) for r in rows]


async def test_map_remap_unmap_are_previewed_explicit_and_audited(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    halden = world.halden
    acme, kestrel = str(halden.accounts["acme"]), str(halden.accounts["kestrel"])
    await as_member(client, halden, "lena")

    preview = await _preview(client, _ref(world, account_id=acme))
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["operation"], body["effect"], body["takes_effect"]) == (
        "map",
        "indexed",
        "next_sync",
    )
    assert body["now"] is None
    assert (body["after"]["key"], body["account_scoped"], body["records_waiting"]) == (
        "acme",
        True,
        0,
    )
    assert body["source"] == {
        "id": str(world.halden_sources["support"]),
        "name": "support",
        "connector_type": "ticketing",
    }
    assert await _mappings(owner_db, halden.id) == []  # a preview changes nothing

    mapped = await _map(client, _ref(world, account_id=acme))
    assert mapped.status_code == 201, mapped.text
    assert mapped.json()["account"]["key"] == "acme"
    assert await _mappings(owner_db, halden.id) == [
        ("ticketing_organization", "360009913", halden.accounts["acme"])
    ]
    # Mapping again is not an overwrite: it is refused, and remapping says what moves.
    again = await _map(client, _ref(world, account_id=kestrel))
    assert again.status_code == 409
    assert again.json()["error"]["details"] == [{"field": "mapping", "issue": "already_mapped"}]
    remap_preview = (await _preview(client, _ref(world, account_id=kestrel))).json()
    assert (remap_preview["operation"], remap_preview["effect"]) == ("remap", "moved")
    assert (remap_preview["now"]["key"], remap_preview["after"]["key"]) == ("acme", "kestrel")
    stale = await post(
        client,
        "account-references/remap",
        _ref(world, current_account_id=kestrel, account_id=acme, reason="x"),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"] == [{"field": "mapping", "issue": "mapping_changed"}]
    remapped = await post(
        client,
        "account-references/remap",
        _ref(world, current_account_id=acme, account_id=kestrel, reason="Bought by Kestrel"),
    )
    assert remapped.status_code == 200, remapped.text
    unmap_preview = (await _preview(client, _ref(world, account_id=None))).json()
    assert (unmap_preview["operation"], unmap_preview["effect"]) == ("unmap", "quarantined")
    unmapped = await post(
        client,
        "account-references/unmap",
        _ref(world, current_account_id=kestrel, reason="Wrong customer"),
    )
    assert unmapped.status_code == 200, unmapped.text
    assert unmapped.json()["account"] is None
    # The reference stays known, unresolved: its records fail closed at the next sync.
    assert await _mappings(owner_db, halden.id) == [("ticketing_organization", "360009913", None)]
    nothing = await _preview(client, _ref(world, account_id=None))
    assert nothing.status_code == 422

    target = {
        "source_id": str(world.halden_sources["support"]),
        "ref_kind": "ticketing_organization",
        "external_ref": "360009913",
    }
    assert [
        (e.action, e.target, e.before, e.after, e.reason) for e in await events(owner_db, halden.id)
    ] == [
        ("account.mapping_changed", target, None, {"account_id": acme}, "Signed"),
        (
            "account.mapping_changed",
            target,
            {"account_id": acme},
            {"account_id": kestrel},
            "Bought by Kestrel",
        ),
        ("account.mapping_changed", target, {"account_id": kestrel}, None, "Wrong customer"),
    ]
    # The audit view names the source, the reference and the accounts.
    latest = (await client.get("/v1/admin/audit-events")).json()["items"][0]
    assert {d["field"]: (d["before"], d["after"]) for d in latest["details"]} == {
        "source": (None, "support"),
        "reference": (None, "Ticketing organization 360009913"),
        "account": ("Kestrel Bank", "Not mapped"),
    }


async def test_unmapping_never_unscopes_even_on_a_source_that_is_not_account_scoped(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    halden = world.halden
    acme, kestrel = str(halden.accounts["acme"]), str(halden.accounts["kestrel"])
    await as_member(client, halden, "lena")
    first = (await _preview(client, _channel(world, account_id=acme))).json()
    assert (first["effect"], first["account_scoped"]) == ("scoped", False)
    assert (await _map(client, _channel(world, account_id=acme))).status_code == 201
    unmap = (await _preview(client, _channel(world, account_id=None))).json()
    # A missing mapping never widens access: the records are quarantined, not unscoped.
    assert (unmap["operation"], unmap["effect"], unmap["account_scoped"]) == (
        "unmap",
        "quarantined",
        False,
    )
    done = await post(
        client, "account-references/unmap", _channel(world, current_account_id=acme, reason="x")
    )
    assert done.status_code == 200, done.text
    assert await _mappings(owner_db, halden.id) == [("chat_channel", "C0GENERAL01", None)]
    # Mapping the unresolved reference again restores (indexes) its records: no longer a
    # channel that was never mapped, so not "scoped".
    again = (await _preview(client, _channel(world, account_id=kestrel))).json()
    assert (again["operation"], again["effect"], again["now"]) == ("map", "indexed", None)
    restored = await _map(client, _channel(world, account_id=kestrel))
    assert restored.status_code == 201, restored.text
    assert await _mappings(owner_db, halden.id) == [
        ("chat_channel", "C0GENERAL01", halden.accounts["kestrel"])
    ]
    assert [(e.before, e.after) for e in await events(owner_db, halden.id)] == [
        (None, {"account_id": acme}),
        ({"account_id": acme}, None),
        (None, {"account_id": kestrel}),
    ]


async def test_one_account_takes_many_references_and_a_reference_one_account(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    acme = world.halden.accounts["acme"]
    await as_member(client, world.halden, "lena")
    for body in (
        _ref(world, account_id=str(acme)),
        _ref(world, external_ref="360001001", account_id=str(acme)),
        _channel(world, account_id=str(acme)),
    ):
        assert (await _map(client, body)).status_code == 201
    detail = (await client.get(f"/v1/admin/access/accounts/{acme}")).json()
    assert detail["references"] == 3
    assert [
        (m["source"]["name"], m["ref_kind"], m["external_ref"]) for m in detail["mappings"]
    ] == [
        ("slack", "chat_channel", "C0GENERAL01"),
        ("support", "ticketing_organization", "360001001"),
        ("support", "ticketing_organization", "360009913"),
    ]
    # The same reference value in another source is another reference.
    with pytest.raises(DBAPIError, match="uq_account_external_refs_reference"):
        async with owner_db.tenant_transaction(world.halden.id) as s:
            await s.execute(
                text(
                    "INSERT INTO account_external_refs (organization_id, source_id, ref_kind, "
                    "external_ref, account_id, created_by) VALUES (:o, :s, "
                    "'ticketing_organization', '360009913', :a, 'operator:test')"
                ),
                {
                    "o": world.halden.id,
                    "s": world.halden_sources["support"],
                    "a": world.halden.accounts["kestrel"],
                },
            )


async def test_archived_accounts_take_no_new_mapping_until_reactivated(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    halden = world.halden
    acme = str(halden.accounts["acme"])
    await as_member(client, halden, "lena")
    assert (await _map(client, _channel(world, account_id=acme))).status_code == 201
    detail = (await client.get(f"/v1/admin/access/accounts/{acme}")).json()
    archived = await post(
        client, f"accounts/{acme}/archive", {"version": detail["version"], "reason": "Churned"}
    )
    assert archived.status_code == 200
    # The existing mapping stays (it keeps resolving at sync).
    assert len(await _mappings(owner_db, halden.id)) == 1
    for response in (
        await _preview(client, _ref(world, account_id=acme)),
        await _map(client, _ref(world, account_id=acme)),
    ):
        assert response.status_code == 422
        assert response.json()["error"]["details"] == [
            {"field": "account_id", "issue": "account_archived"}
        ]
    # Nor as the target of a remap.
    kestrel = str(halden.accounts["kestrel"])
    assert (await _map(client, _ref(world, account_id=kestrel))).status_code == 201
    moved = await post(
        client,
        "account-references/remap",
        _ref(world, current_account_id=kestrel, account_id=acme, reason="x"),
    )
    assert moved.status_code == 422
    # Unmapping from an archived account is allowed (it narrows nothing new).
    away = await post(
        client,
        "account-references/unmap",
        _channel(world, current_account_id=acme, reason="Channel archived too"),
    )
    assert away.status_code == 200
    reactivated = await post(
        client,
        f"accounts/{acme}/reactivate",
        {"version": archived.json()["version"], "reason": "Renewed"},
    )
    assert reactivated.status_code == 200
    assert (await _map(client, _channel(world, account_id=acme))).status_code == 201


@pytest.mark.parametrize(
    ("change", "field", "issue"),
    [
        ({"ref_kind": "chat_channel"}, "ref_kind", "ref_kind_mismatch"),
        ({"external_ref": "0360009913"}, "external_ref", "reference_invalid"),
        ({"external_ref": "acme"}, "external_ref", "reference_invalid"),
        ({"external_ref": "1" * 20}, "external_ref", "reference_invalid"),
    ],
)
async def test_references_must_fit_the_source_and_be_canonical(
    client: httpx.AsyncClient, world: World, change: dict[str, str], field: str, issue: str
) -> None:
    await as_member(client, world.halden, "lena")
    body = _ref(world, account_id=str(world.halden.accounts["acme"]), **change)
    for response in (await _preview(client, body), await _map(client, body)):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"] == [{"field": field, "issue": issue}]


async def test_documents_need_no_mapping(client: httpx.AsyncClient, world: World) -> None:
    """A Markdown export names account keys; its source takes no reference."""
    await as_member(client, world.halden, "lena")
    body = _ref(
        world, source_id=str(world.halden.source_id), account_id=str(world.halden.accounts["acme"])
    )
    response = await _map(client, body)
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": "ref_kind", "issue": "ref_kind_mismatch"}
    ]


async def test_references_are_matched_exactly_as_sent(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    """Trimmed and NFC-normalised as connectors report them; case is preserved."""
    await as_member(client, world.halden, "lena")
    acme = str(world.halden.accounts["acme"])
    assert (
        await _map(client, _channel(world, external_ref="  C0Mixed1 ", account_id=acme))
    ).status_code == 201
    assert (
        await _map(client, _channel(world, external_ref="c0mixed1", account_id=acme))
    ).status_code == 201
    refs = {r[1] for r in await _mappings(owner_db, world.halden.id)}
    assert refs == {"C0Mixed1", "c0mixed1"}


async def test_another_organizations_sources_and_accounts_are_unknown(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    await as_member(client, world.veridian, "victor")
    own_source = str(world.veridian_sources["support"])
    own_account = str(world.veridian.accounts["acme"])
    halden_source = str(world.halden_sources["support"])
    halden_account = str(world.halden.accounts["acme"])

    def body(source: str, account: str) -> dict[str, Any]:
        return {
            "source_id": source,
            "ref_kind": "ticketing_organization",
            "external_ref": "360009913",
            "account_id": account,
        }

    for foreign, random in (
        (body(halden_source, own_account), body(str(uuid.uuid4()), own_account)),
        (body(own_source, halden_account), body(own_source, str(uuid.uuid4()))),
    ):
        for path, extra in (
            ("account-references/preview", {}),
            ("account-references", {"reason": "x"}),
        ):
            a = await post(client, path, {**foreign, **extra})
            b = await post(client, path, {**random, **extra})
            assert a.status_code == b.status_code == 422
            left, right = a.json(), b.json()
            left["error"].pop("request_id", None)
            right["error"].pop("request_id", None)
            assert left == right
    assert await _mappings(owner_db, world.halden.id) == []
    assert await _mappings(owner_db, world.veridian.id) == []
    assert await events(owner_db, world.halden.id) == []


async def test_concurrent_mappings_of_one_reference_map_it_once(
    app_client: Any, world: World, owner_db: Database
) -> None:
    accounts = [str(world.halden.accounts["acme"]), str(world.halden.accounts["kestrel"])]

    async def attempt(account: str) -> int:
        async with app_client() as c:
            await as_member(c, world.halden, "lena")
            response: httpx.Response = await _map(c, _ref(world, account_id=account))
            return response.status_code

    results = sorted(await asyncio.gather(*(attempt(a) for a in accounts)))
    assert results == [201, 409]
    assert len(await _mappings(owner_db, world.halden.id)) == 1
    assert len(await events(owner_db, world.halden.id)) == 1


async def test_mapping_gives_the_administrator_no_content_access(
    client: httpx.AsyncClient, world: World, app_db: Database
) -> None:
    halden = world.halden
    lena = halden.members["lena"]
    before = await readable(app_db, halden.id, lena)
    await as_member(client, halden, "lena")
    for body in (
        _ref(world, account_id=str(halden.accounts["acme"])),
        _channel(world, account_id=str(halden.accounts["kestrel"])),
    ):
        assert (await _map(client, body)).status_code == 201
    assert await readable(app_db, halden.id, lena) == before


# --- the database's own refusals ----------------------------------------------------------------


async def test_the_api_role_writes_mappings_only_through_the_fenced_function(
    world: World, app_db: Database, owner_db: Database
) -> None:
    halden = world.halden
    org, support = halden.id, world.halden_sources["support"]
    acme, lena, maya = halden.accounts["acme"], halden.members["lena"], halden.members["maya"]
    params = {"o": org, "s": support, "a": acme}
    for sql in (
        "INSERT INTO account_external_refs (organization_id, source_id, ref_kind, external_ref, "
        "account_id, created_by) VALUES (:o, :s, 'ticketing_organization', '1', :a, 'member:x')",
        "UPDATE account_external_refs SET account_id = :a",
        "DELETE FROM account_external_refs",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_db.tenant_transaction(org) as s:
                await s.execute(text(sql), params)
    call = "SELECT lattice_set_account_ref(:actor, :s, 'ticketing_organization', '42', NULL, :a)"
    for actor in (maya, uuid.uuid4(), world.veridian.members["victor"]):
        with pytest.raises(DBAPIError, match="not_administrator"):
            async with app_db.tenant_transaction(org) as s:
                await s.execute(text(call), {**params, "actor": actor})
    # Without its event, or with an event for another reference, it does not commit.
    event = (
        "INSERT INTO audit_events (organization_id, actor, action, target, reason) VALUES "
        "(:o, :actor, 'account.mapping_changed', CAST(:t AS jsonb), 'probe')"
    )
    other = (
        f'{{"source_id": "{support}", "ref_kind": "ticketing_organization", "external_ref": "43"}}'
    )
    for extra in ((), ((event, {"o": org, "actor": f"member:{lena}", "t": other}),)):
        with pytest.raises(DBAPIError, match="without an audit event"):
            async with app_db.tenant_transaction(org) as s:
                await s.execute(text(call), {**params, "actor": lena})
                for sql, values in extra:
                    await s.execute(text(sql), values)
    # Another organization's account or source, through the function: refused.
    veridian_acme = world.veridian.accounts["acme"]
    with pytest.raises(DBAPIError, match="account_not_found"):
        async with app_db.tenant_transaction(org) as s:
            await s.execute(text(call), {**params, "actor": lena, "a": veridian_acme})
    with pytest.raises(DBAPIError, match="source_not_found"):
        async with app_db.tenant_transaction(org) as s:
            await s.execute(
                text(call), {**params, "actor": lena, "s": world.veridian_sources["support"]}
            )
    assert await _mappings(owner_db, org) == []


async def test_the_check_trigger_binds_every_writer(world: World, owner_db: Database) -> None:
    """Operators (``lattice sync``) write directly as the owner: the same rules hold."""
    halden = world.halden
    org = halden.id
    insert = (
        "INSERT INTO account_external_refs (organization_id, source_id, ref_kind, external_ref, "
        "account_id, created_by) VALUES (:o, :s, :k, :r, :a, 'operator:test')"
    )

    async def write(sql: str, **params: Any) -> None:
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(text(sql), params)

    base = {"o": org, "s": world.halden_sources["support"], "k": "ticketing_organization", "r": "7"}
    with pytest.raises(DBAPIError, match="ref_kind_mismatch"):
        await write(insert, **{**base, "k": "chat_channel", "a": halden.accounts["acme"]})
    with pytest.raises(DBAPIError, match="ref_kind_mismatch"):
        await write(insert, **{**base, "s": halden.source_id, "a": halden.accounts["acme"]})
    # Another organization's account: invisible to the trigger, refused by the foreign key.
    with pytest.raises(DBAPIError, match=r"account_not_found|foreign key"):
        await write(insert, **{**base, "a": world.veridian.accounts["acme"]})
    with pytest.raises(DBAPIError, match="check constraint"):
        await write(insert, **{**base, "r": " 7", "a": halden.accounts["acme"]})
    await write(
        "UPDATE accounts SET archived_at = now() WHERE id = :a", a=halden.accounts["kestrel"]
    )
    with pytest.raises(DBAPIError, match="account_archived"):
        await write(insert, **{**base, "a": halden.accounts["kestrel"]})
    # A reference starts mapped: only an unmap makes one unresolved (0019).
    with pytest.raises(DBAPIError, match="account_not_found"):
        await write(insert, **{**base, "a": None})
    await write(insert, **{**base, "a": halden.accounts["acme"]})
    with pytest.raises(DBAPIError, match="account_archived"):
        await write(
            "UPDATE account_external_refs SET account_id = :a", a=halden.accounts["kestrel"]
        )
    with pytest.raises(DBAPIError, match="reference_immutable"):
        await write("UPDATE account_external_refs SET external_ref = '8'")
    # An account with mappings cannot be deleted (documents would lose their account).
    with pytest.raises(DBAPIError, match="foreign key"):
        await write("DELETE FROM accounts WHERE id = :a", a=halden.accounts["acme"])
    # Unresolved, then mapped again (never to an archived account).
    await write("UPDATE account_external_refs SET account_id = NULL")
    with pytest.raises(DBAPIError, match="account_archived"):
        await write(
            "UPDATE account_external_refs SET account_id = :a", a=halden.accounts["kestrel"]
        )
    await write("UPDATE account_external_refs SET account_id = :a", a=halden.accounts["acme"])
    # The existing mapping keeps working when its account is archived later.
    await write("UPDATE accounts SET archived_at = now() WHERE id = :a", a=halden.accounts["acme"])
    assert len(await _mappings(owner_db, org)) == 1
