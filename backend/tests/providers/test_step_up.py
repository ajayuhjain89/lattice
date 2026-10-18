"""D-34: raising AI egress needs a fresh identity-provider re-authentication bound to the
exact change (Phase 12, P12.5; phase-12-plan C6.4; ADR-017), end to end through the API
and an in-process identity provider.

Proves: a fresh, verified re-authentication of the same administrator in the same session
commits exactly the previewed change, once; the authorization request asks for a fresh
login (``max_age=0``, ``prompt=login``); an absent, unverifiable, stale or premature
``auth_time`` fails closed; another person, another session, another organization, an
expired, consumed or mismatched grant, a changed policy and a concurrent second use are
refused; a suspended or demoted administrator cannot apply; a stolen Lattice session alone
cannot escalate; an operator's assurance requirement (``acr``) is enforced and reported,
and nothing claims MFA without it; one administrator suffices; the step-up creates no
session and leaves sign-in and test sign-in as they were."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from lattice import metrics
from lattice.db.engine import Database

from ..conftest import CSRF, AppFactory
from ..factories import login
from ..identity.fake_idp import FakeIdp
from .admin_world import (
    API,
    ISSUER,
    KEY,
    KEYS,
    FakeProviders,
    World,
    build_world,
    callback_path,
    cell_map,
    configure,
    events,
    post,
    query_of,
    rows,
    settings_of,
    step_up,
)


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
async def world(owner_db: Database) -> World:
    return await build_world(owner_db)


@pytest.fixture
def idp() -> FakeIdp:
    # A provider that honours max_age: every authorization is a fresh authentication.
    return FakeIdp(issuer=ISSUER, claim_offsets={"auth_time": 0})


@pytest.fixture
def providers() -> FakeProviders:
    return FakeProviders()


@pytest.fixture
async def admin(
    app_client: AppFactory, idp: FakeIdp, providers: FakeProviders, world: World
) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(configure(idp, providers)) as client:
        assert (await login(client, "lena@halden.example")).status_code == 200
        yield client


async def _fake(client: httpx.AsyncClient) -> str:
    created = await post(
        client, "", {"provider": "fake", "model": "fake-extractive-v1", "reason": "model"}
    )
    found = next(p for p in created.json()["providers"] if p["provider"] == "fake")
    version = created.json()["egress"]["version"]
    enabled = await post(
        client,
        "/changes",
        {
            "change": {"kind": "provider", "provider_id": found["id"], "enabled": True},
            "policy_version": version,
            "reason": "enable",
        },
    )
    assert enabled.status_code == 200, enabled.text
    return str(found["id"])


def _approve(provider_id: str, level: str = "internal") -> dict[str, Any]:
    return {"kind": "provider", "provider_id": provider_id, "approval": level}


async def _result(client: httpx.AsyncClient, step_up_id: str) -> httpx.Response:
    return await client.get(f"{API}/step-ups/{step_up_id}")


async def _apply(client: httpx.AsyncClient, step_up_id: str) -> httpx.Response:
    return await client.post(f"{API}/step-ups/{step_up_id}/apply", headers=CSRF)


async def _sessions(db: Database, org: uuid.UUID) -> int:
    return len(await rows(db, org, "SELECT id FROM sessions WHERE revoked_at IS NULL"))


# --- the journey --------------------------------------------------------------------------


async def test_a_fresh_reauthentication_commits_exactly_the_previewed_change_once(
    admin: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    fake = await _fake(admin)
    before = await settings_of(admin)
    sessions = await _sessions(owner_db, world.org)
    started = await post(
        admin,
        "/step-ups",
        {
            "change": _approve(fake),
            "policy_version": before["egress"]["version"],
            "reason": "security review 42",
            "confirmation": "Halden",
        },
    )
    assert started.status_code == 201, started.text
    query = query_of(started.json()["authorization_url"])
    assert (query["prompt"], query["max_age"]) == ("login", "0")
    assert "acr_values" not in query
    step_up_id = started.json()["step_up_id"]
    pending = (await _result(admin, step_up_id)).json()
    assert pending["outcome"] == "pending"
    assert pending["grant"] is None

    back = await admin.get(
        callback_path(idp.authorize(started.json()["authorization_url"], "sub-lena"))
    )
    assert back.status_code == 303
    assert back.headers["location"] == f"/settings/providers?step_up={step_up_id}"
    assert "lattice_session" not in back.headers.get("set-cookie", "")  # no session change
    assert await _sessions(owner_db, world.org) == sessions
    # Nothing is applied by the sign-in itself.
    assert cell_map(await settings_of(admin)) == cell_map(before)

    result = (await _result(admin, step_up_id)).json()
    assert result["outcome"] == "granted"
    assert result["change"] == _approve(fake)
    assert result["grant"]["consumed"] is False
    assert result["grant"]["assurance"] is None  # nothing claims more than a fresh sign-in
    assert result["preview"]["escalation"] is True
    assert [t["level"] for t in result["preview"]["newly_allowed"]] == ["internal"]

    def counted(outcome: str) -> float:
        value = metrics.REGISTRY.get_sample_value(
            "lattice_egress_escalations_total", {"outcome": outcome}
        )
        return value or 0.0

    committed_before = counted("committed")
    applied = await _apply(admin, step_up_id)
    assert applied.status_code == 200, applied.text
    assert counted("committed") == committed_before + 1  # what LatticeEgressEscalation reads
    assert cell_map(applied.json())[(fake, "internal")] is True
    assert not cell_map(applied.json())[(fake, "confidential")]

    (completed,) = await events(owner_db, world.org, "egress.step_up_completed")
    assert completed.actor == f"member:{world.lena}"
    assert completed.reason == "security review 42"
    assert completed.after["change"] == _approve(fake)
    assert len(completed.after["change_digest"]) == 64
    (committed,) = await events(owner_db, world.org, "provider.approval_changed")
    assert committed.after["escalation"] is True
    assert committed.after["step_up"]["grant_id"] == result["grant"]["id"]
    assert committed.target["grant_id"] == result["grant"]["id"]

    again = await _apply(admin, step_up_id)
    assert counted("committed") == committed_before + 1  # a refused replay is not counted
    assert again.status_code == 409
    assert again.json()["error"]["details"][0]["issue"] == "grant_consumed"
    (refused,) = await events(owner_db, world.org, "egress.escalation_refused")
    assert refused.after["refused"] == "grant_consumed"


@pytest.mark.parametrize(
    ("claims", "offsets"),
    [
        ({}, {}),  # no auth_time at all: prompt=login alone proves nothing
        ({"auth_time": "recently"}, {}),  # unverifiable
        ({"auth_time": True}, {}),
        ({}, {"auth_time": -400}),  # older than five minutes
        ({}, {"auth_time": -150}),  # before this step-up began (an old IdP session)
        ({}, {"auth_time": 600}),  # in the future
    ],
)
async def test_without_a_fresh_verified_auth_time_it_fails_closed(
    admin: httpx.AsyncClient,
    idp: FakeIdp,
    owner_db: Database,
    world: World,
    claims: dict[str, Any],
    offsets: dict[str, int],
) -> None:
    fake = await _fake(admin)
    idp.claim_offsets = offsets
    idp.claims = claims
    step_up_id, back = await step_up(admin, idp, _approve(fake))
    assert back.status_code == 303
    result = (await _result(admin, step_up_id)).json()
    assert (result["outcome"], result["grant"]) == ("not_fresh", None)
    refused = await _apply(admin, step_up_id)
    assert refused.status_code == 404  # no grant: nothing to apply
    assert not any(c["permitted"] for c in (await settings_of(admin))["lattice"])
    assert await rows(owner_db, world.org, "SELECT id FROM step_up_grants") == []


async def test_another_person_cannot_complete_it(
    admin: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    fake = await _fake(admin)
    for subject in ("sub-olga", "sub-attacker"):
        step_up_id, _ = await step_up(admin, idp, _approve(fake), subject=subject)
        assert (await _result(admin, step_up_id)).json()["outcome"] == "mismatched"
    assert await rows(owner_db, world.org, "SELECT id FROM step_up_grants") == []


async def test_it_completes_only_in_the_session_that_started_it(
    app_client: AppFactory,
    admin: httpx.AsyncClient,
    idp: FakeIdp,
    providers: FakeProviders,
    owner_db: Database,
    world: World,
) -> None:
    fake = await _fake(admin)
    version = (await settings_of(admin))["egress"]["version"]
    started = await post(
        admin,
        "/step-ups",
        {
            "change": _approve(fake),
            "policy_version": version,
            "reason": "r",
            "confirmation": "Halden",
        },
    )
    url = idp.authorize(started.json()["authorization_url"], "sub-lena")
    binding = admin.cookies.get("lattice_oidc")
    async with app_client(configure(idp, providers)) as other:
        # The same person, signed in in another browser, with the binding value copied.
        assert (await login(other, "lena@halden.example")).status_code == 200
        other.cookies.set("lattice_oidc", binding or "")
        tokens_before = len([r for r in idp.requests if r.url.path.endswith("/token")])
        back = await other.get(callback_path(url))
        assert back.status_code == 303
        # Refused before the authorization code was exchanged.
        assert len([r for r in idp.requests if r.url.path.endswith("/token")]) == tokens_before
        # The other session cannot read it either.
        assert (await _result(other, started.json()["step_up_id"])).status_code == 404
    assert (await _result(admin, started.json()["step_up_id"])).json()["outcome"] == (
        "session_mismatch"
    )


async def test_a_callback_without_a_lattice_session_is_refused(
    app_client: AppFactory,
    admin: httpx.AsyncClient,
    idp: FakeIdp,
    providers: FakeProviders,
) -> None:
    fake = await _fake(admin)
    version = (await settings_of(admin))["egress"]["version"]
    started = await post(
        admin,
        "/step-ups",
        {
            "change": _approve(fake),
            "policy_version": version,
            "reason": "r",
            "confirmation": "Halden",
        },
    )
    url = idp.authorize(started.json()["authorization_url"], "sub-lena")
    async with app_client(configure(idp, providers)) as anonymous:
        anonymous.cookies.set("lattice_oidc", admin.cookies.get("lattice_oidc") or "")
        assert (await anonymous.get(callback_path(url))).status_code == 303
    assert (await _result(admin, started.json()["step_up_id"])).json()["outcome"] == (
        "session_mismatch"
    )


# --- the grant ----------------------------------------------------------------------------


async def _granted(client: httpx.AsyncClient, idp: FakeIdp, change: dict[str, Any]) -> str:
    step_up_id, _ = await step_up(client, idp, change)
    assert (await _result(client, step_up_id)).json()["outcome"] == "granted"
    return step_up_id


async def test_an_expired_grant_is_refused(
    admin: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    fake = await _fake(admin)
    step_up_id = await _granted(admin, idp, _approve(fake))
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(
            text(
                "UPDATE step_up_grants SET auth_time = now() - interval '6 minutes', "
                "created_at = now() - interval '6 minutes', "
                "expires_at = now() - interval '1 minute'"
            )
        )
    refused = await _apply(admin, step_up_id)
    assert refused.status_code == 409
    assert refused.json()["error"]["details"][0]["issue"] == "grant_expired"
    assert not any(c["permitted"] for c in (await settings_of(admin))["lattice"])


async def test_a_policy_change_after_the_preview_voids_the_grant(
    admin: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    fake = await _fake(admin)
    step_up_id = await _granted(admin, idp, _approve(fake))
    # Anyone's change -- here an operator's -- raises the version.
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(
            text(
                "INSERT INTO org_settings (organization_id, default_query_sensitivity) "
                "VALUES (:o, 'confidential')"
            ),
            {"o": world.org},
        )
    result = (await _result(admin, step_up_id)).json()
    assert result["current_version"] != result["policy_version"]
    refused = await _apply(admin, step_up_id)
    assert refused.status_code == 409
    assert refused.json()["error"]["details"][0]["field"] == "policy_version"


async def test_two_concurrent_applications_commit_once(
    admin: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    fake = await _fake(admin)
    step_up_id = await _granted(admin, idp, _approve(fake))
    first, second = await asyncio.gather(_apply(admin, step_up_id), _apply(admin, step_up_id))
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert len(await events(owner_db, world.org, "provider.approval_changed")) == 1
    consumed = await rows(owner_db, world.org, "SELECT consumed_at FROM step_up_grants")
    assert len(consumed) == 1
    assert consumed[0].consumed_at is not None


async def test_a_suspended_or_demoted_administrator_cannot_apply(
    app_client: AppFactory,
    admin: httpx.AsyncClient,
    idp: FakeIdp,
    providers: FakeProviders,
    owner_db: Database,
    world: World,
) -> None:
    fake = await _fake(admin)
    step_up_id = await _granted(admin, idp, _approve(fake))
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(
            text("UPDATE memberships SET is_admin = false WHERE id = :m"), {"m": world.lena}
        )
    assert (await _apply(admin, step_up_id)).status_code == 403
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(
            text("UPDATE memberships SET is_admin = true, status = 'suspended' WHERE id = :m"),
            {"m": world.lena},
        )
    assert (await _apply(admin, step_up_id)).status_code == 401
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(
            text("UPDATE memberships SET status = 'active' WHERE id = :m"), {"m": world.lena}
        )
    # The grant was never used: another administrator's session cannot use it either.
    async with app_client(configure(idp, providers)) as olga:
        assert (await login(olga, "olga@halden.example")).status_code == 200
        assert (await _apply(olga, step_up_id)).status_code == 404
        assert not any(c["permitted"] for c in (await settings_of(olga))["lattice"])


async def test_an_ended_session_cannot_apply_its_grant(
    app_client: AppFactory,
    admin: httpx.AsyncClient,
    idp: FakeIdp,
    providers: FakeProviders,
) -> None:
    fake = await _fake(admin)
    step_up_id = await _granted(admin, idp, _approve(fake))
    assert (await admin.post("/v1/auth/logout", headers=CSRF)).status_code == 204
    assert (await _apply(admin, step_up_id)).status_code == 401
    assert (await login(admin, "lena@halden.example")).status_code == 200
    assert (await _apply(admin, step_up_id)).status_code == 404  # bound to the old session


async def test_a_stolen_session_alone_cannot_escalate(
    admin: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    """The cookie is everything the attacker has: they cannot sign in as the
    administrator at the identity provider, so every route to the change fails."""
    fake = await _fake(admin)
    direct = await post(
        admin,
        "/changes",
        {
            "change": _approve(fake, "restricted"),
            "policy_version": (await settings_of(admin))["egress"]["version"],
            "reason": "x",
        },
    )
    assert direct.json()["error"]["details"][0]["issue"] == "step_up_required"
    step_up_id, _ = await step_up(admin, idp, _approve(fake, "restricted"), subject="sub-mallory")
    assert (await _result(admin, step_up_id)).json()["outcome"] == "mismatched"
    # The attacker's own way past the provider -- here it simply cannot be reached for the
    # token exchange -- ends without a grant.
    started = await post(
        admin,
        "/step-ups",
        {
            "change": _approve(fake, "restricted"),
            "policy_version": (await settings_of(admin))["egress"]["version"],
            "reason": "x",
            "confirmation": "Halden",
        },
    )
    url = idp.authorize(started.json()["authorization_url"], "sub-lena")
    idp.down = True
    await admin.get(callback_path(url))
    outcome = (await _result(admin, started.json()["step_up_id"])).json()["outcome"]
    assert outcome == "provider_unavailable"
    assert await rows(owner_db, world.org, "SELECT id FROM step_up_grants") == []
    assert not any(c["permitted"] for c in (await settings_of(admin))["lattice"])


# --- preconditions ------------------------------------------------------------------------


async def test_confirmation_escalation_and_version_are_checked_before_the_redirect(
    admin: httpx.AsyncClient, idp: FakeIdp
) -> None:
    fake = await _fake(admin)
    version = (await settings_of(admin))["egress"]["version"]

    async def start(change: dict[str, Any], **extra: Any) -> httpx.Response:
        body = {
            "change": change,
            "policy_version": version,
            "reason": "r",
            "confirmation": "Halden",
            **extra,
        }
        return await post(admin, "/step-ups", body)

    wrong = await start(_approve(fake), confirmation="halden")
    assert wrong.status_code == 422
    assert wrong.json()["error"]["details"][0] == {"field": "confirmation", "issue": "mismatch"}
    lowering = await start({"kind": "settings", "default_query_sensitivity": "restricted"})
    assert lowering.json()["error"]["details"][0]["issue"] == "not_escalation"
    stale = await start(_approve(fake), policy_version=version - 1)
    assert stale.status_code == 409
    assert not [r for r in idp.requests if "authorize" in str(r.url)]


async def test_without_an_identity_provider_connection_raising_is_refused(
    app_client: AppFactory, idp: FakeIdp, providers: FakeProviders, world: World
) -> None:
    async with app_client(configure(idp, providers)) as vera:
        assert (await login(vera, "vera@veridian.example")).status_code == 200
        body = await settings_of(vera)
        assert body["step_up"]["available"] is False
        assert body["step_up"]["reason"] == "no_connection"
        fake = await _fake(vera)
        started = await post(
            vera,
            "/step-ups",
            {
                "change": _approve(fake),
                "policy_version": (await settings_of(vera))["egress"]["version"],
                "reason": "x",
                "confirmation": "Veridian",
            },
        )
        assert started.status_code == 422
        assert started.json()["error"]["details"][0]["issue"] == "unavailable_no_connection"


async def test_an_administrator_without_a_directory_identity_cannot_step_up(
    admin: httpx.AsyncClient, owner_db: Database, world: World
) -> None:
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(
            text("DELETE FROM directory_identities WHERE membership_id = :m"), {"m": world.lena}
        )
    body = await settings_of(admin)
    assert (body["step_up"]["available"], body["step_up"]["reason"]) == (False, "no_identity")


async def test_one_administrator_is_enough(
    admin: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(
            text("UPDATE memberships SET is_admin = false WHERE id = :m"), {"m": world.olga}
        )
    fake = await _fake(admin)
    step_up_id = await _granted(admin, idp, _approve(fake))
    assert (await _apply(admin, step_up_id)).status_code == 200


# --- assurance ----------------------------------------------------------------------------


async def test_an_operator_assurance_requirement_is_enforced_and_reported(
    admin: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, world: World
) -> None:
    async with owner_db.tenant_transaction(world.org) as s:
        await s.execute(
            text("UPDATE oidc_connections SET step_up_acr_values = ARRAY['urn:example:mfa']")
        )
    body = await settings_of(admin)
    assert body["step_up"]["assurance_required"] == ["urn:example:mfa"]
    fake = await _fake(admin)
    idp.claims = {"acr": "urn:example:password"}
    step_up_id, _ = await step_up(admin, idp, _approve(fake))
    assert (await _result(admin, step_up_id)).json()["outcome"] == "assurance_insufficient"
    idp.claims = {}
    step_up_id, _ = await step_up(admin, idp, _approve(fake))
    assert (await _result(admin, step_up_id)).json()["outcome"] == "assurance_insufficient"
    idp.claims = {"acr": "urn:example:mfa"}
    version = (await settings_of(admin))["egress"]["version"]
    started = await post(
        admin,
        "/step-ups",
        {
            "change": _approve(fake),
            "policy_version": version,
            "reason": "r",
            "confirmation": "Halden",
        },
    )
    assert query_of(started.json()["authorization_url"])["acr_values"] == "urn:example:mfa"
    await admin.get(callback_path(idp.authorize(started.json()["authorization_url"], "sub-lena")))
    result = (await _result(admin, started.json()["step_up_id"])).json()
    assert result["outcome"] == "granted"
    assert result["grant"]["assurance"] == "urn:example:mfa"
    assert (await _apply(admin, started.json()["step_up_id"])).status_code == 200
    (completed,) = await events(owner_db, world.org, "egress.step_up_completed")
    assert completed.after["assurance"] == "urn:example:mfa"


async def test_an_asserted_acr_is_not_reported_without_a_requirement(
    admin: httpx.AsyncClient, idp: FakeIdp
) -> None:
    fake = await _fake(admin)
    idp.claims = {"acr": "urn:example:mfa"}
    step_up_id = await _granted(admin, idp, _approve(fake))
    assert (await _result(admin, step_up_id)).json()["grant"]["assurance"] is None


# --- identity regression ------------------------------------------------------------------


async def test_sign_in_and_test_sign_in_are_unchanged_by_step_up(
    app_client: AppFactory,
    admin: httpx.AsyncClient,
    idp: FakeIdp,
    providers: FakeProviders,
    owner_db: Database,
    world: World,
) -> None:
    fake = await _fake(admin)
    step_up_id = await _granted(admin, idp, _approve(fake))
    async with app_client(configure(idp, providers)) as browser:
        start = await browser.get(f"/v1/auth/oidc/{KEY}/start")
        query = query_of(start.headers["location"])
        assert "max_age" not in query
        assert "prompt" not in query
        back = await browser.get(
            callback_path(idp.authorize(start.headers["location"], "sub-lena"))
        )
        assert back.status_code == 303
        assert back.headers["location"] == "/ask"
        assert "lattice_session" in back.headers.get("set-cookie", "")
        # A new session cannot use the other session's grant.
        assert (await _apply(browser, step_up_id)).status_code == 404
    tested = await admin.post("/v1/admin/identity/oidc/test-sign-in", json={}, headers=CSRF)
    assert tested.status_code == 201
    back = await admin.get(
        callback_path(idp.authorize(tested.json()["authorization_url"], "sub-lena"))
    )
    assert back.headers["location"].startswith("/settings/identity?test=")
    # A test sign-in is not a step-up: no grant.
    grants = await rows(owner_db, world.org, "SELECT id FROM step_up_grants")
    assert len(grants) == 1
