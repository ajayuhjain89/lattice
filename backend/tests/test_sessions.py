"""Server-side sessions and development login (ADR-005, SEC-11)."""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from lattice.config import Environment, Settings
from lattice.db.engine import Database
from lattice.identity.dev_provider import DevAuthDisabledError, DevIdentityProvider
from lattice.identity.sessions import SessionPolicy, SessionService

from .conftest import APP_URL, CSRF, AppFactory
from .factories import Member, create_member, create_org, login

COOKIE = "lattice_session"


@pytest.fixture
async def maya(owner_db: Database) -> Member:
    org = await create_org(owner_db, "halden")
    return await create_member(owner_db, org, "maya@halden.example")


def _token(client: httpx.AsyncClient) -> str:
    value = client.cookies.get(COOKIE)
    assert value, "expected a session cookie"
    return value


async def _me_with_token(client: httpx.AsyncClient, token: str) -> httpx.Response:
    """Call /v1/me presenting exactly this token (and no cookie from the jar)."""
    saved = dict(client.cookies)
    client.cookies.clear()
    try:
        return await client.get("/v1/me", headers={"Cookie": f"{COOKIE}={token}"})
    finally:
        for name, value in saved.items():
            client.cookies.set(name, value)


async def _owner_sql(owner_db: Database, member: Member, sql: str) -> None:
    async with owner_db.tenant_transaction(member.organization_id) as s:
        await s.execute(text(sql), {"o": member.organization_id, "u": member.user_id})


async def test_valid_session_authenticates(client: httpx.AsyncClient, maya: Member) -> None:
    response = await login(client, maya.email)
    assert response.status_code == 200
    me = await client.get("/v1/me")
    assert me.status_code == 200
    body = me.json()
    assert body["organization"]["id"] == str(maya.organization_id)
    assert body["user"]["email"] == maya.email
    assert body["capabilities"] == []


async def test_session_cookie_attributes(app_client: AppFactory, maya: Member) -> None:
    async with app_client(session_cookie_secure=True) as secure_client:
        response = await login(secure_client, maya.email)
    header = response.headers["set-cookie"]
    assert header.startswith("__Host-lattice_session=")
    for attribute in ("HttpOnly", "Secure", "Path=/", "SameSite=lax"):
        assert attribute.lower() in header.lower()
    assert "domain=" not in header.lower()


async def test_raw_session_token_is_never_stored(
    client: httpx.AsyncClient, maya: Member, superuser_engine: AsyncEngine
) -> None:
    await login(client, maya.email)
    token = _token(client)
    async with superuser_engine.connect() as conn:
        rows = (await conn.execute(text("SELECT s::text AS row, token_hash FROM sessions s"))).all()
    assert len(rows) == 1
    assert bytes(rows[0].token_hash) == hashlib.sha256(token.encode()).digest()
    assert token not in rows[0].row
    assert token.encode().hex() not in rows[0].row


@pytest.mark.parametrize(
    ("label", "sql"),
    [
        (
            "expired",
            "UPDATE sessions SET created_at = now() - interval '2 hours', "
            "expires_at = now() - interval '1 second'",
        ),
        (
            "idle",
            "UPDATE sessions SET created_at = now() - interval '13 hours', "
            "last_seen_at = now() - interval '13 hours'",
        ),
        ("revoked", "UPDATE sessions SET revoked_at = now()"),
        ("suspended membership", "UPDATE memberships SET status = 'suspended' WHERE user_id = :u"),
        ("removed membership", "UPDATE memberships SET status = 'removed' WHERE user_id = :u"),
        ("suspended organization", "UPDATE organizations SET status = 'suspended' WHERE id = :o"),
        ("disabled user", "UPDATE users SET status = 'disabled' WHERE id = :u"),
    ],
)
async def test_invalid_session_states_are_rejected(
    client: httpx.AsyncClient, maya: Member, owner_db: Database, label: str, sql: str
) -> None:
    await login(client, maya.email)
    assert (await client.get("/v1/me")).status_code == 200
    await _owner_sql(owner_db, maya, sql)
    response = await client.get("/v1/me")
    assert response.status_code == 401, label
    assert response.json()["error"]["code"] == "authentication_required"
    # The dead cookie is cleared.
    assert 'lattice_session=""' in response.headers.get("set-cookie", "")


async def test_all_rejections_look_identical(
    client: httpx.AsyncClient, maya: Member, owner_db: Database
) -> None:
    unknown = await _me_with_token(client, "A" * 43)
    malformed = await _me_with_token(client, "not a token")
    await login(client, maya.email)
    await _owner_sql(owner_db, maya, "UPDATE sessions SET revoked_at = now()")
    revoked = await client.get("/v1/me")
    bodies = [r.json()["error"] for r in (unknown, malformed, revoked)]
    assert {r.status_code for r in (unknown, malformed, revoked)} == {401}
    assert len({(b["code"], b["message"]) for b in bodies}) == 1


async def test_logout_revokes_the_server_session(
    client: httpx.AsyncClient, maya: Member, superuser_engine: AsyncEngine
) -> None:
    await login(client, maya.email)
    token = _token(client)
    response = await client.post("/v1/auth/logout", headers=CSRF)
    assert response.status_code == 204
    async with superuser_engine.connect() as conn:
        revoked_at: object = (
            await conn.execute(text("SELECT revoked_at FROM sessions"))
        ).scalar_one()
    assert revoked_at is not None
    # Replaying the old token fails even if the browser kept it.
    replay = await _me_with_token(client, token)
    assert replay.status_code == 401


async def test_login_rotates_any_existing_session(
    client: httpx.AsyncClient, maya: Member, superuser_engine: AsyncEngine
) -> None:
    await login(client, maya.email)
    first = _token(client)
    await login(client, maya.email)
    second = _token(client)
    assert first != second
    assert (await _me_with_token(client, first)).status_code == 401
    assert (await _me_with_token(client, second)).status_code == 200
    async with superuser_engine.connect() as conn:
        states: list[bool] = list(
            (
                await conn.execute(
                    text("SELECT revoked_at IS NOT NULL FROM sessions ORDER BY created_at")
                )
            )
            .scalars()
            .all()
        )
    assert states == [True, False]


async def test_organization_comes_only_from_the_session(
    client: httpx.AsyncClient, owner_db: Database
) -> None:
    org_a = await create_org(owner_db, "org-a")
    org_b = await create_org(owner_db, "org-b")
    await create_member(owner_db, org_a, "both@x.example")
    await create_member(owner_db, org_b, "both@x.example")

    # Two memberships: the persona must choose; an unchosen login fails generically.
    ambiguous = await login(client, "both@x.example")
    assert ambiguous.status_code == 401
    assert (await login(client, "both@x.example", organization="org-a")).status_code == 200

    # Client-supplied organization hints are ignored.
    me = await client.get(
        "/v1/me",
        params={"organization_id": str(org_b), "organization": "org-b"},
        headers={"X-Organization-Id": str(org_b)},
    )
    assert me.json()["organization"]["id"] == str(org_a)


@pytest.mark.parametrize(
    "case", ["unknown persona", "wrong organization", "suspended membership", "no dev identity"]
)
async def test_failed_logins_are_generic(
    client: httpx.AsyncClient, owner_db: Database, case: str
) -> None:
    org = await create_org(owner_db, "halden")
    await create_org(owner_db, "other")
    await create_member(owner_db, org, "active@halden.example")
    await create_member(owner_db, org, "suspended@halden.example", status="suspended")
    await create_member(owner_db, org, "noidentity@halden.example", dev_identity=False)
    subject, organization = {
        "unknown persona": ("nobody@halden.example", None),
        "wrong organization": ("active@halden.example", "other"),
        "suspended membership": ("suspended@halden.example", None),
        "no dev identity": ("noidentity@halden.example", None),
    }[case]
    response = await login(client, subject, organization)
    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "authentication_failed",
        "message": "Sign-in failed.",
        "request_id": response.headers["x-request-id"],
    }
    assert "set-cookie" not in response.headers


async def test_last_seen_is_refreshed_and_idle_timeout_measured_from_it(
    app_db: Database, maya: Member
) -> None:
    now = datetime.now(UTC)
    clock: Callable[[], datetime] = lambda: now  # noqa: E731
    policy = SessionPolicy(idle_timeout=timedelta(minutes=30), absolute_timeout=timedelta(days=1))
    service = SessionService(app_db, policy, clock=lambda: clock())
    token, _ = await service.create(user_id=maya.user_id, organization_id=maya.organization_id)

    clock = lambda: now + timedelta(minutes=20)  # noqa: E731
    await service.authenticate(token)  # refreshes last_seen_at
    clock = lambda: now + timedelta(minutes=45)  # noqa: E731 - 25 min after last activity
    await service.authenticate(token)
    clock = lambda: now + timedelta(minutes=80)  # noqa: E731 - 35 min idle
    with pytest.raises(Exception, match="authentication_required"):
        await service.authenticate(token)


async def test_dev_login_is_not_mounted_in_production(app_client: AppFactory, maya: Member) -> None:
    async with app_client(
        env=Environment.PRODUCTION,
        dev_auth_enabled=False,
        session_cookie_secure=True,
        public_url="https://lattice.example.com",
    ) as prod:
        response = await login(prod, maya.email)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_dev_login_is_not_mounted_when_disabled(app_client: AppFactory, maya: Member) -> None:
    async with app_client(dev_auth_enabled=False) as disabled:
        assert (await login(disabled, maya.email)).status_code == 404


def test_dev_identity_provider_refuses_production(app_db: Database) -> None:
    # model_construct skips validation, simulating a bypassed configuration check.
    forced = Settings.model_construct(
        env=Environment.PRODUCTION, database_url=SecretStr(APP_URL), dev_auth_enabled=True
    )
    with pytest.raises(DevAuthDisabledError):
        DevIdentityProvider(forced, app_db)
