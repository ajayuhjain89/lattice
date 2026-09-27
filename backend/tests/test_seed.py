"""Phase 1 seed tooling: deterministic, idempotent, RLS-respecting."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from lattice.cli import main
from lattice.db.engine import Database
from lattice.seed import Manifest, load_manifest, seed_manifest

DATA = Path(__file__).resolve().parents[2] / "data" / "synthetic"
HALDEN = DATA / "halden" / "org.yaml"
VERIDIAN = DATA / "veridian" / "org.yaml"


async def _org_id(db: Database, slug: str) -> object:
    async with db.unscoped_transaction() as s:
        return (
            await s.execute(text("SELECT id FROM organizations WHERE slug = :s"), {"s": slug})
        ).scalar_one()


async def test_seeding_is_idempotent(owner_db: Database) -> None:
    manifest = load_manifest(HALDEN)
    first = await seed_manifest(owner_db, manifest)
    second = await seed_manifest(owner_db, manifest)
    assert first.counts["membership.written"] == 8
    assert first.counts["user.written"] == 8
    assert not any(key.endswith(".written") for key in second.counts)
    assert second.counts["membership.unchanged"] == 8


async def test_seeded_tenants_are_isolated(owner_db: Database, app_db: Database) -> None:
    await seed_manifest(owner_db, load_manifest(HALDEN))
    await seed_manifest(owner_db, load_manifest(VERIDIAN))
    halden = await _org_id(owner_db, "halden")
    veridian = await _org_id(owner_db, "veridian")
    for org, expected_members in ((halden, 8), (veridian, 2)):
        async with app_db.tenant_transaction(org) as s:  # type: ignore[arg-type]
            members: int = (await s.execute(text("SELECT count(*) FROM memberships"))).scalar_one()
            accounts: int = (
                await s.execute(text("SELECT count(*) FROM accounts WHERE key = 'acme-corp'"))
            ).scalar_one()
            users: int = (await s.execute(text("SELECT count(*) FROM users"))).scalar_one()
        assert members == users == expected_members
        assert accounts == 1  # each org sees exactly its own "Acme Corp"
    async with app_db.unscoped_transaction() as s:
        assert (await s.execute(text("SELECT count(*) FROM memberships"))).scalar_one() == 0


async def test_seed_matches_manifest_assignments(owner_db: Database) -> None:
    await seed_manifest(owner_db, load_manifest(HALDEN))
    org = await _org_id(owner_db, "halden")
    async with owner_db.tenant_transaction(org) as s:  # type: ignore[arg-type]
        rows: list[str] = list(
            (
                await s.execute(
                    text(
                        "SELECT a.key FROM account_assignments aa "
                        "JOIN accounts a ON a.id = aa.account_id "
                        "JOIN memberships m ON m.id = aa.membership_id "
                        "JOIN users u ON u.id = m.user_id "
                        "WHERE u.email = 'maya.okafor@halden.example' "
                        "ORDER BY a.key"
                    )
                )
            )
            .scalars()
            .all()
        )
        admin = (
            await s.execute(
                text(
                    "SELECT m.is_admin, m.clearance FROM memberships m JOIN users u "
                    "ON u.id = m.user_id WHERE u.email = 'lena.brandt@halden.example'"
                )
            )
        ).one()
    assert rows == ["acme-corp", "northpeak-logistics"]
    assert admin == (True, "internal")  # admin capability, no extra clearance


async def test_access_changes_bump_authz_updated_at(owner_db: Database) -> None:
    base = load_manifest(HALDEN).model_dump()
    await seed_manifest(owner_db, Manifest.model_validate(base))
    org = await _org_id(owner_db, "halden")

    async def stamp() -> object:
        async with owner_db.tenant_transaction(org) as s:  # type: ignore[arg-type]
            return (
                await s.execute(
                    text(
                        "SELECT m.authz_updated_at FROM memberships m JOIN users u "
                        "ON u.id = m.user_id WHERE u.email = 'maya.okafor@halden.example'"
                    )
                )
            ).scalar_one()

    before = await stamp()
    base["members"][0]["role_label"] = "Senior Account Manager"  # display only
    await seed_manifest(owner_db, Manifest.model_validate(base))
    assert await stamp() == before
    base["members"][0]["accounts"] = ["acme-corp"]  # access change
    await seed_manifest(owner_db, Manifest.model_validate(base))
    assert await stamp() != before


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda m: m["members"][0].update(departments=["nope"]), "unknown departments"),
        (lambda m: m["accounts"].append(dict(m["accounts"][0])), "duplicate account"),
        (lambda m: m["members"][2].update(accounts=["acme-corp"]), "account_scope=assigned"),
        (lambda m: m["organization"].update(slug="Not Valid"), "lowercase"),
        (lambda m: m["members"][0].update(clearance="restricted"), "clearance"),
        (lambda m: m["members"][0].update(unexpected=True), "Extra inputs"),
    ],
)
def test_invalid_manifests_are_rejected(mutation: object, message: str) -> None:
    raw = load_manifest(HALDEN).model_dump()
    assert callable(mutation)
    mutation(raw)
    with pytest.raises(ValidationError, match=message):
        Manifest.model_validate(raw)


def test_seed_cli_refuses_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LATTICE_ENV", "production")
    assert main(["seed", "--manifest", str(HALDEN)]) == 2


def test_seed_cli_reports_invalid_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LATTICE_ENV", "test")
    bad = tmp_path / "org.yaml"
    bad.write_text("organization: {slug: x}\nmembers: 3\n")
    assert main(["seed", "--manifest", str(bad)]) == 2
