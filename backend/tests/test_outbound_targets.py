"""Operator-registered private outbound targets (C4): exact, per organization and
purpose, audited, revocable -- and the policy built from them admits only that."""

import asyncio
import ipaddress

import pytest
from sqlalchemy import text

from lattice.cli import main
from lattice.db.engine import Database
from lattice.orgs.outbound_targets import (
    TargetRejected,
    add_target,
    list_targets,
    policy_for,
    registration_target,
    revoke_target,
)
from lattice.outbound import IpAddress, OutboundRefused, Target

from .conftest import OWNER_URL
from .factories import create_org

ACTOR = "operator:test"


async def private(host: str, port: int) -> list[IpAddress]:
    return [ipaddress.ip_address("10.20.30.40")]


@pytest.mark.parametrize(
    "url",
    [
        "https://169.254.169.254",
        "http://[fe80::1]:80",
        "https://224.0.0.1",
        "https://user:pw@idp.internal",
        "https://idp.internal/?q=1",
        "ftp://idp.internal",
        "https://*.internal",
    ],
)
def test_never_registrable(url: str) -> None:
    with pytest.raises(TargetRejected):
        registration_target(url)


def test_a_registration_is_an_exact_origin() -> None:
    assert registration_target("https://IdP.Internal:9443/realms/x") == Target(
        "https", "idp.internal", 9443
    )
    assert registration_target("http://10.0.0.5:11434").origin == "http://10.0.0.5:11434"
    # A path is never a range: "/8" registers the one address and nothing around it.
    assert registration_target("https://10.0.0.0/8").origin == "https://10.0.0.0:443"


async def test_targets_are_per_organization_and_purpose(owner_db: Database) -> None:
    halden = await create_org(owner_db, "halden")
    veridian = await create_org(owner_db, "veridian")
    target = registration_target("https://llm.halden.internal:8443")
    async with owner_db.tenant_transaction(halden) as s:
        found, added = await add_target(
            s, halden, target=target, purpose="generation", reason="D-15 server", actor=ACTOR
        )
        again, added_again = await add_target(
            s, halden, target=target, purpose="generation", reason="retry", actor=ACTOR
        )
        assert added
        assert not added_again
        assert again.id == found.id
        halden_generation = await policy_for(s, halden, "generation", resolver=private)
        halden_oidc = await policy_for(s, halden, "oidc", resolver=private)
    async with owner_db.tenant_transaction(veridian) as s:
        veridian_generation = await policy_for(s, veridian, "generation", resolver=private)
    url = "https://llm.halden.internal:8443/v1"
    assert await halden_generation.check_target(url, base=False) == target
    for other in (halden_oidc, veridian_generation):
        with pytest.raises(OutboundRefused):
            await other.check_target(url, base=False)
    # Revoked: refused again; the row is kept for the record.
    async with owner_db.tenant_transaction(halden) as s:
        revoked = await revoke_target(s, halden, found.id, reason="decommissioned", actor=ACTOR)
        assert revoked is not None
        assert await revoke_target(s, halden, found.id, reason="again", actor=ACTOR) is None
        after = await policy_for(s, halden, "generation", resolver=private)
        listed = await list_targets(s, halden)
        events = (
            await s.execute(
                text("SELECT action, after, before FROM audit_events ORDER BY created_at")
            )
        ).all()
    with pytest.raises(OutboundRefused):
        await after.check_target(url, base=False)
    assert len(listed) == 1
    assert listed[0].revoked_at is not None
    assert [e.action for e in events] == ["outbound.target_added", "outbound.target_removed"]
    assert events[0].after == {
        "purpose": "generation",
        "origin": "https://llm.halden.internal:8443",
    }
    # Another organization cannot revoke it either (its rows are invisible there).
    async with owner_db.tenant_transaction(veridian) as s:
        assert await revoke_target(s, veridian, found.id, reason="x", actor=ACTOR) is None


async def test_the_cli(
    owner_db: Database, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    await create_org(owner_db, "halden")
    monkeypatch.setenv("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL)
    allow = [
        "outbound", "allow", "--org", "halden", "--url", "https://idp.halden.internal:9443",
        "--purpose", "oidc", "--reason", "verification IdP",
    ]  # fmt: skip
    assert await asyncio.to_thread(main, allow) == 0
    out = capsys.readouterr().out
    assert "https://idp.halden.internal:9443 registered for oidc" in out
    target_id = out.rsplit("(", 1)[1].rstrip(")\n")
    assert await asyncio.to_thread(main, allow) == 0
    assert "already registered" in capsys.readouterr().out
    assert await asyncio.to_thread(main, ["outbound", "list", "--org", "halden"]) == 0
    assert "live" in capsys.readouterr().out
    revoke = ["outbound", "revoke", "--org", "halden", "--id", target_id, "--reason", "done"]
    assert await asyncio.to_thread(main, revoke) == 0
    assert "revoked" in capsys.readouterr().out
    assert await asyncio.to_thread(main, revoke) == 2
    metadata = [*allow[:5], "https://169.254.169.254", *allow[6:]]
    assert await asyncio.to_thread(main, metadata) == 2
    assert "never registered" in capsys.readouterr().err
