"""The organization's setup record (P12.1; phase-12-plan C12) and the first fenced setup
write, the organization rename (ADR-017).

* ``derive`` -- every (category, reason) branch from facts, deterministically;
* ``GET /v1/admin/setup`` -- administrators only, their organization only, states that
  follow the backend data as it changes, and nothing hidden or secret in the response;
* ``PATCH /v1/admin/organization`` -- audited in the same transaction, enforced by the
  database: the API role cannot rename without the event, cannot touch another
  organization and cannot change anything but the name.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice.db.engine import Database
from lattice.providers import configuration
from lattice.setup.derive import CATEGORIES, MESSAGES, AiFacts, SetupFacts, derive
from lattice.vault.keys import Keyring

from .conftest import CSRF, AppFactory
from .factories import (
    create_member,
    create_oidc_connection,
    create_org,
    create_provisioned_member,
    create_source,
    login,
)

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)
KEY = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="
KEYS = {"LATTICE_SECRET_KEYS": f"k1:{KEY}", "LATTICE_SECRET_ACTIVE_KEY": "k1"}
READY_AI = AiFacts(
    configured=True,
    enabled=True,
    provider="gemini",
    model="m",
    credential_required=True,
    credential_present=True,
    credential_available=True,
    allows_default=True,
)
BASE = SetupFacts(
    active_administrators=2,
    active_members=5,
    connection="active",
    last_success_at=NOW,
    connected_sources=1,
    synced_sources=1,
)


def _get(facts: SetupFacts, key: str) -> tuple[str, str]:
    found = {c.key: c for c in derive(facts)}[key]  # type: ignore[index]
    return found.state, found.reason


# --- derive: every branch -------------------------------------------------------------

CASES: list[tuple[str, SetupFacts, str, str]] = [
    ("organization", BASE, "complete", "active"),
    ("organization", replace(BASE, active_administrators=1), "attention", "single_administrator"),
    ("organization", replace(BASE, organization_status="suspended"), "blocked", "suspended"),
    ("organization", replace(BASE, active_administrators=0), "blocked", "no_administrator"),
    ("identity", replace(BASE, connection="none"), "required", "no_connection"),
    ("identity", replace(BASE, last_success_at=None), "required", "not_verified"),
    ("identity", BASE, "complete", "verified"),
    ("identity", replace(BASE, connection="disabled"), "attention", "connection_disabled"),
    (
        "identity",
        replace(
            BASE,
            last_failure_at=NOW + timedelta(minutes=5),
            last_failure_code="provider_unavailable",
        ),
        "attention",
        "provider_unavailable",
    ),
    (
        "identity",
        replace(
            BASE,
            last_failure_at=NOW - timedelta(minutes=5),
            last_failure_code="provider_unavailable",
        ),
        "complete",
        "verified",
    ),
    # P12.4: why it failed, and the latest administrator test, decide the reason.
    (
        "identity",
        replace(
            BASE,
            last_failure_at=NOW + timedelta(minutes=5),
            last_failure_code="credentials_rejected",
        ),
        "attention",
        "configuration_rejected",
    ),
    (
        "identity",
        replace(BASE, last_test_at=NOW + timedelta(minutes=5), last_test_result="token_invalid"),
        "attention",
        "configuration_rejected",
    ),
    (
        "identity",
        replace(BASE, last_test_at=NOW + timedelta(minutes=5), last_test_result="outbound_blocked"),
        "blocked",
        "outbound_blocked",
    ),
    ("identity", replace(BASE, issuer_access="not_permitted"), "blocked", "outbound_blocked"),
    (
        "identity",
        replace(BASE, last_test_at=NOW + timedelta(minutes=5), last_test_result="valid"),
        "complete",
        "verified",
    ),
    (
        "identity",
        replace(
            BASE,
            last_success_at=None,
            last_test_at=NOW,
            last_test_result="sign_in_failed",  # a person's failure, not the connection's
        ),
        "required",
        "not_verified",
    ),
    ("members", replace(BASE, connection="none"), "blocked", "identity_required"),
    ("members", replace(BASE, active_members=2), "required", "only_administrators"),
    ("members", BASE, "complete", "members_active"),
    ("provisioning", BASE, "optional", "not_configured"),
    ("provisioning", replace(BASE, live_tokens=1, tokens_ever=1), "optional", "token_not_used_yet"),
    (
        "provisioning",
        replace(BASE, live_tokens=1, tokens_ever=1, token_used=True),
        "complete",
        "in_use",
    ),
    (
        "provisioning",
        replace(BASE, tokens_ever=2, provisioned_members=3),
        "attention",
        "no_valid_token",
    ),
    ("provisioning", replace(BASE, tokens_ever=2), "optional", "not_configured"),
    (
        "provisioning",
        replace(BASE, live_tokens=1, tokens_ever=1, token_used=True, refused_recently=2),
        "attention",
        "requests_refused",
    ),
    ("ai_policy", BASE, "optional", "evidence_only"),
    (
        "ai_policy",
        replace(BASE, ai=AiFacts(configured=True, provider="gemini")),
        "optional",
        "not_enabled",
    ),
    ("ai_policy", replace(BASE, ai=READY_AI), "complete", "generation_ready"),
    (
        "ai_policy",
        replace(BASE, ai=replace(READY_AI, approved=False)),
        "attention",
        "not_approved",
    ),
    (
        "ai_policy",
        replace(BASE, ai=replace(READY_AI, outbound_permitted=False)),
        "blocked",
        "outbound_blocked",
    ),
    (
        "ai_policy",
        replace(BASE, ai=replace(READY_AI, last_test_result="outbound_blocked")),
        "blocked",
        "outbound_blocked",
    ),
    (
        "ai_policy",
        replace(BASE, ai=replace(READY_AI, credential_present=False, credential_available=False)),
        "attention",
        "credential_missing",
    ),
    (
        "ai_policy",
        replace(BASE, ai=replace(READY_AI, last_test_result="invalid_credential")),
        "attention",
        "credential_invalid",
    ),
    (
        "ai_policy",
        replace(BASE, ai=replace(READY_AI, last_test_result="provider_unavailable")),
        "attention",
        "provider_unavailable",
    ),
    (
        "ai_policy",
        replace(BASE, ai=replace(READY_AI, credential_available=False)),
        "attention",
        "credential_failed",
    ),
    (
        "ai_policy",
        replace(BASE, ai=replace(READY_AI, allows_default=False)),
        "attention",
        "policy_excludes_default",
    ),
    (
        "ai_policy",
        replace(BASE, ai=replace(READY_AI, unusable=True)),
        "attention",
        "provider_unavailable",
    ),
    (
        "ai_policy",
        replace(BASE, ai=replace(READY_AI, credential_required=False, credential_available=False)),
        "complete",
        "generation_ready",
    ),
    ("sources", replace(BASE, connected_sources=0, synced_sources=0), "required", "no_sources"),
    ("sources", replace(BASE, synced_sources=0), "required", "not_synced"),
    ("sources", replace(BASE, syncing_sources=1, failed_sources=1), "in_progress", "syncing"),
    ("sources", replace(BASE, failed_sources=1), "attention", "sync_failed"),
    ("sources", BASE, "complete", "synced"),
    ("accounts", BASE, "optional", "not_needed"),
    ("accounts", replace(BASE, account_sources=1, synced_sources=0), "blocked", "sources_required"),
    (
        "accounts",
        replace(BASE, account_sources=1, unmapped_references=4),
        "attention",
        "unmapped_references",
    ),
    (
        "accounts",
        replace(BASE, account_sources=1, references_awaiting_sync=1),
        "attention",
        "sync_required",
    ),
    ("accounts", replace(BASE, account_sources=1), "complete", "mapped"),
    ("first_answer", replace(BASE, synced_sources=0), "blocked", "sources_required"),
    ("first_answer", BASE, "required", "no_answer_yet"),
    ("first_answer", replace(BASE, synced_sources=0, answered=True), "complete", "answered"),
]


@pytest.mark.parametrize(("key", "facts", "state", "reason"), CASES)
def test_every_reason_is_derived_from_its_condition(
    key: str, facts: SetupFacts, state: str, reason: str
) -> None:
    assert _get(facts, key) == (state, reason)


def test_every_reason_has_a_case_and_a_message() -> None:
    covered = {(key, reason) for key, _, _, reason in CASES}
    assert covered == set(MESSAGES)
    categories = derive(BASE)
    assert [c.key for c in categories] == list(CATEGORIES)
    assert derive(BASE) == categories  # deterministic
    for category in categories:
        assert category.message == MESSAGES[(category.key, category.reason)]
        assert "%" not in category.message


def test_actions_link_only_to_pages_that_exist() -> None:
    hrefs = set()
    for _, facts, _, _ in CASES:
        hrefs |= {c.action.href for c in derive(facts) if c.action is not None}
    pages = {"/access", "/settings/providers", "/settings/identity", "/sources", "/ask"}
    # P12.3: the account pages under Access.
    assert hrefs <= pages | {"/access/accounts", "/access/accounts/references"}


def test_members_and_administrators_point_to_the_access_page() -> None:
    """P12.2: members are added and administrators chosen in Access; the record says
    whether adding by hand is possible (it needs the active connection)."""
    by_key = {c.key: c for c in derive(replace(BASE, active_members=2, active_administrators=2))}
    members = by_key["members"]
    assert (members.state, members.reason) == ("required", "only_administrators")
    assert members.action is not None
    assert (members.action.href, members.action.label) == ("/access", "Add a member")
    assert members.facts["manual_members_available"] is True
    blocked = {c.key: c for c in derive(replace(BASE, connection="none"))}["members"]
    assert (blocked.reason, blocked.facts["manual_members_available"]) == (
        "identity_required",
        False,
    )
    single = {c.key: c for c in derive(replace(BASE, active_administrators=1))}["organization"]
    assert single.action is not None
    assert single.action.href == "/access"
    none = {c.key: c for c in derive(replace(BASE, active_administrators=0))}["organization"]
    assert (none.state, none.reason, none.action) == ("blocked", "no_administrator", None)


# --- the endpoint ------------------------------------------------------------------------


async def _client_for(app_client: AppFactory, email: str, org: str) -> AsyncIterator[Any]:
    async with app_client() as client:
        assert (await login(client, email, org)).status_code == 200
        yield client


@pytest.fixture
async def halden(owner_db: Database) -> dict[str, Any]:
    org = await create_org(owner_db, "halden")
    admin = await create_member(owner_db, org, "ada@halden.example", is_admin=True)
    member = await create_member(owner_db, org, "max@halden.example")
    return {"org": org, "admin": admin, "member": member}


async def _setup(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get("/v1/admin/setup")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _states(body: dict[str, Any]) -> dict[str, tuple[str, str]]:
    return {c["key"]: (c["state"], c["reason"]) for c in body["categories"]}


async def test_administrators_only(
    app_client: AppFactory, owner_db: Database, halden: dict[str, Any]
) -> None:
    async with app_client() as client:
        assert (await client.get("/v1/admin/setup")).status_code == 401
        await login(client, "max@halden.example", "halden")
        denied = await client.get("/v1/admin/setup")
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "permission_denied"
        rename = await client.patch(
            "/v1/admin/organization",
            json={"name": "X", "version": NOW.isoformat(), "reason": "r"},
            headers=CSRF,
        )
        assert rename.status_code == 403


async def test_a_fresh_organization_and_its_transitions(
    app_client: AppFactory,
    owner_db: Database,
    halden: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org: uuid.UUID = halden["org"]
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)
    async with app_client() as client:
        await login(client, "ada@halden.example", "halden")
        body = await _setup(client)
        assert body["organization"]["slug"] == "halden"
        assert _states(body) == {
            "organization": ("attention", "single_administrator"),
            "identity": ("required", "no_connection"),
            "members": ("blocked", "identity_required"),
            "provisioning": ("optional", "not_configured"),
            "ai_policy": ("optional", "evidence_only"),
            "sources": ("required", "no_sources"),
            "accounts": ("optional", "not_needed"),
            "first_answer": ("blocked", "sources_required"),
        }

        # Identity: connected, verified, then failing, then disabled.
        connection = await create_oidc_connection(owner_db, org)
        assert _states(await _setup(client))["identity"] == ("required", "not_verified")
        assert _states(await _setup(client))["members"] == ("complete", "members_active")
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(
                text("UPDATE oidc_connections SET last_success_at = now() - interval '1 hour'")
            )
        assert _states(await _setup(client))["identity"] == ("complete", "verified")
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(
                text(
                    "UPDATE oidc_connections SET last_failure_at = now(), "
                    "last_failure_code = 'provider_unavailable'"
                )
            )
        assert _states(await _setup(client))["identity"] == ("attention", "provider_unavailable")

        # Provisioning: a token, used by the identity provider, then all revoked.
        async with owner_db.tenant_transaction(org) as s:
            token: uuid.UUID = (
                await s.execute(
                    text(
                        "INSERT INTO scim_tokens (organization_id, connection_id, token_hash) "
                        "VALUES (:o, :c, sha256('t'::bytea)) RETURNING id"
                    ),
                    {"o": org, "c": connection},
                )
            ).scalar_one()
        assert _states(await _setup(client))["provisioning"] == ("optional", "token_not_used_yet")
        provisioned = await create_provisioned_member(
            owner_db, org, connection, "pat@halden.example", "sub-pat"
        )
        assert provisioned.membership_id is not None
        # The identity provider authenticated with the token (P12.4: last_used_at).
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(text("SELECT lattice_scim_token_used(:t)"), {"t": token})
        assert _states(await _setup(client))["provisioning"] == ("complete", "in_use")
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(text("UPDATE scim_tokens SET revoked_at = now()"))
        assert _states(await _setup(client))["provisioning"] == ("attention", "no_valid_token")

        # Sources: connected, queued, failed, synced; account mapping; first answer.
        source = await create_source(owner_db, org, "tickets", "ticketing")
        assert _states(await _setup(client))["sources"] == ("required", "not_synced")
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(
                text(
                    "INSERT INTO jobs (organization_id, kind, source_id, requested_by) "
                    "VALUES (:o, 'source_sync', :s, :m)"
                ),
                {"o": org, "s": source, "m": halden["admin"].membership_id},
            )
        assert _states(await _setup(client))["sources"] == ("in_progress", "syncing")
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(text("UPDATE jobs SET status = 'failed', finished_at = now()"))
            await s.execute(
                text(
                    "INSERT INTO source_sync_runs (organization_id, source_id, mode, status, "
                    "finished_at, started_at) VALUES (:o, :s, 'full', 'failed', now(), "
                    "now() - interval '2 minutes')"
                ),
                {"o": org, "s": source},
            )
        assert _states(await _setup(client))["sources"] == ("attention", "sync_failed")
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(text("UPDATE sources SET account_scoped = true"))
            run: uuid.UUID = (
                await s.execute(
                    text(
                        "INSERT INTO source_sync_runs (organization_id, source_id, mode, status, "
                        "finished_at) VALUES (:o, :s, 'full', 'partial', now()) RETURNING id"
                    ),
                    {"o": org, "s": source},
                )
            ).scalar_one()
            for ref in ("9001", "9001", "9002"):
                await s.execute(
                    text(
                        "INSERT INTO sync_record_failures (organization_id, sync_run_id, "
                        "opaque_ref, stage, error_code, message_params) VALUES (:o, :r, "
                        "'rec_0123456789abcdef', 'map_access', 'access_unmappable:account', "
                        "jsonb_build_object('external_account_ref', CAST(:x AS text)))"
                    ),
                    {"o": org, "r": run, "x": ref},
                )
        body = await _setup(client)
        assert _states(body)["sources"] == ("complete", "synced")
        accounts = {c["key"]: c for c in body["categories"]}["accounts"]
        assert (accounts["state"], accounts["reason"]) == ("attention", "unmapped_references")
        assert accounts["facts"]["unmapped_references"] == 2
        assert "9001" not in str(accounts)  # counts, not the references themselves
        assert _states(body)["first_answer"] == ("required", "no_answer_yet")
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(
                text(
                    "INSERT INTO query_traces (organization_id, membership_id, question_text, "
                    "question_sensitivity, authz_snapshot, retrieval, egress, validation, "
                    "status, timings_ms) VALUES (:o, :m, 'q', 'internal', '{}', '{}', '{}', "
                    "'{}', 'answered', '{}')"
                ),
                {"o": org, "m": halden["member"].membership_id},
            )
        assert _states(await _setup(client))["first_answer"] == ("complete", "answered")

        # AI policy: an external provider without a credential, then an encrypted one,
        # then a default question sensitivity the provider is not approved for.
        async with owner_db.tenant_transaction(org) as s:
            await configuration.configure_provider(
                s,
                org,
                purpose="generation",
                provider="gemini",
                model="gemini-2.5-flash",
                deployment="external_api",
                endpoint=None,
                retention_mode="none",
                training_opt_out=True,
                reason="test",
                actor="operator:test",
            )
        assert _states(await _setup(client))["ai_policy"] == ("attention", "credential_missing")
        async with owner_db.tenant_transaction(org) as s:
            await configuration.set_credential(
                s,
                org,
                provider="gemini",
                value=SecretStr("AIza-test-key-0123456789abcdef"),
                keyring=Keyring.from_environment(KEYS),
                data_use="paid",
                reason="test",
                actor="operator:test",
            )
        body = await _setup(client)
        ai = {c["key"]: c for c in body["categories"]}["ai_policy"]
        assert (ai["state"], ai["reason"]) == ("complete", "generation_ready")
        assert ai["facts"]["provider"] == "gemini"
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(
                text(
                    "INSERT INTO org_settings (organization_id, default_query_sensitivity) "
                    "VALUES (:o, 'confidential') ON CONFLICT (organization_id) DO UPDATE SET "
                    "default_query_sensitivity = 'confidential'"
                ),
                {"o": org},
            )
        assert _states(await _setup(client))["ai_policy"] == (
            "attention",
            "policy_excludes_default",
        )
        text_ = (await client.get("/v1/admin/setup")).text
        for hidden in ("vault:", "env:", "AIza", "tickets", "max@halden", "pat@halden", "sub-pat"):
            assert hidden not in text_

        # Identity disabled: sign-in stops; members become blocked.
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(text("UPDATE oidc_connections SET status = 'disabled'"))
        states = _states(await _setup(client))
        assert states["identity"] == ("attention", "connection_disabled")
        assert states["members"] == ("blocked", "identity_required")


async def test_each_administrator_sees_their_own_organization(
    app_client: AppFactory, owner_db: Database, halden: dict[str, Any]
) -> None:
    other = await create_org(owner_db, "veridian")
    await create_member(owner_db, other, "vic@veridian.example", is_admin=True)
    await create_member(owner_db, other, "val@veridian.example", is_admin=True)
    await create_oidc_connection(owner_db, other, login_key="veridiankeyveridiankey01")
    await create_source(owner_db, other, "docs")
    async with app_client() as client:
        await login(client, "ada@halden.example", "halden")
        mine = await _setup(client)
    async with app_client() as client:
        await login(client, "vic@veridian.example", "veridian")
        theirs = await _setup(client)
    assert mine["organization"]["slug"] == "halden"
    assert _states(mine)["identity"] == ("required", "no_connection")
    assert _states(mine)["sources"] == ("required", "no_sources")
    assert _states(mine)["organization"] == ("attention", "single_administrator")
    assert theirs["organization"]["slug"] == "veridian"
    assert _states(theirs)["identity"] == ("required", "not_verified")
    assert _states(theirs)["organization"] == ("complete", "active")


async def test_setup_reads_are_not_audited(
    app_client: AppFactory, owner_db: Database, halden: dict[str, Any]
) -> None:
    async with app_client() as client:
        await login(client, "ada@halden.example", "halden")
        await _setup(client)
    async with owner_db.tenant_snapshot(halden["org"]) as s:
        count: int = (await s.execute(text("SELECT count(*) FROM audit_events"))).scalar_one()
    assert count == 0


# --- rename: the first ADR-017 fence -------------------------------------------------------


async def test_rename_is_audited_and_versioned(
    app_client: AppFactory, owner_db: Database, halden: dict[str, Any]
) -> None:
    org = halden["org"]
    async with app_client() as client:
        await login(client, "ada@halden.example", "halden")
        version = (await _setup(client))["organization"]["version"]
        renamed = await client.patch(
            "/v1/admin/organization",
            json={"name": "  Halden Group  ", "version": version, "reason": "Rebrand"},
            headers=CSRF,
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "Halden Group"
        stale = await client.patch(
            "/v1/admin/organization",
            json={"name": "Other", "version": version, "reason": "Late"},
            headers=CSRF,
        )
        assert stale.status_code == 409
        current = renamed.json()["version"]
        same = await client.patch(
            "/v1/admin/organization",
            json={"name": "Halden Group", "version": current, "reason": "No-op"},
            headers=CSRF,
        )
        assert same.status_code == 200
        for bad in (
            {"name": "", "version": current, "reason": "r"},
            {"name": "x\u0007y", "version": current, "reason": "r"},
            {"name": "x" * 201, "version": current, "reason": "r"},
            {"name": "ok", "version": current, "reason": "  "},
            {"name": "ok", "version": current, "reason": "r", "slug": "evil"},
        ):
            response = await client.patch("/v1/admin/organization", json=bad, headers=CSRF)
            assert response.status_code == 422, bad
        no_csrf = await client.patch(
            "/v1/admin/organization",
            json={"name": "ok", "version": current, "reason": "r"},
        )
        assert no_csrf.status_code == 403
    async with owner_db.tenant_snapshot(org) as s:
        events = (
            (
                await s.execute(
                    text("SELECT actor, action, target, before, after, reason FROM audit_events")
                )
            )
            .mappings()
            .all()
        )
        slug: str = (await s.execute(text("SELECT slug FROM organizations"))).scalar_one()
    assert len(events) == 1  # the no-op wrote nothing
    [event] = events
    assert event["action"] == "organization.updated"
    assert event["actor"] == f"member:{halden['admin'].membership_id}"
    assert event["target"] == {"organization_id": str(org)}
    assert (event["before"], event["after"]) == ({"name": "Halden"}, {"name": "Halden Group"})
    assert event["reason"] == "Rebrand"
    assert slug == "halden"


async def test_the_api_role_cannot_rename_without_the_event(
    app_db: Database, owner_db: Database, halden: dict[str, Any]
) -> None:
    org = halden["org"]
    other = await create_org(owner_db, "veridian")
    with pytest.raises(DBAPIError, match="without an audit event"):
        async with app_db.tenant_transaction(org) as s:
            await s.execute(
                text("UPDATE organizations SET name = 'Sneaky' WHERE id = :o"), {"o": org}
            )
    # An event of another action, or for another organization, does not count.
    for action, target in (("access.member_changed", org), ("organization.updated", other)):
        with pytest.raises(DBAPIError):
            async with app_db.tenant_transaction(org) as s:
                await s.execute(
                    text("UPDATE organizations SET name = 'Sneaky' WHERE id = :o"), {"o": org}
                )
                await s.execute(
                    text(
                        "INSERT INTO audit_events (organization_id, actor, action, target, "
                        "reason) VALUES (:o, 'member:x', :a, jsonb_build_object("
                        "'organization_id', CAST(:t AS text), 'membership_id', 'x'), 'r')"
                    ),
                    {"o": org, "a": action, "t": str(target)},
                )
    # The event alone, with the change, commits.
    async with app_db.tenant_transaction(org) as s:
        await s.execute(text("UPDATE organizations SET name = 'Coupled' WHERE id = :o"), {"o": org})
        await s.execute(
            text(
                "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
                "VALUES (:o, 'member:x', 'organization.updated', "
                "jsonb_build_object('organization_id', CAST(:t AS text)), 'r')"
            ),
            {"o": org, "t": str(org)},
        )
    # Another organization's row is invisible, so nothing is updated there.
    async with app_db.tenant_transaction(org) as s:
        changed = await s.execute(
            text("UPDATE organizations SET name = 'Cross' WHERE id = :o RETURNING id"),
            {"o": other},
        )
        assert changed.first() is None
    async with owner_db.unscoped_transaction() as s:
        names = {
            str(slug): str(name)
            for slug, name in (await s.execute(text("SELECT slug, name FROM organizations"))).all()
        }
    assert names == {"halden": "Coupled", "veridian": "Veridian"}


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE organizations SET slug = 'x'",
        "UPDATE organizations SET status = 'suspended'",
        "UPDATE organizations SET created_at = now()",
        "INSERT INTO organizations (slug, name) VALUES ('new-org', 'New')",
        "DELETE FROM organizations",
        "UPDATE org_settings SET external_egress_level = 'restricted'",
        "UPDATE org_settings SET data_origin = 'synthetic'",
        "UPDATE memberships SET is_admin = true",
        "INSERT INTO oidc_connections (organization_id, login_key, issuer, client_id) VALUES "
        "(lattice_current_org_id(), 'kkkkkkkkkkkkkkkkkkkkkk', 'https://i.example', 'c')",
        "UPDATE oidc_connections SET client_id = 'x'",
        "INSERT INTO scim_tokens (organization_id, connection_id, token_hash) VALUES "
        "(lattice_current_org_id(), gen_random_uuid(), sha256('x'::bytea))",
        "UPDATE model_providers SET approved_for_level = 'restricted'",
        "UPDATE provider_credentials SET secret_ref = 'env:LATTICE_PROVIDER_SECRET_X'",
        "UPDATE sources SET config = '{}'",
        "INSERT INTO outbound_targets (organization_id, purpose, scheme, host, port) VALUES "
        "(lattice_current_org_id(), 'generation', 'http', '10.0.0.1', 80)",
    ],
)
async def test_setup_tables_stay_unwritable_by_the_api_role(
    app_db: Database, halden: dict[str, Any], statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_db.tenant_transaction(halden["org"]) as s:
            await s.execute(text(statement))


async def test_a_suspended_organization_cannot_be_renamed_by_the_api_role(
    app_db: Database, owner_db: Database
) -> None:
    org = await create_org(owner_db, "halden", status="suspended")
    async with app_db.tenant_transaction(org) as s:
        changed = await s.execute(
            text("UPDATE organizations SET name = 'X' WHERE id = :o RETURNING id"), {"o": org}
        )
        assert changed.first() is None


async def test_the_audit_policy_admits_only_member_organization_events(
    app_db: Database, halden: dict[str, Any]
) -> None:
    org = halden["org"]
    for actor, action in (
        ("scim:x", "organization.updated"),
        ("operator:x", "organization.updated"),
        ("member:x", "organization.created"),
        ("member:x", "secret.rekeyed"),
        ("member:x", "outbound.target_added"),
        # P12.5 admits egress events from member actors only (ADR-017).
        ("operator:x", "egress.levels_changed"),
        ("scim:x", "provider.approval_changed"),
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            async with app_db.tenant_transaction(org) as s:
                await s.execute(
                    text(
                        "INSERT INTO audit_events (organization_id, actor, action, target, "
                        "reason) VALUES (:o, :a, :x, '{}', 'r')"
                    ),
                    {"o": org, "a": actor, "x": action},
                )
