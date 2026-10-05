"""Shared limits (Phase 11, P11.4; D-24, plan §10): the in-flight Ask limit and the
pre-authentication limit hold across API processes, because both live in PostgreSQL.

``ask_slots``: leased rows, taken atomically, released by lease, freed by expiry.
``pre_auth_rate_limits``: reachable only through ``lattice_auth`` functions, keyed by an
HMAC of the client address under ``LATTICE_CLIENT_KEY_SECRET`` -- no address stored.
Two engines (two "processes") and two applications on one database prove the sharing."""

import asyncio
import contextlib
import hashlib
import hmac
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from lattice.api.app import create_app
from lattice.config import Environment
from lattice.db.engine import Database, create_engine
from lattice.ratelimit import AskSlots, Bucket, PreAuthLimiter, RateLimited, client_identity

from .conftest import APP_URL, CSRF, WORKER_URL, make_settings
from .factories import Member, create_member, create_org, login

ASK = {"question": "What does the onboarding checklist say?"}
KEY = b"k" * 32


@pytest.fixture
async def second_app_db() -> AsyncIterator[Database]:
    """Another API process's pool: its own engine on the same database."""
    db = Database(create_engine(APP_URL, application_name="lattice-tests-api-2"))
    yield db
    await db.dispose()


@pytest.fixture
async def halden(owner_db: Database) -> tuple[uuid.UUID, Member]:
    org = await create_org(owner_db, "halden")
    return org, await create_member(owner_db, org, "maya@halden.example")


# --- in-flight Asks -------------------------------------------------------------------------


async def test_ask_slots_are_shared_by_processes_and_released(
    app_db: Database, second_app_db: Database, halden: tuple[uuid.UUID, Member]
) -> None:
    org, maya = halden
    one = AskSlots(app_db, Bucket.ASK_CONCURRENT, 2, 180)
    two = AskSlots(second_app_db, Bucket.ASK_CONCURRENT, 2, 180)
    async with one.slot(org, maya.membership_id), two.slot(org, maya.membership_id):
        for limiter in (one, two):
            with pytest.raises(RateLimited) as refused:
                async with limiter.slot(org, maya.membership_id):
                    pass
            assert (refused.value.bucket, refused.value.retry_after) == (Bucket.ASK_CONCURRENT, 1)
    # Released on exit -- also when the Ask raised.
    with pytest.raises(RuntimeError):
        async with one.slot(org, maya.membership_id):
            raise RuntimeError
    async with one.slot(org, maya.membership_id), two.slot(org, maya.membership_id):
        pass


async def test_parallel_requests_never_exceed_the_slots(
    app_db: Database, second_app_db: Database, halden: tuple[uuid.UUID, Member]
) -> None:
    org, maya = halden
    limiters = [AskSlots(db, Bucket.ASK_CONCURRENT, 2, 180) for db in (app_db, second_app_db) * 5]
    held = asyncio.Event()
    taken: list[int] = []

    async def ask(limiter: AskSlots) -> None:
        with contextlib.suppress(RateLimited):
            async with limiter.slot(org, maya.membership_id):
                taken.append(1)
                await held.wait()

    tasks = [asyncio.create_task(ask(limiter)) for limiter in limiters]
    await asyncio.sleep(0.5)
    assert len(taken) == 2
    held.set()
    await asyncio.gather(*tasks)


async def test_a_crashed_processes_slot_frees_itself_at_expiry(
    app_db: Database, owner_db: Database, halden: tuple[uuid.UUID, Member]
) -> None:
    org, maya = halden
    limiter = AskSlots(app_db, Bucket.ASK_CONCURRENT, 1, 180)
    # A process took the slot and died: its row stays until the lease expires.
    async with owner_db.tenant_transaction(org) as s:
        await s.execute(
            text(
                "INSERT INTO ask_slots (organization_id, membership_id, slot, lease_id, "
                "expires_at) VALUES (:o, :m, 0, gen_random_uuid(), now() + interval '1 minute')"
            ),
            {"o": org, "m": maya.membership_id},
        )
    with pytest.raises(RateLimited):
        async with limiter.slot(org, maya.membership_id):
            pass
    async with owner_db.tenant_transaction(org) as s:
        await s.execute(text("UPDATE ask_slots SET expires_at = now() - interval '1 second'"))
    async with limiter.slot(org, maya.membership_id):
        pass


async def test_a_released_lease_never_frees_another_requests_slot(
    app_db: Database, owner_db: Database, halden: tuple[uuid.UUID, Member]
) -> None:
    """A slow request whose lease expired and was taken over releases nothing."""
    org, maya = halden
    limiter = AskSlots(app_db, Bucket.ASK_CONCURRENT, 1, 180)
    async with limiter.slot(org, maya.membership_id):
        async with owner_db.tenant_transaction(org) as s:  # the lease expires meanwhile
            await s.execute(text("UPDATE ask_slots SET expires_at = now() - interval '1 second'"))
        async with limiter.slot(org, maya.membership_id):  # ...and another request takes it
            pass
        async with owner_db.tenant_transaction(org) as s:  # a third holds it now
            await s.execute(
                text(
                    "INSERT INTO ask_slots VALUES (:o, :m, 0, gen_random_uuid(), "
                    "now() + interval '1 minute') ON CONFLICT (organization_id, membership_id, "
                    "slot) DO UPDATE SET lease_id = EXCLUDED.lease_id, "
                    "expires_at = EXCLUDED.expires_at"
                ),
                {"o": org, "m": maya.membership_id},
            )
    with pytest.raises(RateLimited):  # the first request's release did not free the third's
        async with limiter.slot(org, maya.membership_id):
            pass


async def test_slots_are_per_member_and_organization(
    app_db: Database, owner_db: Database, halden: tuple[uuid.UUID, Member]
) -> None:
    org, maya = halden
    omar = await create_member(owner_db, org, "omar@halden.example")
    other = await create_org(owner_db, "veridian")
    ola = await create_member(owner_db, other, "ola@veridian.example")
    limiter = AskSlots(app_db, Bucket.ASK_CONCURRENT, 1, 180)
    async with (
        limiter.slot(org, maya.membership_id),
        limiter.slot(org, omar.membership_id),
        limiter.slot(other, ola.membership_id),
    ):
        async with app_db.tenant_transaction(org) as s:
            seen: set[uuid.UUID] = set(
                (await s.execute(text("SELECT organization_id FROM ask_slots"))).scalars()
            )
        assert seen == {org}  # row-level security


@contextlib.asynccontextmanager
async def _two_apps(
    **overrides: object,
) -> AsyncIterator[tuple[httpx.AsyncClient, httpx.AsyncClient, list[FastAPI]]]:
    async with contextlib.AsyncExitStack() as stack:
        apps = []
        clients = []
        for _ in range(2):
            app = create_app(make_settings(**overrides))
            await stack.enter_async_context(app.router.lifespan_context(app))
            apps.append(app)
            clients.append(
                await stack.enter_async_context(
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
                    )
                )
            )
        yield clients[0], clients[1], apps


async def test_an_ask_in_flight_on_one_instance_counts_on_the_other(
    halden: tuple[uuid.UUID, Member],
) -> None:
    org, maya = halden
    async with _two_apps(rate_limit_ask_concurrent=1) as (a, b, apps):
        for client in (a, b):
            assert (await login(client, maya.email)).status_code == 200
        holder: AskSlots = apps[0].state.limits.ask_concurrency
        async with holder.slot(org, maya.membership_id):  # an Ask in flight on instance a
            refused = await b.post("/v1/ask", json=ASK, headers=CSRF)
        assert refused.status_code == 429
        assert refused.headers["retry-after"] == "1"
        # Finished Asks leave no slot behind, on either instance.
        for client in (a, b, a):
            assert (await client.post("/v1/ask", json=ASK, headers=CSRF)).status_code == 200


async def test_an_ask_refused_by_the_minute_limit_releases_its_slot(
    app_db: Database, halden: tuple[uuid.UUID, Member]
) -> None:
    org, maya = halden
    async with _two_apps(rate_limit_ask_concurrent=1, rate_limit_ask_per_minute=1) as (a, b, _):
        for client in (a, b):
            assert (await login(client, maya.email)).status_code == 200
        assert (await a.post("/v1/ask", json=ASK, headers=CSRF)).status_code == 200
        refused = await b.post("/v1/ask", json=ASK, headers=CSRF)
        assert refused.status_code == 429
    async with app_db.tenant_transaction(org) as s:
        live: int = (
            await s.execute(text("SELECT count(*) FROM ask_slots WHERE expires_at > now()"))
        ).scalar_one()
    assert live == 0


# --- pre-authentication --------------------------------------------------------------------


async def test_the_pre_auth_limit_is_shared_by_processes(
    app_db: Database, second_app_db: Database
) -> None:
    one = PreAuthLimiter(app_db, KEY, 3)
    two = PreAuthLimiter(second_app_db, KEY, 3)
    await one.hit("203.0.113.7")
    await two.hit("203.0.113.7")
    await one.check("203.0.113.7")  # checking counts nothing
    await one.hit("203.0.113.7")
    for limiter in (one, two):
        with pytest.raises(RateLimited) as refused:
            await limiter.hit("203.0.113.7")
        assert refused.value.bucket is Bucket.SIGN_IN
        assert 1 <= refused.value.retry_after <= 60
        with pytest.raises(RateLimited):
            await limiter.check("203.0.113.7")
    await two.hit("203.0.113.8")  # another client


async def test_parallel_pre_auth_hits_never_exceed_the_limit(
    app_db: Database, second_app_db: Database
) -> None:
    limiters = [PreAuthLimiter(db, KEY, 4) for db in (app_db, second_app_db)]

    async def one(i: int) -> bool:
        try:
            await limiters[i % 2].hit("198.51.100.1")
        except RateLimited:
            return False
        return True

    accepted = await asyncio.gather(*(one(i) for i in range(12)))
    assert sum(accepted) == 4


async def test_no_address_is_stored_and_the_key_depends_on_the_secret(
    app_db: Database, superuser_engine: AsyncEngine
) -> None:
    limiter = PreAuthLimiter(app_db, KEY, 5)
    await limiter.hit("203.0.113.9")
    async with superuser_engine.connect() as conn:  # no login role may read it directly
        stored = (await conn.execute(text("SELECT client_key FROM pre_auth_rate_limits"))).all()
    assert [bytes(r.client_key) for r in stored] == [
        hmac.new(KEY, b"203.0.113.9", hashlib.sha256).digest()
    ]
    assert b"203.0.113" not in bytes(stored[0].client_key)
    assert PreAuthLimiter(app_db, b"x" * 32, 5).client_key("203.0.113.9") != stored[0].client_key


@pytest.mark.parametrize(
    ("address", "identity"),
    [
        ("203.0.113.9", "203.0.113.9"),
        ("::ffff:203.0.113.9", "203.0.113.9"),
        ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
        ("2001:DB8:1:2:bbbb::9", "2001:db8:1:2::/64"),
        (None, "unknown"),
        ("testclient", "unparsable"),
    ],
)
def test_client_identity(address: str | None, identity: str) -> None:
    assert client_identity(address) == identity


@pytest.mark.parametrize(
    ("role_url", "sql"),
    [
        (APP_URL, "SELECT * FROM pre_auth_rate_limits"),
        (APP_URL, "DELETE FROM pre_auth_rate_limits"),
        (APP_URL, "SELECT lattice_pre_auth_retention(10)"),
        (WORKER_URL, "SELECT * FROM pre_auth_rate_limits"),
        (
            WORKER_URL,
            "SELECT lattice_pre_auth_hit('sign_in', decode(repeat('00', 32), 'hex'), 1, 60, true)",
        ),
    ],
)
async def test_the_counters_are_reachable_only_through_their_functions(
    role_url: str, sql: str
) -> None:
    db = Database(create_engine(role_url, application_name="lattice-tests-pre-auth"))
    try:
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with db.unscoped_transaction() as s:
                await s.execute(text(sql))
    finally:
        await db.dispose()


@pytest.mark.parametrize(
    "args",
    [
        "'Sign In', decode(repeat('00', 32), 'hex'), 1, 60, true",
        "'sign_in', decode('00', 'hex'), 1, 60, true",
        "'sign_in', decode(repeat('00', 32), 'hex'), 0, 60, true",
        "'sign_in', decode(repeat('00', 32), 'hex'), 1, 0, true",
        "'sign_in', decode(repeat('00', 32), 'hex'), 1, 60, NULL",
    ],
)
async def test_the_function_refuses_malformed_input(app_db: Database, args: str) -> None:
    with pytest.raises(DBAPIError, match="invalid pre-authentication limit"):
        async with app_db.unscoped_transaction() as s:
            await s.execute(text(f"SELECT lattice_pre_auth_hit({args})"))


async def test_sign_in_on_either_instance_counts_once(owner_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    await create_member(owner_db, org, "maya@halden.example")
    async with _two_apps(rate_limit_sign_in_per_minute=3) as (a, b, _):
        assert (await login(a, "maya@halden.example")).status_code == 200
        assert (await login(b, "nobody@halden.example")).status_code == 401
        assert (await login(a, "maya@halden.example")).status_code == 200
        for client in (a, b):
            response = await login(client, "maya@halden.example")
            assert response.status_code == 429
            assert response.json()["error"]["code"] == "rate_limited"


async def test_a_spoofed_forwarded_header_does_not_change_the_client(owner_db: Database) -> None:
    """No trusted proxy is configured: X-Forwarded-For is ignored, the peer counts."""
    org = await create_org(owner_db, "halden")
    await create_member(owner_db, org, "maya@halden.example")
    async with _two_apps(rate_limit_sign_in_per_minute=2) as (a, b, _):
        for i, client in enumerate((a, b)):
            client.headers["X-Forwarded-For"] = f"192.0.2.{i}"
            assert (await login(client, "maya@halden.example")).status_code == 200
        a.headers["X-Forwarded-For"] = "192.0.2.99"
        assert (await login(a, "maya@halden.example")).status_code == 429


def test_production_requires_a_client_key_secret() -> None:
    production: dict[str, object] = {
        "env": Environment.PRODUCTION,
        "dev_auth_enabled": False,
        "session_cookie_secure": True,
        "public_url": "https://lattice.example.com",
    }
    with pytest.raises(ValueError, match="LATTICE_CLIENT_KEY_SECRET"):
        make_settings(**production)
    with pytest.raises(ValueError, match="32 bytes"):
        make_settings(**production, client_key_secret="short")
    settings = make_settings(**production, client_key_secret="s" * 32)
    assert settings.client_key == b"s" * 32
    # Outside production a fixed development key is used, the same in every process.
    assert make_settings().client_key == make_settings().client_key
