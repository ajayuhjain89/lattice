"""Identity administration in the product (Phase 12, P12.4; phase-12-plan C3, C4, C5,
C11, C12; ADR-017), against an in-process identity provider.

Proves, for the OIDC connection: administrators of the organization only; the client
secret is sealed in the vault, rotated with the old ciphertext destroyed, never returned,
logged, audited or echoed; client-ID and secret changes keep sessions, disabling ends
them and stops sign-in, enabling restores it; a stale screen gets 409; an issuer or claim
change needs the administrator's own matched test sign-in under exactly the new values
(a changed-subject migration is refused) and ends every session; a test sign-in creates
no session and shows only the administrator's own identifier; the check tells valid,
invalid configuration, rejected credentials, an unavailable provider and the outbound
policy apart -- and a bogus authorization code never marks the provider; a private issuer
works only while an operator registration exists. For SCIM: tokens shown once and kept
as hashes, rotated and revoked; ``last_used_at``; ``scim.request_refused`` only for
authenticated, actionable refusals, rate-limited; a hand-added member is never taken over;
a re-hired person resolves to the same identity."""

import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice.db.engine import Database
from lattice.identity.oidc import OidcSignIn
from lattice.orgs.outbound_targets import add_target, revoke_target
from lattice.outbound import parse_target
from lattice.providers.secrets import EnvironmentSecretStore

from ..conftest import CSRF, AppFactory
from ..factories import (
    create_member,
    create_oidc_connection,
    create_org,
    create_provisioned_member,
    login,
)
from .fake_idp import FakeIdp, oidc_client, public_resolver, resolving_to

KEY = "halden-login-key-0123456789"
ISSUER = "https://idp.halden.example"
NEW_ISSUER = "https://login.halden.example"
KEYS = {
    "LATTICE_SECRET_KEYS": "a:QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=",
    "LATTICE_SECRET_ACTIVE_KEY": "a",
}
SECRET = "client-secret-0001-very-secret-value"
NEW_SECRET = "client-secret-0002-another-secret-value"
API = "/v1/admin/identity"


@dataclass
class World:
    org: uuid.UUID
    connection: uuid.UUID
    lena: uuid.UUID  # administrator, bound to the connection as "sub-lena"
    pat: uuid.UUID  # provisioned member, "sub-pat"


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
async def world(owner_db: Database) -> World:
    org = await create_org(owner_db, "halden")
    connection = await create_oidc_connection(owner_db, org, issuer=ISSUER, login_key=KEY)
    lena = await create_member(owner_db, org, "lena@halden.example", is_admin=True)
    await create_member(owner_db, org, "olga@halden.example", is_admin=True)
    await create_member(owner_db, org, "maya@halden.example")
    async with owner_db.tenant_transaction(org) as s:
        await s.execute(
            text(
                "INSERT INTO directory_identities (organization_id, connection_id, "
                "membership_id, external_id, user_name, managed_by) "
                "VALUES (:o, :c, :m, 'sub-lena', 'lena@halden.example', 'organization')"
            ),
            {"o": org, "c": connection, "m": lena.membership_id},
        )
    pat = await create_provisioned_member(
        owner_db, org, connection, "pat@halden.example", "sub-pat"
    )
    return World(org, connection, lena.membership_id, pat.membership_id)


class Idps:
    """Identity providers by host; the current one answers the configured issuer."""

    def __init__(self, *found: FakeIdp) -> None:
        self.by_host = {urlsplit(i.issuer).hostname: i for i in found}

    def handler(self, request: httpx.Request) -> httpx.Response:
        idp = self.by_host.get(request.url.host)
        if idp is None:
            raise httpx.ConnectError("unknown host", request=request)
        return idp.handler(request)

    def __getitem__(self, issuer: str) -> FakeIdp:
        found = self.by_host[urlsplit(issuer).hostname]
        return found


def _configure(idps: Idps, resolver: Callable[..., Any] = public_resolver) -> Any:
    def configure(app: FastAPI) -> None:
        app.state.oidc = OidcSignIn(
            app.state.db,
            oidc_client(idps.handler, resolver=resolver),
            web_origin="http://127.0.0.1:3000",
            secrets=EnvironmentSecretStore(keyring=app.state.keyring),
        )

    return configure


@pytest.fixture
def idps() -> Idps:
    return Idps(FakeIdp(issuer=ISSUER), FakeIdp(issuer=NEW_ISSUER))


@pytest.fixture
async def admin(
    app_client: AppFactory, idps: Idps, world: World
) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(_configure(idps)) as client:
        assert (await login(client, "lena@halden.example")).status_code == 200
        yield client


def _callback(url: str) -> str:
    parts = urlsplit(url)
    return parts.path.removeprefix("/api") + "?" + parts.query


async def _oidc_sign_in(client: httpx.AsyncClient, idp: FakeIdp, subject: str) -> httpx.Response:
    start = await client.get(f"/v1/auth/oidc/{KEY}/start")
    assert start.status_code == 303
    location = start.headers["location"]
    if location.startswith("/sign-in"):
        return start
    return await client.get(_callback(idp.authorize(location, subject)))


async def _identity(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get(API)
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


async def _version(client: httpx.AsyncClient) -> str:
    oidc = (await _identity(client))["oidc"]
    assert oidc is not None
    return str(oidc["version"])


async def _post(client: httpx.AsyncClient, path: str, body: Any = None) -> httpx.Response:
    return await client.post(f"{API}{path}", json=body, headers=CSRF)


async def _test_sign_in(
    client: httpx.AsyncClient, idp: FakeIdp, subject: str, **proposed: str
) -> dict[str, Any]:
    """An administrator's test sign-in, followed to the end: its result."""
    started = await _post(client, "/oidc/test-sign-in", proposed)
    assert started.status_code == 201, started.text
    assert started.headers["cache-control"] == "no-store"
    body = started.json()
    query = {k: v[0] for k, v in parse_qs(urlsplit(body["authorization_url"]).query).items()}
    assert (query["prompt"], query["max_age"]) == ("login", "0")  # a fresh authentication
    back = await client.get(_callback(idp.authorize(body["authorization_url"], subject)))
    assert back.status_code == 303
    assert back.headers["location"] == f"/settings/identity?test={body['test_id']}"
    assert "lattice_session" not in back.headers.get("set-cookie", "")  # no session
    result = await client.get(f"{API}/oidc/test-sign-in/{body['test_id']}")
    assert result.status_code == 200
    found: dict[str, Any] = result.json()
    return found


async def _rows(db: Database, org: uuid.UUID, sql: str, **params: Any) -> list[Any]:
    async with db.tenant_transaction(org) as s:
        return list((await s.execute(text(sql), params)).all())


async def _events(db: Database, org: uuid.UUID, like: str) -> list[Any]:
    return await _rows(
        db,
        org,
        "SELECT actor, action, target, before, after, reason FROM audit_events "
        "WHERE action LIKE :a ORDER BY created_at, id",
        a=like,
    )


# --- reading -------------------------------------------------------------------------------


async def test_administrators_read_the_connection_without_any_secret(
    admin: httpx.AsyncClient, world: World, app_client: AppFactory, idps: Idps
) -> None:
    body = await _identity(admin)
    oidc = body["oidc"]
    assert (oidc["status"], oidc["issuer"], oidc["client_id"], oidc["subject_claim"]) == (
        "active",
        ISSUER,
        "lattice",
        "sub",
    )
    assert oidc["client_secret"] == {"state": "none", "set_at": None}
    assert oidc["issuer_access"] == "public"
    assert oidc["sign_in_url"] == f"http://127.0.0.1:3000/sso/{KEY}"
    assert oidc["redirect_uri"] == f"http://127.0.0.1:3000/api/v1/auth/oidc/{KEY}/callback"
    assert (oidc["members_bound"], body["can_add_connection"]) == (2, False)
    assert (body["sign_in_status"]["state"], body["sign_in_status"]["reason"]) == (
        "required",
        "not_verified",
    )
    assert body["scim"]["base_url"] == "http://127.0.0.1:3000/api/scim/v2"
    assert body["scim"]["identifying_claim"] == "sub"
    # Members: 403.
    async with app_client(_configure(idps)) as member:
        assert (await login(member, "maya@halden.example")).status_code == 200
        assert (await member.get(API)).status_code == 403
        assert (await _post(member, "/scim/tokens", {"reason": "x"})).status_code == 403


# --- the client secret -------------------------------------------------------------------------


async def test_the_client_secret_is_sealed_rotated_and_never_returned(
    admin: httpx.AsyncClient,
    world: World,
    owner_db: Database,
    idps: Idps,
    app_client: AppFactory,
    capfd: pytest.CaptureFixture[str],
) -> None:
    version = await _version(admin)
    put = await admin.put(
        f"{API}/oidc/{world.connection}/client-secret",
        json={"client_secret": SECRET, "version": version, "reason": "Confidential client"},
        headers=CSRF,
    )
    assert put.status_code == 200, put.text
    assert put.headers["cache-control"] == "no-store"
    assert SECRET not in put.text
    assert "vault:" not in put.text
    assert put.json()["oidc"]["client_secret"]["state"] == "encrypted"
    assert put.json()["oidc"]["client_secret"]["set_at"] is not None
    # A stale screen: 409, nothing changed.
    stale = await admin.put(
        f"{API}/oidc/{world.connection}/client-secret",
        json={"client_secret": NEW_SECRET, "version": version, "reason": "Rotate"},
        headers=CSRF,
    )
    assert stale.status_code == 409
    [(reference,)] = await _rows(
        owner_db, world.org, "SELECT client_secret_ref FROM oidc_connections"
    )
    assert reference.startswith("vault:")

    # Sign-in presents it (the provider checks the client secret).
    idps[ISSUER].client_secret = SECRET
    async with app_client(_configure(idps)) as pat:
        assert (await _oidc_sign_in(pat, idps[ISSUER], "sub-pat")).headers["location"] == "/ask"

    # Rotation: a new ciphertext; the old one destroyed; sessions kept.
    rotated = await admin.put(
        f"{API}/oidc/{world.connection}/client-secret",
        json={"client_secret": NEW_SECRET, "version": await _version(admin), "reason": "Rotate"},
        headers=CSRF,
    )
    assert rotated.status_code == 200
    rows = await _rows(
        owner_db,
        world.org,
        "SELECT id, destroyed_at IS NOT NULL AS gone, ciphertext IS NULL AS empty "
        "FROM encrypted_secrets WHERE owner_id = :c ORDER BY created_at",
        c=world.connection,
    )
    assert [(r.gone, r.empty) for r in rows] == [(True, True), (False, False)]
    [(reference,)] = await _rows(
        owner_db, world.org, "SELECT client_secret_ref FROM oidc_connections"
    )
    assert reference == f"vault:{rows[1].id}"
    async with app_client(_configure(idps)) as pat:
        # The provider still expects the old secret: the client is rejected now.
        failed = await _oidc_sign_in(pat, idps[ISSUER], "sub-pat")
        assert failed.headers["location"] == "/sign-in?error=sso_unavailable"
        idps[ISSUER].client_secret = NEW_SECRET
        assert (await _oidc_sign_in(pat, idps[ISSUER], "sub-pat")).headers["location"] == "/ask"
    [(code,)] = await _rows(owner_db, world.org, "SELECT last_failure_code FROM oidc_connections")
    assert code == "credentials_rejected"

    # Removed: a public client; the ciphertext destroyed.
    removed = await _post(
        admin,
        f"/oidc/{world.connection}/client-secret/remove",
        {"version": await _version(admin), "reason": "PKCE only"},
    )
    assert removed.status_code == 200
    assert removed.json()["oidc"]["client_secret"]["state"] == "none"
    assert (
        await _rows(
            owner_db,
            world.org,
            "SELECT id FROM encrypted_secrets WHERE owner_id = :c AND destroyed_at IS NULL",
            c=world.connection,
        )
        == []
    )

    # Never in an event, a response or a log line; validation errors name the field only.
    events = await _events(owner_db, world.org, "sso.%")
    assert [e.action for e in events] == ["sso.connection_updated"] * 3
    assert [(e.before, e.after) for e in events] == [
        ({"client_secret": "none"}, {"client_secret": "encrypted"}),
        ({"client_secret": "encrypted"}, {"client_secret": "encrypted (replaced)"}),
        ({"client_secret": "encrypted"}, {"client_secret": "none"}),
    ]
    marker = "secret-marker-\x00-never-echoed"
    invalid = await admin.put(
        f"{API}/oidc/{world.connection}/client-secret",
        json={"client_secret": marker, "version": await _version(admin), "reason": "x"},
        headers=CSRF,
    )
    assert invalid.status_code == 422
    assert "secret-marker" not in invalid.text
    logged = "".join(capfd.readouterr())
    for value in (SECRET, NEW_SECRET, "secret-marker"):
        assert value not in logged
    assert SECRET not in json.dumps([list(e) for e in events], default=str)


# --- client ID, disable, enable ----------------------------------------------------------------


async def test_client_id_keeps_sessions_disable_ends_them_enable_restores_sign_in(
    admin: httpx.AsyncClient, world: World, owner_db: Database, idps: Idps, app_client: AppFactory
) -> None:
    async with app_client(_configure(idps)) as pat:
        assert (await _oidc_sign_in(pat, idps[ISSUER], "sub-pat")).headers["location"] == "/ask"
        changed = await admin.patch(
            f"{API}/oidc/{world.connection}",
            json={"client_id": "lattice-2", "version": await _version(admin), "reason": "New app"},
            headers=CSRF,
        )
        assert changed.status_code == 200
        assert (await pat.get("/v1/me")).status_code == 200  # kept
        back = await admin.patch(
            f"{API}/oidc/{world.connection}",
            json={"client_id": "lattice", "version": await _version(admin), "reason": "Back"},
            headers=CSRF,
        )
        assert back.status_code == 200

        preview = await _post(admin, f"/oidc/{world.connection}/status/preview", {"enabled": False})
        assert preview.json() == {
            "enabled": False,
            "sessions_ending": 1,
            "members_bound": 2,
            "ends_own_session": False,  # Lena signed in with development sign-in
        }
        disabled = await _post(
            admin,
            f"/oidc/{world.connection}/status",
            {"enabled": False, "version": await _version(admin), "reason": "Incident"},
        )
        assert disabled.status_code == 200
        body = disabled.json()
        assert (body["oidc"]["status"], body["sign_in_status"]["reason"]) == (
            "disabled",
            "connection_disabled",
        )
        assert (await pat.get("/v1/me")).status_code == 401  # ended at once
        refused = await _oidc_sign_in(pat, idps[ISSUER], "sub-pat")
        assert refused.headers["location"] == "/sign-in?error=sso_failed"

        enabled = await _post(
            admin,
            f"/oidc/{world.connection}/status",
            {"enabled": True, "version": await _version(admin), "reason": "Resolved"},
        )
        assert enabled.status_code == 200
        assert (await _oidc_sign_in(pat, idps[ISSUER], "sub-pat")).headers["location"] == "/ask"
    events = await _events(owner_db, world.org, "sso.%")
    assert [e.action for e in events] == [
        "sso.connection_updated",
        "sso.connection_updated",
        "sso.connection_disabled",
        "sso.connection_enabled",
    ]
    assert events[2].after == {"status": "disabled", "sessions_revoked": 1}


async def test_only_one_connection_is_active_and_none_strands_members(
    app_client: AppFactory, owner_db: Database, idps: Idps
) -> None:
    org = await create_org(owner_db, "veridian")
    await create_member(owner_db, org, "victor@veridian.example", is_admin=True)
    async with app_client(_configure(idps)) as admin:
        assert (await login(admin, "victor@veridian.example")).status_code == 200
        empty = await _identity(admin)
        assert (empty["oidc"], empty["can_add_connection"]) == (None, True)
        assert empty["sign_in_status"]["reason"] == "no_connection"
        body = {"issuer": ISSUER, "client_id": "veridian", "reason": "Connect the IdP"}
        created = await _post(admin, "/oidc", {**body, "client_secret": SECRET})
        assert created.status_code == 201, created.text
        assert SECRET not in created.text
        oidc = created.json()["oidc"]
        assert (oidc["client_secret"]["state"], oidc["subject_claim"]) == ("encrypted", "sub")
        again = await _post(admin, "/oidc", body)
        assert again.status_code == 409
        assert again.json()["error"]["details"] == [
            {"field": "connection", "issue": "connection_exists"}
        ]
        # A disabled connection that still signs a member in: re-enable it instead.
        connection = uuid.UUID(oidc["id"])
        await create_provisioned_member(owner_db, org, connection, "val@veridian.example", "v")
        await _post(
            admin,
            f"/oidc/{connection}/status",
            {"enabled": False, "version": await _version(admin), "reason": "Moving"},
        )
        stranded = await _post(admin, "/oidc", body)
        assert stranded.status_code == 422
        assert stranded.json()["error"]["details"] == [
            {"field": "connection", "issue": "identity_namespace_in_use"}
        ]
        assert (await _identity(admin))["can_add_connection"] is False


async def test_an_issuer_the_outbound_policy_refuses_cannot_be_entered(
    app_client: AppFactory, owner_db: Database, idps: Idps
) -> None:
    org = await create_org(owner_db, "veridian")
    await create_member(owner_db, org, "victor@veridian.example", is_admin=True)
    async with app_client(_configure(idps, resolving_to("10.0.0.7"))) as admin:
        assert (await login(admin, "victor@veridian.example")).status_code == 200
        for issuer in ("https://idp.internal.example", "http://idp.example", "https://idp:9443"):
            refused = await _post(
                admin, "/oidc", {"issuer": issuer, "client_id": "v", "reason": "Connect"}
            )
            assert refused.status_code == 422, issuer
            assert refused.json()["error"]["details"] == [
                {"field": "issuer", "issue": "outbound_blocked"}
            ]
    assert await _rows(owner_db, org, "SELECT id FROM oidc_connections") == []


# --- issuer and identifying claim ------------------------------------------------------------


async def test_an_issuer_change_needs_the_administrators_matched_test_sign_in(
    admin: httpx.AsyncClient, world: World, owner_db: Database, idps: Idps, app_client: AppFactory
) -> None:
    async with app_client(_configure(idps)) as pat:
        assert (await _oidc_sign_in(pat, idps[ISSUER], "sub-pat")).headers["location"] == "/ask"
        preview = await _post(
            admin, f"/oidc/{world.connection}/identity/preview", {"issuer": NEW_ISSUER}
        )
        assert preview.status_code == 200
        shown = preview.json()
        assert (shown["issuer_changed"], shown["claim_changed"], shown["proof"]) == (
            True,
            False,
            None,
        )
        assert (shown["sessions_ending"], shown["members_bound"]) == (1, 2)
        change = {
            "issuer": NEW_ISSUER,
            "subject_claim": "sub",
            "proof_id": str(uuid.uuid4()),
            "confirmation": NEW_ISSUER,
            "version": await _version(admin),
            "reason": "The provider moved",
        }
        unproven = await _post(admin, f"/oidc/{world.connection}/identity", change)
        assert unproven.status_code == 422
        assert unproven.json()["error"]["details"] == [
            {"field": "proof_id", "issue": "identity_unverified"}
        ]

        # Lena signs in at the new address as herself: the same subject.
        result = await _test_sign_in(admin, idps[NEW_ISSUER], "sub-lena", issuer=NEW_ISSUER)
        assert (result["outcome"], result["identifier"], result["proposed"]) == (
            "matched",
            "sub-lena",
            True,
        )
        assert result["proof_available"] is True
        proof = (
            await _post(admin, f"/oidc/{world.connection}/identity/preview", {"issuer": NEW_ISSUER})
        ).json()["proof"]
        assert proof["test_id"] == result["test_id"]
        mistyped = await _post(
            admin,
            f"/oidc/{world.connection}/identity",
            {**change, "proof_id": proof["test_id"], "confirmation": "yes"},
        )
        assert mistyped.status_code == 422
        applied = await _post(
            admin, f"/oidc/{world.connection}/identity", {**change, "proof_id": proof["test_id"]}
        )
        assert applied.status_code == 200, applied.text
        oidc = applied.json()["oidc"]
        assert oidc["issuer"] == NEW_ISSUER
        assert applied.json()["sign_in_status"]["reason"] == "verified"  # the proof
        assert (await pat.get("/v1/me")).status_code == 401  # every session ended
        assert (await _oidc_sign_in(pat, idps[NEW_ISSUER], "sub-pat")).headers["location"] == "/ask"
        # The proof is single-use.
        reused = await _post(
            admin,
            f"/oidc/{world.connection}/identity",
            {
                **change,
                "issuer": ISSUER,
                "confirmation": ISSUER,
                "proof_id": proof["test_id"],
                "version": oidc["version"],
            },
        )
        assert reused.status_code == 422
    [event] = [e for e in await _events(owner_db, world.org, "sso.connection_updated")]
    assert event.before == {"issuer": ISSUER, "subject_claim": "sub"}
    assert event.after == {
        "issuer": NEW_ISSUER,
        "subject_claim": "sub",
        "sessions_revoked": 1,
        "verified_by_test_sign_in": True,
    }


async def test_a_changed_subject_migration_is_refused(
    admin: httpx.AsyncClient, world: World, owner_db: Database, idps: Idps
) -> None:
    """Switching the identifying claim to one whose values differ (here ``email``) would
    make every stored identity unusable: the test shows the mismatch, nothing applies,
    and no identity is remapped by e-mail."""
    idps[ISSUER].claims = {"email": "lena@halden.example"}
    result = await _test_sign_in(admin, idps[ISSUER], "sub-lena", subject_claim="email")
    assert (result["outcome"], result["identifier"], result["proof_available"]) == (
        "mismatched",
        "lena@halden.example",
        False,
    )
    preview = (
        await _post(admin, f"/oidc/{world.connection}/identity/preview", {"subject_claim": "email"})
    ).json()
    assert preview["proof"] is None
    refused = await _post(
        admin,
        f"/oidc/{world.connection}/identity",
        {
            "issuer": ISSUER,
            "subject_claim": "email",
            "proof_id": result["test_id"],
            "confirmation": "email",
            "version": await _version(admin),
            "reason": "Use e-mail",
        },
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["details"] == [
        {"field": "proof_id", "issue": "identity_unverified"}
    ]
    assert await _rows(
        owner_db, world.org, "SELECT subject_claim, issuer FROM oidc_connections"
    ) == [("sub", ISSUER)]
    assert await _rows(
        owner_db, world.org, "SELECT external_id FROM directory_identities ORDER BY external_id"
    ) == [("sub-lena",), ("sub-pat",)]


# --- tests --------------------------------------------------------------------------------------


async def test_a_test_sign_in_creates_no_session_and_is_readable_by_its_administrator_only(
    admin: httpx.AsyncClient,
    world: World,
    owner_db: Database,
    idps: Idps,
    app_client: AppFactory,
) -> None:
    [(sessions_before,)] = await _rows(owner_db, world.org, "SELECT count(*) FROM sessions")
    me_before = (await admin.get("/v1/me")).json()
    result = await _test_sign_in(admin, idps[ISSUER], "sub-lena")
    assert (result["outcome"], result["identifier"], result["proposed"]) == (
        "matched",
        "sub-lena",
        False,
    )
    assert result["proof_available"] is False  # nothing proposed: nothing to prove
    [(sessions_after,)] = await _rows(owner_db, world.org, "SELECT count(*) FROM sessions")
    assert sessions_after == sessions_before
    assert (await admin.get("/v1/me")).json() == me_before  # still the same session
    body = await _identity(admin)
    assert body["sign_in_status"]["reason"] == "verified"
    assert body["oidc"]["last_test"]["result"] == "matched"
    # Another administrator: the same answer as an unknown test.
    async with app_client(_configure(idps)) as olga:
        assert (await login(olga, "olga@halden.example")).status_code == 200
        theirs = await olga.get(f"{API}/oidc/test-sign-in/{result['test_id']}")
        unknown = await olga.get(f"{API}/oidc/test-sign-in/{uuid.uuid4()}")
        assert theirs.status_code == unknown.status_code == 404
    # Pat (no directory identity of Lena's): someone else signing in is "mismatched".
    other = await _test_sign_in(admin, idps[ISSUER], "sub-pat")
    assert (other["outcome"], other["identifier"]) == ("mismatched", "sub-pat")
    # A bogus code at the callback: the test failed, the provider is not marked.
    started = (await _post(admin, "/oidc/test-sign-in", {})).json()
    query = {k: v[0] for k, v in parse_qs(urlsplit(started["authorization_url"]).query).items()}
    bogus = await admin.get(
        f"/v1/auth/oidc/{KEY}/callback", params={"code": "forged-code", "state": query["state"]}
    )
    assert bogus.headers["location"] == f"/settings/identity?test={started['test_id']}"
    failed = (await admin.get(f"{API}/oidc/test-sign-in/{started['test_id']}")).json()
    assert failed["outcome"] == "sign_in_failed"
    [(failure,)] = await _rows(owner_db, world.org, "SELECT last_failure_at FROM oidc_connections")
    assert failure is None
    tested = await _events(owner_db, world.org, "sso.connection_tested")
    assert [e.after["result"] for e in tested] == ["matched", "mismatched", "sign_in_failed"]


async def test_a_bogus_authorization_code_never_marks_the_provider_unavailable(
    app_client: AppFactory, world: World, owner_db: Database, idps: Idps
) -> None:
    async with app_client(_configure(idps)) as anyone:
        start = await anyone.get(f"/v1/auth/oidc/{KEY}/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        answer = await anyone.get(
            f"/v1/auth/oidc/{KEY}/callback", params={"code": "made-up", "state": state}
        )
        assert answer.headers["location"] == "/sign-in?error=sso_unavailable"
    [(failure, code)] = await _rows(
        owner_db, world.org, "SELECT last_failure_at, last_failure_code FROM oidc_connections"
    )
    assert (failure, code) == (None, None)


async def test_the_check_tells_each_outcome_apart(
    admin: httpx.AsyncClient, world: World, owner_db: Database, idps: Idps, app_client: AppFactory
) -> None:
    idp = idps[ISSUER]

    async def check() -> dict[str, Any]:
        response = await _post(admin, f"/oidc/{world.connection}/check")
        assert response.status_code == 200, response.text
        found: dict[str, Any] = response.json()
        return found

    assert (await check())["result"] == "valid"  # the provider refused the random code
    assert (await check())["result"] == "valid"
    idp.client_secret = "the-provider-expects-a-secret"
    assert (await check()) | {"checked_at": None} == {
        "result": "credentials_rejected",
        "step": "client",
        "checked_at": None,
    }
    idp.client_secret = None
    idp.discovery_issuer = "https://somewhere.else.example"
    assert ((await check())["result"], (await check())["step"]) == (
        "invalid_configuration",
        "discovery",
    )
    idp.discovery_issuer = None
    idp.down = True
    assert ((await check())["result"], (await check())["step"]) == (
        "provider_unavailable",
        "discovery",
    )
    idp.down = False
    async with app_client(_configure(idps, resolving_to("192.168.4.4"))) as blocked:
        assert (await login(blocked, "lena@halden.example")).status_code == 200
        found = (await _post(blocked, f"/oidc/{world.connection}/check")).json()
        assert (found["result"], found["step"]) == ("outbound_blocked", "issuer")
    body = await _identity(admin)
    assert body["oidc"]["last_test"]["result"] == "outbound_blocked"
    assert (body["sign_in_status"]["state"], body["sign_in_status"]["reason"]) == (
        "blocked",
        "outbound_blocked",
    )
    # One event per result within the hour (two "valid" checks: one event).
    results = [
        e.after["result"] for e in await _events(owner_db, world.org, "sso.connection_tested")
    ]
    assert results == [
        "valid",
        "credentials_rejected",
        "invalid_configuration",
        "provider_unavailable",
        "outbound_blocked",
    ]


async def test_a_private_issuer_works_only_while_an_operator_registration_exists(
    app_client: AppFactory, owner_db: Database, idps: Idps
) -> None:
    private = "https://idp.corp.example:9443"
    org = await create_org(owner_db, "veridian")
    connection = await create_oidc_connection(owner_db, org, issuer=private, login_key=KEY)
    await create_member(owner_db, org, "victor@veridian.example", is_admin=True)
    await create_provisioned_member(owner_db, org, connection, "val@veridian.example", "sub-val")
    corp = FakeIdp(issuer=private)
    configure = _configure(Idps(corp), resolving_to("10.20.0.10"))
    async with app_client(configure) as admin:
        assert (await login(admin, "victor@veridian.example")).status_code == 200
        shown = await _identity(admin)
        assert shown["oidc"]["issuer_access"] == "not_permitted"
        assert shown["sign_in_status"]["reason"] == "outbound_blocked"
        assert (await _post(admin, f"/oidc/{connection}/check")).json()["result"] == (
            "outbound_blocked"
        )
        async with app_client(configure) as val:
            refused = await _oidc_sign_in(val, corp, "sub-val")
            assert refused.headers["location"] == "/sign-in?error=sso_unavailable"
        [(code,)] = await _rows(owner_db, org, "SELECT last_failure_code FROM oidc_connections")
        assert code == "outbound_blocked"

        # The operator registers the exact target for this organization: it works.
        async with owner_db.tenant_transaction(org) as s:
            registered, _ = await add_target(
                s,
                org,
                target=parse_target(private),
                purpose="oidc",
                reason="Customer's own IdP",
                actor="operator:test",
            )
        assert (await _identity(admin))["oidc"]["issuer_access"] == "operator_approved"
        assert (await _post(admin, f"/oidc/{connection}/check")).json()["result"] == "valid"
        async with app_client(configure) as val:
            assert (await _oidc_sign_in(val, corp, "sub-val")).headers["location"] == "/ask"

        # Revoked: refused again, for tests and for sign-in.
        async with owner_db.tenant_transaction(org) as s:
            await revoke_target(s, org, registered.id, reason="Offboarded", actor="operator:test")
        assert (await _post(admin, f"/oidc/{connection}/check")).json()["result"] == (
            "outbound_blocked"
        )
        async with app_client(configure) as val:
            again = await _oidc_sign_in(val, corp, "sub-val")
            assert again.headers["location"] == "/sign-in?error=sso_unavailable"


# --- SCIM tokens ------------------------------------------------------------------------------


async def _scim(client: httpx.AsyncClient, token: str, method: str, path: str, **kw: Any) -> Any:
    return await client.request(
        method, f"/scim/v2{path}", headers={"Authorization": f"Bearer {token}"}, **kw
    )


def _user(external_id: str, email: str) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "externalId": external_id,
        "userName": email,
        "emails": [{"value": email, "primary": True}],
    }


async def test_scim_tokens_are_shown_once_kept_as_hashes_rotated_and_revoked(
    admin: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    created = await _post(admin, "/scim/tokens", {"reason": "Okta provisioning"})
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    token = created.json()["revealed_token"]
    shown = created.json()["token"]
    assert token.startswith("lsc_")
    assert shown["hint"] == token[-4:]
    assert shown["last_used_at"] is None
    assert shown["current_connection"] is True
    [(stored,)] = await _rows(owner_db, world.org, "SELECT token_hash FROM scim_tokens")
    assert bytes(stored) == hashlib.sha256(token.encode()).digest()
    listed = await admin.get(API)
    assert token not in listed.text
    assert stored.hex() not in listed.text
    assert [t["id"] for t in listed.json()["scim"]["tokens"]] == [shown["id"]]

    # Used: last_used_at, minute precision.
    assert (await _scim(admin, token, "GET", "/Users")).status_code == 200
    [(used,)] = await _rows(owner_db, world.org, "SELECT last_used_at FROM scim_tokens")
    assert used is not None
    assert used.second == 0
    assert used.microsecond == 0
    assert (await _identity(admin))["scim"]["status"]["reason"] == "in_use"

    # Rotated: a new token now; the old one refused from the same moment.
    rotated = await _post(admin, f"/scim/tokens/{shown['id']}/rotate", {"reason": "Quarterly"})
    assert rotated.status_code == 200
    new = rotated.json()["revealed_token"]
    assert (await _scim(admin, token, "GET", "/Users")).status_code == 401
    assert (await _scim(admin, new, "GET", "/Users")).status_code == 200
    # Revoke: previewed, then final.
    new_id = rotated.json()["token"]["id"]
    preview = (await _post(admin, f"/scim/tokens/{new_id}/revoke/preview")).json()
    assert preview == {"last_live_token": True, "provisioned_members": 1}
    revoked = await _post(admin, f"/scim/tokens/{new_id}/revoke", {"reason": "IdP replaced"})
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    assert (await _scim(admin, new, "GET", "/Users")).status_code == 401
    again = await _post(admin, f"/scim/tokens/{new_id}/revoke", {"reason": "Again"})
    assert again.status_code == 409
    assert (await _identity(admin))["scim"]["status"]["reason"] == "no_valid_token"
    events = await _events(owner_db, world.org, "scim.token_%")
    assert [(e.action, set(e.target)) for e in events] == [
        ("scim.token_created", {"token_id"}),
        ("scim.token_rotated", {"token_id", "replaced_token_id"}),
        ("scim.token_revoked", {"token_id"}),
    ]
    dumped = json.dumps([list(e) for e in events], default=str)
    assert token not in dumped
    assert new not in dumped


async def test_actionable_scim_refusals_are_audited_narrowly(
    admin: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    token = (await _post(admin, "/scim/tokens", {"reason": "Provisioning"})).json()[
        "revealed_token"
    ]
    # A member added by hand, with the identifier the provider will send.
    hand = await _post_member(admin, "kim@halden.example", "sub-kim")
    for _ in range(3):  # the same refusal, repeated: one event
        conflict = await _scim(
            admin, token, "POST", "/Users", json=_user("sub-kim", "k2@x.example")
        )
        assert conflict.status_code == 409
        assert conflict.json()["scimType"] == "uniqueness"
    # Not recorded: unauthenticated, malformed, or nothing an administrator can act on.
    assert (
        await admin.post("/scim/v2/Users", json=_user("sub-kim", "k@x.example"))
    ).status_code == 401
    assert (await _scim(admin, token, "POST", "/Users", content=b"{")).status_code == 400
    assert (await _scim(admin, token, "GET", f"/Users/{uuid.uuid4()}")).status_code == 404
    # An attempt to move a person to another identifier: recorded.
    pat_id = (await _scim(admin, token, "GET", '/Users?filter=externalId eq "sub-pat"')).json()[
        "Resources"
    ][0]["id"]
    moved = await _scim(
        admin,
        token,
        "PATCH",
        f"/Users/{pat_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "externalId", "value": "sub-other"}],
        },
    )
    assert moved.status_code == 400
    events = await _events(owner_db, world.org, "scim.request_refused")
    assert [
        (e.target.get("membership_id"), e.after["reason"], e.target["operation"]) for e in events
    ] == [
        (hand, "manual_member_conflict", "create"),
        (None, "immutable_id", "patch"),
    ]
    assert all(e.actor.startswith("scim:") for e in events)
    dumped = json.dumps([list(e) for e in events], default=str)
    for value in ("sub-kim", "k2@x.example", "sub-other"):
        assert value not in dumped  # never a value from the request
    # No takeover: Kim stays hand-managed, unchanged, and only once in the directory.
    rows = await _rows(
        owner_db,
        world.org,
        "SELECT managed_by, external_id FROM directory_identities WHERE membership_id = :m",
        m=uuid.UUID(hand),
    )
    assert rows == [("organization", "sub-kim")]
    # The provisioning activity, for administrators.
    activity = (
        await admin.get("/v1/admin/audit-events", params={"actor": "identity_provider"})
    ).json()
    refused = [i for i in activity["items"] if i["action"] == "scim.request_refused"]
    assert [i["member"]["name"] if i["member"] else None for i in refused] == [None, "kim"]
    assert all(i["actor"]["kind"] == "identity_provider" for i in activity["items"])
    assert (await _identity(admin))["scim"]["status"]["reason"] == "requests_refused"


async def _post_member(client: httpx.AsyncClient, email: str, identifier: str) -> str:
    created = await client.post(
        "/v1/admin/access/members",
        json={
            "email": email,
            "name": email.split("@")[0],
            "identifier": identifier,
            "reason": "Hired",
        },
        headers=CSRF,
    )
    assert created.status_code == 201, created.text
    found: str = created.json()["member"]["id"]
    return found


async def test_a_rehired_person_resolves_to_the_same_identity_with_least_access(
    admin: httpx.AsyncClient, world: World, owner_db: Database
) -> None:
    token = (await _post(admin, "/scim/tokens", {"reason": "Provisioning"})).json()[
        "revealed_token"
    ]
    created = await _scim(
        admin, token, "POST", "/Users", json=_user("sub-ren", "ren@halden.example")
    )
    assert created.status_code == 201
    resource = created.json()["id"]
    assert (await _scim(admin, token, "DELETE", f"/Users/{resource}")).status_code == 204
    [(membership,)] = await _rows(
        owner_db,
        world.org,
        "SELECT membership_id FROM directory_identities WHERE external_id = 'sub-ren'",
    )
    member = (await admin.get(f"/v1/admin/access/members/{membership}")).json()
    reinstated = await _post_reinstate(admin, membership, member["version"])
    assert reinstated.status_code == 200, reinstated.text
    # The provider finds the same person; creating it again is a conflict, not a duplicate.
    found = (await _scim(admin, token, "GET", '/Users?filter=externalId eq "sub-ren"')).json()
    assert [r["id"] for r in found["Resources"]] == [resource]
    again = await _scim(admin, token, "POST", "/Users", json=_user("sub-ren", "ren@halden.example"))
    assert again.status_code == 409
    assert await _rows(
        owner_db,
        world.org,
        "SELECT count(*) FROM directory_identities WHERE external_id = 'sub-ren'",
    ) == [(1,)]
    [(clearance, scope, admin_flag)] = await _rows(
        owner_db,
        world.org,
        "SELECT clearance, account_scope, is_admin FROM memberships WHERE id = :m",
        m=membership,
    )
    assert (clearance, scope, admin_flag) == ("internal", "assigned", False)


async def _post_reinstate(
    client: httpx.AsyncClient, membership: Any, version: str
) -> httpx.Response:
    return await client.post(
        f"/v1/admin/access/members/{membership}/reinstate",
        json={"version": version, "reason": "Re-hired"},
        headers=CSRF,
    )


# --- the database's own refusals --------------------------------------------------------------


async def test_the_api_role_writes_connections_and_tokens_only_through_the_fences(
    app_db: Database, owner_db: Database, world: World
) -> None:
    org = world.org
    for sql in (
        "UPDATE oidc_connections SET issuer = 'https://evil.example'",
        "UPDATE oidc_connections SET status = 'disabled'",
        "UPDATE oidc_connections SET client_secret_ref = NULL",
        "INSERT INTO scim_tokens (organization_id, connection_id, token_hash) "
        "VALUES (lattice_current_org_id(), gen_random_uuid(), sha256('x'::bytea))",
        "UPDATE scim_tokens SET revoked_at = NULL",
        "UPDATE scim_tokens SET last_used_at = now()",
        "INSERT INTO encrypted_secrets (id, organization_id, owner_kind, owner_id, key_id, "
        "nonce, ciphertext) VALUES (gen_random_uuid(), lattice_current_org_id(), "
        "'oidc_client_secret', gen_random_uuid(), 'a', '\\x00', '\\x00')",
        "UPDATE oidc_login_attempts SET proof_used_at = NULL",
        "INSERT INTO outbound_targets (organization_id, purpose, scheme, host, port) VALUES "
        "(lattice_current_org_id(), 'oidc', 'https', '10.0.0.1', 443)",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_db.tenant_transaction(org) as s:
                await s.execute(text(sql))
    version = (await _rows(owner_db, org, "SELECT updated_at FROM oidc_connections"))[0][0]
    call = "SELECT lattice_oidc_set_status(:a, :c, :v, false)"
    # Not an administrator (a member, a stranger, another organization's administrator).
    other_org = await create_org(owner_db, "veridian")
    victor = await create_member(owner_db, other_org, "victor@veridian.example", is_admin=True)
    for actor in (world.pat, uuid.uuid4(), victor.membership_id):
        with pytest.raises(DBAPIError, match="not_administrator"):
            async with app_db.tenant_transaction(org) as s:
                await s.execute(text(call), {"a": actor, "c": world.connection, "v": version})
    # Another organization's connection: unknown.
    theirs = await create_oidc_connection(owner_db, other_org)
    with pytest.raises(DBAPIError, match="connection_not_found"):
        async with app_db.tenant_transaction(org) as s:
            await s.execute(text(call), {"a": world.lena, "c": theirs, "v": version})
    # Without its audit event, the change does not commit.
    with pytest.raises(DBAPIError, match="without an audit event"):
        async with app_db.tenant_transaction(org) as s:
            await s.execute(text(call), {"a": world.lena, "c": world.connection, "v": version})
    with pytest.raises(DBAPIError, match="without an audit event"):
        async with app_db.tenant_transaction(org) as s:
            await s.execute(
                text("SELECT lattice_create_scim_token(:a, sha256('t'::bytea), 'abcd')"),
                {"a": world.lena},
            )
    # A proof that is someone else's, unmatched or for other values: refused.
    attempt = await _rows(
        owner_db,
        org,
        "INSERT INTO oidc_login_attempts (organization_id, connection_id, state_hash, "
        "binding_hash, nonce, code_verifier, expires_at, purpose, membership_id, "
        "proposed_issuer, outcome, observed_identifier, completed_at) VALUES (:o, :c, "
        "sha256('s'::bytea), sha256('b'::bytea), repeat('n', 43), repeat('v', 43), "
        "now() + interval '10 minutes', 'test', :m, :i, 'matched', 'sub-lena', now()) "
        "RETURNING id",
        o=org,
        c=world.connection,
        m=world.lena,
        i=NEW_ISSUER,
    )
    change = "SELECT lattice_oidc_change_identity(:a, :c, :v, :i, 'sub', :p)"
    for actor, issuer in ((world.lena, "https://third.example"),):
        with pytest.raises(DBAPIError, match="identity_unverified"):
            async with app_db.tenant_transaction(org) as s:
                await s.execute(
                    text(change),
                    {
                        "a": actor,
                        "c": world.connection,
                        "v": version,
                        "i": issuer,
                        "p": attempt[0][0],
                    },
                )
    olga = (
        await _rows(
            owner_db,
            org,
            "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id WHERE u.email = :e",
            e="olga@halden.example",
        )
    )[0][0]
    with pytest.raises(DBAPIError, match="identity_unverified"):
        async with app_db.tenant_transaction(org) as s:
            await s.execute(
                text(change),
                {
                    "a": olga,
                    "c": world.connection,
                    "v": version,
                    "i": NEW_ISSUER,
                    "p": attempt[0][0],
                },
            )
    # A stale version: refused before anything else changes.
    with pytest.raises(DBAPIError, match="connection_changed"):
        async with app_db.tenant_transaction(org) as s:
            await s.execute(
                text(call),
                {"a": world.lena, "c": world.connection, "v": datetime(2020, 1, 1, tzinfo=UTC)},
            )


async def test_the_audit_policy_admits_the_identity_actions_from_their_actors_only(
    app_db: Database, world: World
) -> None:
    allowed = (
        ("member:x", "sso.connection_updated"),
        ("member:x", "sso.connection_tested"),
        ("member:x", "scim.token_rotated"),
        ("scim:x", "scim.request_refused"),
    )
    refused = (
        ("member:x", "scim.request_refused"),
        ("scim:x", "sso.connection_updated"),
        ("scim:x", "scim.token_created"),
        ("operator:x", "sso.connection_disabled"),
        ("member:x", "sso.connection_deleted"),
    )
    insert = (
        "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
        "VALUES (:o, :a, :x, '{}', 'r')"
    )
    for actor, action in allowed:
        async with app_db.tenant_transaction(world.org) as s:
            await s.execute(text(insert), {"o": world.org, "a": actor, "x": action})
    for actor, action in refused:
        with pytest.raises(DBAPIError, match="row-level security"):
            async with app_db.tenant_transaction(world.org) as s:
                await s.execute(text(insert), {"o": world.org, "a": actor, "x": action})
