"""Provisioning lifecycle takes effect immediately everywhere (Phase 10; SEC-4, SEC-11).

A provisioned member is deactivated, reactivated and removed by the identity provider.
Each change ends (or allows) access on the very next request: the session, Ask,
retrieval, the member's own traces, and what administrators see and may do on the
Access page."""

import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from lattice.authz import AuthorizationContextUnavailable, resolve_authorization_context
from lattice.db.engine import Database
from lattice.identity.sessions import SessionPolicy, SessionService

from ..conftest import CSRF
from ..factories import login
from .conftest import World, patch, user

BASE = "/scim/v2"
ASK = {"question": "What does the onboarding checklist say?"}
_HOUR = timedelta(hours=1)


async def _provision(scim: httpx.AsyncClient, world: World) -> dict[str, Any]:
    response = await scim.post(
        f"{BASE}/Users", json=user("okta-pat", "pat@halden.example"), headers=world.halden.auth
    )
    assert response.status_code == 201
    created: dict[str, Any] = response.json()
    return created


async def _ids(owner_db: Database, world: World, resource_id: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with owner_db.tenant_transaction(world.halden.org) as s:
        row = (
            await s.execute(
                text(
                    "SELECT m.id, m.user_id FROM directory_identities d JOIN memberships m "
                    "ON m.organization_id = d.organization_id AND m.id = d.membership_id "
                    "WHERE d.id = :d"
                ),
                {"d": uuid.UUID(resource_id)},
            )
        ).one()
    return row.id, row.user_id


async def _session(app_db: Database, world: World, user_id: uuid.UUID) -> str:
    sessions = SessionService(app_db, SessionPolicy(idle_timeout=_HOUR, absolute_timeout=_HOUR))
    token, _ = await sessions.create(user_id=user_id, organization_id=world.halden.org)
    return token


def _as(client: httpx.AsyncClient, token: str) -> None:
    client.cookies.clear()
    client.cookies.set("lattice_session", token)


async def _set_active(
    scim: httpx.AsyncClient, world: World, resource_id: str, active: bool
) -> None:
    response = await scim.patch(
        f"{BASE}/Users/{resource_id}",
        json=patch({"op": "replace", "path": "active", "value": active}),
        headers=world.halden.auth,
    )
    assert response.status_code == 200


@pytest.mark.parametrize("ending", ["deactivate", "remove"])
async def test_access_ends_on_the_next_request_everywhere(
    scim: httpx.AsyncClient,
    world: World,
    owner_db: Database,
    app_db: Database,
    ending: str,
) -> None:
    created = await _provision(scim, world)
    membership_id, user_id = await _ids(owner_db, world, created["id"])
    token = await _session(app_db, world, user_id)

    # Provisioned and active: the member can ask and read their own trace.
    _as(scim, token)
    assert (await scim.get("/v1/me")).status_code == 200
    answer = await scim.post("/v1/ask", json=ASK, headers=CSRF)
    assert answer.status_code == 200
    trace_id = answer.json()["trace_id"]
    assert (await scim.get(f"/v1/traces/{trace_id}")).status_code == 200

    scim.cookies.clear()
    if ending == "deactivate":
        await _set_active(scim, world, created["id"], False)
    else:
        assert (
            await scim.delete(f"{BASE}/Users/{created['id']}", headers=world.halden.auth)
        ).status_code == 204

    # The same session: refused everywhere, identically.
    _as(scim, token)
    for method, path, body in (
        ("GET", "/v1/me", None),
        ("POST", "/v1/ask", ASK),
        ("GET", f"/v1/traces/{trace_id}", None),
        ("GET", "/v1/accounts", None),
    ):
        response = await scim.request(method, path, json=body, headers=CSRF)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "authentication_required"
    # The session row itself was revoked in the change's transaction.
    async with owner_db.tenant_transaction(world.halden.org) as s:
        revoked: bool = (
            await s.execute(
                text("SELECT revoked_at IS NOT NULL FROM sessions WHERE user_id = :u"),
                {"u": user_id},
            )
        ).scalar_one()
    assert revoked
    # Retrieval cannot even build an authorization context for the member.
    with pytest.raises(AuthorizationContextUnavailable):
        async with app_db.tenant_snapshot(world.halden.org) as s:
            await resolve_authorization_context(
                s, organization_id=world.halden.org, membership_id=membership_id
            )

    # Administrators see the new state and the identity provider as its owner.
    scim.cookies.clear()
    assert (await login(scim, world.admin.email)).status_code == 200
    member = (await scim.get(f"/v1/admin/access/members/{membership_id}")).json()["member"]
    expected = "suspended" if ending == "deactivate" else "removed"
    assert (member["status"], member["managed_by"]) == (expected, "identity_provider")


async def test_reactivation_restores_access_but_never_old_sessions(
    scim: httpx.AsyncClient, world: World, owner_db: Database, app_db: Database
) -> None:
    created = await _provision(scim, world)
    _, user_id = await _ids(owner_db, world, created["id"])
    old = await _session(app_db, world, user_id)
    await _set_active(scim, world, created["id"], False)
    await _set_active(scim, world, created["id"], True)
    _as(scim, old)
    assert (await scim.get("/v1/me")).status_code == 401
    _as(scim, await _session(app_db, world, user_id))
    assert (await scim.get("/v1/me")).status_code == 200


async def test_the_access_page_leaves_lifecycle_to_the_identity_provider(
    scim: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    created = await _provision(scim, world)
    membership_id, _ = await _ids(owner_db, world, created["id"])
    assert (await login(scim, world.admin.email)).status_code == 200
    view = (await scim.get(f"/v1/admin/access/members/{membership_id}")).json()
    url = f"/v1/admin/access/members/{membership_id}"
    for path, body in (
        (f"{url}/preview", {"status": "suspended"}),
        (url, {"status": "suspended", "version": view["version"], "reason": "Leaving"}),
    ):
        method = "PATCH" if path == url else "POST"
        response = await scim.request(method, path, json=body, headers=CSRF)
        assert response.status_code == 422
        assert response.json()["error"]["details"] == [
            {"field": "status", "issue": "managed_by_identity_provider"}
        ]
    # Access stays the organization's to manage.
    changed = await scim.patch(
        url,
        json={"clearance": "confidential", "version": view["version"], "reason": "Finance"},
        headers=CSRF,
    )
    assert changed.status_code == 200
    assert changed.json()["member"]["clearance"] == "confidential"
    # Manifest members are not affected: an administrator may still suspend them.
    maya = (await scim.get(f"/v1/admin/access/members/{world.maya.membership_id}")).json()
    assert maya["member"]["managed_by"] is None
    suspended = await scim.patch(
        f"/v1/admin/access/members/{world.maya.membership_id}",
        json={"status": "suspended", "version": maya["version"], "reason": "Leave"},
        headers=CSRF,
    )
    assert suspended.status_code == 200


async def test_the_audit_view_names_the_identity_provider(
    scim: httpx.AsyncClient, world: World
) -> None:
    created = await _provision(scim, world)
    renamed = await scim.patch(
        f"{BASE}/Users/{created['id']}",
        json=patch({"op": "replace", "path": "displayName", "value": "Patricia Doe"}),
        headers=world.halden.auth,
    )
    assert renamed.status_code == 200
    assert (await login(scim, world.admin.email)).status_code == 200
    items = (await scim.get("/v1/admin/audit-events")).json()["items"]
    by_action = {i["action"]: i for i in items}
    provisioned = by_action["access.member_changed"]
    assert provisioned["actor"] == {"kind": "identity_provider", "name": None, "operator": None}
    assert provisioned["member"]["name"] == "Patricia Doe"
    identity = by_action["access.identity_changed"]
    assert {d["field"]: (d["before"], d["after"]) for d in identity["details"]} == {
        "name": ("Pat Doe", "Patricia Doe")
    }
    assert "scim:" not in str(items)
