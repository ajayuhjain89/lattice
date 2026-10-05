"""Seed tooling: deterministic, idempotent, RLS-respecting."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from lattice.cli import main
from lattice.db.engine import Database
from lattice.seed import Manifest, load_manifest, seed_manifest

from .factories import create_source

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
    for org, expected_members in ((halden, 8), (veridian, 3)):
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
        (lambda m: m["groups"][0].update(members=["nobody@halden.example"]), "unknown members"),
        (lambda m: m["groups"].append(dict(m["groups"][0])), "duplicate group"),
        (lambda m: m["groups"][0].update(key="Exec Staff"), "lowercase"),
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


# --- Admin-defined access groups (D-9: identified by their stable key) --------------------


async def _groups(db: Database, org: object) -> dict[str, tuple[object, str, set[str]]]:
    """key -> (id, name, member emails)."""
    async with db.tenant_transaction(org) as s:  # type: ignore[arg-type]
        rows = (
            await s.execute(
                text(
                    "SELECT g.key, g.id, g.name, g.origin, u.email FROM access_groups g "
                    "LEFT JOIN access_group_members gm ON gm.group_id = g.id "
                    "LEFT JOIN memberships m ON m.id = gm.membership_id "
                    "LEFT JOIN users u ON u.id = m.user_id"
                )
            )
        ).all()
    groups: dict[str, tuple[object, str, set[str]]] = {}
    for key, group_id, name, origin, email in rows:
        assert origin == "admin"
        entry = groups.setdefault(str(key), (group_id, str(name), set()))
        if email is not None:
            entry[2].add(str(email))
    return groups


async def test_groups_are_seeded_by_key_in_each_organization(owner_db: Database) -> None:
    await seed_manifest(owner_db, load_manifest(HALDEN))
    await seed_manifest(owner_db, load_manifest(VERIDIAN))
    halden = await _groups(owner_db, await _org_id(owner_db, "halden"))
    veridian = await _groups(owner_db, await _org_id(owner_db, "veridian"))
    assert {k: v[1:] for k, v in halden.items()} == {
        "exec-staff": (
            "Exec staff",
            {"grace.whitfield@halden.example", "arjun.mehta@halden.example"},
        )
    }
    # The same key in another organization is a different group.
    assert veridian["exec-staff"][2] == {"ines.carvalho@veridian.example"}
    assert veridian["exec-staff"][0] != halden["exec-staff"][0]


async def test_renaming_a_group_keeps_its_identity(owner_db: Database) -> None:
    base = load_manifest(HALDEN).model_dump()
    await seed_manifest(owner_db, Manifest.model_validate(base))
    org = await _org_id(owner_db, "halden")
    before = await _groups(owner_db, org)
    base["groups"][0]["name"] = "Executive staff"
    report = await seed_manifest(owner_db, Manifest.model_validate(base))
    after = await _groups(owner_db, org)
    assert report.counts["group.written"] == 1
    assert after["exec-staff"][0] == before["exec-staff"][0]
    assert after["exec-staff"][1:] == ("Executive staff", before["exec-staff"][2])


async def test_a_new_key_is_a_new_group_and_the_old_one_is_left_alone(
    owner_db: Database,
) -> None:
    """Changing a key is an identity change, not a rename: the seed creates a group under
    the new key and never deletes or rewires the old one (it may carry grants)."""
    base = load_manifest(HALDEN).model_dump()
    await seed_manifest(owner_db, Manifest.model_validate(base))
    org = await _org_id(owner_db, "halden")
    old = await _groups(owner_db, org)
    base["groups"][0]["key"] = "leadership-team"
    await seed_manifest(owner_db, Manifest.model_validate(base))
    groups = await _groups(owner_db, org)
    assert set(groups) == {"exec-staff", "leadership-team"}
    assert groups["exec-staff"] == old["exec-staff"]
    assert groups["leadership-team"][0] != old["exec-staff"][0]


async def test_group_membership_changes_bump_authz_updated_at(owner_db: Database) -> None:
    base = load_manifest(HALDEN).model_dump()
    await seed_manifest(owner_db, Manifest.model_validate(base))
    org = await _org_id(owner_db, "halden")

    async def stamps() -> dict[str, object]:
        async with owner_db.tenant_transaction(org) as s:  # type: ignore[arg-type]
            rows = await s.execute(
                text(
                    "SELECT u.email, m.authz_updated_at FROM memberships m "
                    "JOIN users u ON u.id = m.user_id"
                )
            )
            return {str(email): stamp for email, stamp in rows.all()}

    before = await stamps()
    base["groups"][0]["members"] = ["arjun.mehta@halden.example"]  # Grace leaves
    await seed_manifest(owner_db, Manifest.model_validate(base))
    after = await stamps()
    changed = {email for email in after if after[email] != before[email]}
    assert changed == {"grace.whitfield@halden.example"}
    assert (await _groups(owner_db, org))["exec-staff"][2] == {"arjun.mehta@halden.example"}


async def test_seed_never_takes_over_a_source_synced_group(owner_db: Database) -> None:
    await seed_manifest(owner_db, load_manifest(HALDEN))
    org = await _org_id(owner_db, "halden")
    source = await create_source(owner_db, org, "halden-slack", "chat_export")  # type: ignore[arg-type]
    async with owner_db.tenant_transaction(org) as s:  # type: ignore[arg-type]
        await s.execute(
            text(
                "INSERT INTO access_groups (organization_id, key, origin, source_id, "
                "external_ref, name) "
                "VALUES (:o, 'acct-acme', 'source', :src, 'G0ACCTACME1', '#acct-acme')"
            ),
            {"o": org, "src": source},
        )
    base = load_manifest(HALDEN).model_dump()
    base["groups"].append({"key": "acct-acme", "name": "Acme team", "members": []})
    with pytest.raises(ValueError, match="not admin-defined"):
        await seed_manifest(owner_db, Manifest.model_validate(base))


# --- Audit (Phase 9) -------------------------------------------------------------------


async def _seed_events(db: Database, org: object) -> list[tuple[object, ...]]:
    async with db.tenant_transaction(org) as s:  # type: ignore[arg-type]
        return [
            tuple(r)
            for r in await s.execute(
                text(
                    "SELECT actor, action, target ->> 'membership_id', before IS NULL, reason "
                    "FROM audit_events ORDER BY created_at"
                )
            )
        ]


async def test_the_seed_audits_provisioning_and_changes_but_not_reruns(
    owner_db: Database,
) -> None:
    base = load_manifest(HALDEN).model_dump()
    await seed_manifest(owner_db, Manifest.model_validate(base))
    org = await _org_id(owner_db, "halden")
    provisioned = await _seed_events(owner_db, org)
    assert len(provisioned) == 8
    assert {(e[0], e[1], e[3], e[4]) for e in provisioned} == {
        (
            "operator:seed",
            "access.member_changed",
            True,
            "Provisioned from the organization manifest",
        )
    }
    await seed_manifest(owner_db, Manifest.model_validate(base))
    assert len(await _seed_events(owner_db, org)) == 8  # unchanged: nothing written
    base["members"][0]["role_label"] = "Senior Account Manager"  # display only
    await seed_manifest(owner_db, Manifest.model_validate(base))
    assert len(await _seed_events(owner_db, org)) == 8
    base["members"][0]["accounts"] = ["acme-corp"]
    await seed_manifest(owner_db, Manifest.model_validate(base))
    events = await _seed_events(owner_db, org)
    assert len(events) == 9
    assert events[-1][3:] == (False, "Updated from the organization manifest")
    async with owner_db.tenant_transaction(org) as s:  # type: ignore[arg-type]
        before, after = (
            await s.execute(
                text("SELECT before, after FROM audit_events ORDER BY created_at DESC LIMIT 1")
            )
        ).one()
    assert len(before["accounts"]) == 2
    assert len(after["accounts"]) == 1
    assert after["is_admin"] is False


def test_a_synthetic_only_bootstrap_refuses_any_other_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Phase 11 verification stacks are bootstrapped with synthetic data only."""
    from .conftest import OWNER_URL

    monkeypatch.setenv("LATTICE_ENV", "test")
    monkeypatch.setenv("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL)
    customer = tmp_path / "customer.yaml"
    customer.write_text(
        HALDEN.read_text().replace("data_origin: synthetic", "data_origin: customer")
    )
    assert main(["seed", "--require-synthetic", "--manifest", str(customer)]) == 2
    assert "data_origin: synthetic" in capsys.readouterr().err
    unmarked = tmp_path / "unmarked.yaml"
    unmarked.write_text(HALDEN.read_text().replace("data_origin: synthetic", ""))
    assert main(["seed", "--require-synthetic", "--manifest", str(unmarked)]) == 2
    args = ["seed", "--require-synthetic", "--manifest", str(HALDEN), "--manifest", str(VERIDIAN)]
    assert main(args) == 0
