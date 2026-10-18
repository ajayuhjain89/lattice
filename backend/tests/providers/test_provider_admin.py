"""Provider administration in the product (Phase 12, P12.5; phase-12-plan C3, C4, C6,
C11, C12; ADR-017), through the API against an in-process identity provider and a fake
provider network.

Proves: administrators only, foreign and random IDs alike; a record is created disabled
and unapproved; an API key is sealed in the vault, shown only as state, kept out of
responses, audit events and logs, replaced with the old ciphertext destroyed, revoked,
and never changes the egress lattice or the policy version; a test sends no organization
content, changes no policy and reports fixed codes; an Ollama endpoint is an
operator-registered target whose trust class follows it, and moving it withdraws the
approval; lowering applies without a step-up while an escalation is refused without one
(and recorded); embedding stays the deployment's; the setup status follows."""

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.orgs.outbound_targets import revoke_target
from lattice.providers import admin as service
from lattice.providers.admin_models import NewProviderRequest

from ..conftest import CSRF, AppFactory
from ..factories import login
from ..identity.fake_idp import FakeIdp
from .admin_world import (
    API,
    GEMINI_KEY,
    ISSUER,
    KEYS,
    MODEL,
    NEW_GEMINI_KEY,
    FakeProviders,
    World,
    build_world,
    cell_map,
    change,
    configure,
    create_gemini,
    events,
    post,
    preview,
    put,
    register_generation_target,
    rows,
    set_key,
    settings_of,
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


async def _fake_enabled(client: httpx.AsyncClient) -> str:
    """The test double, enabled but unapproved: nothing may be sent yet."""
    created = await post(
        client,
        "",
        {"provider": "fake", "model": "fake-extractive-v1", "reason": "development model"},
    )
    assert created.status_code == 201, created.text
    found = next(p for p in created.json()["providers"] if p["provider"] == "fake")
    enabled = await change(
        client, {"kind": "provider", "provider_id": found["id"], "enabled": True}
    )
    assert enabled.status_code == 200, enabled.text
    return str(found["id"])


# --- capability and masking ---------------------------------------------------------------


async def test_only_administrators_and_identical_refusals(
    app_client: AppFactory, idp: FakeIdp, providers: FakeProviders, world: World
) -> None:
    random_id = uuid.uuid4()
    async with app_client(configure(idp, providers)) as client:
        assert (await client.get(API)).status_code == 401
        assert (await login(client, "maya@halden.example")).status_code == 200
        for method, path, body in (
            ("GET", "", None),
            ("POST", "", {"provider": "fake", "model": "m", "reason": "r"}),
            ("POST", "/changes/preview", {"change": {"kind": "settings"}}),
            ("POST", f"/{random_id}/test", None),
            (
                "PUT",
                f"/{random_id}/credential",
                {"api_key": "k", "data_use": "paid", "expected_credential_id": None, "reason": "r"},
            ),
            ("GET", f"/step-ups/{random_id}", None),
        ):
            response = await client.request(method, f"{API}{path}", json=body, headers=CSRF)
            assert response.status_code == 403, (method, path, response.text)


async def test_a_fresh_organization_is_evidence_only(admin: httpx.AsyncClient) -> None:
    body = await settings_of(admin)
    assert body["providers"] == []
    assert body["lattice"] == []
    assert body["egress"]["external_egress_level"] == "internal"
    assert body["egress"]["organization_controlled_level"] is None
    assert body["step_up"] == {
        "available": True,
        "reason": "available",
        "assurance_required": None,
        "max_age_seconds": 300,
    }
    assert body["confirmation"] == "Halden"
    assert (body["status"]["state"], body["status"]["reason"]) == ("optional", "evidence_only")
    assert {s["provider"] for s in body["supported"]} == {"gemini", "ollama", "fake"}


def test_production_never_offers_or_creates_the_test_double() -> None:
    assert "fake" not in {s.provider for s in service._supported(production=True)}


async def test_production_refuses_creating_the_test_double(app_db: Database) -> None:
    with pytest.raises(Exception, match="validation_failed"):
        await service.create_provider(
            app_db,
            uuid.uuid4(),
            uuid.uuid4(),
            NewProviderRequest(provider="fake", model="fake-extractive-v1", reason="x"),
            production=True,
        )


# --- configure ----------------------------------------------------------------------------


async def test_a_provider_is_created_disabled_and_unapproved(
    admin: httpx.AsyncClient, owner_db: Database, world: World
) -> None:
    before = (await settings_of(admin))["egress"]["version"]
    provider_id = await create_gemini(admin)
    body = await settings_of(admin)
    gemini = next(p for p in body["providers"] if p["id"] == provider_id)
    assert (gemini["enabled"], gemini["explicitly_approved"]) == (False, False)
    assert (gemini["allowed_up_to"], gemini["blocked_by"]) == (None, "provider_disabled")
    assert gemini["deployment"] == "external_api"
    assert gemini["destination"] == {
        "kind": "vendor_api",
        "origin": "https://generativelanguage.googleapis.com",
        "access": "public",
    }
    assert gemini["requires_credential"] is True
    assert gemini["credential"]["state"] == "none"
    assert not any(c["permitted"] for c in body["lattice"])
    assert body["egress"]["version"] == before + 1  # a record is policy, nothing is allowed
    (event,) = await events(owner_db, world.org, "provider.%")
    assert event.action == "provider.configured"
    assert event.actor == f"member:{world.lena}"
    assert event.target["provider_id"] == provider_id
    assert event.after["enabled"] is False
    assert event.after["approval"] is None
    assert (body["status"]["state"], body["status"]["reason"]) == ("optional", "not_enabled")

    again = await post(
        admin, "", {"provider": "gemini", "model": MODEL, "training_opt_out": True, "reason": "x"}
    )
    assert again.status_code == 409
    for bad in (
        {"provider": "gemini", "model": "gemini-0-unknown", "training_opt_out": True},
        {"provider": "gemini", "model": MODEL},
        {"provider": "ollama", "model": "qwen3.5:4b"},
        {"provider": "ollama", "model": "qwen3.5:4b", "target_id": str(uuid.uuid4())},
        {"provider": "openai", "model": "gpt"},
    ):
        refused = await post(admin, "", {**bad, "reason": "x"})
        assert refused.status_code == 422, (bad, refused.text)


# --- credentials --------------------------------------------------------------------------


async def test_credential_lifecycle_is_sealed_masked_and_never_widens_egress(
    admin: httpx.AsyncClient,
    owner_db: Database,
    world: World,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    provider_id = await create_gemini(admin)
    before = await settings_of(admin)

    response = await set_key(admin, provider_id)
    assert response.status_code == 200, response.text
    assert GEMINI_KEY not in response.text
    body = response.json()
    credential = next(p for p in body["providers"] if p["id"] == provider_id)["credential"]
    assert credential["state"] == "active"
    assert (credential["source"], credential["managed_by"]) == ("vault", "organization")
    assert (credential["hint"], credential["resolvable"], credential["data_use"]) == (
        "WXYZ",
        True,
        "paid",
    )
    # A key changes no cell and no policy (C6.1).
    assert cell_map(body) == cell_map(before)
    assert body["egress"]["version"] == before["egress"]["version"]

    stored = await rows(
        owner_db,
        world.org,
        "SELECT c.secret_ref, s.ciphertext, s.key_id FROM provider_credentials c "
        "JOIN encrypted_secrets s ON s.owner_id = c.id WHERE c.status = 'active'",
    )
    assert len(stored) == 1
    assert stored[0].secret_ref.startswith("vault:")
    assert GEMINI_KEY.encode() not in bytes(stored[0].ciphertext)

    stale = await set_key(admin, provider_id, NEW_GEMINI_KEY, expected=None)
    assert stale.status_code == 409
    replaced = await set_key(admin, provider_id, NEW_GEMINI_KEY, expected=credential["id"])
    assert replaced.status_code == 200, replaced.text
    current = next(p for p in replaced.json()["providers"] if p["id"] == provider_id)["credential"]
    assert current["hint"] == "ABCD"
    assert current["id"] != credential["id"]
    destroyed = await rows(
        owner_db,
        world.org,
        "SELECT s.ciphertext, s.destroyed_at FROM encrypted_secrets s WHERE s.owner_id = :c",
        c=uuid.UUID(credential["id"]),
    )
    assert destroyed[0].ciphertext is None
    assert destroyed[0].destroyed_at is not None

    revoked = await post(
        admin,
        f"/{provider_id}/credential/revoke",
        {"expected_credential_id": current["id"], "reason": "leaked"},
    )
    assert revoked.status_code == 200, revoked.text
    after = next(p for p in revoked.json()["providers"] if p["id"] == provider_id)
    assert after["credential"]["state"] == "none"
    assert cell_map(revoked.json()) == cell_map(before)

    audit = await events(owner_db, world.org, "credential.%")
    assert [e.action for e in audit] == [
        "credential.set",
        "credential.rotated",
        "credential.revoked",
    ]
    for event in audit:
        assert event.target == {"provider": "gemini"}
        text_ = f"{event.before} {event.after} {event.reason}"
        assert GEMINI_KEY not in text_
        assert NEW_GEMINI_KEY not in text_
        assert "vault:" not in text_
    for record in caplog.records:
        message = record.getMessage()
        assert GEMINI_KEY not in message
        assert NEW_GEMINI_KEY not in message
    assert GEMINI_KEY not in (await admin.get(API)).text


async def test_a_credential_cannot_be_set_where_none_is_used(admin: httpx.AsyncClient) -> None:
    fake = await _fake_enabled(admin)
    refused = await set_key(admin, fake)
    assert refused.status_code == 422
    assert refused.json()["error"]["details"][0]["issue"] == "credential_not_used"
    assert GEMINI_KEY not in refused.text


async def test_a_blank_key_is_refused_without_echoing_it(admin: httpx.AsyncClient) -> None:
    provider_id = await create_gemini(admin)
    for key in ("   ", "key\nwith-newline-0123456789"):
        refused = await set_key(admin, provider_id, key)
        assert refused.status_code == 422
        assert "newline" not in refused.text


# --- tests --------------------------------------------------------------------------------


async def test_provider_tests_send_no_content_and_change_no_policy(
    admin: httpx.AsyncClient, providers: FakeProviders, owner_db: Database, world: World
) -> None:
    provider_id = await create_gemini(admin)
    missing = await post(admin, f"/{provider_id}/test")
    assert missing.status_code == 200
    assert missing.json()["result"] == "credential_missing"
    assert providers.requests == []

    await set_key(admin, provider_id)
    before = await settings_of(admin)
    valid = await post(admin, f"/{provider_id}/test")
    assert valid.json()["result"] == "valid"
    assert "approve" in valid.json()["note"]
    (request,) = providers.requests
    assert request.method == "GET"
    assert request.content == b""
    assert request.url.path == f"/v1beta/models/{MODEL}"
    after = await settings_of(admin)
    assert cell_map(after) == cell_map(before)
    assert after["egress"]["version"] == before["egress"]["version"]
    gemini = next(p for p in after["providers"] if p["id"] == provider_id)
    assert gemini["last_test"]["result"] == "valid"
    assert gemini["last_test"]["current"] is True
    assert gemini["enabled"] is False  # a passing test enables nothing
    assert await events(owner_db, world.org, "provider.tested%") == []

    providers.valid_keys = {NEW_GEMINI_KEY}
    assert (await post(admin, f"/{provider_id}/test")).json()["result"] == "invalid_credential"
    providers.down = True
    assert (await post(admin, f"/{provider_id}/test")).json()["result"] == "provider_unavailable"
    providers.down = False

    credential_id = gemini["credential"]["id"]
    await set_key(admin, provider_id, NEW_GEMINI_KEY, expected=credential_id)
    replaced = next(p for p in (await settings_of(admin))["providers"] if p["id"] == provider_id)
    assert replaced["last_test"]["current"] is False  # test the new key again


# --- organization-controlled servers ------------------------------------------------------


async def test_an_ollama_endpoint_is_an_operator_registered_target(
    admin: httpx.AsyncClient, owner_db: Database, world: World, providers: FakeProviders
) -> None:
    target = await register_generation_target(
        owner_db, world.org, "https://llm.halden.internal:11434"
    )
    plain = await register_generation_target(owner_db, world.org, "http://10.0.0.5:11434")
    body = await settings_of(admin)
    by_id = {t["id"]: t for t in body["targets"]}
    assert by_id[str(target)]["trust_class"] == "organization_controlled"
    assert by_id[str(target)]["usable"] is True
    assert by_id[str(plain)]["usable"] is False

    refused = await post(
        admin,
        "",
        {"provider": "ollama", "model": "qwen3.5:4b", "target_id": str(plain), "reason": "x"},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["details"][0]["issue"] == "not_https"
    # Another organization's target is not one.
    other = await register_generation_target(owner_db, world.other_org, "https://llm.v.internal")
    foreign = await post(
        admin,
        "",
        {"provider": "ollama", "model": "qwen3.5:4b", "target_id": str(other), "reason": "x"},
    )
    assert foreign.status_code == 422

    created = await post(
        admin,
        "",
        {"provider": "ollama", "model": "qwen3.5:4b", "target_id": str(target), "reason": "x"},
    )
    assert created.status_code == 201, created.text
    ollama = next(p for p in created.json()["providers"] if p["provider"] == "ollama")
    assert ollama["deployment"] == "organization_controlled"
    assert ollama["destination"] == {
        "kind": "registered_target",
        "origin": "https://llm.halden.internal:11434",
        "access": "operator_approved",
    }
    assert (await post(admin, f"/{ollama['id']}/test")).json()["result"] == "valid"
    providers.ollama_models = ["other:1b"]
    assert (await post(admin, f"/{ollama['id']}/test")).json()["result"] == (
        "configuration_invalid"
    )

    async with owner_db.tenant_transaction(world.org) as s:
        await revoke_target(s, world.org, target, reason="decommissioned", actor="operator:t")
    revoked = next(p for p in (await settings_of(admin))["providers"] if p["id"] == ollama["id"])
    assert revoked["destination"]["access"] == "not_permitted"
    assert (await post(admin, f"/{ollama['id']}/test")).json()["result"] == "outbound_blocked"


async def test_a_loopback_target_is_local_only_by_the_operators_registration(
    admin: httpx.AsyncClient, owner_db: Database, world: World
) -> None:
    loopback = await register_generation_target(owner_db, world.org, "http://127.0.0.1:11434")
    created = await post(
        admin,
        "",
        {"provider": "ollama", "model": "qwen3.5:4b", "target_id": str(loopback), "reason": "x"},
    )
    assert created.status_code == 201, created.text
    ollama = next(p for p in created.json()["providers"] if p["provider"] == "ollama")
    assert ollama["deployment"] == "local"
    assert ollama["destination"]["kind"] == "deployment_local"


async def test_moving_an_endpoint_withdraws_its_approval_and_keeps_its_trust_class(
    admin: httpx.AsyncClient, owner_db: Database, world: World
) -> None:
    first = await register_generation_target(owner_db, world.org, "https://a.halden.internal")
    second = await register_generation_target(owner_db, world.org, "https://b.halden.internal")
    loopback = await register_generation_target(owner_db, world.org, "http://127.0.0.1:11434")
    created = await post(
        admin,
        "",
        {"provider": "ollama", "model": "qwen3.5:4b", "target_id": str(first), "reason": "x"},
    )
    provider_id = next(p for p in created.json()["providers"] if p["provider"] == "ollama")["id"]
    # The class is not allowed (no organization-controlled ceiling): approving adds no cell,
    # so it is not an escalation.
    approved = await change(
        admin, {"kind": "provider", "provider_id": provider_id, "approval": "internal"}
    )
    assert approved.status_code == 200, approved.text
    version = approved.json()["egress"]["version"]

    moved = await put(
        admin,
        f"/{provider_id}/endpoint",
        {"target_id": str(second), "policy_version": version, "reason": "new server"},
    )
    assert moved.status_code == 200, moved.text
    record = next(p for p in moved.json()["providers"] if p["id"] == provider_id)
    assert record["destination"]["origin"] == "https://b.halden.internal:443"
    assert record["explicitly_approved"] is False
    reclass = await put(
        admin,
        f"/{provider_id}/endpoint",
        {
            "target_id": str(loopback),
            "policy_version": moved.json()["egress"]["version"],
            "reason": "x",
        },
    )
    assert reclass.status_code == 422
    assert reclass.json()["error"]["details"][0]["issue"] == "trust_class_change"
    stale = await put(
        admin,
        f"/{provider_id}/endpoint",
        {"target_id": str(first), "policy_version": version, "reason": "x"},
    )
    assert stale.status_code == 409


# --- egress changes -----------------------------------------------------------------------


async def test_an_escalation_is_refused_without_a_step_up_and_recorded(
    admin: httpx.AsyncClient, owner_db: Database, world: World
) -> None:
    fake = await _fake_enabled(admin)
    approve = {"kind": "provider", "provider_id": fake, "approval": "internal"}
    shown = await preview(admin, approve)
    assert shown["escalation"] is True
    assert [(t["provider"], t["level"]) for t in shown["newly_allowed"]] == [("fake", "internal")]
    assert "authorization_first" in shown["remaining"]
    assert "never_sent:restricted" in shown["remaining"]

    refused = await change(admin, approve)
    assert refused.status_code == 422
    assert refused.json()["error"]["details"][0]["issue"] == "step_up_required"
    body = await settings_of(admin)
    assert not any(c["permitted"] for c in body["lattice"])
    (event,) = await events(owner_db, world.org, "egress.escalation_refused")
    assert event.after["refused"] == "step_up_required"
    assert event.after["change"] == approve


async def test_lowering_applies_at_once_without_a_step_up(
    admin: httpx.AsyncClient, owner_db: Database, world: World
) -> None:
    fake = await _fake_enabled(admin)
    async with owner_db.tenant_transaction(world.org) as s:  # an operator approved it
        await s.execute(
            text(
                "UPDATE model_providers SET approved_at = now(), approved_for_level = "
                "'confidential' WHERE id = :p"
            ),
            {"p": uuid.UUID(fake)},
        )
        await s.execute(
            text(
                "INSERT INTO org_settings (organization_id, external_egress_level) "
                "VALUES (:o, 'confidential')"
            ),
            {"o": world.org},
        )
    assert cell_map(await settings_of(admin))[(fake, "confidential")] is True

    lowered = await change(admin, {"kind": "settings", "external_egress_level": "internal"})
    assert lowered.status_code == 200, lowered.text
    cells = cell_map(lowered.json())
    assert cells[(fake, "internal")]
    assert not cells[(fake, "confidential")]
    denied = await change(admin, {"kind": "settings", "external_egress_level": None})
    assert denied.status_code == 200, denied.text
    assert not any(cell_map(denied.json()).values())
    assert denied.json()["egress"]["external_egress_level"] is None
    raised_default = await change(
        admin, {"kind": "settings", "default_query_sensitivity": "restricted"}
    )
    assert raised_default.status_code == 200
    withdrawn = await change(admin, {"kind": "provider", "provider_id": fake, "approval": None})
    assert withdrawn.status_code == 200
    disabled = await change(admin, {"kind": "provider", "provider_id": fake, "enabled": False})
    assert disabled.status_code == 200
    actions = [e.action for e in await events(owner_db, world.org, "%")]
    assert actions.count("egress.levels_changed") == 3
    assert "provider.disabled" in actions
    assert "provider.approval_changed" in actions
    for event in await events(owner_db, world.org, "egress.levels_changed"):
        assert event.after["escalation"] is False

    # Lowering the default question sensitivity is an escalation (C6.2).
    lower = await preview(admin, {"kind": "settings", "default_query_sensitivity": "internal"})
    assert lower["escalation"] is True
    assert lower["default_query_sensitivity"] == {"before": "restricted", "after": "internal"}


async def test_a_stale_or_empty_change_is_refused(admin: httpx.AsyncClient) -> None:
    version = (await settings_of(admin))["egress"]["version"]
    await change(admin, {"kind": "settings", "default_query_sensitivity": "confidential"})
    stale = await change(
        admin, {"kind": "settings", "default_query_sensitivity": "restricted"}, version=version
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"][0]["field"] == "policy_version"
    same = await change(admin, {"kind": "settings", "default_query_sensitivity": "confidential"})
    assert same.status_code == 422
    unknown = await post(admin, "/changes/preview", {"change": {"kind": "settings", "x": 1}})
    assert unknown.status_code == 422
    null_default = await post(
        admin,
        "/changes/preview",
        {"change": {"kind": "settings", "default_query_sensitivity": None}},
    )
    assert null_default.status_code == 422


async def test_embedding_is_the_deployments_only_its_approval_is_the_organizations(
    admin: httpx.AsyncClient, owner_db: Database, world: World
) -> None:
    async with owner_db.tenant_transaction(world.org) as s:
        embedding: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO model_providers (organization_id, purpose, provider, model, "
                    "deployment, retention_mode, training_opt_out) VALUES (:o, 'embedding', "
                    "'fake', 'fake-hash-1024', 'external_api', 'none', true) RETURNING id"
                ),
                {"o": world.org},
            )
        ).scalar_one()
    body = await settings_of(admin)
    record = next(p for p in body["providers"] if p["id"] == str(embedding))
    assert record["managed_by"] == "deployment"
    assert record["requires_credential"] is False
    assert cell_map(body)[(str(embedding), "internal")] is True  # the default approval
    for refused_change in (
        {"kind": "provider", "provider_id": str(embedding), "enabled": False},
        {"kind": "provider", "provider_id": str(embedding), "training_opt_out": False},
    ):
        refused = await change(admin, refused_change)
        assert refused.status_code == 422
        assert refused.json()["error"]["details"][0]["issue"] == "deployment_managed"
    await change(admin, {"kind": "settings", "external_egress_level": "restricted"})
    raise_ = await change(
        admin, {"kind": "provider", "provider_id": str(embedding), "approval": "confidential"}
    )
    assert raise_.json()["error"]["details"][0]["issue"] == "step_up_required"


async def test_reads_record_no_audit_events(
    admin: httpx.AsyncClient, owner_db: Database, world: World
) -> None:
    await settings_of(admin)
    await preview(admin, {"kind": "settings", "default_query_sensitivity": "restricted"})
    assert await events(owner_db, world.org, "%") == []


# --- tenant isolation ---------------------------------------------------------------------


async def test_another_organizations_provider_answers_as_a_random_id(
    app_client: AppFactory,
    idp: FakeIdp,
    providers: FakeProviders,
    admin: httpx.AsyncClient,
    owner_db: Database,
    world: World,
) -> None:
    provider_id = await create_gemini(admin)
    await set_key(admin, provider_id)
    async with app_client(configure(idp, providers)) as vera:
        assert (await login(vera, "vera@veridian.example")).status_code == 200
        assert (await settings_of(vera))["providers"] == []
        for target in (provider_id, str(uuid.uuid4())):
            answers: list[Any] = []
            for method, path, body in (
                ("POST", f"/{target}/test", None),
                (
                    "PUT",
                    f"/{target}/credential",
                    {
                        "api_key": "AIzaSy-attacker-key-000000",
                        "data_use": "paid",
                        "expected_credential_id": None,
                        "reason": "x",
                    },
                ),
                (
                    "POST",
                    f"/{target}/credential/revoke",
                    {"expected_credential_id": str(uuid.uuid4()), "reason": "x"},
                ),
            ):
                response = await vera.request(method, f"{API}{path}", json=body, headers=CSRF)
                answers.append((response.status_code, response.json()))
            assert all(a[0] == 404 for a in answers), answers
        hijack = await change(
            vera, {"kind": "provider", "provider_id": provider_id, "enabled": True}
        )
        assert hijack.status_code == 404
    record = next(p for p in (await settings_of(admin))["providers"] if p["id"] == provider_id)
    assert record["credential"]["hint"] == "WXYZ"
    assert record["enabled"] is False
