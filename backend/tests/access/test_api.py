"""Access management over HTTP (Phase 9): the contract and the behaviour of each
endpoint. Security properties (403/404 equality, nothing about unreadable documents,
counts only over readable ones) are in ``tests/security/test_access_management.py``."""

import uuid
from typing import Any

import httpx
from sqlalchemy import text

from lattice.db.engine import Database

from ..conftest import CSRF
from ..retrieval.world import Org
from .conftest import add_to_group, sign_in


async def _member(client: httpx.AsyncClient, org: Org, name: str) -> dict[str, Any]:
    response = await client.get(f"/v1/admin/access/members/{org.members[name]}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _events(owner: Database, org: Org) -> list[dict[str, Any]]:
    async with owner.tenant_transaction(org.id) as s:
        rows = await s.execute(
            text(
                "SELECT actor, action, target, before, after, reason FROM audit_events "
                "ORDER BY created_at"
            )
        )
        return [dict(r._mapping) for r in rows]


async def _explain(client: httpx.AsyncClient, org: Org, member: str, doc: str) -> httpx.Response:
    return await client.get(
        f"/v1/admin/access/members/{org.members[member]}/documents/{org.documents[doc]}"
    )


# --- the member's own accounts (Ask scope) ---------------------------------------------


async def test_accounts_are_the_ones_the_member_may_know(
    client: httpx.AsyncClient, org: Org
) -> None:
    await sign_in(client, org, "maya")
    assert (await client.get("/v1/accounts")).json() == {
        "account_scope": "assigned",
        "accounts": [{"key": "acme", "name": "Acme Corp"}],
    }
    await sign_in(client, org, "tom")
    assert (await client.get("/v1/accounts")).json() == {
        "account_scope": "all",
        "accounts": [
            {"key": "acme", "name": "Acme Corp"},
            {"key": "kestrel", "name": "Kestrel Bank"},
        ],
    }


async def test_accounts_follow_an_assignment_on_the_next_request(
    client: httpx.AsyncClient, org: Org
) -> None:
    await sign_in(client, org, "maya")
    await org.execute(
        "DELETE FROM account_assignments WHERE membership_id = :m", m=org.members["maya"]
    )
    assert (await client.get("/v1/accounts")).json()["accounts"] == []


# --- directory -------------------------------------------------------------------------


async def test_members_are_paged_by_name_and_filtered(client: httpx.AsyncClient, org: Org) -> None:
    await sign_in(client, org, "lena")
    seen: list[str] = []
    after: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 2, **({"after": after} if after else {})}
        page = (await client.get("/v1/admin/access/members", params=params)).json()
        seen += [m["name"] for m in page["items"]]
        after = page["next_after"]
        if after is None:
            break
    assert seen == ["grace", "lena", "maya", "olga", "tom"]
    found = (await client.get("/v1/admin/access/members", params={"q": "MAY"})).json()
    assert [m["email"] for m in found["items"]] == ["maya@halden.example"]
    maya = found["items"][0]
    assert maya["account_scope"] == "assigned"
    assert maya["assigned_accounts"] == 1
    assert [d["name"] for d in maya["departments"]] == ["Sales"]
    # LIKE wildcards are literal.
    assert (await client.get("/v1/admin/access/members", params={"q": "%"})).json()["items"] == []


async def test_directory_lists_what_can_be_assigned(client: httpx.AsyncClient, org: Org) -> None:
    await sign_in(client, org, "lena")
    body = (await client.get("/v1/admin/access/directory")).json()
    assert [a["name"] for a in body["accounts"]] == ["Acme Corp", "Kestrel Bank"]
    assert [d["key"] for d in body["departments"]] == ["it", "leadership", "sales"]
    # Administrator-defined groups only: the synced channel is not assignable.
    assert body["groups"] == [
        {"id": str(org.groups["exec"]), "key": "exec", "name": "exec", "members": 1}
    ]


# --- a member's access -----------------------------------------------------------------


async def test_member_access_describes_scope_groups_and_grants(
    client: httpx.AsyncClient, org: Org
) -> None:
    await sign_in(client, org, "lena")
    maya = await _member(client, org, "maya")
    assert maya["member"]["name"] == "maya"
    assert maya["is_self"] is False
    assert [a["name"] for a in maya["accounts"]] == ["Acme Corp"]
    assert [d["name"] for d in maya["departments"]] == ["Sales"]
    [channel] = maya["groups"]
    # Lena is not in the private channel: no name, an opaque reference, its members.
    assert channel["origin"] == "source"
    assert channel["name"] is None
    assert channel["reference"].startswith("rec_")
    assert channel["connector_type"] == "document_repository"
    assert [m["name"] for m in channel["members"]] == ["maya"]
    assert channel["editable"] is False
    assert maya["grants"] == []
    assert maya["has_other_grants"] is False
    assert (await _member(client, org, "lena"))["is_self"] is True


async def test_a_synced_group_is_named_for_an_administrator_in_it(
    client: httpx.AsyncClient, org: Org
) -> None:
    await add_to_group(org, "acct-acme-private", "lena")
    await sign_in(client, org, "lena")
    [channel] = (await _member(client, org, "maya"))["groups"]
    assert channel["name"].startswith("#acct-acme-private")
    assert channel["reference"] is None


async def test_unknown_member_is_not_found(client: httpx.AsyncClient, org: Org) -> None:
    await sign_in(client, org, "lena")
    response = await client.get(f"/v1/admin/access/members/{uuid.uuid4()}")
    assert response.status_code == 404


# --- documents and explanations --------------------------------------------------------


async def test_document_search_and_explanation(client: httpx.AsyncClient, org: Org) -> None:
    await sign_in(client, org, "lena")
    found = (await client.get("/v1/admin/access/documents", params={"q": "kb-"})).json()
    assert [d["title"] for d in found["items"]] == ["Kestrel renewal notes"]
    assert found["items"][0]["classification"] == "internal"
    denied = (await _explain(client, org, "maya", "kestrel-notes")).json()
    assert denied["readable"] is False
    assert denied["path"] == "none"
    assert "account_not_in_scope" in denied["reasons"]
    allowed = (await _explain(client, org, "maya", "acme-plan")).json()
    assert allowed["readable"] is True
    assert allowed["path"] == "policy"
    assert allowed["reasons"] == [
        "clearance_covers_sensitivity",
        "account_assigned",
        "no_department_restriction",
    ]


# --- previews and changes --------------------------------------------------------------


async def test_preview_describes_rules_and_counts_readable_documents(
    client: httpx.AsyncClient, org: Org
) -> None:
    await sign_in(client, org, "lena")
    url = f"/v1/admin/access/members/{org.members['maya']}/preview"
    body = {"accounts": [str(org.accounts["kestrel"])]}
    preview = (await client.post(url, json=body, headers=CSRF)).json()
    assert preview["effects"] == [
        {
            "kind": "account_added",
            "before": None,
            "after": None,
            "subject": {"id": str(org.accounts["kestrel"]), "name": "Kestrel Bank"},
        },
        {
            "kind": "account_removed",
            "before": None,
            "after": None,
            "subject": {"id": str(org.accounts["acme"]), "name": "Acme Corp"},
        },
    ]
    assert (preview["documents_gained"], preview["documents_lost"]) == (1, 1)
    suspend = (await client.post(url, json={"status": "suspended"}, headers=CSRF)).json()
    assert suspend["effects"] == [
        {"kind": "suspended", "before": None, "after": None, "subject": None}
    ]
    # Of what Lena can read, Maya reads the Acme plan and the handbook.
    assert (suspend["documents_gained"], suspend["documents_lost"]) == (0, 2)


async def test_a_change_is_applied_audited_and_versioned(
    client: httpx.AsyncClient, org: Org, owner_db: Database
) -> None:
    await sign_in(client, org, "lena")
    before = await _member(client, org, "maya")
    url = f"/v1/admin/access/members/{org.members['maya']}"
    change = {
        "version": before["version"],
        "clearance": "confidential",
        "accounts": [str(org.accounts["acme"]), str(org.accounts["kestrel"])],
        "groups": [str(org.groups["exec"])],
        "reason": "  Covering   Kestrel during the renewal ",
    }
    response = await client.patch(url, json=change, headers=CSRF)
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["version"] != before["version"]
    assert after["member"]["clearance"] == "confidential"
    assert [a["name"] for a in after["accounts"]] == ["Acme Corp", "Kestrel Bank"]
    # Administrator group added; the synced channel kept as it was.
    assert sorted(g["origin"] for g in after["groups"]) == ["admin", "source"]
    [event] = await _events(owner_db, org)
    assert event["actor"] == f"member:{org.members['lena']}"
    assert event["action"] == "access.member_changed"
    assert event["target"] == {"membership_id": str(org.members["maya"])}
    assert event["before"]["clearance"] == "internal"
    assert event["after"]["clearance"] == "confidential"
    assert event["after"]["groups"] == [str(org.groups["exec"])]
    assert event["reason"] == "Covering Kestrel during the renewal"
    # A second change from the stale view is refused, and nothing is written.
    stale = await client.patch(url, json={**change, "clearance": "internal"}, headers=CSRF)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"
    assert len(await _events(owner_db, org)) == 1


async def test_change_refusals(client: httpx.AsyncClient, org: Org, owner_db: Database) -> None:
    await sign_in(client, org, "lena")
    maya = await _member(client, org, "maya")

    async def patch(name: str, **fields: Any) -> httpx.Response:
        version = (await _member(client, org, name))["version"]
        body = {"version": version, "reason": "Because", **fields}
        return await client.patch(
            f"/v1/admin/access/members/{org.members[name]}", json=body, headers=CSRF
        )

    def issue(response: httpx.Response) -> str:
        assert response.status_code == 422, response.text
        detail: str = response.json()["error"]["details"][0]["issue"]
        return detail

    assert issue(await patch("maya", clearance="internal")) == "no_change"
    assert issue(await patch("maya", accounts=[str(uuid.uuid4())])) == "unknown"
    assert (
        issue(await patch("maya", groups=[str(org.groups["acct-acme-private"])])) == "source_group"
    )
    own = await patch("lena", clearance="confidential")
    assert own.status_code == 403
    assert own.json()["error"]["details"] == [{"field": "membership", "issue": "own_access"}]
    blank = await client.patch(
        f"/v1/admin/access/members/{org.members['maya']}",
        json={"version": maya["version"], "clearance": "confidential", "reason": "   "},
        headers=CSRF,
    )
    assert blank.status_code == 422
    removed = await client.patch(
        f"/v1/admin/access/members/{org.members['maya']}",
        json={"version": maya["version"], "status": "removed", "reason": "Left"},
        headers=CSRF,
    )
    assert removed.status_code == 422  # not a settable status
    await org.execute(
        "UPDATE memberships SET status = 'removed' WHERE id = :m", m=org.members["tom"]
    )
    assert issue(await patch("tom", clearance="confidential")) == "member_removed"
    assert await _events(owner_db, org) == []


async def test_suspension_takes_effect_on_the_next_request(app_client: Any, org: Org) -> None:
    async with app_client() as admin, app_client() as member:
        await sign_in(member, org, "maya")
        assert (await member.get("/v1/accounts")).status_code == 200
        await sign_in(admin, org, "lena")
        version = (await _member(admin, org, "maya"))["version"]
        response = await admin.patch(
            f"/v1/admin/access/members/{org.members['maya']}",
            json={"version": version, "status": "suspended", "reason": "Left the account team"},
            headers=CSRF,
        )
        assert response.status_code == 200
        assert response.json()["member"]["status"] == "suspended"
        assert (await member.get("/v1/accounts")).status_code == 401
        version = response.json()["version"]
        back = await admin.patch(
            f"/v1/admin/access/members/{org.members['maya']}",
            json={"version": version, "status": "active", "reason": "Returned"},
            headers=CSRF,
        )
        assert back.json()["member"]["status"] == "active"


# --- grants ----------------------------------------------------------------------------


async def test_grant_and_revoke_a_readable_document(
    client: httpx.AsyncClient, org: Org, owner_db: Database
) -> None:
    await sign_in(client, org, "lena")
    url = f"/v1/admin/access/members/{org.members['maya']}/grants"
    body = {"document_id": str(org.documents["kestrel-notes"]), "reason": "Renewal cover"}
    created = await client.post(url, json=body, headers=CSRF)
    assert created.status_code == 201, created.text
    grant = created.json()
    assert grant["document"]["title"] == "Kestrel renewal notes"
    assert grant["origin"] == "admin"
    assert grant["granted_by"] == "lena"
    assert grant["reason"] == "Renewal cover"
    assert grant["revocable"] is True
    again = await client.post(url, json=body, headers=CSRF)
    assert again.status_code == 200
    assert again.json()["id"] == grant["id"]
    explained = (await _explain(client, org, "maya", "kestrel-notes")).json()
    assert (explained["readable"], explained["path"]) == (True, "grant")
    assert explained["via"] == [{"principal_type": "membership", "name": "maya", "reference": None}]
    assert [g["id"] for g in (await _member(client, org, "maya"))["grants"]] == [grant["id"]]

    revoked = await client.post(
        f"/v1/admin/access/grants/{grant['id']}/revoke",
        json={"reason": "Renewal closed"},
        headers=CSRF,
    )
    assert revoked.status_code == 204
    assert (await _explain(client, org, "maya", "kestrel-notes")).json()["readable"] is False
    events = await _events(owner_db, org)
    assert [e["action"] for e in events] == ["access.grant_created", "access.grant_revoked"]
    assert events[0]["target"]["document_id"] == str(org.documents["kestrel-notes"])
    assert events[0]["target"]["reference"].startswith("rec_")
    assert events[1]["reason"] == "Renewal closed"
    gone = await client.post(
        f"/v1/admin/access/grants/{grant['id']}/revoke", json={"reason": "x"}, headers=CSRF
    )
    assert gone.status_code == 404


async def test_source_grants_are_shown_but_not_revocable(
    client: httpx.AsyncClient, org: Org
) -> None:
    await org.execute(
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "membership_id, origin) VALUES (:o, :d, 'membership', :m, 'source')",
        o=org.id,
        d=org.documents["kestrel-notes"],
        m=org.members["maya"],
    )
    await sign_in(client, org, "lena")
    [grant] = (await _member(client, org, "maya"))["grants"]
    assert (grant["origin"], grant["revocable"], grant["granted_by"]) == ("source", False, None)
    refused = await client.post(
        f"/v1/admin/access/grants/{grant['id']}/revoke", json={"reason": "x"}, headers=CSRF
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["details"] == [{"field": "grant", "issue": "source_grant"}]


async def test_nobody_grants_themselves(client: httpx.AsyncClient, org: Org) -> None:
    await sign_in(client, org, "lena")
    response = await client.post(
        f"/v1/admin/access/members/{org.members['lena']}/grants",
        json={"document_id": str(org.documents["kestrel-notes"]), "reason": "Mine"},
        headers=CSRF,
    )
    assert response.status_code == 403


# --- the audit view --------------------------------------------------------------------


async def test_audit_events_are_listed_newest_first_with_effects(
    client: httpx.AsyncClient, org: Org
) -> None:
    await org.execute(
        "INSERT INTO audit_events (organization_id, actor, action, target, before, after, "
        "reason) VALUES (:o, 'operator:cli', 'provider.enabled', "
        "CAST(:t AS jsonb), CAST(:b AS jsonb), CAST(:a AS jsonb), 'Pilot')",
        o=org.id,
        t='{"purpose": "generation", "provider": "fake"}',
        b='{"enabled": false}',
        a='{"enabled": true}',
    )
    await sign_in(client, org, "lena")
    version = (await _member(client, org, "maya"))["version"]
    await client.patch(
        f"/v1/admin/access/members/{org.members['maya']}",
        json={"version": version, "departments": [], "reason": "Moved team"},
        headers=CSRF,
    )
    page = (await client.get("/v1/admin/audit-events", params={"limit": 1})).json()
    [latest] = page["items"]
    assert latest["action"] == "access.member_changed"
    assert latest["actor"] == {"kind": "member", "name": "lena", "operator": None}
    assert latest["member"] == {"id": str(org.members["maya"]), "name": "maya"}
    assert latest["effects"] == [
        {
            "kind": "department_removed",
            "before": None,
            "after": None,
            "subject": {"id": str(org.departments["sales"]), "name": "Sales"},
        }
    ]
    older = (
        await client.get("/v1/admin/audit-events", params={"before": page["next_before"]})
    ).json()
    [provider] = older["items"]
    assert older["next_before"] is None
    assert provider["actor"] == {"kind": "operator", "name": None, "operator": "cli"}
    assert {(d["field"], d["before"], d["after"]) for d in provider["details"]} == {
        ("provider", None, "fake"),
        ("purpose", None, "generation"),
        ("enabled", "False", "True"),
    }
