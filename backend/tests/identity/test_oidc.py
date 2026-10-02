"""OIDC sign-in (Phase 10; D-1, ADR-015) against an in-process identity provider.

Proves: the authorization-code flow with PKCE, state, nonce and the browser binding;
every ID-token defect is refused; a state works once; identity mapping is
deterministic and organization-scoped (no e-mail linking, no just-in-time creation,
nothing from another organization); failures are one generic redirect and leave no
session; nothing from the provider reaches the logs."""

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.identity.oidc import (
    Connection,
    OidcClient,
    OidcFailure,
    OidcSignIn,
    ProviderMetadata,
    resolve_client_secret,
    safe_return_to,
)

from ..conftest import AppFactory
from ..factories import (
    Member,
    create_member,
    create_oidc_connection,
    create_org,
    create_provisioned_member,
    login,
)
from .fake_idp import FakeIdp

KEY = "halden-login-key-0123456789"
FAILED = "/sign-in?error=sso_failed"
UNAVAILABLE = "/sign-in?error=sso_unavailable"


@dataclass
class World:
    org: uuid.UUID
    connection: uuid.UUID
    pat: Member


@pytest.fixture
async def world(owner_db: Database) -> World:
    org = await create_org(owner_db, "halden")
    connection = await create_oidc_connection(
        owner_db, org, issuer="https://idp.halden.example", login_key=KEY
    )
    pat = await create_provisioned_member(
        owner_db, org, connection, "pat@halden.example", "sub-pat"
    )
    return World(org, connection, pat)


@pytest.fixture
def idp() -> FakeIdp:
    return FakeIdp()


def _configure(idp: FakeIdp) -> Any:
    def configure(app: FastAPI) -> None:
        app.state.oidc = OidcSignIn(
            app.state.db,
            OidcClient(
                timeout_seconds=5, require_https=False, transport=httpx.MockTransport(idp.handler)
            ),
            web_origin="http://127.0.0.1:3000",
        )

    return configure


@pytest.fixture
async def client(app_client: AppFactory, idp: FakeIdp) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(_configure(idp)) as c:
        yield c


def _callback_path(url: str) -> str:
    """The provider redirects the browser to the public origin; the web app proxies
    /api/* to the API, so the API sees the path without /api."""
    parts = urlsplit(url)
    assert parts.netloc == "127.0.0.1:3000"
    assert parts.path.startswith("/api/")
    return parts.path.removeprefix("/api") + "?" + parts.query


async def _sign_in(
    client: httpx.AsyncClient,
    idp: FakeIdp,
    subject: Any = "sub-pat",
    *,
    key: str = KEY,
    next_: str | None = None,
) -> httpx.Response:
    params = {"next": next_} if next_ else {}
    start = await client.get(f"/v1/auth/oidc/{key}/start", params=params)
    assert start.status_code == 303
    location = start.headers["location"]
    if location.startswith("/sign-in"):
        return start
    return await client.get(_callback_path(idp.authorize(location, subject)))


async def _me(client: httpx.AsyncClient) -> dict[str, Any] | None:
    response = await client.get("/v1/me")
    return response.json() if response.status_code == 200 else None


# --- the flow ----------------------------------------------------------------------------


async def test_sign_in_with_pkce_state_nonce_and_binding(
    client: httpx.AsyncClient, idp: FakeIdp, world: World
) -> None:
    start = await client.get(f"/v1/auth/oidc/{KEY}/start", params={"next": "/sources"})
    assert start.status_code == 303
    location = start.headers["location"]
    query = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
    assert location.startswith("https://idp.halden.example/authorize?")
    assert query["redirect_uri"] == f"http://127.0.0.1:3000/api/v1/auth/oidc/{KEY}/callback"
    assert query["client_id"] == "lattice"
    assert len(query["state"]) >= 43
    assert len(query["nonce"]) >= 43
    assert len(query["code_challenge"]) == 43
    binding = start.headers["set-cookie"]
    assert binding.startswith("lattice_oidc=")
    for attribute in ("HttpOnly", "SameSite=lax", "Max-Age=600", "Path=/"):
        assert attribute in binding

    done = await client.get(_callback_path(idp.authorize(location, "sub-pat")))
    assert done.status_code == 303
    assert done.headers["location"] == "/sources"
    cookies = done.headers.get_list("set-cookie")
    assert any(c.startswith("lattice_session=") and "HttpOnly" in c for c in cookies)
    assert any(c.startswith('lattice_oidc=""') for c in cookies)  # binding cleared
    me = await _me(client)
    assert me is not None
    assert (me["user"]["email"], me["organization"]["slug"]) == ("pat@halden.example", "halden")


@pytest.mark.parametrize("next_", ["//evil.example/x", "https://evil.example", "/\\evil", "x"])
async def test_only_same_origin_paths_are_followed(
    client: httpx.AsyncClient, idp: FakeIdp, world: World, next_: str
) -> None:
    done = await _sign_in(client, idp, next_=next_)
    assert done.headers["location"] == "/ask"


async def test_a_confidential_client_authenticates_with_its_secret_reference(
    owner_db: Database, app_client: AppFactory, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    idp = FakeIdp(client_secret="s3cr3t-value")
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(
            text("UPDATE oidc_connections SET client_secret_ref = 'env:LATTICE_OIDC_SECRET_HALDEN'")
        )
    async with app_client(_configure(idp)) as client:
        monkeypatch.delenv("LATTICE_OIDC_SECRET_HALDEN", raising=False)
        missing = await _sign_in(client, idp)
        assert missing.headers["location"] == UNAVAILABLE
        monkeypatch.setenv("LATTICE_OIDC_SECRET_HALDEN", "s3cr3t-value")
        done = await _sign_in(client, idp)
        assert done.headers["location"] == "/ask"


def test_a_secret_reference_names_oidc_secrets_only() -> None:
    for reference in ("env:LATTICE_DATABASE_URL", "env:LATTICE_PROVIDER_SECRET_X", "LATTICE_OIDC"):
        with pytest.raises(OidcFailure):
            resolve_client_secret(reference)


async def test_sign_in_rotates_a_presented_session(
    client: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    await create_member(owner_db, world.org, "maya@halden.example")
    assert (await login(client, "maya@halden.example")).status_code == 200
    old = client.cookies["lattice_session"]
    await _sign_in(client, idp)
    assert client.cookies["lattice_session"] != old
    client.cookies.set("lattice_session", old)
    assert await _me(client) is None


# --- ID token defects ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "defect",
    [
        {"claims": {"iss": "https://evil.example"}},
        {"claims": {"aud": "someone-else"}},
        {"claims": {"aud": ["lattice", "other"]}},  # several audiences, no azp
        {"claims": {"azp": "other"}},
        {"claims": {"exp": int(time.time()) - 3600}},
        {"claims": {"iat": int(time.time()) + 3600}},
        {"claims": {"exp": None}},
        {"claims": {"nonce": None}},
        {"claims": {"nonce": "a-different-nonce-value"}},
        {"claims": {"sub": None}},
        {"claims": {"sub": 12345}},
        {"signing": "other-rsa"},  # signature by a key the provider does not publish
        {"signing": "none"},
        {"signing": "hs256-public"},  # algorithm confusion
        {"header": {"kid": "unknown-key"}},
    ],
)
async def test_every_id_token_defect_is_refused(
    app_client: AppFactory, world: World, defect: dict[str, Any]
) -> None:
    idp = FakeIdp(**defect)
    async with app_client(_configure(idp)) as client:
        done = await _sign_in(client, idp)
        assert done.status_code == 303
        assert done.headers["location"] == FAILED
        assert "lattice_session=" not in done.headers.get("set-cookie", "")
        assert await _me(client) is None


async def test_an_ec_signed_token_is_accepted(app_client: AppFactory, world: World) -> None:
    idp = FakeIdp(signing="ec")
    async with app_client(_configure(idp)) as client:
        assert (await _sign_in(client, idp)).headers["location"] == "/ask"


async def test_several_audiences_with_the_right_azp_are_accepted(
    app_client: AppFactory, world: World
) -> None:
    idp = FakeIdp(claims={"aud": ["lattice", "other"], "azp": "lattice"})
    async with app_client(_configure(idp)) as client:
        assert (await _sign_in(client, idp)).headers["location"] == "/ask"


# --- state and binding -------------------------------------------------------------------


async def test_a_state_works_once(client: httpx.AsyncClient, idp: FakeIdp, world: World) -> None:
    start = await client.get(f"/v1/auth/oidc/{KEY}/start")
    callback = _callback_path(idp.authorize(start.headers["location"], "sub-pat"))
    assert (await client.get(callback)).headers["location"] == "/ask"
    client.cookies.clear()
    client.cookies.set("lattice_oidc", start.cookies["lattice_oidc"])
    assert (await client.get(callback)).headers["location"] == FAILED


@pytest.mark.parametrize("binding", [None, "x" * 43])
async def test_the_callback_needs_the_browser_that_started(
    client: httpx.AsyncClient, idp: FakeIdp, world: World, binding: str | None
) -> None:
    """Login CSRF: a callback URL (code + state) from someone else's browser is useless."""
    start = await client.get(f"/v1/auth/oidc/{KEY}/start")
    callback = _callback_path(idp.authorize(start.headers["location"], "sub-pat"))
    client.cookies.clear()
    if binding:
        client.cookies.set("lattice_oidc", binding)
    assert (await client.get(callback)).headers["location"] == FAILED
    # ...and the attempt is spent: the right browser cannot use it afterwards either.
    client.cookies.set("lattice_oidc", start.cookies["lattice_oidc"])
    assert (await client.get(callback)).headers["location"] == FAILED


async def test_an_expired_attempt_is_refused(
    client: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    start = await client.get(f"/v1/auth/oidc/{KEY}/start")
    callback = _callback_path(idp.authorize(start.headers["location"], "sub-pat"))
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(
            text(
                "UPDATE oidc_login_attempts SET created_at = now() - interval '20 minutes', "
                "expires_at = now() - interval '10 minutes'"
            )
        )
    assert (await client.get(callback)).headers["location"] == FAILED


async def test_a_state_belongs_to_its_connection(
    client: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    other_org = await create_org(owner_db, "veridian")
    other_key = "veridian-login-key-012345"
    await create_oidc_connection(
        owner_db, other_org, issuer="https://idp.halden.example", login_key=other_key
    )
    start = await client.get(f"/v1/auth/oidc/{KEY}/start")
    callback = _callback_path(idp.authorize(start.headers["location"], "sub-pat"))
    assert (await client.get(callback.replace(KEY, other_key))).headers["location"] == FAILED


async def test_a_provider_error_spends_the_attempt(
    client: httpx.AsyncClient, idp: FakeIdp, world: World
) -> None:
    start = await client.get(f"/v1/auth/oidc/{KEY}/start")
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    refused = await client.get(
        f"/v1/auth/oidc/{KEY}/callback", params={"error": "access_denied", "state": state}
    )
    assert refused.headers["location"] == UNAVAILABLE
    replay = await client.get(f"/v1/auth/oidc/{KEY}/callback", params={"code": "x", "state": state})
    assert replay.headers["location"] == FAILED


# --- identity mapping --------------------------------------------------------------------


async def test_an_unprovisioned_subject_is_refused_even_with_a_members_email(
    client: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    """No e-mail linking, no just-in-time membership."""
    await create_member(owner_db, world.org, "maya@halden.example")
    idp.claims = {"email": "maya@halden.example", "email_verified": True}
    assert (await _sign_in(client, idp, "sub-maya")).headers["location"] == FAILED
    async with owner_db.tenant_transaction(world.org) as s:
        count: int = (await s.execute(text("SELECT count(*) FROM memberships"))).scalar_one()
    assert count == 2  # Pat and Maya; nobody created


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE memberships SET status = 'suspended' WHERE id = :m",
        "UPDATE memberships SET status = 'removed' WHERE id = :m",
        "UPDATE users SET status = 'disabled' WHERE id = (SELECT user_id FROM memberships "
        "WHERE id = :m)",
    ],
)
async def test_an_inactive_member_cannot_sign_in(
    client: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World, sql: str
) -> None:
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(text(sql), {"m": world.pat.membership_id})
    assert (await _sign_in(client, idp)).headers["location"] == FAILED


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE oidc_connections SET status = 'disabled'",
        "UPDATE organizations SET status = 'suspended'",
    ],
)
async def test_a_disabled_connection_or_suspended_organization_cannot_start(
    client: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World, sql: str
) -> None:
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(text(sql))
    start = await client.get(f"/v1/auth/oidc/{KEY}/start")
    assert start.headers["location"] == FAILED
    assert idp.requests == []  # the provider is not even contacted


@pytest.mark.parametrize(
    "key", ["unknown-login-key-0123456789", "short", "bad*key*with*symbols*0123456"]
)
async def test_an_unknown_login_key_fails_generically(
    client: httpx.AsyncClient, idp: FakeIdp, world: World, key: str
) -> None:
    start = await client.get(f"/v1/auth/oidc/{key}/start")
    assert (start.status_code, start.headers["location"]) == (303, FAILED)


async def test_the_same_subject_in_two_organizations_signs_in_to_the_connections_only(
    client: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    """Subjects are namespaced by connection: Veridian provisioning 'sub-pat' under the
    same issuer gives Veridian a different person and no way into Halden."""
    other_org = await create_org(owner_db, "veridian")
    other_key = "veridian-login-key-012345"
    other_connection = await create_oidc_connection(
        owner_db, other_org, issuer="https://idp.halden.example", login_key=other_key
    )
    await create_provisioned_member(
        owner_db, other_org, other_connection, "pat@veridian.example", "sub-pat"
    )
    await _sign_in(client, idp, key=other_key)
    me = await _me(client)
    assert me is not None
    assert (me["organization"]["slug"], me["user"]["email"]) == ("veridian", "pat@veridian.example")
    client.cookies.clear()
    await _sign_in(client, idp)
    me = await _me(client)
    assert me is not None
    assert (me["organization"]["slug"], me["user"]["email"]) == ("halden", "pat@halden.example")


async def test_a_subject_of_a_previous_connection_never_matches(
    client: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    """The organization switched identity providers: the old connection is disabled and
    a new one active. The new provider's 'sub-pat' is a different person in its own
    namespace -- it must not sign in as the member the old provider called 'sub-pat'."""
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(text("UPDATE oidc_connections SET status = 'disabled'"))
    await create_oidc_connection(
        owner_db,
        world.org,
        issuer="https://idp.halden.example",
        login_key="halden-new-login-key-00000",
    )
    done = await _sign_in(client, idp, "sub-pat", key="halden-new-login-key-00000")
    assert done.headers["location"] == FAILED
    assert await _me(client) is None


async def test_a_custom_identifying_claim(
    app_client: AppFactory, owner_db: Database, world: World
) -> None:
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(text("UPDATE oidc_connections SET subject_claim = 'oid'"))
    idp = FakeIdp(claims={"oid": "sub-pat"})
    async with app_client(_configure(idp)) as client:
        assert (await _sign_in(client, idp, "pairwise-sub-differs")).headers["location"] == "/ask"


# --- provider failures and bounds --------------------------------------------------------


@pytest.mark.parametrize(
    "fault",
    [
        {"discovery_issuer": "https://idp.other.example"},
        {"token_status": 400},
        {"token_status": 500},
        {"oversized": True},
    ],
)
async def test_provider_faults_are_sso_unavailable(
    app_client: AppFactory, world: World, fault: dict[str, Any]
) -> None:
    idp = FakeIdp(**fault)
    async with app_client(_configure(idp)) as client:
        done = await _sign_in(client, idp)
        assert done.headers["location"] == UNAVAILABLE
        assert await _me(client) is None


async def test_https_is_required_when_configured() -> None:
    client = OidcClient(
        timeout_seconds=1,
        require_https=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    with pytest.raises(OidcFailure) as failure:
        await client.metadata("http://idp.example")
    assert failure.value.outcome == "provider_error"


async def test_redirects_are_not_followed() -> None:
    client = OidcClient(
        timeout_seconds=1,
        require_https=False,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        ),
    )
    with pytest.raises(OidcFailure):
        await client.metadata("https://idp.example")


async def test_a_key_rotation_is_picked_up_once_a_minute_at_most() -> None:
    idp = FakeIdp()
    now = [0.0]
    client = OidcClient(
        timeout_seconds=1,
        require_https=False,
        transport=httpx.MockTransport(idp.handler),
        clock=lambda: now[0],
    )
    metadata = ProviderMetadata(
        issuer=idp.issuer,
        authorization_endpoint=f"{idp.issuer}/authorize",
        token_endpoint=f"{idp.issuer}/token",
        jwks_uri=f"{idp.issuer}/jwks",
    )
    connection = Connection(uuid.uuid4(), uuid.uuid4(), idp.issuer, "lattice", None, "sub")
    idp.header = {"kid": "rotated"}
    token = idp._id_token(idp.grants.setdefault("c", _grant()))
    for _ in range(3):
        with pytest.raises(OidcFailure):
            await client.verify(token, metadata, connection, nonce="n" * 43)
    jwks_fetches = [r for r in idp.requests if r.url.path.endswith("/jwks")]
    assert len(jwks_fetches) == 2  # the first fetch and one refresh


def _grant() -> Any:
    from .fake_idp import Grant

    return Grant(subject="s", nonce="n" * 43, challenge="", redirect_uri="", client_id="lattice")


def test_safe_return_to() -> None:
    assert safe_return_to("/traces/1?x=2") == "/traces/1?x=2"
    for bad in (None, "", "//x", "/\\x", "https://x", "/x\ny", "/" + "a" * 600):
        assert safe_return_to(bad) == "/ask"


# --- limits and logs ---------------------------------------------------------------------


async def test_sign_in_is_rate_limited_per_client(
    app_client: AppFactory, idp: FakeIdp, world: World
) -> None:
    async with app_client(_configure(idp), rate_limit_sign_in_per_minute=2) as client:
        assert (await _sign_in(client, idp)).headers["location"] == "/ask"  # start + callback
        start = await client.get(f"/v1/auth/oidc/{KEY}/start")
        assert start.headers["location"] == "/sign-in?error=rate_limited"


async def test_nothing_from_the_provider_reaches_the_logs(
    app_client: AppFactory, world: World, capfd: pytest.CaptureFixture[str]
) -> None:
    """The JSON log lines (lattice.log) of a failed sign-in carry outcome codes only."""
    idp = FakeIdp(claims={"email": "secret.person@halden.example", "aud": "wrong"})
    async with app_client(_configure(idp)) as client:
        await _sign_in(client, idp, "sub-secret-subject")
    captured = capfd.readouterr()
    logged = captured.out + captured.err
    assert "auth.oidc.failed" in logged
    for leaked in ("secret.person", "sub-secret-subject", "eyJ", "code="):
        assert leaked not in logged
