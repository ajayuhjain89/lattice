"""Access management must not become an oracle (security-model §10, SEC-5, SEC-14,
SEC-15; threat model T3, T20).

* Being an administrator grants no data access: every document named on the Access
  page, in an explanation, a grant, a search result or the audit view is one the
  viewing administrator can read *now*; nothing else is named or counted.
* Previews count only documents the administrator can read: a document they cannot
  read never changes a preview.
* Non-administrators get one 403 whether or not the target exists; another
  organization's members, grants and events do not exist (404, never listed).
* Every change is audited in its own transaction; a refused change writes nothing.
"""

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from lattice.db.engine import Database

from ..access.conftest import CANARY, add_to_group, build, sign_in
from ..conftest import CSRF, AppFactory
from ..retrieval.world import Org


@pytest.fixture
async def org(owner_db: Database) -> Org:
    return await build(owner_db)


@pytest.fixture
async def other(owner_db: Database) -> Org:
    return await build(owner_db, "veridian")


@pytest.fixture
async def client(app_client: AppFactory) -> Any:
    async with app_client() as c:
        yield c


def _error(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()["error"]
    return {k: v for k, v in body.items() if k != "request_id"}


def _requests(org: Org, member: uuid.UUID, document: uuid.UUID, grant: uuid.UUID) -> list[Any]:
    reason = {"reason": "Because"}
    return [
        ("GET", "/v1/admin/access/members", None),
        ("GET", "/v1/admin/access/directory", None),
        ("GET", f"/v1/admin/access/members/{member}", None),
        ("POST", f"/v1/admin/access/members/{member}/preview", {"clearance": "confidential"}),
        (
            "PATCH",
            f"/v1/admin/access/members/{member}",
            {"version": "2026-01-01T00:00:00Z", "clearance": "confidential", **reason},
        ),
        ("GET", "/v1/admin/access/documents?q=acme", None),
        ("GET", f"/v1/admin/access/members/{member}/documents/{document}", None),
        (
            "POST",
            f"/v1/admin/access/members/{member}/grants",
            {"document_id": str(document), **reason},
        ),
        ("POST", f"/v1/admin/access/grants/{grant}/revoke", reason),
        ("GET", "/v1/admin/audit-events", None),
    ]


async def _send(client: httpx.AsyncClient, method: str, url: str, body: Any) -> httpx.Response:
    return await client.request(method, url, json=body, headers=CSRF)


async def _grant_row(org: Org, document: str, member: str, origin: str = "admin") -> uuid.UUID:
    result = await org.execute(
        "INSERT INTO document_grants (organization_id, document_id, principal_type, "
        "membership_id, origin) VALUES (:o, :d, 'membership', :m, :origin) RETURNING id",
        o=org.id,
        d=org.documents[document],
        m=org.members[member],
        origin=origin,
    )
    grant_id: uuid.UUID = result.scalar_one()
    return grant_id


async def _event_count(owner: Database, org: Org) -> int:
    async with owner.tenant_transaction(org.id) as s:
        count: int = (await s.execute(text("SELECT count(*) FROM audit_events"))).scalar_one()
    return count


# --- capability --------------------------------------------------------------------------


async def test_non_administrators_get_one_answer_whether_or_not_the_target_exists(
    client: httpx.AsyncClient, org: Org, owner_db: Database
) -> None:
    grant = await _grant_row(org, "kestrel-notes", "maya")
    await sign_in(client, org, "tom")
    real = _requests(org, org.members["maya"], org.documents["kestrel-notes"], grant)
    fake = _requests(org, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    for (method, url, body), (_, other_url, other_body) in zip(real, fake, strict=True):
        a = await _send(client, method, url, body)
        b = await _send(client, method, other_url, other_body)
        assert a.status_code == b.status_code == 403, url
        assert _error(a) == _error(b)
    assert await _event_count(owner_db, org) == 0


async def test_losing_the_capability_applies_on_the_next_request(
    client: httpx.AsyncClient, org: Org
) -> None:
    await sign_in(client, org, "lena")
    assert (await client.get("/v1/admin/access/members")).status_code == 200
    await org.execute(
        "UPDATE memberships SET is_admin = false WHERE id = :m", m=org.members["lena"]
    )
    assert (await client.get("/v1/admin/access/members")).status_code == 403


# --- organizations -----------------------------------------------------------------------


async def test_another_organizations_members_grants_and_events_do_not_exist(
    client: httpx.AsyncClient, org: Org, other: Org
) -> None:
    grant = await _grant_row(other, "kestrel-notes", "maya")
    await other.execute(
        "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
        "VALUES (:o, 'operator:seed', 'access.member_changed', CAST(:t AS jsonb), 'x')",
        o=other.id,
        t=json.dumps({"membership_id": str(other.members["maya"])}),
    )
    await sign_in(client, org, "lena")
    missing = _requests(org, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    foreign = _requests(org, other.members["maya"], other.documents["kestrel-notes"], grant)
    for (method, url, body), (_, foreign_url, foreign_body) in zip(missing, foreign, strict=True):
        if url.endswith(("members", "directory", "audit-events")) or "?" in url:
            continue  # lists: checked below
        a = await _send(client, method, url, body)
        b = await _send(client, method, foreign_url, foreign_body)
        assert a.status_code == b.status_code == 404, foreign_url
        assert _error(a) == _error(b)
    listed = json.dumps(
        [
            (await client.get("/v1/admin/access/members")).json(),
            (await client.get("/v1/admin/access/directory")).json(),
            (await client.get("/v1/admin/audit-events")).json(),
            (await client.get("/v1/admin/access/documents", params={"q": "ac"})).json(),
        ]
    )
    for value in [*other.members.values(), *other.documents.values(), other.id]:
        assert str(value) not in listed


# --- documents the administrator cannot read ---------------------------------------------


async def test_search_and_explanations_cover_only_readable_documents(
    client: httpx.AsyncClient, org: Org
) -> None:
    await sign_in(client, org, "lena")
    titles: set[str] = set()
    for q in ("acme", "kestrel", "handbook", "memo", "pricing", "thread", "canary", "hidden"):
        found = (await client.get("/v1/admin/access/documents", params={"q": q})).json()
        assert CANARY not in json.dumps(found)
        titles |= {d["title"] for d in found["items"]}
    assert titles == {"Acme Q3 success plan", "Kestrel renewal notes", "Employee handbook"}
    for hidden in ("exec-memo", "pricing", "channel-thread"):
        unreadable = await client.get(
            f"/v1/admin/access/members/{org.members['grace']}/documents/{org.documents[hidden]}"
        )
        missing = await client.get(
            f"/v1/admin/access/members/{org.members['grace']}/documents/{uuid.uuid4()}"
        )
        assert unreadable.status_code == missing.status_code == 404
        assert _error(unreadable) == _error(missing)


async def test_grants_on_unreadable_documents_are_neither_listed_nor_counted(
    client: httpx.AsyncClient, org: Org
) -> None:
    await sign_in(client, org, "lena")
    without = (await client.get(f"/v1/admin/access/members/{org.members['grace']}")).json()
    await _grant_row(org, "exec-memo", "grace")
    await _grant_row(org, "pricing", "grace")
    with_them = (await client.get(f"/v1/admin/access/members/{org.members['grace']}")).json()
    assert with_them["grants"] == without["grants"] == []
    assert (without["has_other_grants"], with_them["has_other_grants"]) == (False, True)
    assert CANARY not in json.dumps(with_them)
    for key in ("exec-memo", "pricing"):
        assert str(org.documents[key]) not in json.dumps(with_them)


async def test_nothing_is_granted_or_revoked_beyond_what_the_administrator_can_read(
    client: httpx.AsyncClient, org: Org, owner_db: Database
) -> None:
    hidden_grant = await _grant_row(org, "pricing", "maya")
    await sign_in(client, org, "lena")
    grant = await client.post(
        f"/v1/admin/access/members/{org.members['maya']}/grants",
        json={"document_id": str(org.documents["exec-memo"]), "reason": "x"},
        headers=CSRF,
    )
    missing = await client.post(
        f"/v1/admin/access/members/{org.members['maya']}/grants",
        json={"document_id": str(uuid.uuid4()), "reason": "x"},
        headers=CSRF,
    )
    assert grant.status_code == missing.status_code == 404
    assert _error(grant) == _error(missing)
    revoke = await client.post(
        f"/v1/admin/access/grants/{hidden_grant}/revoke", json={"reason": "x"}, headers=CSRF
    )
    assert revoke.status_code == 404
    assert _error(revoke) == _error(missing)
    assert await _event_count(owner_db, org) == 0


async def test_private_channels_are_named_only_for_their_members(
    client: httpx.AsyncClient, org: Org
) -> None:
    await sign_in(client, org, "lena")
    body = json.dumps((await client.get(f"/v1/admin/access/members/{org.members['maya']}")).json())
    assert CANARY not in body
    assert "acct-acme-private" not in body
    await add_to_group(org, "acct-acme-private", "lena")
    named = json.dumps((await client.get(f"/v1/admin/access/members/{org.members['maya']}")).json())
    assert "acct-acme-private" in named


async def test_explanations_name_a_private_channel_only_for_its_members(
    client: httpx.AsyncClient, org: Org
) -> None:
    # The handbook, readable by Lena, granted to the private channel.
    await org.execute(
        "INSERT INTO document_grants (organization_id, document_id, principal_type, group_id, "
        "origin) VALUES (:o, :d, 'group', :g, 'source')",
        o=org.id,
        d=org.documents["handbook"],
        g=org.groups["acct-acme-private"],
    )
    await sign_in(client, org, "lena")
    explained = (
        await client.get(
            f"/v1/admin/access/members/{org.members['maya']}/documents/{org.documents['handbook']}"
        )
    ).json()
    [via] = explained["via"]
    assert via["principal_type"] == "group"
    assert via["name"] is None
    assert via["reference"].startswith("rec_")
    assert CANARY not in json.dumps(explained)


# --- previews ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"clearance": "confidential"},
        {"status": "suspended"},
        {"account_scope": "all"},
        {"groups": "exec"},
    ],
)
async def test_previews_never_count_documents_the_administrator_cannot_read(
    client: httpx.AsyncClient, org: Org, change: dict[str, str]
) -> None:
    """Each change would give Maya (or take from her) documents Lena cannot read; the
    preview is the same with or without them."""
    body: dict[str, Any] = dict(change)
    if "groups" in body:
        body["groups"] = [str(org.groups["exec"])]
    await sign_in(client, org, "lena")
    url = f"/v1/admin/access/members/{org.members['maya']}/preview"
    before = (await client.post(url, json=body, headers=CSRF)).json()
    for n in range(3):
        await org.document(
            f"hidden-confidential-{n}", f"Hidden {CANARY}", ["x"], sensitivity="confidential"
        )
        await org.document(
            f"hidden-exec-{n}",
            f"Hidden {CANARY}",
            ["x"],
            sensitivity="restricted",
            access_mode="grants_only",
            grants=[("group", "exec")],
        )
        await org.document(
            f"hidden-channel-{n}",
            f"Hidden {CANARY}",
            ["x"],
            access_mode="grants_only",
            grants=[("group", "acct-acme-private")],
        )
    after = (await client.post(url, json=body, headers=CSRF)).json()
    assert after == before
    assert CANARY not in json.dumps(after)


async def test_preview_counts_only_changes_among_readable_documents(
    client: httpx.AsyncClient, org: Org
) -> None:
    """Grace would lose her Leadership access and clearance: of what Lena can read she
    keeps the same three documents (internal, unrestricted), so nothing is counted."""
    await sign_in(client, org, "lena")
    preview = (
        await client.post(
            f"/v1/admin/access/members/{org.members['grace']}/preview",
            json={"clearance": "internal", "departments": [], "groups": []},
            headers=CSRF,
        )
    ).json()
    assert (preview["documents_gained"], preview["documents_lost"]) == (0, 0)
    kinds = [e["kind"] for e in preview["effects"]]
    assert kinds == ["clearance", "department_removed", "group_removed"]


# --- audit -------------------------------------------------------------------------------


async def test_audit_names_documents_only_while_the_viewer_can_read_them(
    client: httpx.AsyncClient, org: Org
) -> None:
    await sign_in(client, org, "lena")
    created = await client.post(
        f"/v1/admin/access/members/{org.members['maya']}/grants",
        json={"document_id": str(org.documents["kestrel-notes"]), "reason": "Cover"},
        headers=CSRF,
    )
    assert created.status_code == 201
    [event] = (await client.get("/v1/admin/audit-events")).json()["items"]
    assert event["document"]["title"] == "Kestrel renewal notes"
    assert event["document_reference"] is None
    # The document becomes confidential: Lena can no longer read it.
    await org.execute(
        "UPDATE documents SET sensitivity = 'confidential', title = :t WHERE id = :d",
        t=f"Kestrel renewal notes {CANARY}",
        d=org.documents["kestrel-notes"],
    )
    [event] = (await client.get("/v1/admin/audit-events")).json()["items"]
    assert event["document"] is None
    assert event["document_reference"].startswith("rec_")
    page = json.dumps((await client.get("/v1/admin/audit-events")).json())
    assert CANARY not in page
    assert str(org.documents["kestrel-notes"]) not in page


async def test_every_change_writes_one_event_and_a_refused_one_writes_none(
    client: httpx.AsyncClient, org: Org, owner_db: Database
) -> None:
    await sign_in(client, org, "lena")
    url = f"/v1/admin/access/members/{org.members['maya']}"
    version = (await client.get(url)).json()["version"]
    ok = await client.patch(
        url, json={"version": version, "clearance": "confidential", "reason": "x"}, headers=CSRF
    )
    assert ok.status_code == 200
    assert await _event_count(owner_db, org) == 1
    # Refused at different points: stale version, unknown account, source group.
    for body in (
        {"version": version, "clearance": "internal"},
        {"version": ok.json()["version"], "accounts": [str(uuid.uuid4())]},
        {"version": ok.json()["version"], "groups": [str(org.groups["acct-acme-private"])]},
    ):
        refused = await client.patch(url, json={**body, "reason": "x"}, headers=CSRF)
        assert refused.status_code in (409, 422)
    assert await _event_count(owner_db, org) == 1
    async with owner_db.tenant_transaction(org.id) as s:
        clearance: str = (
            await s.execute(
                text("SELECT clearance FROM memberships WHERE id = :m"), {"m": org.members["maya"]}
            )
        ).scalar_one()
    assert clearance == "confidential"


async def test_requests_without_the_csrf_header_change_nothing(
    client: httpx.AsyncClient, org: Org, owner_db: Database
) -> None:
    await sign_in(client, org, "lena")
    response = await client.post(
        f"/v1/admin/access/members/{org.members['maya']}/grants",
        json={"document_id": str(org.documents["kestrel-notes"]), "reason": "x"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_rejected"
    assert await _event_count(owner_db, org) == 0


# --- the member's accounts (Ask scope) ---------------------------------------------------


async def test_a_hidden_account_is_refused_like_an_unknown_one(
    client: httpx.AsyncClient, org: Org
) -> None:
    await sign_in(client, org, "maya")
    listed = (await client.get("/v1/accounts")).json()
    assert [a["key"] for a in listed["accounts"]] == ["acme"]
    hidden = await client.post(
        "/v1/ask", json={"question": "renewal", "scope": {"account": "kestrel"}}, headers=CSRF
    )
    unknown = await client.post(
        "/v1/ask", json={"question": "renewal", "scope": {"account": "nonexistent"}}, headers=CSRF
    )
    assert hidden.status_code == unknown.status_code == 422
    assert _error(hidden) == _error(unknown)
