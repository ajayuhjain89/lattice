"""Operator lifecycle and incident controls (Phase 11, P11.3; D-25, D-26): the SSO
connection's disable/enable/update and guarded add, member suspension and reinstatement,
session revocation and the organization emergency stop. Each is one transaction with one
audit event (``operator:<user>``, a reason, never a secret or secret reference), and
refuses what would orphan provisioned members or revive a session."""

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from lattice.cli import main
from lattice.db.engine import Database
from lattice.identity import admin
from lattice.identity.sessions import SessionPolicy, SessionService

from ..conftest import OWNER_URL
from ..factories import Member, create_member, create_org, create_provisioned_member

ACTOR = "operator:test"
_HOUR = timedelta(hours=1)


async def _events(owner_db: Database, org: uuid.UUID) -> list[dict[str, Any]]:
    async with owner_db.tenant_transaction(org) as s:
        rows = await s.execute(
            text(
                "SELECT actor, action, target, before, after, reason FROM audit_events "
                "ORDER BY created_at, id"
            )
        )
        return [dict(r._mapping) for r in rows]


async def _op(owner_db: Database, org: uuid.UUID, call: Any) -> Any:
    async with owner_db.tenant_transaction(org) as s:
        return await call(s)


async def _connection(owner_db: Database, org: uuid.UUID, **overrides: Any) -> admin.ConnectionView:
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


async def _session(
    app_db: Database, org: uuid.UUID, member: Member, connection: uuid.UUID | None = None
) -> str:
    sessions = SessionService(app_db, SessionPolicy(idle_timeout=_HOUR, absolute_timeout=_HOUR))
    token, _ = await sessions.create(
        user_id=member.user_id, organization_id=org, oidc_connection_id=connection
    )
    return token


async def _live(owner_db: Database, org: uuid.UUID) -> int:
    async with owner_db.tenant_transaction(org) as s:
        count: int = (
            await s.execute(text("SELECT count(*) FROM sessions WHERE revoked_at IS NULL"))
        ).scalar_one()
    return count


async def _member_status(owner_db: Database, org: uuid.UUID, member: Member) -> str:
    async with owner_db.tenant_transaction(org) as s:
        status: str = (
            await s.execute(
                text("SELECT status FROM memberships WHERE id = :m"), {"m": member.membership_id}
            )
        ).scalar_one()
    return status


# --- SSO connection lifecycle -----------------------------------------------------------


async def test_disable_revokes_only_the_connections_sessions(
    owner_db: Database, app_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    connection = await _connection(owner_db, org)
    pat = await create_provisioned_member(owner_db, org, connection.id, "pat@halden.example", "p")
    maya = await create_member(owner_db, org, "maya@halden.example")
    await _session(app_db, org, pat, connection.id)
    await _session(app_db, org, pat, connection.id)
    await _session(app_db, org, maya)  # development sign-in: no connection

    disabled, revoked = await _op(
        owner_db, org, lambda s: admin.disable_connection(s, org, reason="Leaving", actor=ACTOR)
    )

    assert (disabled.status, revoked) == ("disabled", 2)
    assert await _live(owner_db, org) == 1
    event = (await _events(owner_db, org))[-1]
    assert (event["action"], event["actor"], event["reason"]) == (
        "sso.connection_disabled",
        ACTOR,
        "Leaving",
    )
    assert event["after"] == {"status": "disabled", "sessions_revoked": 2}


async def test_a_session_cannot_be_created_for_an_inactive_connection(
    owner_db: Database, app_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    connection = await _connection(owner_db, org)
    pat = await create_provisioned_member(owner_db, org, connection.id, "pat@halden.example", "p")
    await _op(owner_db, org, lambda s: admin.disable_connection(s, org, reason="r", actor=ACTOR))
    with pytest.raises(Exception, match="authentication"):
        await _session(app_db, org, pat, connection.id)
    other = await create_org(owner_db, "veridian")
    foreign = await _connection(owner_db, other)
    with pytest.raises(Exception, match="authentication"):  # another organization's connection
        await _session(app_db, org, pat, foreign.id)
    assert await _live(owner_db, org) == 0


async def test_enable_restores_the_same_connection(owner_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    with pytest.raises(admin.OperatorRejected, match="no disabled connection"):
        await _op(
            owner_db,
            org,
            lambda s: admin.enable_connection(s, org, connection_id=None, reason="r", actor=ACTOR),
        )
    first = await _connection(owner_db, org)
    with pytest.raises(admin.OperatorRejected, match="already has an active"):
        await _op(
            owner_db,
            org,
            lambda s: admin.enable_connection(s, org, connection_id=None, reason="r", actor=ACTOR),
        )
    await _op(owner_db, org, lambda s: admin.disable_connection(s, org, reason="r", actor=ACTOR))
    second = await _connection(owner_db, org, issuer="https://login.example")
    await _op(owner_db, org, lambda s: admin.disable_connection(s, org, reason="r", actor=ACTOR))
    with pytest.raises(admin.OperatorRejected, match="--connection-id"):
        await _op(
            owner_db,
            org,
            lambda s: admin.enable_connection(s, org, connection_id=None, reason="r", actor=ACTOR),
        )
    enabled = await _op(
        owner_db,
        org,
        lambda s: admin.enable_connection(
            s, org, connection_id=first.id, reason="Back to Okta", actor=ACTOR
        ),
    )
    assert (enabled.id, enabled.login_key, enabled.status) == (first.id, first.login_key, "active")
    assert second.id != first.id
    listed = await _op(owner_db, org, lambda s: admin.connections(s, org))
    assert [(c.id, c.status) for c in listed] == [(first.id, "active"), (second.id, "disabled")]
    event = (await _events(owner_db, org))[-1]
    assert (event["action"], event["target"], event["after"]) == (
        "sso.connection_enabled",
        {"connection_id": str(first.id)},
        {"status": "active"},
    )


async def test_update_changes_a_connection_in_place(owner_db: Database, app_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    connection = await _connection(owner_db, org)
    pat = await create_provisioned_member(owner_db, org, connection.id, "pat@halden.example", "p")
    await _session(app_db, org, pat, connection.id)

    async def update(**changes: Any) -> tuple[admin.ConnectionView, int]:
        result: tuple[admin.ConnectionView, int] = await _op(
            owner_db,
            org,
            lambda s: admin.update_connection(
                s, org, connection_id=None, reason="change", actor=ACTOR, **changes
            ),
        )
        return result

    with pytest.raises(admin.OperatorRejected, match="nothing to change"):
        await update(client_id="lattice")
    view, revoked = await update(client_id="lattice-v2")
    assert (view.id, view.client_id, revoked) == (connection.id, "lattice-v2", 0)
    view, revoked = await update(secret_ref="env:LATTICE_OIDC_SECRET_HALDEN_NEXT")
    assert (view.confidential, revoked) == (True, 0)
    with pytest.raises(admin.OperatorRejected, match="reference"):
        await update(secret_ref="env:LATTICE_DATABASE_URL")
    with pytest.raises(admin.OperatorRejected, match="https"):
        await update(issuer="http://idp.example")
    with pytest.raises(admin.OperatorRejected, match="refused by the database"):
        await update(subject_claim="bad claim", subjects_unchanged=True)
    assert await _live(owner_db, org) == 1  # nothing so far touched a session
    with pytest.raises(admin.OperatorRejected, match="--subjects-unchanged"):
        await update(issuer="https://login.halden.example")
    view, revoked = await update(issuer="https://login.halden.example", subjects_unchanged=True)
    assert (view.issuer, revoked) == ("https://login.halden.example", 1)
    view, _ = await update(secret_ref=None)
    assert not view.confidential

    events = [e for e in await _events(owner_db, org) if e["action"] == "sso.connection_updated"]
    assert [e["after"].get("client_secret") for e in events] == [
        "reference",
        "reference (rotated)",
        "reference",
        "none",
    ]
    assert events[2]["after"]["sessions_revoked"] == 1
    assert "LATTICE_OIDC_SECRET" not in str(await _events(owner_db, org))
    assert view.login_key == connection.login_key  # the sign-in URL never changes


async def test_add_refuses_to_orphan_provisioned_members(owner_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    old = await _connection(owner_db, org)
    await create_provisioned_member(owner_db, org, old.id, "pat@halden.example", "p")
    await _op(owner_db, org, lambda s: admin.disable_connection(s, org, reason="r", actor=ACTOR))
    with pytest.raises(admin.OperatorRejected, match="1 provisioned member"):
        await _connection(owner_db, org, issuer="https://login.example")
    added = await _connection(
        owner_db, org, issuer="https://login.example", new_identity_namespace=True
    )
    assert added.id != old.id
    event = (await _events(owner_db, org))[-1]
    assert (event["action"], event["after"]["new_identity_namespace"]) == (
        "sso.connection_created",
        True,
    )


async def test_add_is_free_when_the_disabled_connections_members_were_removed(
    owner_db: Database,
) -> None:
    org = await create_org(owner_db, "halden")
    old = await _connection(owner_db, org)
    await create_provisioned_member(
        owner_db, org, old.id, "pat@halden.example", "p", status="removed"
    )
    await _op(owner_db, org, lambda s: admin.disable_connection(s, org, reason="r", actor=ACTOR))
    added = await _connection(owner_db, org, issuer="https://login.example")
    assert "new_identity_namespace" not in (await _events(owner_db, org))[-1]["after"]
    assert added.status == "active"


# --- members, sessions, organization ----------------------------------------------------


async def test_suspend_and_reinstate_a_member(owner_db: Database, app_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    maya = await create_member(owner_db, org, "maya@halden.example")
    await _session(app_db, org, maya)
    await _session(app_db, org, maya)

    suspended = await _op(
        owner_db,
        org,
        lambda s: admin.suspend_member(
            s, org, email="maya@halden.example", reason="Lost laptop", actor=ACTOR
        ),
    )
    assert suspended is not None
    member, revoked = suspended
    assert (member.membership_id, member.provisioned, revoked) == (maya.membership_id, False, 2)
    assert await _member_status(owner_db, org, maya) == "suspended"
    assert await _live(owner_db, org) == 0
    again = await _op(
        owner_db,
        org,
        lambda s: admin.suspend_member(
            s, org, email="maya@halden.example", reason="r", actor=ACTOR
        ),
    )
    assert again is None  # nothing written the second time

    reinstated = await _op(
        owner_db,
        org,
        lambda s: admin.reinstate_member(
            s, org, email="maya@halden.example", reason="Laptop found", actor=ACTOR
        ),
    )
    assert reinstated is not None
    assert await _member_status(owner_db, org, maya) == "active"
    assert await _live(owner_db, org) == 0  # no session comes back

    events = await _events(owner_db, org)
    assert [(e["action"], e["before"]["status"], e["after"]["status"]) for e in events] == [
        ("access.member_changed", "active", "suspended"),
        ("access.member_changed", "suspended", "active"),
    ]
    assert {e["target"]["membership_id"] for e in events} == {str(maya.membership_id)}
    assert [e["reason"] for e in events] == ["Lost laptop", "Laptop found"]


async def test_reinstate_is_the_operator_path_after_a_scim_removal(owner_db: Database) -> None:
    """D-20: SCIM removal is terminal for the identity provider; re-hiring the person is
    an explicit, audited operator act."""
    org = await create_org(owner_db, "halden")
    connection = await _connection(owner_db, org)
    pat = await create_provisioned_member(
        owner_db, org, connection.id, "pat@halden.example", "p", status="removed"
    )
    with pytest.raises(admin.OperatorRejected, match="removed"):
        await _op(
            owner_db,
            org,
            lambda s: admin.suspend_member(
                s, org, email="pat@halden.example", reason="r", actor=ACTOR
            ),
        )
    await _op(
        owner_db,
        org,
        lambda s: admin.reinstate_member(
            s, org, email="pat@halden.example", reason="Re-hired", actor=ACTOR
        ),
    )
    assert await _member_status(owner_db, org, pat) == "active"
    with pytest.raises(admin.OperatorRejected, match="no single member"):
        await _op(
            owner_db,
            org,
            lambda s: admin.reinstate_member(
                s, org, email="nobody@halden.example", reason="r", actor=ACTOR
            ),
        )


async def test_session_revocation_by_member_and_for_everyone(
    owner_db: Database, app_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    maya = await create_member(owner_db, org, "maya@halden.example")
    lena = await create_member(owner_db, org, "lena@halden.example")
    other = await create_org(owner_db, "veridian")
    ola = await create_member(owner_db, other, "ola@veridian.example")
    for member in (maya, maya, lena):
        await _session(app_db, org, member)
    await _session(app_db, other, ola)

    one = await _op(
        owner_db,
        org,
        lambda s: admin.revoke_sessions(
            s, org, email="maya@halden.example", reason="Shared device", actor=ACTOR
        ),
    )
    everyone = await _op(
        owner_db,
        org,
        lambda s: admin.revoke_sessions(s, org, email=None, reason="Key leak", actor=ACTOR),
    )
    nothing = await _op(
        owner_db,
        org,
        lambda s: admin.revoke_sessions(s, org, email=None, reason="Again", actor=ACTOR),
    )
    assert (one, everyone, nothing) == (2, 1, 0)
    assert await _live(owner_db, other) == 1  # another organization is untouched
    events = await _events(owner_db, org)
    assert [(e["action"], e["target"], e["after"]) for e in events] == [
        ("session.revoked", {"membership_id": str(maya.membership_id)}, {"sessions_revoked": 2}),
        ("session.revoked", {"scope": "organization"}, {"sessions_revoked": 1}),
        ("session.revoked", {"scope": "organization"}, {"sessions_revoked": 0}),
    ]


async def test_organization_suspension_and_reactivation(
    owner_db: Database, app_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    maya = await create_member(owner_db, org, "maya@halden.example")
    await _session(app_db, org, maya)

    def status(suspend: bool, reason: str) -> Any:
        return lambda s: admin.set_organization_status(
            s, org, suspend=suspend, reason=reason, actor=ACTOR
        )

    assert await _op(owner_db, org, status(True, "Contract dispute")) == 1
    assert await _op(owner_db, org, status(True, "again")) is None
    assert await _live(owner_db, org) == 0
    assert await _op(owner_db, org, status(False, "Resolved")) == 0
    assert await _op(owner_db, org, status(False, "again")) is None
    events = await _events(owner_db, org)
    assert [(e["action"], e["before"], e["after"]) for e in events] == [
        (
            "organization.suspended",
            {"status": "active"},
            {"status": "suspended", "sessions_revoked": 1},
        ),
        ("organization.reactivated", {"status": "suspended"}, {"status": "active"}),
    ]
    async with owner_db.tenant_transaction(org) as s:
        await s.execute(text("UPDATE organizations SET status = 'deleting'"))
    with pytest.raises(admin.OperatorRejected, match="deleting"):
        await _op(owner_db, org, status(False, "r"))


# --- the commands ---------------------------------------------------------------------------


async def test_the_lifecycle_commands(
    owner_db: Database,
    app_db: Database,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    org = await create_org(owner_db, "halden")
    maya = await create_member(owner_db, org, "maya@halden.example")
    monkeypatch.setenv("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL)
    monkeypatch.setenv("LATTICE_PUBLIC_URL", "https://lattice.example.com")

    async def run(*argv: str) -> tuple[int, str]:
        code = await asyncio.to_thread(main, list(argv))
        return code, capsys.readouterr().out

    def org_args(*argv: str) -> list[str]:
        return [*argv, "--org", "halden"]

    added = await _connection(owner_db, org)
    code, out = await run(*org_args("sso", "list"))
    assert code == 0
    assert f"{added.id}  active" in out
    code, out = await run(*org_args("sso", "disable"), "--reason", "r")
    assert code == 0
    assert "0 session(s) it issued revoked" in out
    code, out = await run(*org_args("sso", "enable"), "--reason", "r")
    assert code == 0
    assert "(active)" in out
    code, out = await run(*org_args("sso", "update"), "--client-id", "v2", "--reason", "r")
    assert code == 0
    assert "v2 (confidential)" in out
    code, out = await run(*org_args("sso", "update"), "--no-client-secret", "--reason", "r")
    assert code == 0
    assert "public, PKCE" in out
    code, _ = await run(
        *org_args("sso", "update"), "--issuer", "https://x.example", "--reason", "r"
    )
    assert code == 1  # an identity-shape change needs --subjects-unchanged
    code, _ = await run(*org_args("sso", "enable"), "--connection-id", "nope", "--reason", "r")
    assert code == 1

    await _session(app_db, org, maya)
    email = ["--email", "maya@halden.example"]
    code, out = await run(*org_args("member", "suspend"), *email, "--reason", "r")
    assert code == 0
    assert "suspended; 1 session(s) revoked" in out
    code, out = await run(*org_args("member", "suspend"), *email, "--reason", "r")
    assert code == 0
    assert "already suspended" in out
    code, out = await run(*org_args("member", "reinstate"), *email, "--reason", "r")
    assert code == 0
    assert "active again" in out
    await _session(app_db, org, maya)
    code, out = await run(*org_args("session", "revoke"), "--all", "--reason", "r")
    assert code == 0
    assert "1 session(s) of every member revoked" in out
    code, out = await run(*org_args("session", "revoke"), *email, "--reason", "r")
    assert code == 0
    assert "0 session(s) of maya@halden.example revoked" in out
    with pytest.raises(SystemExit):  # --email or --all is required (argparse)
        await run(*org_args("session", "revoke"), "--reason", "r")
    capsys.readouterr()
    code, out = await run(*org_args("org", "suspend"), "--reason", "r")
    assert code == 0
    assert "suspended" in out
    code, out = await run(*org_args("org", "suspend"), "--reason", "r")
    assert code == 0
    assert "already suspended" in out
    code, out = await run(*org_args("org", "reactivate"), "--reason", "r")
    assert code == 0
    assert "reactivated" in out
    code, _ = await run(*org_args("org", "reactivate"), "--reason", " ")
    assert code == 1  # a reason is required
    actors = {e["actor"] for e in await _events(owner_db, org)}
    assert all(a.startswith("operator:") for a in actors)
