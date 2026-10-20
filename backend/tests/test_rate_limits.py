"""Rate limits and resource protection (Phase 10; threat model T19, T24).

Server-side, per membership / SCIM token in PostgreSQL and per client before
authentication (shared across processes since P11.4: tests/test_shared_limits.py);
429 ``rate_limited`` with ``Retry-After``; no role is exempt; a 429
reveals nothing about the request's target or another organization; parallel requests
never exceed a limit; metric labels are the bucket name only."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from prometheus_client import generate_latest
from sqlalchemy import text

from lattice import metrics
from lattice.db.engine import Database
from lattice.ratelimit import (
    AskSlots,
    Bucket,
    Limit,
    PreAuthLimiter,
    RateLimited,
    RateLimiter,
    retry_after,
    window_start,
)

from .conftest import CSRF, AppFactory, FreshWindow
from .factories import create_account, create_member, create_org, login

ASK = {"question": "What does the onboarding checklist say?"}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


# --- the limiter ---------------------------------------------------------------------------


def test_limits_cannot_be_zero() -> None:
    with pytest.raises(ValueError, match="at least one"):
        Limit(Bucket.ASK_MINUTE, 0, 60)
    with pytest.raises(ValueError, match="at least one"):
        AskSlots(None, Bucket.ASK_CONCURRENT, 0, 180)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one"):
        PreAuthLimiter(None, b"k" * 32, 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="32-byte"):
        PreAuthLimiter(None, b"short", 1)  # type: ignore[arg-type]


def test_windows_are_aligned_and_retry_after_counts_to_the_end() -> None:
    now = datetime(2026, 10, 2, 12, 0, 42, tzinfo=UTC)
    started = window_start(now, 60)
    assert started == datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    assert retry_after(now, started, 60) == 18
    assert window_start(now, 86_400) == datetime(2026, 10, 2, tzinfo=UTC)


async def test_counts_up_to_the_limit_then_refuses_until_the_next_window(
    owner_db: Database, app_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    member = uuid.uuid4()
    clock = Clock(datetime(2026, 10, 2, 12, 0, 30, tzinfo=UTC))
    limiter = RateLimiter(app_db, clock)
    limit = (Limit(Bucket.ASK_MINUTE, 3, 60),)
    for _ in range(3):
        await limiter.hit(org, member, limit)
    with pytest.raises(RateLimited) as refused:
        await limiter.hit(org, member, limit)
    assert (refused.value.retry_after, refused.value.status) == (30, 429)
    clock.now += timedelta(seconds=30)
    await limiter.hit(org, member, limit)


async def test_a_refused_request_counts_against_no_limit(
    owner_db: Database, app_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    member = uuid.uuid4()
    limiter = RateLimiter(app_db, Clock(datetime(2026, 10, 2, 12, tzinfo=UTC)))
    minute = Limit(Bucket.ASK_MINUTE, 100, 60)
    day = Limit(Bucket.ASK_DAY, 2, 86_400)
    await limiter.hit(org, member, (minute, day))
    await limiter.hit(org, member, (minute, day))
    for _ in range(3):
        with pytest.raises(RateLimited) as refused:
            await limiter.hit(org, member, (minute, day))
        assert refused.value.bucket is Bucket.ASK_DAY
    async with owner_db.tenant_transaction(org) as s:
        rows = await s.execute(text("SELECT bucket, count FROM rate_limit_counters"))
        counts = {str(bucket): int(count) for bucket, count in rows}
    assert counts == {"ask_minute": 2, "ask_day": 2}


async def test_parallel_requests_never_exceed_the_limit(
    owner_db: Database, app_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    member = uuid.uuid4()
    limiter = RateLimiter(app_db, Clock(datetime(2026, 10, 2, 12, tzinfo=UTC)))
    limit = (Limit(Bucket.ACCESS_WRITE, 5, 3600),)

    async def attempt() -> bool:
        try:
            await limiter.hit(org, member, limit)
        except RateLimited:
            return False
        return True

    results = await asyncio.gather(*(attempt() for _ in range(25)))
    assert sum(results) == 5


async def test_organizations_and_subjects_are_counted_separately(
    owner_db: Database, app_db: Database
) -> None:
    a = await create_org(owner_db, "halden")
    b = await create_org(owner_db, "veridian")
    subject = uuid.uuid4()  # deliberately the same ID in both organizations
    limiter = RateLimiter(app_db, Clock(datetime(2026, 10, 2, 12, tzinfo=UTC)))
    limit = (Limit(Bucket.SCIM, 1, 3600),)
    await limiter.hit(a, subject, limit)
    await limiter.hit(b, subject, limit)  # another organization's counter
    await limiter.hit(a, uuid.uuid4(), limit)  # another subject's counter
    with pytest.raises(RateLimited):
        await limiter.hit(a, subject, limit)
    # Row-level security: one organization never sees the other's counters.
    async with app_db.tenant_transaction(a) as s:
        orgs: set[uuid.UUID] = set(
            (await s.execute(text("SELECT organization_id FROM rate_limit_counters"))).scalars()
        )
    assert orgs == {a}


# --- through the API -----------------------------------------------------------------------


async def _signed_in(client: httpx.AsyncClient, email: str) -> None:
    assert (await login(client, email)).status_code == 200


async def test_asks_are_limited_per_member(
    owner_db: Database, app_client: AppFactory, fresh_window: FreshWindow
) -> None:
    org = await create_org(owner_db, "halden")
    await create_member(owner_db, org, "maya@halden.example")
    await create_member(owner_db, org, "omar@halden.example")
    async with app_client(rate_limit_ask_per_minute=2) as maya:
        await _signed_in(maya, "maya@halden.example")
        await fresh_window()
        for _ in range(2):
            assert (await maya.post("/v1/ask", json=ASK, headers=CSRF)).status_code == 200
        refused = await maya.post("/v1/ask", json=ASK, headers=CSRF)
        assert refused.status_code == 429
        assert refused.json()["error"]["code"] == "rate_limited"
        assert 1 <= int(refused.headers["retry-after"]) <= 60
        # A second session of the same member shares the member's limit...
        async with app_client(rate_limit_ask_per_minute=2) as again:
            await _signed_in(again, "maya@halden.example")
            assert (await again.post("/v1/ask", json=ASK, headers=CSRF)).status_code == 429
            # ...and another member has their own.
            await _signed_in(again, "omar@halden.example")
            assert (await again.post("/v1/ask", json=ASK, headers=CSRF)).status_code == 200


async def test_administrators_are_not_exempt(
    owner_db: Database, app_client: AppFactory, fresh_window: FreshWindow
) -> None:
    org = await create_org(owner_db, "halden")
    await create_member(owner_db, org, "lena@halden.example", is_admin=True)
    async with app_client(rate_limit_ask_per_minute=1) as client:
        await _signed_in(client, "lena@halden.example")
        await fresh_window()
        assert (await client.post("/v1/ask", json=ASK, headers=CSRF)).status_code == 200
        assert (await client.post("/v1/ask", json=ASK, headers=CSRF)).status_code == 429


async def test_a_refused_ask_says_nothing_about_the_named_account(
    owner_db: Database, app_client: AppFactory, fresh_window: FreshWindow
) -> None:
    org = await create_org(owner_db, "halden")
    await create_member(owner_db, org, "maya@halden.example")
    await create_account(owner_db, org, "kestrel")  # exists, but not in Maya's scope
    async with app_client(rate_limit_ask_per_minute=1) as client:
        await _signed_in(client, "maya@halden.example")
        await fresh_window()
        assert (await client.post("/v1/ask", json=ASK, headers=CSRF)).status_code == 200
        bodies = []
        for account in ("kestrel", "no-such-account"):
            response = await client.post(
                "/v1/ask", json={**ASK, "scope": {"account": account}}, headers=CSRF
            )
            assert response.status_code == 429
            body = response.json()
            body["error"].pop("request_id")
            bodies.append(body)
        assert bodies[0] == bodies[1]


async def test_concurrent_asks_are_limited_per_member(
    owner_db: Database, app_client: AppFactory
) -> None:
    org = await create_org(owner_db, "halden")
    member = await create_member(owner_db, org, "maya@halden.example")

    holder: dict[str, AskSlots] = {}

    def configure(app: object) -> None:
        holder["limiter"] = app.state.limits.ask_concurrency  # type: ignore[attr-defined]

    async with app_client(configure, rate_limit_ask_concurrent=1) as client:
        await _signed_in(client, "maya@halden.example")
        async with holder["limiter"].slot(org, member.membership_id):  # an Ask in flight
            refused = await client.post("/v1/ask", json=ASK, headers=CSRF)
        assert refused.status_code == 429
        assert refused.headers["retry-after"] == "1"
        assert (await client.post("/v1/ask", json=ASK, headers=CSRF)).status_code == 200


async def test_access_writes_are_limited_after_the_capability_check(
    owner_db: Database, app_client: AppFactory, fresh_window: FreshWindow
) -> None:
    org = await create_org(owner_db, "halden")
    await create_member(owner_db, org, "lena@halden.example", is_admin=True)
    maya = await create_member(owner_db, org, "maya@halden.example")
    change = {"clearance": "confidential"}
    async with app_client(rate_limit_access_writes_per_minute=1) as client:
        await _signed_in(client, "maya@halden.example")
        await fresh_window()
        for _ in range(3):  # non-administrators: always the capability's 403
            response = await client.post(
                f"/v1/admin/access/members/{uuid.uuid4()}/preview", json=change, headers=CSRF
            )
            assert response.status_code == 403
        await _signed_in(client, "lena@halden.example")
        first = await client.post(
            f"/v1/admin/access/members/{maya.membership_id}/preview", json=change, headers=CSRF
        )
        assert first.status_code == 200
        # Exhausted: the same 429 for an existing member and a missing one.
        bodies = []
        for target in (maya.membership_id, uuid.uuid4()):
            response = await client.post(
                f"/v1/admin/access/members/{target}/preview", json=change, headers=CSRF
            )
            assert response.status_code == 429
            body = response.json()
            body["error"].pop("request_id")
            bodies.append(body)
        assert bodies[0] == bodies[1]


async def test_development_sign_in_is_limited_per_client_for_any_subject(
    owner_db: Database, app_client: AppFactory, fresh_window: FreshWindow
) -> None:
    org = await create_org(owner_db, "halden")
    await create_member(owner_db, org, "maya@halden.example")
    async with app_client(rate_limit_sign_in_per_minute=2) as client:
        await fresh_window()
        assert (await login(client, "maya@halden.example")).status_code == 200
        assert (await login(client, "nobody@halden.example")).status_code == 401
        for subject in ("maya@halden.example", "nobody@halden.example"):
            response = await login(client, subject)
            assert response.status_code == 429
            assert response.json()["error"]["code"] == "rate_limited"


async def test_the_metric_carries_the_bucket_only(
    owner_db: Database, app_client: AppFactory, fresh_window: FreshWindow
) -> None:
    org = await create_org(owner_db, "halden")
    member = await create_member(owner_db, org, "maya@halden.example")
    async with app_client(rate_limit_ask_per_minute=1) as client:
        await _signed_in(client, "maya@halden.example")
        await fresh_window()
        await client.post("/v1/ask", json=ASK, headers=CSRF)
        assert (await client.post("/v1/ask", json=ASK, headers=CSRF)).status_code == 429
    exposition = generate_latest(metrics.REGISTRY).decode()
    lines = [line for line in exposition.splitlines() if line.startswith("lattice_rate_limited")]
    assert any('bucket="ask_minute"' in line for line in lines)
    for line in lines:
        assert str(member.membership_id) not in line
        assert str(org) not in line
        assert "maya" not in line
