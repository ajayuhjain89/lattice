"""Members and administrators operated in the product (Phase 12, P12.2; phase-12-plan
C2.1-C2.3; ADR-017; migration 0017).

Proves: a member added by hand is bound to the exact identifier of the active
connection, with least access, audited, conflict-safe and organization-scoped (another
organization's people neither collide nor show); the administrator capability is
granted and revoked for another member only, never changes content access, and the
organization never loses its last active administrator through the product -- also
under concurrent requests -- while the identity provider may remove them; a removed
member is reinstated on their original identity with least access, and SCIM resolves
the same person afterwards; suspension ends sessions; and the database refuses what the
code refuses."""

import asyncio
import unicodedata
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice.access import lifecycle
from lattice.access.changes import change_member
from lattice.access.lifecycle import set_administrator
from lattice.access.models import AdministratorRequest, MemberChangeRequest
from lattice.authz import resolve_member_context
from lattice.db.engine import Database
from lattice.errors import LatticeError

from ..conftest import CSRF, AppFactory
from ..factories import (
    create_member,
    create_oidc_connection,
    create_org,
    create_scim_token,
    login,
)
from ..provisioning.conftest import patch, user

CANARY = "LATTICE-CANARY-MEMBERS-5521"


@dataclass
class Tenant:
    org: uuid.UUID
    slug: str
    connection: uuid.UUID
    token: str
    admins: dict[str, uuid.UUID]

    @property
    def scim(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class World:
    halden: Tenant
    veridian: Tenant
    maya: uuid.UUID  # a Halden member (global user), not an administrator


@pytest.fixture
async def world(owner_db: Database) -> World:
    tenants: list[Tenant] = []
    for slug, admins in (("halden", ("lena", "olga")), ("veridian", ("victor",))):
        org = await create_org(owner_db, slug)
        connection = await create_oidc_connection(
            owner_db, org, issuer=f"https://idp.{slug}.example"
        )
        token = await create_scim_token(owner_db, org, connection)
        ids = {
            name: (
                await create_member(owner_db, org, f"{name}@{slug}.example", is_admin=True)
            ).membership_id
            for name in admins
        }
        tenants.append(Tenant(org, slug, connection, token, ids))
    maya = await create_member(owner_db, tenants[0].org, "maya@halden.example")
    return World(tenants[0], tenants[1], maya.membership_id)


@pytest.fixture
async def client(app_client: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client() as c:
        yield c


async def _as(client: httpx.AsyncClient, email: str) -> None:
    client.cookies.clear()
    assert (await login(client, email)).status_code == 200


def _new(
    identifier: str = "pat-001", email: str = "pat@halden.example", **extra: Any
) -> dict[str, Any]:
    return {
        "email": email,
        "name": "Pat Doe",
        "given_name": "Pat",
        "family_name": "Doe",
        "identifier": identifier,
        "reason": "Joined the support team",
        **extra,
    }


async def _add(client: httpx.AsyncClient, **body: Any) -> httpx.Response:
    return await client.post("/v1/admin/access/members", json=_new(**body), headers=CSRF)


async def _rows(owner: Database, org: uuid.UUID, sql: str, **params: Any) -> list[Any]:
    async with owner.tenant_transaction(org) as s:
        result = await s.execute(text(sql), params)
        return list(result.all()) if getattr(result, "returns_rows", False) else []


async def _counts(owner: Database, org: uuid.UUID) -> tuple[int, int, int]:
    rows = await _rows(
        owner,
        org,
        "SELECT (SELECT count(*) FROM users WHERE home_organization_id = :o), "
        "(SELECT count(*) FROM memberships), (SELECT count(*) FROM directory_identities)",
        o=org,
    )
    users, memberships, identities = rows[0]
    return int(users), int(memberships), int(identities)


async def _member(client: httpx.AsyncClient, membership_id: uuid.UUID | str) -> dict[str, Any]:
    response = await client.get(f"/v1/admin/access/members/{membership_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _active_admins(owner: Database, org: uuid.UUID) -> int:
    rows = await _rows(
        owner,
        org,
        "SELECT count(*) FROM memberships m JOIN users u ON u.id = m.user_id "
        "WHERE m.is_admin AND m.status = 'active' AND u.status = 'active'",
    )
    return int(rows[0][0])


# --- add a member by hand (C2.1) ------------------------------------------------------------


async def test_the_identity_requirement_names_the_active_connection_claim(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    await _as(client, "lena@halden.example")
    body = (await client.get("/v1/admin/access/identity-requirement")).json()
    assert body == {
        "available": True,
        "subject_claim": "sub",
        "issuer": "https://idp.halden.example",
    }
    await _rows(owner_db, world.halden.org, "UPDATE oidc_connections SET status = 'disabled'")
    body = (await client.get("/v1/admin/access/identity-requirement")).json()
    assert body == {"available": False, "subject_claim": None, "issuer": None}


async def test_a_member_added_by_hand_is_bound_exactly_with_least_access(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    await _as(client, "lena@halden.example")
    response = await _add(client, identifier="Pat.Doe@IdP-01")
    assert response.status_code == 201, response.text
    access = response.json()
    member = access["member"]
    assert (member["name"], member["email"], member["status"]) == (
        "Pat Doe",
        "pat@halden.example",
        "active",
    )
    # Least access (D-20): nothing chosen at creation.
    assert member["is_admin"] is False
    assert (member["clearance"], member["account_scope"]) == ("internal", "assigned")
    assert member["assigned_accounts"] == 0
    assert member["departments"] == []
    assert access["accounts"] == access["groups"] == access["grants"] == []
    assert not access["has_hidden_groups"]
    assert not access["has_other_grants"]
    # Managed in the product, not by the identity provider.
    assert member["managed_by"] is None
    lifecycle_ = access["lifecycle"]
    assert lifecycle_["sign_in_identifier"] == "Pat.Doe@IdP-01"  # case preserved
    assert lifecycle_["identity_managed_by"] == "organization"
    assert lifecycle_["suspend"] == {"available": True, "blocked_by": None}
    assert lifecycle_["grant_administrator"] == {"available": True, "blocked_by": None}
    assert lifecycle_["reinstate"] is None
    assert lifecycle_["revoke_administrator"] is None

    membership_id = uuid.UUID(member["id"])
    identity = await _rows(
        owner_db,
        world.halden.org,
        "SELECT d.connection_id, d.external_id, d.user_name, d.given_name, d.family_name, "
        "d.managed_by, u.home_organization_id, m.is_admin, m.clearance, m.account_scope "
        "FROM directory_identities d JOIN memberships m ON m.id = d.membership_id "
        "JOIN users u ON u.id = m.user_id WHERE d.membership_id = :m",
        m=membership_id,
    )
    assert [tuple(r) for r in identity] == [
        (
            world.halden.connection,
            "Pat.Doe@IdP-01",
            "pat@halden.example",
            "Pat",
            "Doe",
            "organization",
            world.halden.org,
            False,
            "internal",
            "assigned",
        )
    ]
    events = await _rows(
        owner_db,
        world.halden.org,
        "SELECT actor, action, target, before, after, reason FROM audit_events",
    )
    assert len(events) == 1
    actor, action, target, before, after, reason = events[0]
    assert (actor, action, target) == (
        f"member:{world.halden.admins['lena']}",
        "access.member_changed",
        {"membership_id": str(membership_id)},
    )
    assert before is None
    assert after == {
        "status": "active",
        "is_admin": False,
        "clearance": "internal",
        "account_scope": "assigned",
        "accounts": [],
        "departments": [],
        "groups": [],
        # The binding itself (C18): an administrator who typed the wrong identifier is
        # traceable.
        "identity": {"identifier": "Pat.Doe@IdP-01", "email": "pat@halden.example"},
    }
    assert reason == "Joined the support team"
    # The audit view says "added", by the administrator, and names the binding.
    item = (await client.get("/v1/admin/audit-events")).json()["items"][0]
    assert item["effects"][0]["kind"] == "created"
    assert item["actor"]["kind"] == "member"
    assert item["details"] == [
        {"field": "sign_in_identifier", "before": None, "after": "Pat.Doe@IdP-01"},
        {"field": "email", "before": None, "after": "pat@halden.example"},
    ]


async def test_the_identifier_is_nfc_normalised_and_trimmed_never_case_folded(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    await _as(client, "lena@halden.example")
    decomposed = "  René-ID  "
    response = await _add(client, identifier=decomposed)
    assert response.status_code == 201, response.text
    stored = response.json()["lifecycle"]["sign_in_identifier"]
    assert stored == unicodedata.normalize("NFC", "René-ID") == "René-ID"
    # A different case is a different person to the identity provider.
    other = await _add(client, identifier="rené-id", email="rene2@halden.example")
    assert other.status_code == 201


async def test_the_request_cannot_carry_access(client: httpx.AsyncClient, world: World) -> None:
    await _as(client, "lena@halden.example")
    for extra in ({"is_admin": True}, {"clearance": "confidential"}, {"account_scope": "all"}):
        response = await client.post(
            "/v1/admin/access/members", json={**_new(), **extra}, headers=CSRF
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


async def test_invalid_input_is_refused_without_echoing_it(
    client: httpx.AsyncClient, world: World
) -> None:
    await _as(client, "lena@halden.example")
    for body in (
        _new(email=f"not-an-address-{CANARY}"),
        _new(identifier=f"bad\x07{CANARY}"),
        _new(identifier="   "),
        {**_new(), "reason": "   "},
        {**_new(), "name": f"\x00{CANARY}"},
    ):
        response = await client.post("/v1/admin/access/members", json=body, headers=CSRF)
        assert response.status_code == 422, body
        assert CANARY not in response.text


async def test_duplicates_in_the_organization_are_refused_deterministically(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    await _as(client, "lena@halden.example")
    assert (await _add(client)).status_code == 201
    # A SCIM-provisioned person and a removed one keep their identifier and e-mail.
    scim = await client.post(
        "/scim/v2/Users",
        json=user("scim-sam", "sam@halden.example"),
        headers=world.halden.scim,
    )
    assert scim.status_code == 201
    gone = await client.post(
        "/scim/v2/Users", json=user("scim-gus", "gus@halden.example"), headers=world.halden.scim
    )
    await client.delete(f"/scim/v2/Users/{gone.json()['id']}", headers=world.halden.scim)
    before = await _counts(owner_db, world.halden.org)
    cases = (
        ({"identifier": "pat-001", "email": "new1@halden.example"}, "identifier"),
        ({"identifier": "scim-sam", "email": "new2@halden.example"}, "identifier"),
        ({"identifier": "scim-gus", "email": "new3@halden.example"}, "identifier"),
        ({"identifier": "fresh-1", "email": "PAT@halden.example"}, "email"),
        ({"identifier": "fresh-2", "email": "sam@halden.example"}, "email"),
        ({"identifier": "fresh-3", "email": "gus@halden.example"}, "email"),
        ({"identifier": "fresh-4", "email": "maya@halden.example"}, "email"),  # global user
    )
    for body, field in cases:
        first = await _add(client, **body)
        again = await _add(client, **body)
        assert first.status_code == again.status_code == 409, body
        assert first.json()["error"]["details"] == [{"field": field, "issue": "taken"}]
        assert first.json()["error"]["message"] == again.json()["error"]["message"]
    assert await _counts(owner_db, world.halden.org) == before


async def test_another_organizations_people_never_collide_or_show(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    await _as(client, "lena@halden.example")
    assert (
        await _add(client, identifier="shared-77", email="pat@shared.example")
    ).status_code == 201
    # Victor (Veridian) adds the same identifier and e-mail: a new, separate person.
    await _as(client, "victor@veridian.example")
    taken_elsewhere = await _add(client, identifier="shared-77", email="pat@shared.example")
    never_seen = await _add(client, identifier="random-88", email="r@shared.example")
    assert taken_elsewhere.status_code == never_seen.status_code == 201
    vid = taken_elsewhere.json()["member"]["id"]
    assert (
        await _rows(
            owner_db,
            world.veridian.org,
            "SELECT count(*) FROM memberships WHERE id = :m",
            m=uuid.UUID(vid),
        )
    )[0][0] == 1
    # Halden's member is still Halden's alone, and Veridian cannot read it.
    halden_member = (
        await _rows(
            owner_db,
            world.halden.org,
            "SELECT membership_id FROM directory_identities WHERE external_id = 'shared-77'",
        )
    )[0][0]
    foreign = await client.get(f"/v1/admin/access/members/{halden_member}")
    missing = await client.get(f"/v1/admin/access/members/{uuid.uuid4()}")
    assert foreign.status_code == missing.status_code == 404
    foreign_body, missing_body = foreign.json(), missing.json()
    foreign_body["error"].pop("request_id")
    missing_body["error"].pop("request_id")
    assert foreign_body == missing_body
    # Within Veridian, its own duplicate conflicts; the error names no organization.
    clash = await _add(client, identifier="shared-77", email="x@shared.example")
    assert clash.status_code == 409
    assert "halden" not in clash.text.lower()


async def test_adding_needs_an_active_connection(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    await _rows(owner_db, world.halden.org, "UPDATE oidc_connections SET status = 'disabled'")
    await _as(client, "lena@halden.example")
    response = await _add(client)
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": "identity", "issue": "no_active_connection"}
    ]
    assert await _counts(owner_db, world.halden.org) == (0, 3, 0)


async def test_members_cannot_add_members(client: httpx.AsyncClient, world: World) -> None:
    await _as(client, "maya@halden.example")
    assert (await _add(client)).status_code == 403


async def test_nothing_is_created_without_its_audit_event(
    world: World, owner_db: Database, app_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The database refuses to commit the person without the event (0013 triggers fire
    for the fenced function's writes too), so a failure leaves nothing behind."""

    async def no_event(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(lifecycle, "record_event", no_event)
    from lattice.access.models import NewMemberRequest

    with pytest.raises(DBAPIError, match="without an audit event"):
        await lifecycle.create_member(
            app_db,
            organization_id=world.halden.org,
            actor_membership_id=world.halden.admins["lena"],
            request=NewMemberRequest(**_new()),
        )
    assert await _counts(owner_db, world.halden.org) == (0, 3, 0)


# --- signing in: the exact identifier, never the e-mail ------------------------------------


async def test_scim_neither_lists_nor_changes_a_member_added_by_hand(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    await _as(client, "lena@halden.example")
    added = await _add(client, identifier="hand-1")
    membership_id = uuid.UUID(added.json()["member"]["id"])
    resource = (
        await _rows(
            owner_db,
            world.halden.org,
            "SELECT id FROM directory_identities WHERE membership_id = :m",
            m=membership_id,
        )
    )[0][0]
    listed = (await client.get("/scim/v2/Users", headers=world.halden.scim)).json()
    assert listed["totalResults"] == 0
    found = await client.get(
        "/scim/v2/Users", params={"filter": 'externalId eq "hand-1"'}, headers=world.halden.scim
    )
    assert found.json()["totalResults"] == 0
    for method in ("GET", "DELETE"):
        response = await client.request(
            method, f"/scim/v2/Users/{resource}", headers=world.halden.scim
        )
        assert response.status_code == 404
    clash = await client.post(
        "/scim/v2/Users", json=user("hand-1", "other@halden.example"), headers=world.halden.scim
    )
    assert clash.status_code == 409
    assert clash.json()["scimType"] == "uniqueness"
    status = await _rows(
        owner_db, world.halden.org, "SELECT status FROM memberships WHERE id = :m", m=membership_id
    )
    assert status[0][0] == "active"


# --- the administrator capability (C2.2) ----------------------------------------------------


async def _context(owner: Database, org: uuid.UUID, membership_id: uuid.UUID) -> dict[str, Any]:
    async with owner.tenant_snapshot(org) as s:
        ctx = await resolve_member_context(s, organization_id=org, membership_id=membership_id)
    assert ctx is not None
    return ctx.model_dump(exclude={"authz_updated_at"})


async def test_granting_and_revoking_administration_never_changes_content_access(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    await _as(client, "lena@halden.example")
    before_ctx = await _context(owner_db, world.halden.org, world.maya)
    member = await _member(client, world.maya)
    preview = await client.post(
        f"/v1/admin/access/members/{world.maya}/administrator/preview",
        json={"administrator": True},
        headers=CSRF,
    )
    assert preview.json() == {"now": False, "after": True, "content_access_changes": False}
    granted = await client.post(
        f"/v1/admin/access/members/{world.maya}/administrator",
        json={"administrator": True, "version": member["version"], "reason": "Covers support"},
        headers=CSRF,
    )
    assert granted.status_code == 200, granted.text
    after = granted.json()
    assert after["member"]["is_admin"] is True
    assert after["lifecycle"]["revoke_administrator"] == {"available": True, "blocked_by": None}
    assert await _context(owner_db, world.halden.org, world.maya) == before_ctx
    # The stale version is refused.
    stale = await client.post(
        f"/v1/admin/access/members/{world.maya}/administrator",
        json={"administrator": False, "version": member["version"], "reason": "r"},
        headers=CSRF,
    )
    assert stale.status_code == 409
    # Maya can administer now; revoked, her next request is refused.
    await _as(client, "maya@halden.example")
    assert (await client.get("/v1/admin/access/members")).status_code == 200
    await _as(client, "lena@halden.example")
    revoked = await client.post(
        f"/v1/admin/access/members/{world.maya}/administrator",
        json={"administrator": False, "version": after["version"], "reason": "Back to support"},
        headers=CSRF,
    )
    assert revoked.status_code == 200
    assert await _context(owner_db, world.halden.org, world.maya) == before_ctx
    await _as(client, "maya@halden.example")
    assert (await client.get("/v1/admin/access/members")).status_code == 403
    events = await _rows(
        owner_db,
        world.halden.org,
        "SELECT actor, before ->> 'is_admin', after ->> 'is_admin', reason FROM audit_events "
        "ORDER BY created_at",
    )
    lena = f"member:{world.halden.admins['lena']}"
    assert [tuple(e) for e in events] == [
        (lena, "false", "true", "Covers support"),
        (lena, "true", "false", "Back to support"),
    ]


async def test_nobody_changes_their_own_administration(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    lena = world.halden.admins["lena"]
    await _as(client, "lena@halden.example")
    own = await _member(client, lena)
    assert own["lifecycle"]["revoke_administrator"] == {
        "available": False,
        "blocked_by": "own_membership",
    }
    assert own["lifecycle"]["suspend"] == {"available": False, "blocked_by": "own_membership"}
    for path, body in (
        ("administrator/preview", {"administrator": False}),
        ("administrator", {"administrator": False, "version": own["version"], "reason": "r"}),
    ):
        response = await client.post(
            f"/v1/admin/access/members/{lena}/{path}", json=body, headers=CSRF
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"] == [
            {"field": "membership", "issue": "own_access"}
        ]
    assert await _active_admins(owner_db, world.halden.org) == 2


async def test_a_removed_member_is_never_made_an_administrator(
    client: httpx.AsyncClient, world: World
) -> None:
    await _as(client, "lena@halden.example")
    created = await client.post(
        "/scim/v2/Users", json=user("scim-rae", "rae@halden.example"), headers=world.halden.scim
    )
    await client.delete(f"/scim/v2/Users/{created.json()['id']}", headers=world.halden.scim)
    page = (await client.get("/v1/admin/access/members", params={"q": "rae"})).json()
    rae = page["items"][0]["id"]
    access = await _member(client, rae)
    assert access["lifecycle"]["grant_administrator"] is None
    response = await client.post(
        f"/v1/admin/access/members/{rae}/administrator",
        json={"administrator": True, "version": access["version"], "reason": "r"},
        headers=CSRF,
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": "membership", "issue": "member_removed"}
    ]


# --- the last active administrator (ADR-017) ------------------------------------------------


async def _version(owner: Database, org: uuid.UUID, membership_id: uuid.UUID) -> datetime:
    rows = await _rows(
        owner, org, "SELECT authz_updated_at FROM memberships WHERE id = :m", m=membership_id
    )
    value: datetime = rows[0][0]
    return value


async def _outcomes(*calls: Any) -> list[str]:
    results = await asyncio.gather(*calls, return_exceptions=True)
    found: list[str] = []
    for r in results:
        if r is None:
            found.append("ok")
        elif isinstance(r, LatticeError):
            issues = [d.issue for d in r.details or []]
            found.append(issues[0] if issues else r.code.value)
        else:
            raise r
    return found


@pytest.mark.parametrize(
    "kinds", [("revoke", "revoke"), ("suspend", "suspend"), ("revoke", "suspend")]
)
async def test_two_administrators_cannot_remove_each_other_at_once(
    world: World, owner_db: Database, app_db: Database, kinds: tuple[str, str]
) -> None:
    org = world.halden.org
    lena, olga = world.halden.admins["lena"], world.halden.admins["olga"]
    for _ in range(3):

        async def act(kind: str, actor: uuid.UUID, target: uuid.UUID) -> None:
            version = await _version(owner_db, org, target)
            if kind == "revoke":
                await set_administrator(
                    app_db,
                    organization_id=org,
                    actor_membership_id=actor,
                    membership_id=target,
                    request=AdministratorRequest(administrator=False, version=version, reason="r"),
                )
            else:
                await change_member(
                    app_db,
                    organization_id=org,
                    actor_membership_id=actor,
                    membership_id=target,
                    request=MemberChangeRequest(status="suspended", version=version, reason="r"),
                )

        outcomes = await _outcomes(act(kinds[0], lena, olga), act(kinds[1], olga, lena))
        # Exactly one wins; the other finds it is no longer an administrator (or that
        # the target is the last one) after waiting for the administrator-set lock.
        assert outcomes.count("ok") == 1, outcomes
        assert await _active_admins(owner_db, org) == 1
        # Restore both for the next round (as the operator).
        await _rows(
            owner_db,
            org,
            "UPDATE memberships SET is_admin = true, status = 'active' WHERE id IN (:a, :b)",
            a=lena,
            b=olga,
        )


async def test_the_database_refuses_an_api_write_that_leaves_no_administrator(
    world: World, owner_db: Database, app_db: Database
) -> None:
    """A direct API-role statement that slipped past the code: suspending both
    administrators in two transactions that commit one after the other."""
    org = world.halden.org
    lena, olga = world.halden.admins["lena"], world.halden.admins["olga"]

    async def suspend(target: uuid.UUID, gate: asyncio.Event, go: asyncio.Event) -> None:
        async with app_db.tenant_transaction(org) as s:
            await s.execute(
                text("UPDATE memberships SET status = 'suspended' WHERE id = :m"), {"m": target}
            )
            await s.execute(
                text(
                    "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
                    "VALUES (:o, :a, 'access.member_changed', "
                    "jsonb_build_object('membership_id', CAST(:m AS text)), 'bypass')"
                ),
                {"o": org, "a": f"member:{lena}", "m": str(target)},
            )
            gate.set()
            await go.wait()

    ready_a, ready_b, go = asyncio.Event(), asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(suspend(lena, ready_a, go))
    second = asyncio.create_task(suspend(olga, ready_b, go))
    await ready_a.wait()
    await ready_b.wait()
    go.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(failures) == 1
    assert "last_administrator" in str(failures[0])
    assert await _active_admins(owner_db, org) == 1


async def test_the_api_role_cannot_set_the_capability_directly(
    world: World, app_db: Database
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_db.tenant_transaction(world.halden.org) as s:
            await s.execute(
                text("UPDATE memberships SET is_admin = true WHERE id = :m"), {"m": world.maya}
            )


async def test_the_identity_provider_may_remove_the_last_administrator(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    """C18: a person who left loses access even when they were the last administrator.
    The organization then reads *No active administrator* and needs an operator."""
    org = world.halden.org
    created = await client.post(
        "/scim/v2/Users", json=user("scim-ada", "ada@halden.example"), headers=world.halden.scim
    )
    resource = created.json()["id"]
    await _rows(
        owner_db,
        org,
        "UPDATE memberships SET is_admin = (user_id = (SELECT id FROM users "
        "WHERE email = 'ada@halden.example'))",
    )
    assert await _active_admins(owner_db, org) == 1
    deactivated = await client.patch(
        f"/scim/v2/Users/{resource}",
        json=patch({"op": "replace", "path": "active", "value": False}),
        headers=world.halden.scim,
    )
    assert deactivated.status_code == 200
    assert await _active_admins(owner_db, org) == 0
    reactivated = await client.patch(
        f"/scim/v2/Users/{resource}",
        json=patch({"op": "replace", "path": "active", "value": True}),
        headers=world.halden.scim,
    )
    assert reactivated.status_code == 200
    assert (
        await client.delete(f"/scim/v2/Users/{resource}", headers=world.halden.scim)
    ).status_code == 204
    assert await _active_admins(owner_db, org) == 0


# --- suspension (Phase 9 + P12.2) ------------------------------------------------------------


async def _dev_identity(
    owner: Database, org: uuid.UUID, membership_id: uuid.UUID, email: str
) -> None:
    await _rows(
        owner,
        org,
        "INSERT INTO external_identities (user_id, issuer, subject) "
        "SELECT user_id, 'dev', :e FROM memberships WHERE id = :m",
        e=email,
        m=membership_id,
    )


async def test_suspending_a_member_added_by_hand_ends_their_sessions(
    app_client: AppFactory, world: World, owner_db: Database
) -> None:
    async with app_client() as admin, app_client() as pat:
        await _as(admin, "lena@halden.example")
        added = await _add(admin)
        membership_id = added.json()["member"]["id"]
        await _dev_identity(
            owner_db, world.halden.org, uuid.UUID(membership_id), "pat@halden.example"
        )
        await _as(pat, "pat@halden.example")
        assert (await pat.get("/v1/me")).status_code == 200
        access = await _member(admin, membership_id)
        suspended = await admin.patch(
            f"/v1/admin/access/members/{membership_id}",
            json={"status": "suspended", "version": access["version"], "reason": "Leave"},
            headers=CSRF,
        )
        assert suspended.status_code == 200, suspended.text
        assert suspended.json()["lifecycle"]["reactivate"] == {
            "available": True,
            "blocked_by": None,
        }
        # The next request follows Phase 11 semantics: the session is gone.
        assert (await pat.get("/v1/me")).status_code == 401
        reactivated = await admin.patch(
            f"/v1/admin/access/members/{membership_id}",
            json={"status": "active", "version": suspended.json()["version"], "reason": "Back"},
            headers=CSRF,
        )
        assert reactivated.status_code == 200
        # Reactivation restores the rules, never the old session.
        assert (await pat.get("/v1/me")).status_code == 401
        revoked = await _rows(
            owner_db,
            world.halden.org,
            "SELECT count(*) FILTER (WHERE revoked_at IS NULL) FROM sessions s JOIN memberships m "
            "ON m.user_id = s.user_id WHERE m.id = :m",
            m=uuid.UUID(membership_id),
        )
        assert revoked[0][0] == 0


async def test_an_identity_provider_member_is_still_suspended_only_there(
    client: httpx.AsyncClient, world: World
) -> None:
    created = await client.post(
        "/scim/v2/Users", json=user("scim-ivy", "ivy@halden.example"), headers=world.halden.scim
    )
    assert created.status_code == 201
    await _as(client, "lena@halden.example")
    rows = (await client.get("/v1/admin/access/members", params={"q": "ivy"})).json()["items"]
    ivy = rows[0]
    assert ivy["managed_by"] == "identity_provider"
    access = await _member(client, ivy["id"])
    assert access["lifecycle"]["identity_managed_by"] == "identity_provider"
    assert access["lifecycle"]["suspend"] == {
        "available": False,
        "blocked_by": "managed_by_identity_provider",
    }
    response = await client.patch(
        f"/v1/admin/access/members/{ivy['id']}",
        json={"status": "suspended", "version": access["version"], "reason": "r"},
        headers=CSRF,
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["issue"] == "managed_by_identity_provider"


# --- reinstatement (C2.3, D-36) --------------------------------------------------------------


async def _removed_with_history(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Pat, provisioned by SCIM, given broad access (and the capability) by the operator,
    then removed by the identity provider. Returns (membership, directory identity,
    SCIM resource ID)."""
    org = world.halden.org
    created = await client.post(
        "/scim/v2/Users", json=user("scim-pat", "pat@halden.example"), headers=world.halden.scim
    )
    resource = created.json()["id"]
    rows = await _rows(
        owner_db,
        org,
        "SELECT membership_id, id FROM directory_identities WHERE id = :r",
        r=uuid.UUID(resource),
    )
    membership_id, identity_id = rows[0]
    account = (
        await _rows(
            owner_db,
            org,
            "INSERT INTO accounts (organization_id, key, name) "
            "VALUES (:o, 'acme', 'Acme') RETURNING id",
            o=org,
        )
    )[0][0]
    department = (
        await _rows(
            owner_db,
            org,
            "INSERT INTO departments (organization_id, key, "
            "name) VALUES (:o, 'sales', 'Sales') RETURNING id",
            o=org,
        )
    )[0][0]
    group = (
        await _rows(
            owner_db,
            org,
            "INSERT INTO access_groups (organization_id, key, "
            "origin, name) VALUES (:o, 'exec', 'admin', 'Exec') RETURNING id",
            o=org,
        )
    )[0][0]
    for sql in (
        "UPDATE memberships SET clearance = 'confidential', account_scope = 'all', "
        "is_admin = true WHERE id = :m",
        "INSERT INTO account_assignments (organization_id, membership_id, account_id) "
        "VALUES (:o, :m, :a)",
        "INSERT INTO membership_departments (organization_id, membership_id, department_id) "
        "VALUES (:o, :m, :d)",
        "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
        "VALUES (:o, :g, :m)",
    ):
        await _rows(owner_db, org, sql, o=org, m=membership_id, a=account, d=department, g=group)
    deleted = await client.delete(f"/scim/v2/Users/{resource}", headers=world.halden.scim)
    assert deleted.status_code == 204
    return membership_id, identity_id, resource


async def test_reinstatement_restores_the_identity_with_least_access(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    org = world.halden.org
    membership_id, identity_id, resource = await _removed_with_history(client, world, owner_db)
    await _as(client, "lena@halden.example")
    access = await _member(client, membership_id)
    assert access["member"]["status"] == "removed"
    assert access["lifecycle"]["reinstate"] == {"available": True, "blocked_by": None}
    assert access["lifecycle"]["grant_administrator"] is None
    preview = await client.post(
        f"/v1/admin/access/members/{membership_id}/reinstate/preview", headers=CSRF
    )
    assert preview.status_code == 200, preview.text
    kinds = [e["kind"] for e in preview.json()["effects"]]
    assert kinds[0] == "reinstated"
    assert {
        "administrator",
        "clearance",
        "account_scope",
        "account_removed",
        "department_removed",
        "group_removed",
    } <= set(kinds)
    assert preview.json()["documents_lost"] == 0
    response = await client.post(
        f"/v1/admin/access/members/{membership_id}/reinstate",
        json={"version": access["version"], "reason": "Re-hired"},
        headers=CSRF,
    )
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["member"]["status"] == "active"
    assert after["member"]["is_admin"] is False
    assert (after["member"]["clearance"], after["member"]["account_scope"]) == (
        "internal",
        "assigned",
    )
    assert after["accounts"] == after["departments"] == after["groups"] == []
    assert after["lifecycle"]["identity_managed_by"] == "identity_provider"
    state = await _rows(
        owner_db,
        org,
        "SELECT (SELECT count(*) FROM memberships), (SELECT count(*) FROM directory_identities), "
        "(SELECT count(*) FROM account_assignments), (SELECT count(*) FROM "
        "membership_departments), (SELECT count(*) FROM access_group_members), "
        "(SELECT id FROM directory_identities WHERE membership_id = :m), "
        "(SELECT external_id FROM directory_identities WHERE membership_id = :m)",
        m=membership_id,
    )
    assert tuple(state[0]) == (4, 1, 0, 0, 0, identity_id, "scim-pat")
    events = await _rows(
        owner_db,
        org,
        "SELECT actor, before ->> 'status', after, reason FROM audit_events "
        "WHERE reason = 'Re-hired'",
    )
    assert [tuple(e) for e in events] == [
        (
            f"member:{world.halden.admins['lena']}",
            "removed",
            {
                "status": "active",
                "is_admin": False,
                "clearance": "internal",
                "account_scope": "assigned",
                "accounts": [],
                "departments": [],
                "groups": [],
            },
            "Re-hired",
        )
    ]
    # SCIM resolves the same person deterministically afterwards.
    found = await client.get(
        "/scim/v2/Users", params={"filter": 'externalId eq "scim-pat"'}, headers=world.halden.scim
    )
    assert found.json()["totalResults"] == 1
    assert found.json()["Resources"][0]["id"] == resource
    assert found.json()["Resources"][0]["active"] is True
    clash = await client.post(
        "/scim/v2/Users", json=user("scim-pat", "pat2@halden.example"), headers=world.halden.scim
    )
    assert clash.status_code == 409
    # The identity provider stays authoritative for the lifecycle.
    off = await client.patch(
        f"/scim/v2/Users/{resource}",
        json=patch({"op": "replace", "path": "active", "value": False}),
        headers=world.halden.scim,
    )
    assert off.status_code == 200
    status = await _rows(
        owner_db, org, "SELECT status FROM memberships WHERE id = :m", m=membership_id
    )
    assert status[0][0] == "suspended"


async def test_reinstatement_is_refused_when_it_cannot_be_the_same_identity(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    org = world.halden.org
    membership_id, _, _ = await _removed_with_history(client, world, owner_db)
    await _as(client, "lena@halden.example")
    # Not removed: nothing to reinstate.
    maya = await _member(client, world.maya)
    assert maya["lifecycle"]["reinstate"] is None
    refused = await client.post(
        f"/v1/admin/access/members/{world.maya}/reinstate",
        json={"version": maya["version"], "reason": "r"},
        headers=CSRF,
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["details"] == [{"field": "status", "issue": "not_removed"}]
    # The identity namespace was replaced: the old identity cannot be relinked here.
    await _rows(owner_db, org, "UPDATE oidc_connections SET status = 'disabled'")
    await create_oidc_connection(owner_db, org, issuer="https://new-idp.halden.example")
    access = await _member(client, membership_id)
    assert access["lifecycle"]["reinstate"] == {
        "available": False,
        "blocked_by": "identity_not_current",
    }
    for path, body in (
        ("reinstate/preview", None),
        ("reinstate", {"version": access["version"], "reason": "r"}),
    ):
        response = await client.post(
            f"/v1/admin/access/members/{membership_id}/{path}", json=body, headers=CSRF
        )
        assert response.status_code == 422
        assert response.json()["error"]["details"] == [
            {"field": "identity", "issue": "identity_not_current"}
        ]
    status = await _rows(
        owner_db, org, "SELECT status FROM memberships WHERE id = :m", m=membership_id
    )
    assert status[0][0] == "removed"


async def test_the_operator_reinstatement_still_restores_prior_access(
    client: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    """The deliberate divergence (C18): the operator's path reverses an incident."""
    from lattice.identity import admin

    membership_id, _, _ = await _removed_with_history(client, world, owner_db)
    async with owner_db.tenant_transaction(world.halden.org) as s:
        await admin.reinstate_member(
            s,
            world.halden.org,
            email="pat@halden.example",
            reason="IdP error",
            actor="operator:cli",
        )
    state = await _rows(
        owner_db,
        world.halden.org,
        "SELECT status, is_admin, clearance, (SELECT count(*) FROM account_assignments "
        "WHERE membership_id = :m) FROM memberships WHERE id = :m",
        m=membership_id,
    )
    assert tuple(state[0]) == ("active", True, "confidential", 1)


# --- the fenced functions themselves ----------------------------------------------------------


async def _call(app_db: Database, org: uuid.UUID | None, sql: str, **params: Any) -> Any:
    if org is None:
        async with app_db.unscoped_transaction() as s:
            return (await s.execute(text(sql), params)).scalar_one()
    async with app_db.tenant_transaction(org) as s:
        return (await s.execute(text(sql), params)).scalar_one()


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT lattice_set_admin(:actor, :target, true)", "not_administrator"),
        (
            "SELECT lattice_create_member(:actor, 'a@b.example', 'A', NULL, NULL, 'id-1')",
            "not_administrator",
        ),
        ("SELECT lattice_reinstate_member(:actor, :target)", "not_administrator"),
    ],
)
async def test_the_functions_refuse_an_actor_who_is_not_an_administrator_here(
    world: World, app_db: Database, sql: str, message: str
) -> None:
    # Maya (a member) and Victor (Veridian's administrator) both fail in Halden.
    for actor in (world.maya, world.veridian.admins["victor"]):
        with pytest.raises(DBAPIError, match=message):
            await _call(app_db, world.halden.org, sql, actor=actor, target=world.maya)


async def test_the_functions_need_a_bound_organization(world: World, app_db: Database) -> None:
    with pytest.raises(DBAPIError, match="no organization is bound"):
        await _call(
            app_db,
            None,
            "SELECT lattice_set_admin(:a, :t, true)",
            a=world.halden.admins["lena"],
            t=world.maya,
        )


async def test_a_function_write_cannot_commit_without_its_event(
    world: World, app_db: Database, owner_db: Database
) -> None:
    with pytest.raises(DBAPIError, match="without an audit event"):
        await _call(
            app_db,
            world.halden.org,
            "SELECT lattice_set_admin(:a, :t, true)",
            a=world.halden.admins["lena"],
            t=world.maya,
        )
    assert await _active_admins(owner_db, world.halden.org) == 2


async def test_the_function_refuses_the_actors_own_membership(
    world: World, app_db: Database
) -> None:
    lena = world.halden.admins["lena"]
    with pytest.raises(DBAPIError, match="own_access"):
        await _call(app_db, world.halden.org, "SELECT lattice_set_admin(:a, :a, false)", a=lena)
