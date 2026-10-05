"""``lattice verify-restore`` (Phase 11, P11.5): a restored copy must pass every
deployment check, keep its audit triggers, and hold exactly the rows the backup-time
manifest counted, organization by organization. Checked here against the test database
(standing in for a restored copy); the real restores are runbook §8 "P11.5"."""

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from lattice.cli import main
from lattice.db.deployment import Plan
from lattice.db.engine import Database
from lattice.db.restore import Manifest, manifest_differences, verify_restore, write_manifest
from lattice.db.schema import SCHEMA_REVISION

from .conftest import OWNER_URL
from .factories import create_member, create_org


@pytest.fixture
async def two_orgs(owner_db: Database) -> None:
    for slug, people in (("halden", 2), ("veridian", 1)):
        org = await create_org(owner_db, slug)
        for i in range(people):
            await create_member(owner_db, org, f"p{i}@{slug}.example")


def _by_name(checks: list) -> dict[str, bool]:  # type: ignore[type-arg]
    return {c.name: c.ok for c in checks}


async def test_a_manifest_counts_each_organization_separately(two_orgs: None) -> None:
    manifest = await write_manifest(OWNER_URL)
    assert manifest.revision == SCHEMA_REVISION
    assert manifest.organizations["halden"]["memberships"] == 2
    assert manifest.organizations["veridian"]["memberships"] == 1
    assert manifest.globals["organizations"] == 2
    assert "pre_auth_rate_limits" not in manifest.organizations["halden"]  # no organization
    assert Manifest.from_json(manifest.to_json()) == manifest
    # Counts and slugs only: no identifier, e-mail or content.
    assert "@" not in manifest.to_json()


async def test_verification_passes_on_an_intact_copy(two_orgs: None) -> None:
    manifest = await write_manifest(OWNER_URL)
    checks = await verify_restore(OWNER_URL, manifest, Plan(api_pool_size=3))
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    assert {"privilege snapshot", "audit integrity", "row counts"} <= set(_by_name(checks))


async def test_missing_rows_are_found_per_organization(owner_db: Database, two_orgs: None) -> None:
    manifest = await write_manifest(OWNER_URL)
    veridian = await create_org(owner_db, "lost-org")
    await create_member(owner_db, veridian, "x@lost.example")
    checks = _by_name(await verify_restore(OWNER_URL, manifest, Plan(api_pool_size=3)))
    assert checks["row counts"] is False
    actual = await write_manifest(OWNER_URL)
    problems = manifest_differences(manifest, actual)
    assert "organization lost-org: unexpected" in problems
    assert any(p.startswith("organizations:") for p in problems)


async def test_a_disabled_audit_trigger_fails_the_verification(
    superuser_engine: AsyncEngine, two_orgs: None
) -> None:
    async with superuser_engine.begin() as conn:
        await conn.execute(
            text("ALTER TABLE audit_events DISABLE TRIGGER trg_audit_events_immutable")
        )
    try:
        checks = _by_name(await verify_restore(OWNER_URL, None, Plan(api_pool_size=3)))
    finally:
        async with superuser_engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER trg_audit_events_immutable")
            )
    # The snapshot records that the trigger exists; only this check sees it disabled.
    assert checks["audit integrity"] is False


async def test_the_command(
    two_orgs: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL)
    assert await asyncio.to_thread(main, ["verify-restore", "--write-manifest"]) == 0
    written = capsys.readouterr().out
    assert json.loads(written)["organizations"]["halden"]["memberships"] == 2
    path = tmp_path / "manifest.json"
    path.write_text(written)
    code = await asyncio.to_thread(main, ["verify-restore", "--manifest", str(path)])
    out = capsys.readouterr().out
    # The test database's connection budget differs from production's; every other check
    # (and the row counts) must pass.
    failed = [line for line in out.splitlines() if line.startswith("FAIL")]
    assert all("connection budget" in line for line in failed), failed
    assert "PASS  row counts" in out
    assert code in (0, 1)
