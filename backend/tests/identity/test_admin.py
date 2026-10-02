"""Operator identity controls (Phase 10): ``lattice sso``, ``lattice scim token`` and
``lattice member admin`` -- audited, with no secret or secret reference in any event,
and no path from the API."""

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from lattice.cli import main
from lattice.db.engine import Database
from lattice.identity import admin
from lattice.provisioning.service import resolve_token

from ..conftest import OWNER_URL
from ..factories import create_member, create_org

ACTOR = "operator:test"


async def _events(owner_db: Database, org: uuid.UUID) -> list[dict[str, Any]]:
    async with owner_db.tenant_transaction(org) as s:
        rows = await s.execute(
            text(
                "SELECT actor, action, target, before, after, reason FROM audit_events "
                "ORDER BY created_at, id"
            )
        )
        return [dict(r._mapping) for r in rows]


async def _add(owner_db: Database, org: uuid.UUID, **overrides: Any) -> admin.ConnectionView:
    values: dict[str, Any] = {
        "issuer": "https://idp.halden.example",
        "client_id": "lattice",
        "secret_ref": "env:LATTICE_OIDC_SECRET_HALDEN",
        "subject_claim": "sub",
        "reason": "Pilot SSO",
        "actor": ACTOR,
    }
    values.update(overrides)
    async with owner_db.tenant_transaction(org) as s:
        return await admin.add_connection(s, org, **values)


async def test_a_connection_is_created_audited_and_unique(owner_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    found = await _add(owner_db, org)
    assert len(found.login_key) >= 32
    assert found.confidential
    events = await _events(owner_db, org)
    assert [(e["action"], e["actor"]) for e in events] == [("sso.connection_created", ACTOR)]
    assert events[0]["after"]["client_secret"] == "reference"
    assert "LATTICE_OIDC_SECRET" not in str(events)
    with pytest.raises(admin.OperatorRejected, match="already has an active connection"):
        await _add(owner_db, org)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"issuer": "http://idp.example"}, "https"),
        ({"issuer": "https://user:pw@idp.example"}, "credentials"),
        ({"secret_ref": "env:LATTICE_DATABASE_URL"}, "reference"),
        ({"subject_claim": "bad claim"}, "refused by the database"),
    ],
)
async def test_invalid_connections_are_refused(
    owner_db: Database, overrides: dict[str, Any], message: str
) -> None:
    org = await create_org(owner_db, "halden")
    with pytest.raises(admin.OperatorRejected, match=message):
        await _add(owner_db, org, **overrides)
    assert await _events(owner_db, org) == []


async def test_http_is_allowed_for_a_local_issuer_only(owner_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    found = await _add(owner_db, org, issuer="http://127.0.0.1:9000/realms/dev")
    assert found.issuer == "http://127.0.0.1:9000/realms/dev"


async def test_scim_tokens_are_shown_once_stored_hashed_and_revocable(
    owner_db: Database, app_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    async with owner_db.tenant_transaction(org) as s:
        with pytest.raises(admin.OperatorRejected, match="active OIDC connection"):
            await admin.create_scim_token(s, org, reason="r", actor=ACTOR)
    connection = await _add(owner_db, org)
    async with owner_db.tenant_transaction(org) as s:
        token_id, token = await admin.create_scim_token(s, org, reason="Okta", actor=ACTOR)
    caller = await resolve_token(app_db, token)
    assert caller is not None
    assert (caller.token_id, caller.organization_id, caller.connection_id) == (
        token_id,
        org,
        connection.id,
    )
    async with owner_db.tenant_transaction(org) as s:
        stored: bytes = (await s.execute(text("SELECT token_hash FROM scim_tokens"))).scalar_one()
    assert token.encode() not in bytes(stored)
    assert token not in str(await _events(owner_db, org))
    async with owner_db.tenant_transaction(org) as s:
        assert await admin.revoke_scim_token(s, org, token_id, reason="Rotated", actor=ACTOR)
        assert not await admin.revoke_scim_token(s, org, token_id, reason="Again", actor=ACTOR)
    assert await resolve_token(app_db, token) is None
    actions = [e["action"] for e in await _events(owner_db, org)]
    assert actions == ["sso.connection_created", "scim.token_created", "scim.token_revoked"]


async def test_disabling_the_connection_stops_its_tokens(
    owner_db: Database, app_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    await _add(owner_db, org)
    async with owner_db.tenant_transaction(org) as s:
        _, token = await admin.create_scim_token(s, org, reason="Okta", actor=ACTOR)
        await admin.disable_connection(s, org, reason="Leaving Okta", actor=ACTOR)
    assert await resolve_token(app_db, token) is None
    # A new connection can then be added.
    await _add(owner_db, org, issuer="https://login.example")


async def test_the_administrator_capability_is_an_audited_operator_action(
    owner_db: Database,
) -> None:
    org = await create_org(owner_db, "halden")
    member = await create_member(owner_db, org, "pat@halden.example")
    async with owner_db.tenant_transaction(org) as s:
        assert await admin.set_admin(
            s, org, email="pat@halden.example", grant=True, reason="First admin", actor=ACTOR
        )
        assert not await admin.set_admin(
            s, org, email="pat@halden.example", grant=True, reason="Again", actor=ACTOR
        )
    events = await _events(owner_db, org)
    assert len(events) == 1
    assert events[0]["target"] == {"membership_id": str(member.membership_id)}
    assert (events[0]["before"]["is_admin"], events[0]["after"]["is_admin"]) == (False, True)
    async with owner_db.tenant_transaction(org) as s:
        await admin.set_admin(
            s, org, email="pat@halden.example", grant=False, reason="Handover", actor=ACTOR
        )
        is_admin: bool = (
            await s.execute(
                text("SELECT is_admin FROM memberships WHERE id = :m"), {"m": member.membership_id}
            )
        ).scalar_one()
    assert is_admin is False


async def test_admin_changes_refuse_removed_or_ambiguous_members(owner_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    await create_member(owner_db, org, "gone@halden.example", status="removed")
    async with owner_db.tenant_transaction(org) as s:
        with pytest.raises(admin.OperatorRejected, match="removed"):
            await admin.set_admin(
                s, org, email="gone@halden.example", grant=True, reason="r", actor=ACTOR
            )
        with pytest.raises(admin.OperatorRejected, match="no single member"):
            await admin.set_admin(
                s, org, email="nobody@halden.example", grant=True, reason="r", actor=ACTOR
            )


async def test_the_cli(
    owner_db: Database, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    org = await create_org(owner_db, "halden")
    await create_member(owner_db, org, "pat@halden.example")
    monkeypatch.setenv("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL)
    monkeypatch.setenv("LATTICE_PUBLIC_URL", "https://lattice.example.com")

    def run(*argv: str) -> int:
        return main(list(argv))

    assert (
        await asyncio.to_thread(
            run,
            "sso",
            "add",
            "--org",
            "halden",
            "--issuer",
            "https://idp.halden.example",
            "--client-id",
            "lattice",
            "--reason",
            "Pilot",
        )
        == 0
    )
    shown = capsys.readouterr().out
    assert "https://lattice.example.com/sso/" in shown
    assert "https://lattice.example.com/api/scim/v2" in shown
    assert (
        await asyncio.to_thread(
            run, "scim", "token", "create", "--org", "halden", "--reason", "Okta"
        )
        == 0
    )
    created = capsys.readouterr().out
    token = next(line for line in created.splitlines() if line.startswith("lsc_"))
    assert await asyncio.to_thread(run, "scim", "token", "list", "--org", "halden") == 0
    assert token not in capsys.readouterr().out
    assert (
        await asyncio.to_thread(
            run,
            "member",
            "admin",
            "--org",
            "halden",
            "--email",
            "pat@halden.example",
            "--grant",
            "--reason",
            "First admin",
        )
        == 0
    )
    assert "now an administrator" in capsys.readouterr().out
    assert (
        await asyncio.to_thread(
            run,
            "sso",
            "add",
            "--org",
            "nowhere",
            "--issuer",
            "https://x.example",
            "--client-id",
            "c",
            "--reason",
            "r",
        )
        == 1
    )
    assert (
        await asyncio.to_thread(
            run,
            "member",
            "admin",
            "--org",
            "halden",
            "--email",
            "pat@halden.example",
            "--revoke",
            "--reason",
            " ",
        )
        == 1
    )
