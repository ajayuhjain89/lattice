"""CSRF protection for state-changing requests (docs/authorization-model.md §2)."""

import httpx
import pytest

from lattice.db.engine import Database

from .conftest import CSRF
from .factories import Member, create_member, create_org, login


@pytest.fixture
async def maya(owner_db: Database) -> Member:
    return await create_member(
        owner_db, await create_org(owner_db, "halden"), "maya@halden.example"
    )


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Lattice-Request": "true"},
        {"X-Lattice-Request": ""},
        {"X-Lattice-Request": "1", "Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://testserver"},  # same-origin Origin alone is not enough
    ],
    ids=["missing", "wrong-value", "empty", "cross-site", "origin-only"],
)
async def test_sign_in_without_csrf_proof_is_rejected(
    client: httpx.AsyncClient, maya: Member, headers: dict[str, str]
) -> None:
    response = await client.post(
        "/v1/auth/dev-login", json={"subject": maya.email}, headers=headers
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_rejected"
    assert "set-cookie" not in response.headers


async def test_sign_in_with_csrf_proof_succeeds(client: httpx.AsyncClient, maya: Member) -> None:
    response = await client.post(
        "/v1/auth/dev-login",
        json={"subject": maya.email},
        headers={**CSRF, "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 200


async def test_logout_without_csrf_proof_is_rejected_and_session_survives(
    client: httpx.AsyncClient, maya: Member
) -> None:
    await login(client, maya.email)
    rejected = await client.post("/v1/auth/logout")
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "csrf_rejected"
    assert (await client.get("/v1/me")).status_code == 200


async def test_logout_with_csrf_proof_succeeds(client: httpx.AsyncClient, maya: Member) -> None:
    await login(client, maya.email)
    assert (await client.post("/v1/auth/logout", headers=CSRF)).status_code == 204


async def test_csrf_is_checked_before_authentication(client: httpx.AsyncClient) -> None:
    # An unauthenticated cross-site POST is refused as CSRF, not processed further.
    response = await client.post("/v1/auth/logout")
    assert response.status_code == 403


async def test_safe_methods_do_not_need_the_header(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/health")).status_code == 200
