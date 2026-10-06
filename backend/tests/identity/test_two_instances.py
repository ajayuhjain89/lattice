"""Identity across two API instances (Phase 11, P11.3; phase-11-plan §15.3).

Two independently built applications share one database, as the reference stack's
``api1`` and ``api2`` do behind the ingress; one browser (one cookie jar) talks to both.
Each instance has its own OIDC client (its own discovery and key caches). Every case of
§15.3 runs with its requests spread over both instances: sign-in started on one and
finished on the other, replay, session expiry edges, logout, SSO disable and its race
with a sign-in, re-enable, in-place updates and secret rotation, operator suspension and
reinstatement, SCIM suspension/removal and token rotation, an identity-provider outage,
and the ID-token time boundaries (60 s leeway)."""

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from http.cookiejar import CookieJar
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from lattice.api.app import create_app
from lattice.db.engine import Database
from lattice.identity import admin
from lattice.metrics import CLOCK_SKEW

from ..conftest import CSRF, make_settings
from ..factories import (
    Member,
    create_member,
    create_oidc_connection,
    create_org,
    create_provisioned_member,
    create_scim_token,
    login,
)
from ..provisioning.conftest import patch, user
from .fake_idp import FakeIdp
from .test_oidc import FAILED, KEY, UNAVAILABLE, _callback_path, _configure

ACTOR = "operator:test"
SCIM = "/scim/v2"
# Every authenticated surface a member uses, read and write.
ROUTES = (
    ("GET", "/v1/me"),
    ("GET", "/v1/accounts"),
    ("GET", "/v1/activity"),
    ("POST", "/v1/ask"),
)


@dataclass
class Browser:
    """One cookie jar, a client per instance."""

    a: httpx.AsyncClient
    b: httpx.AsyncClient

    def __iter__(self) -> Any:
        return iter((self.a, self.b))

    @property
    def token(self) -> str | None:
        return self.a.cookies.get("lattice_session")

    def use(self, token: str | None) -> None:
        self.a.cookies.clear()
        if token is not None:
            # The domain the cookie jar gives the server's own cookie for "testserver".
            self.a.cookies.set("lattice_session", token, domain="testserver.local")


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


@pytest.fixture
async def browsers(idp: FakeIdp) -> AsyncIterator[Callable[[], Awaitable[Browser]]]:
    """Two API instances (separate apps, pools and OIDC caches) on one database; each
    call returns a new browser talking to both."""
    async with contextlib.AsyncExitStack() as stack:
        apps: list[FastAPI] = []
        for _ in range(2):
            app = create_app(make_settings())
            _configure(idp)(app)
            await stack.enter_async_context(app.router.lifespan_context(app))
            apps.append(app)

        async def browser() -> Browser:
            jar = CookieJar()
            clients = [
                await stack.enter_async_context(
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                        cookies=jar,
                    )
                )
                for app in apps
            ]
            return Browser(*clients)

        yield browser


async def _sign_in(
    start_on: httpx.AsyncClient,
    finish_on: httpx.AsyncClient,
    idp: FakeIdp,
    subject: str = "sub-pat",
) -> str:
    """The location the callback redirects to: ``/ask`` on success."""
    start = await start_on.get(f"/v1/auth/oidc/{KEY}/start")
    assert start.status_code == 303
    location: str = start.headers["location"]
    if location.startswith("/sign-in"):
        return location
    done = await finish_on.get(_callback_path(idp.authorize(location, subject)))
    assert done.status_code == 303
    finished: str = done.headers["location"]
    return finished


async def _status(client: httpx.AsyncClient, method: str = "GET", path: str = "/v1/me") -> int:
    body = {"question": "What changed?"} if method == "POST" else None
    return (await client.request(method, path, json=body, headers=CSRF)).status_code


async def _signed_in(browser: Browser) -> bool:
    """The session works on both instances (or on neither: a mixed answer fails). The
    same token is presented to each: a 401 makes the browser drop its cookie."""
    token = browser.token
    codes = set()
    for client in browser:
        browser.use(token)
        codes.add(await _status(client))
    assert len(codes) == 1, codes
    browser.use(token)
    return codes == {200}


async def _refused_everywhere(browser: Browser) -> None:
    token = browser.token
    assert token is not None
    for client in browser:
        for method, path in ROUTES:
            browser.use(token)
            response = await client.request(
                method, path, json={"question": "x"} if method == "POST" else None, headers=CSRF
            )
            assert response.status_code == 401, (path, response.status_code)
            assert response.json()["error"]["code"] == "authentication_required"
    browser.use(token)


async def _sessions(owner_db: Database, org: uuid.UUID) -> list[dict[str, Any]]:
    async with owner_db.tenant_transaction(org) as s:
        rows = await s.execute(
            text("SELECT user_id, oidc_connection_id, revoked_at FROM sessions ORDER BY created_at")
        )
        return [dict(r._mapping) for r in rows]


async def _op(owner_db: Database, org: uuid.UUID, action: Callable[[Any], Awaitable[Any]]) -> Any:
    async with owner_db.tenant_transaction(org) as s:
        return await action(s)


# --- sign-in, replay, expiry, logout ---------------------------------------------------------


@pytest.mark.parametrize("order", ["a-then-b", "b-then-a"])
async def test_sign_in_starts_on_one_instance_and_finishes_on_the_other(
    browsers: Callable[[], Awaitable[Browser]],
    idp: FakeIdp,
    owner_db: Database,
    world: World,
    order: str,
) -> None:
    browser = await browsers()
    first, second = (browser.a, browser.b) if order == "a-then-b" else (browser.b, browser.a)
    assert await _sign_in(first, second, idp) == "/ask"
    assert await _signed_in(browser)
    [row] = await _sessions(owner_db, world.org)
    assert (row["user_id"], row["oidc_connection_id"]) == (world.pat.user_id, world.connection)


async def test_a_used_or_foreign_state_is_refused_on_either_instance(
    browsers: Callable[[], Awaitable[Browser]], idp: FakeIdp, owner_db: Database, world: World
) -> None:
    browser = await browsers()
    start = await browser.a.get(f"/v1/auth/oidc/{KEY}/start")
    callback = _callback_path(idp.authorize(start.headers["location"], "sub-pat"))
    # Another browser, with a binding cookie of its own, cannot finish this sign-in.
    other = await browsers()
    await other.b.get(f"/v1/auth/oidc/{KEY}/start")
    assert (await other.b.get(callback)).headers["location"] == FAILED
    # Nor can a browser with no binding cookie at all.
    bare = await browsers()
    assert (await bare.a.get(callback)).headers["location"] == FAILED
    assert not await _signed_in(other)
    assert not await _signed_in(bare)
    # A state that was used, on either instance, is spent everywhere.
    fresh = await browser.a.get(f"/v1/auth/oidc/{KEY}/start")
    used = _callback_path(idp.authorize(fresh.headers["location"], "sub-pat"))
    assert (await browser.b.get(used)).headers["location"] == "/ask"
    for client in browser:
        assert (await client.get(used)).headers["location"] == FAILED
    assert [r["revoked_at"] is None for r in await _sessions(owner_db, world.org)] == [True]


async def test_idle_and_absolute_expiry_edges_hold_on_both_instances(
    browsers: Callable[[], Awaitable[Browser]], idp: FakeIdp, owner_db: Database, world: World
) -> None:
    idle = timedelta(minutes=make_settings().session_idle_timeout_minutes)
    margin = timedelta(seconds=30)
    browser = await browsers()
    await _sign_in(browser.a, browser.b, idp)

    async def set_session(column: str, value: str, delta: timedelta) -> None:
        async with owner_db.tenant_transaction(world.org) as s:
            await s.execute(
                text(f"UPDATE sessions SET {column} = {value}"),  # noqa: S608 - fixed text
                {"d": delta},
            )

    for client in browser:
        await set_session("last_seen_at", "now() - CAST(:d AS interval)", idle - margin)
        assert await _status(client) == 200  # just inside the idle timeout
    await set_session("last_seen_at", "now() - CAST(:d AS interval)", idle + margin)
    assert not await _signed_in(browser)  # just past it, on both

    await _sign_in(browser.b, browser.a, idp)
    # A week-old session, active a moment ago, at its absolute expiry.
    await set_session("created_at", "now() - CAST(:d AS interval)", timedelta(days=7))
    await set_session("last_seen_at", "now() - CAST(:d AS interval)", timedelta(seconds=5))
    await set_session("expires_at", "now() + CAST(:d AS interval)", margin)
    assert await _signed_in(browser)  # just before the absolute expiry
    await set_session("expires_at", "now() - CAST(:d AS interval)", timedelta(seconds=1))
    assert not await _signed_in(browser)


async def test_logout_on_one_instance_ends_the_session_on_both(
    browsers: Callable[[], Awaitable[Browser]], idp: FakeIdp, world: World
) -> None:
    browser = await browsers()
    await _sign_in(browser.a, browser.b, idp)
    token = browser.token
    assert (await browser.b.post("/v1/auth/logout", headers=CSRF)).status_code == 204
    browser.use(token)  # a copy of the cookie kept by an attacker
    await _refused_everywhere(browser)


# --- SSO connection lifecycle (D-25) ------------------------------------------------------


async def test_disabling_sso_refuses_sign_in_and_revokes_only_its_sessions(
    browsers: Callable[[], Awaitable[Browser]], idp: FakeIdp, owner_db: Database, world: World
) -> None:
    first, second = await browsers(), await browsers()
    await _sign_in(first.a, first.b, idp)
    await _sign_in(second.b, second.a, idp)
    # Development sign-in sessions: one in this organization, one in another.
    await create_member(owner_db, world.org, "maya@halden.example")
    veridian = await create_org(owner_db, "veridian")
    await create_member(owner_db, veridian, "ola@veridian.example")
    maya, ola = await browsers(), await browsers()
    assert (await login(maya.a, "maya@halden.example")).status_code == 200
    assert (await login(ola.b, "ola@veridian.example")).status_code == 200
    scim_token = await create_scim_token(owner_db, world.org, world.connection)

    _, revoked = await _op(
        owner_db,
        world.org,
        lambda s: admin.disable_connection(s, world.org, reason="IdP compromised", actor=ACTOR),
    )

    assert revoked == 2
    await _refused_everywhere(first)
    await _refused_everywhere(second)
    assert await _signed_in(maya)
    assert await _signed_in(ola)
    for start_on in (first.a, first.b):
        assert await _sign_in(start_on, start_on, idp) == FAILED
    for client in maya:
        response = await client.get(
            f"{SCIM}/Users", headers={"Authorization": f"Bearer {scim_token}"}
        )
        assert response.status_code == 401


async def test_a_sign_in_racing_a_disable_leaves_no_session(
    browsers: Callable[[], Awaitable[Browser]], idp: FakeIdp, owner_db: Database, world: World
) -> None:
    """The disable holds the connection's lock while the callback runs on the other
    instance: the callback waits, then sees the connection disabled and creates nothing."""
    browser = await browsers()
    start = await browser.a.get(f"/v1/auth/oidc/{KEY}/start")
    callback = _callback_path(idp.authorize(start.headers["location"], "sub-pat"))
    async with owner_db.tenant_transaction(world.org) as s:
        await admin.disable_connection(s, world.org, reason="Race drill", actor=ACTOR)
        racing = asyncio.create_task(browser.b.get(callback))
        await asyncio.sleep(0.5)
        assert not racing.done()  # waiting on the connection lock, not finished
    finished = await racing
    assert finished.headers["location"] == FAILED
    assert await _sessions(owner_db, world.org) == []
    assert not await _signed_in(browser)


async def test_re_enabling_restores_sign_in_for_the_same_members(
    browsers: Callable[[], Awaitable[Browser]], idp: FakeIdp, owner_db: Database, world: World
) -> None:
    before = await browsers()
    await _sign_in(before.a, before.b, idp)
    await _op(
        owner_db,
        world.org,
        lambda s: admin.disable_connection(s, world.org, reason="r", actor=ACTOR),
    )
    enabled = await _op(
        owner_db,
        world.org,
        lambda s: admin.enable_connection(
            s, world.org, connection_id=None, reason="IdP restored", actor=ACTOR
        ),
    )
    assert (enabled.id, enabled.status) == (world.connection, "active")
    after = await browsers()
    assert await _sign_in(after.b, after.a, idp) == "/ask"
    assert await _signed_in(after)
    assert not await _signed_in(before)  # revoked sessions stay revoked
    rows = await _sessions(owner_db, world.org)
    assert {r["oidc_connection_id"] for r in rows} == {world.connection}


async def test_a_client_id_update_applies_on_both_instances_and_keeps_sessions(
    browsers: Callable[[], Awaitable[Browser]], idp: FakeIdp, owner_db: Database, world: World
) -> None:
    existing = await browsers()
    await _sign_in(existing.a, existing.b, idp)
    _, revoked = await _op(
        owner_db,
        world.org,
        lambda s: admin.update_connection(
            s, world.org, connection_id=None, client_id="lattice-v2", reason="r", actor=ACTOR
        ),
    )
    assert revoked == 0
    idp.client_id = "lattice-v2"  # the provider now knows only the new client
    for start_on, finish_on in ((existing.a, existing.b), (existing.b, existing.a)):
        fresh = await browsers()
        start_client = fresh.a if start_on is existing.a else fresh.b
        finish_client = fresh.b if finish_on is existing.b else fresh.a
        assert await _sign_in(start_client, finish_client, idp) == "/ask"
    assert await _signed_in(existing)


async def test_client_secret_rotation_by_value_and_by_reference(
    browsers: Callable[[], Awaitable[Browser]],
    idp: FakeIdp,
    owner_db: Database,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def sign_in_everywhere() -> str:
        outcomes = set()
        for order in ((0, 1), (1, 0)):
            fresh = await browsers()
            clients = (fresh.a, fresh.b)
            outcomes.add(await _sign_in(clients[order[0]], clients[order[1]], idp))
        assert len(outcomes) == 1, outcomes
        return outcomes.pop()

    monkeypatch.setenv("LATTICE_OIDC_SECRET_HALDEN", "first-secret")
    idp.client_secret = "first-secret"
    await _op(
        owner_db,
        world.org,
        lambda s: admin.update_connection(
            s,
            world.org,
            connection_id=None,
            secret_ref="env:LATTICE_OIDC_SECRET_HALDEN",
            reason="confidential client",
            actor=ACTOR,
        ),
    )
    assert await sign_in_everywhere() == "/ask"
    kept = await browsers()
    await _sign_in(kept.a, kept.b, idp)

    # Same reference, new value: the provider rotates, the secret store follows and the
    # instances restart (here: the environment changes under both).
    idp.client_secret = "second-secret"
    assert await sign_in_everywhere() == UNAVAILABLE  # not yet rolled out
    monkeypatch.setenv("LATTICE_OIDC_SECRET_HALDEN", "second-secret")
    assert await sign_in_everywhere() == "/ask"

    # A new reference: the new variable first, then the connection, then the old one goes.
    monkeypatch.setenv("LATTICE_OIDC_SECRET_HALDEN_NEXT", "third-secret")
    view, revoked = await _op(
        owner_db,
        world.org,
        lambda s: admin.update_connection(
            s,
            world.org,
            connection_id=None,
            secret_ref="env:LATTICE_OIDC_SECRET_HALDEN_NEXT",
            reason="rotation",
            actor=ACTOR,
        ),
    )
    assert (view.confidential, revoked) == (True, 0)
    idp.client_secret = "third-secret"
    monkeypatch.delenv("LATTICE_OIDC_SECRET_HALDEN")
    assert await sign_in_everywhere() == "/ask"
    assert await _signed_in(kept)  # no rotation touched a session


async def test_an_issuer_rename_revokes_the_connections_sessions(
    browsers: Callable[[], Awaitable[Browser]], idp: FakeIdp, owner_db: Database, world: World
) -> None:
    existing = await browsers()
    await _sign_in(existing.a, existing.b, idp)
    _, revoked = await _op(
        owner_db,
        world.org,
        lambda s: admin.update_connection(
            s,
            world.org,
            connection_id=None,
            issuer="https://login.halden.example",
            subjects_unchanged=True,
            reason="issuer URL renamed",
            actor=ACTOR,
        ),
    )
    assert revoked == 1
    await _refused_everywhere(existing)
    idp.issuer = "https://login.halden.example"
    fresh = await browsers()
    assert await _sign_in(fresh.b, fresh.a, idp) == "/ask"  # same subject, same member


# --- members and SCIM ----------------------------------------------------------------------


async def test_operator_suspension_and_reinstatement(
    browsers: Callable[[], Awaitable[Browser]], idp: FakeIdp, owner_db: Database, world: World
) -> None:
    first, second = await browsers(), await browsers()
    await _sign_in(first.a, first.b, idp)
    await _sign_in(second.b, second.a, idp)
    suspended = await _op(
        owner_db,
        world.org,
        lambda s: admin.suspend_member(
            s, world.org, email="pat@halden.example", reason="incident", actor=ACTOR
        ),
    )
    assert suspended is not None
    assert suspended[1] == 2
    await _refused_everywhere(first)
    await _refused_everywhere(second)
    assert await _sign_in(first.a, first.b, idp) == FAILED

    await _op(
        owner_db,
        world.org,
        lambda s: admin.reinstate_member(
            s, world.org, email="pat@halden.example", reason="cleared", actor=ACTOR
        ),
    )
    assert not await _signed_in(first)  # reinstatement revives no session
    fresh = await browsers()
    assert await _sign_in(fresh.b, fresh.a, idp) == "/ask"
    assert not await _signed_in(second)  # sessions revoked by the suspension stay revoked


@pytest.mark.parametrize("ending", ["deactivate", "remove"])
async def test_scim_suspension_and_removal_end_sessions_on_both_instances(
    browsers: Callable[[], Awaitable[Browser]],
    idp: FakeIdp,
    owner_db: Database,
    world: World,
    ending: str,
) -> None:
    token = await create_scim_token(owner_db, world.org, world.connection)
    auth = {"Authorization": f"Bearer {token}"}
    provisioner = await browsers()
    created = await provisioner.a.post(
        f"{SCIM}/Users", json=user("okta-kim", "kim@halden.example"), headers=auth
    )
    assert created.status_code == 201
    resource = created.json()["id"]
    browser = await browsers()
    assert await _sign_in(browser.a, browser.b, idp, "okta-kim") == "/ask"
    assert await _signed_in(browser)
    if ending == "deactivate":
        response = await provisioner.b.patch(
            f"{SCIM}/Users/{resource}",
            json=patch({"op": "replace", "path": "active", "value": False}),
            headers=auth,
        )
        assert response.status_code == 200
    else:
        response = await provisioner.b.delete(f"{SCIM}/Users/{resource}", headers=auth)
        assert response.status_code == 204
    await _refused_everywhere(browser)
    assert await _sign_in(browser.b, browser.a, idp, "okta-kim") == FAILED


async def test_scim_token_rotation_overlaps_and_revocation_is_immediate(
    browsers: Callable[[], Awaitable[Browser]], owner_db: Database, world: World
) -> None:
    browser = await browsers()
    first_id, first = await _op(
        owner_db,
        world.org,
        lambda s: admin.create_scim_token(s, world.org, reason="r", actor=ACTOR),
    )
    _, second = await _op(
        owner_db,
        world.org,
        lambda s: admin.create_scim_token(s, world.org, reason="r", actor=ACTOR),
    )

    async def codes(token: str) -> set[int]:
        auth = {"Authorization": f"Bearer {token}"}
        return {(await c.get(f"{SCIM}/Users", headers=auth)).status_code for c in browser}

    assert await codes(first) == await codes(second) == {200}  # both work while switching
    await _op(
        owner_db,
        world.org,
        lambda s: admin.revoke_scim_token(s, world.org, first_id, reason="switched", actor=ACTOR),
    )
    assert await codes(first) == {401}
    assert await codes(second) == {200}


# --- provider outage and token times -----------------------------------------------------


async def test_an_identity_provider_outage_keeps_existing_sessions(
    browsers: Callable[[], Awaitable[Browser]], idp: FakeIdp, world: World
) -> None:
    existing = await browsers()
    await _sign_in(existing.a, existing.b, idp)  # instance a has the provider cached
    idp.down = True
    for start_on, finish_on in ((existing.a, existing.b), (existing.b, existing.a)):
        fresh = await browsers()
        start_client = fresh.a if start_on is existing.a else fresh.b
        finish_client = fresh.b if finish_on is existing.b else fresh.a
        assert await _sign_in(start_client, finish_client, idp) == UNAVAILABLE
        assert not await _signed_in(fresh)
    assert await _signed_in(existing)
    idp.down = False
    recovered = await browsers()
    assert await _sign_in(recovered.b, recovered.a, idp) == "/ask"


async def _early_in_a_second() -> None:
    """ID-token times are whole seconds: start a boundary case early in a second, so the
    few milliseconds of the flow cannot cross into the next one."""
    fraction = time.time() % 1
    if fraction > 0.5:
        await asyncio.sleep(1.05 - fraction)


@pytest.mark.parametrize(
    ("claim", "offset", "accepted"),
    [("exp", -59, True), ("exp", -61, False), ("iat", 59, True), ("iat", 61, False)],
)
async def test_id_token_times_have_sixty_seconds_of_leeway(
    browsers: Callable[[], Awaitable[Browser]],
    idp: FakeIdp,
    world: World,
    claim: str,
    offset: int,
    accepted: bool,
) -> None:
    idp.claim_offsets = {claim: offset}
    browser = await browsers()
    await _early_in_a_second()
    assert await _sign_in(browser.a, browser.b, idp) == ("/ask" if accepted else FAILED)
    assert await _signed_in(browser) is accepted


async def test_readiness_measures_clock_skew_against_the_database(
    browsers: Callable[[], Awaitable[Browser]], world: World
) -> None:
    CLOCK_SKEW.set(1e6)
    browser = await browsers()
    for client in browser:
        assert (await client.get("/v1/health/ready")).status_code == 200
        assert abs(CLOCK_SKEW._value.get()) < 2  # same host: well under a second
