"""Pen-test style isolation review, as a test (Phase 10; SEC-2, SEC-5, SEC-15).

1. **Inventory.** Every route the API serves is classified here as public, member,
   administrator or SCIM. A new route fails this test until it is classified -- and so
   until it is swept by the checks below.
2. **Anonymous callers.** Every member and administrator route answers 401 without a
   session; every SCIM route answers 401 without a token.
3. **Members.** Every administrator route answers the same 403 to a member, for an
   existing target and a missing one.
4. **Other organizations.** Every route that takes an identifier answers another
   organization's real identifier exactly as it answers a random one (status and body),
   for an administrator of the other organization -- and changes nothing.
"""

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import text

from lattice.api.app import create_app
from lattice.db.engine import Database

from ..conftest import CSRF, AppFactory, make_settings
from ..factories import add_grant, create_oidc_connection, create_provisioned_member, login
from ..retrieval.world import Org, new_org

PUBLIC = {
    ("GET", "/v1/health"),
    ("GET", "/v1/health/live"),
    ("GET", "/v1/health/ready"),
    ("POST", "/v1/auth/dev-login"),
    ("GET", "/v1/auth/oidc/{login_key}/start"),
    ("GET", "/v1/auth/oidc/{login_key}/callback"),
}
MEMBER = {
    ("GET", "/v1/me"),
    ("POST", "/v1/auth/logout"),
    ("POST", "/v1/ask"),
    ("GET", "/v1/evidence/{evidence_id}"),
    ("GET", "/v1/traces/{trace_id}"),
    ("GET", "/v1/sources"),
    ("GET", "/v1/activity"),
    ("GET", "/v1/accounts"),
}
ADMIN = {
    ("GET", "/v1/admin/providers"),
    ("GET", "/v1/admin/sources"),
    ("GET", "/v1/admin/sources/{source_id}/failures"),
    ("POST", "/v1/admin/sources/{source_id}/sync"),
    ("GET", "/v1/admin/activity"),
    ("GET", "/v1/admin/access/members"),
    ("GET", "/v1/admin/access/directory"),
    ("GET", "/v1/admin/access/members/{membership_id}"),
    ("POST", "/v1/admin/access/members/{membership_id}/preview"),
    ("PATCH", "/v1/admin/access/members/{membership_id}"),
    ("GET", "/v1/admin/access/documents"),
    ("GET", "/v1/admin/access/members/{membership_id}/documents/{document_id}"),
    ("POST", "/v1/admin/access/members/{membership_id}/grants"),
    ("POST", "/v1/admin/access/grants/{grant_id}/revoke"),
    ("GET", "/v1/admin/audit-events"),
    # Phase 12 (P12.1): the setup record and the organization's name.
    ("GET", "/v1/admin/setup"),
    ("PATCH", "/v1/admin/organization"),
    # Phase 12 (P12.2): members added by hand, the administrator capability, reinstatement.
    ("GET", "/v1/admin/access/identity-requirement"),
    ("POST", "/v1/admin/access/members"),
    ("POST", "/v1/admin/access/members/{membership_id}/administrator/preview"),
    ("POST", "/v1/admin/access/members/{membership_id}/administrator"),
    ("POST", "/v1/admin/access/members/{membership_id}/reinstate/preview"),
    ("POST", "/v1/admin/access/members/{membership_id}/reinstate"),
    # Phase 12 (P12.3): accounts and their external-reference mappings.
    ("GET", "/v1/admin/access/accounts"),
    ("POST", "/v1/admin/access/accounts"),
    ("GET", "/v1/admin/access/accounts/{account_id}"),
    ("PATCH", "/v1/admin/access/accounts/{account_id}"),
    ("POST", "/v1/admin/access/accounts/{account_id}/archive"),
    ("POST", "/v1/admin/access/accounts/{account_id}/reactivate"),
    ("GET", "/v1/admin/access/account-references/unmapped"),
    ("POST", "/v1/admin/access/account-references/preview"),
    ("POST", "/v1/admin/access/account-references"),
    ("POST", "/v1/admin/access/account-references/remap"),
    ("POST", "/v1/admin/access/account-references/unmap"),
    # Phase 12 (P12.4): identity administration (OIDC connection, SCIM tokens).
    ("GET", "/v1/admin/identity"),
    ("POST", "/v1/admin/identity/oidc"),
    ("PATCH", "/v1/admin/identity/oidc/{connection_id}"),
    ("PUT", "/v1/admin/identity/oidc/{connection_id}/client-secret"),
    ("POST", "/v1/admin/identity/oidc/{connection_id}/client-secret/remove"),
    ("POST", "/v1/admin/identity/oidc/{connection_id}/status/preview"),
    ("POST", "/v1/admin/identity/oidc/{connection_id}/status"),
    ("POST", "/v1/admin/identity/oidc/{connection_id}/identity/preview"),
    ("POST", "/v1/admin/identity/oidc/{connection_id}/identity"),
    ("POST", "/v1/admin/identity/oidc/{connection_id}/check"),
    ("POST", "/v1/admin/identity/oidc/test-sign-in"),
    ("GET", "/v1/admin/identity/oidc/test-sign-in/{test_id}"),
    ("POST", "/v1/admin/identity/scim/tokens"),
    ("POST", "/v1/admin/identity/scim/tokens/{token_id}/rotate"),
    ("POST", "/v1/admin/identity/scim/tokens/{token_id}/revoke/preview"),
    ("POST", "/v1/admin/identity/scim/tokens/{token_id}/revoke"),
}
SCIM = {
    ("GET", "/scim/v2/ServiceProviderConfig"),
    ("GET", "/scim/v2/ResourceTypes"),
    ("GET", "/scim/v2/Schemas"),
    ("GET", "/scim/v2/Users"),
    ("POST", "/scim/v2/Users"),
    ("GET", "/scim/v2/Users/{resource_id}"),
    ("PUT", "/scim/v2/Users/{resource_id}"),
    ("PATCH", "/scim/v2/Users/{resource_id}"),
    ("DELETE", "/scim/v2/Users/{resource_id}"),
}
_REFERENCE = {
    "source_id": str(uuid.uuid4()),
    "ref_kind": "ticketing_organization",
    "external_ref": "360001001",
}
# Valid bodies, so that a refusal is about authority, never about the request's shape.
BODIES: dict[tuple[str, str], Any] = {
    ("POST", "/v1/ask"): {"question": "What does the onboarding checklist say?"},
    ("POST", "/v1/admin/sources/{source_id}/sync"): None,
    ("POST", "/v1/admin/access/members/{membership_id}/preview"): {"clearance": "confidential"},
    ("PATCH", "/v1/admin/access/members/{membership_id}"): {
        "clearance": "confidential",
        "version": "2026-10-02T00:00:00Z",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/members/{membership_id}/grants"): {
        "document_id": str(uuid.uuid4()),
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/grants/{grant_id}/revoke"): {"reason": "Probe"},
    ("PATCH", "/v1/admin/organization"): {
        "name": "Probe",
        "version": "2026-10-02T00:00:00Z",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/members"): {
        "email": "probe@halden.example",
        "name": "Probe",
        "identifier": "probe",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/members/{membership_id}/administrator/preview"): {
        "administrator": True
    },
    ("POST", "/v1/admin/access/members/{membership_id}/administrator"): {
        "administrator": True,
        "version": "2026-10-02T00:00:00Z",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/members/{membership_id}/reinstate/preview"): None,
    ("POST", "/v1/admin/access/members/{membership_id}/reinstate"): {
        "version": "2026-10-02T00:00:00Z",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/accounts"): {"key": "probe", "name": "Probe", "reason": "Probe"},
    ("PATCH", "/v1/admin/access/accounts/{account_id}"): {
        "version": "2026-10-02T00:00:00Z",
        "name": "Probe",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/accounts/{account_id}/archive"): {
        "version": "2026-10-02T00:00:00Z",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/accounts/{account_id}/reactivate"): {
        "version": "2026-10-02T00:00:00Z",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/account-references/preview"): {
        **_REFERENCE,
        "account_id": None,
    },
    ("POST", "/v1/admin/access/account-references"): {
        **_REFERENCE,
        "account_id": str(uuid.uuid4()),
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/account-references/remap"): {
        **_REFERENCE,
        "current_account_id": str(uuid.uuid4()),
        "account_id": str(uuid.uuid4()),
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/account-references/unmap"): {
        **_REFERENCE,
        "current_account_id": str(uuid.uuid4()),
        "reason": "Probe",
    },
}
_VERSIONED = {"version": "2026-10-02T00:00:00Z", "reason": "Probe"}
BODIES |= {
    ("POST", "/v1/admin/identity/oidc"): {
        "issuer": "https://idp.probe.example",
        "client_id": "probe",
        "reason": "Probe",
    },
    ("PATCH", "/v1/admin/identity/oidc/{connection_id}"): {"client_id": "probe", **_VERSIONED},
    ("PUT", "/v1/admin/identity/oidc/{connection_id}/client-secret"): {
        "client_secret": "probe-secret-value",
        **_VERSIONED,
    },
    ("POST", "/v1/admin/identity/oidc/{connection_id}/client-secret/remove"): _VERSIONED,
    ("POST", "/v1/admin/identity/oidc/{connection_id}/status/preview"): {"enabled": False},
    ("POST", "/v1/admin/identity/oidc/{connection_id}/status"): {"enabled": False, **_VERSIONED},
    ("POST", "/v1/admin/identity/oidc/{connection_id}/identity/preview"): {
        "subject_claim": "email"
    },
    ("POST", "/v1/admin/identity/oidc/{connection_id}/identity"): {
        "issuer": "https://idp.probe.example",
        "subject_claim": "sub",
        "proof_id": str(uuid.uuid4()),
        "confirmation": "https://idp.probe.example",
        **_VERSIONED,
    },
    ("POST", "/v1/admin/identity/oidc/{connection_id}/check"): None,
    ("POST", "/v1/admin/identity/oidc/test-sign-in"): {},
    ("POST", "/v1/admin/identity/scim/tokens"): {"reason": "Probe"},
    ("POST", "/v1/admin/identity/scim/tokens/{token_id}/rotate"): {"reason": "Probe"},
    ("POST", "/v1/admin/identity/scim/tokens/{token_id}/revoke/preview"): None,
    ("POST", "/v1/admin/identity/scim/tokens/{token_id}/revoke"): {"reason": "Probe"},
}
QUERIES = {("GET", "/v1/admin/access/documents"): "?q=onboarding"}


def _routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every (method, path) the app serves. This FastAPI includes routers lazily (an
    internal wrapper holding ``original_router``): walk into them. None of the routers
    is included with an extra prefix, so each route's own path is the served path."""
    found: set[tuple[str, str]] = set()
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        included = getattr(route, "original_router", None)
        if included is not None:
            pending.extend(included.routes)
        elif isinstance(route, APIRoute):
            found |= {(method, route.path) for method in route.methods or ()}
    return found


def _url(path: str, ids: dict[str, str]) -> str:
    for name, value in ids.items():
        path = path.replace("{" + name + "}", value)
    return path


def _random_ids() -> dict[str, str]:
    names = (
        "evidence_id",
        "trace_id",
        "source_id",
        "membership_id",
        "document_id",
        "grant_id",
        "account_id",
        "connection_id",
        "token_id",
        "test_id",
    )
    return {name: str(uuid.uuid4()) for name in names} | {"resource_id": str(uuid.uuid4())}


def _comparable(response: httpx.Response) -> tuple[int, Any]:
    try:
        body = response.json()
    except ValueError:
        return response.status_code, response.text
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body["error"].pop("request_id", None)
    return response.status_code, body


async def _call(
    client: httpx.AsyncClient, method: str, path: str, ids: dict[str, str]
) -> httpx.Response:
    url = _url(path, ids) + QUERIES.get((method, path), "")
    body = BODIES.get((method, path))
    if (method, path) == ("POST", "/v1/admin/access/members/{membership_id}/grants"):
        body = {"document_id": ids["document_id"], "reason": "Probe"}
    return await client.request(method, url, json=body, headers=CSRF)


def test_every_route_is_classified() -> None:
    app = create_app(make_settings())
    served = _routes(app)
    classified = PUBLIC | MEMBER | ADMIN | SCIM
    assert served - classified == set(), "classify the new route (and so sweep it)"
    assert classified - served == set(), "a classified route no longer exists"
    groups = (PUBLIC, MEMBER, ADMIN, SCIM)
    assert sum(len(g) for g in groups) == len(classified), "a route is in two classes"


@pytest.mark.parametrize(("method", "path"), sorted(MEMBER | ADMIN))
async def test_anonymous_callers_are_refused(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await _call(client, method, path, _random_ids())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize(("method", "path"), sorted(SCIM))
async def test_scim_needs_its_token(client: httpx.AsyncClient, method: str, path: str) -> None:
    body: dict[str, Any] | None = {"schemas": []} if method in ("POST", "PUT", "PATCH") else None
    response = await client.request(method, _url(path, _random_ids()), json=body)
    assert response.status_code == 401
    assert response.json()["status"] == "401"


@dataclass
class Worlds:
    halden: Org
    veridian: Org
    halden_ids: dict[str, str]


@pytest.fixture
async def worlds(owner_db: Database, app_client: AppFactory) -> Worlds:
    halden = await new_org(owner_db, "halden")
    veridian = await new_org(owner_db, "veridian")
    await halden.member("maya")
    await halden.member("lena")
    await halden.execute(
        "UPDATE memberships SET is_admin = true WHERE id = :m", m=halden.members["lena"]
    )
    await veridian.member("victor")
    await veridian.member("vera")
    await veridian.execute(
        "UPDATE memberships SET is_admin = true WHERE id = :m", m=veridian.members["victor"]
    )
    await halden.document("checklist", "Onboarding checklist", ["Five steps to onboard."])
    await halden.account("acme", "Acme Corp")
    await add_grant(
        owner_db, halden.id, halden.documents["checklist"], "membership", halden.members["maya"]
    )
    async with owner_db.tenant_transaction(halden.id) as s:
        grant: uuid.UUID = (await s.execute(text("SELECT id FROM document_grants"))).scalar_one()
    connection = await create_oidc_connection(owner_db, halden.id)
    await create_provisioned_member(owner_db, halden.id, connection, "pat@halden.example", "x")
    from ..factories import create_scim_token

    await create_scim_token(owner_db, halden.id, connection)
    async with owner_db.tenant_transaction(halden.id) as s:
        token_id: uuid.UUID = (await s.execute(text("SELECT id FROM scim_tokens"))).scalar_one()
        # A test sign-in of Lena's (P12.4), still pending.
        test_id: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO oidc_login_attempts (organization_id, connection_id, "
                    "state_hash, binding_hash, nonce, code_verifier, expires_at, purpose, "
                    "membership_id) VALUES (:o, :c, sha256('s'::bytea), sha256('b'::bytea), "
                    "repeat('n', 43), repeat('v', 43), now() + interval '10 minutes', 'test', "
                    ":m) RETURNING id"
                ),
                {"o": halden.id, "c": connection, "m": halden.members["lena"]},
            )
        ).scalar_one()
    async with owner_db.tenant_transaction(halden.id) as s:
        resource: uuid.UUID = (
            await s.execute(text("SELECT id FROM directory_identities"))
        ).scalar_one()
    # A trace of Maya's.
    async with app_client() as c:
        assert (await login(c, "maya@halden.example")).status_code == 200
        answer = await c.post("/v1/ask", json=BODIES[("POST", "/v1/ask")], headers=CSRF)
        trace = answer.json()["trace_id"]
    ids = {
        "evidence_id": str(halden.chunks["checklist"][0]),
        "trace_id": str(trace),
        "source_id": str(halden.source_id),
        "membership_id": str(halden.members["maya"]),
        "document_id": str(halden.documents["checklist"]),
        "grant_id": str(grant),
        "resource_id": str(resource),
        "account_id": str(halden.accounts["acme"]),
        "connection_id": str(connection),
        "token_id": str(token_id),
        "test_id": str(test_id),
    }
    return Worlds(halden, veridian, ids)


_IDENTIFIED = sorted((m, p) for m, p in MEMBER | ADMIN if "{" in p)


@pytest.mark.parametrize(("method", "path"), _IDENTIFIED)
async def test_another_organizations_identifiers_are_unknown(
    client: httpx.AsyncClient, worlds: Worlds, owner_db: Database, method: str, path: str
) -> None:
    assert (await login(client, "victor@veridian.example")).status_code == 200
    foreign = await _call(client, method, path, worlds.halden_ids)
    missing = await _call(client, method, path, _random_ids())
    assert foreign.status_code == 404
    assert _comparable(foreign) == _comparable(missing)
    # Veridian's own member combined with Halden's document is unknown as well.
    if "{document_id}" in path:
        mixed = await _call(
            client,
            method,
            path,
            {**worlds.halden_ids, "membership_id": str(worlds.veridian.members["vera"])},
        )
        assert _comparable(mixed)[0] == 404
    # Nothing of Halden's changed.
    async with owner_db.tenant_transaction(worlds.halden.id) as s:
        state = (
            await s.execute(
                text(
                    "SELECT (SELECT count(*) FROM document_grants), (SELECT clearance FROM "
                    "memberships WHERE id = :m), (SELECT count(*) FROM jobs), "
                    "(SELECT count(*) FROM audit_events WHERE action LIKE 'access.%' "
                    "OR action LIKE 'account.%' OR action LIKE 'sso.%' "
                    "OR action LIKE 'scim.%'), "
                    "(SELECT count(*) FROM scim_tokens WHERE revoked_at IS NULL), "
                    "(SELECT client_id || status FROM oidc_connections)"
                ),
                {"m": uuid.UUID(worlds.halden_ids["membership_id"])},
            )
        ).one()
    assert tuple(state) == (1, "internal", 0, 0, 1, "latticeactive")


@pytest.mark.parametrize(("method", "path"), sorted(ADMIN))
async def test_members_get_one_403_from_every_administrator_route(
    client: httpx.AsyncClient, worlds: Worlds, method: str, path: str
) -> None:
    assert (await login(client, "maya@halden.example")).status_code == 200
    own = await _call(client, method, path, worlds.halden_ids)
    missing = await _call(client, method, path, _random_ids())
    assert own.status_code == 403
    assert _comparable(own) == _comparable(missing)


async def test_a_scim_token_cannot_reach_another_organization(
    client: httpx.AsyncClient, worlds: Worlds, owner_db: Database
) -> None:
    from ..factories import create_scim_token

    connection = await create_oidc_connection(owner_db, worlds.veridian.id)
    token = await create_scim_token(owner_db, worlds.veridian.id, connection)
    auth = {"Authorization": f"Bearer {token}"}
    for method in ("GET", "DELETE"):
        foreign = await client.request(
            method, f"/scim/v2/Users/{worlds.halden_ids['resource_id']}", headers=auth
        )
        missing = await client.request(method, f"/scim/v2/Users/{uuid.uuid4()}", headers=auth)
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()
    listed = (await client.get("/scim/v2/Users", headers=auth)).json()
    assert listed["totalResults"] == 0


async def test_identity_metrics_carry_closed_labels_only(
    client: httpx.AsyncClient, worlds: Worlds
) -> None:
    """SCIM and OIDC counters: operation, status class and outcome codes only."""
    from prometheus_client import generate_latest

    from lattice import metrics

    await client.get("/scim/v2/Users", headers={"Authorization": "Bearer nope"})
    await client.get("/v1/auth/oidc/not-a-real-login-key-000000/start")
    exposition = generate_latest(metrics.REGISTRY).decode()
    for line in exposition.splitlines():
        if not line.startswith(("lattice_scim_operations", "lattice_sso_sign_ins")):
            continue
        labels = line[line.index("{") + 1 : line.index("}")]
        for pair in labels.split(","):
            name, _, value = pair.partition("=")
            value = value.strip('"')
            allowed = {
                "operation": metrics.SCIM_OPERATIONS_SET | {metrics.OTHER},
                "status": {"2xx", "4xx", "5xx"},
                "outcome": metrics.SSO_OUTCOMES | {metrics.OTHER},
            }[name]
            assert value in allowed, line
