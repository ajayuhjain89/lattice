"""The dataset's expected-access oracle agrees with the real system, and the dataset
keeps the persona and isolation assumptions later phases depend on.

* The oracle's persona contexts equal what ``resolve_authorization_context`` produces
  after seeding the same manifests into PostgreSQL (by key; channel groups are synced
  by the Slack connector in Phase 4, so only admin groups are compared).
* Personas match docs/evaluation-plan.md §2 exactly.
* Records that deliberately share an identifier across organizations are distinct
  resources that no persona of the other organization can read.
"""

import uuid
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import text

from lattice.authz import resolve_authorization_context
from lattice.dataset.access import OrgWorld
from lattice.dataset.lint import Dataset, load_dataset
from lattice.db.engine import Database
from lattice.seed import seed_manifest

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "synthetic"
GOLDEN = REPO / "evals" / "golden"


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_dataset(DATA, GOLDEN)


def _keys(world: OrgWorld, ids: frozenset[uuid.UUID], kind: str) -> set[str]:
    lookup = {
        "account": {world.account_id(k): k for k in world.account_keys},
        "department": {world.department_id(k): k for k in world.department_keys},
        "group": {world.admin_group_id(k): k for k in world.admin_group_keys},
    }[kind]
    return {lookup[i] for i in ids if i in lookup}


async def test_oracle_contexts_match_the_seeded_database(
    dataset: Dataset, owner_db: Database, app_db: Database
) -> None:
    for slug, world in dataset.worlds.items():
        await seed_manifest(owner_db, world.manifest)
        async with owner_db.unscoped_transaction() as s:
            org: uuid.UUID = (
                await s.execute(text("SELECT id FROM organizations WHERE slug = :s"), {"s": slug})
            ).scalar_one()
        async with owner_db.tenant_transaction(org) as s:
            rows = await s.execute(
                text("SELECT u.email, m.id FROM memberships m JOIN users u ON u.id = m.user_id")
            )
            members = {row[0]: row[1] for row in rows.all()}
            names = {
                kind: {row[0]: row[1] for row in (await s.execute(text(sql))).all()}
                for kind, sql in (
                    ("account", "SELECT id, key FROM accounts"),
                    ("department", "SELECT id, key FROM departments"),
                    ("group", "SELECT id, key FROM access_groups"),
                )
            }
        for persona, email in world.personas.items():
            async with app_db.tenant_snapshot(org) as s:
                db = await resolve_authorization_context(
                    s, organization_id=org, membership_id=members[email]
                )
            oracle = world.context(persona)
            assert (db.clearance, db.account_scope, db.membership_status) == (
                oracle.clearance,
                oracle.account_scope,
                oracle.membership_status,
            ), persona
            assert {names["account"][i] for i in db.assigned_account_ids} == _keys(
                world, oracle.assigned_account_ids, "account"
            ), persona
            assert {names["department"][i] for i in db.department_ids} == _keys(
                world, oracle.department_ids, "department"
            ), persona
            assert {names["group"][i] for i in db.group_ids} == _keys(
                world, oracle.group_ids, "group"
            ), persona


# docs/evaluation-plan.md §2, the persona table (groups include private channels, which
# come from the Slack export; Grace is also in #acct-acme, as in the Phase 2 fixture).
PERSONAS = {
    "maya": (
        "internal",
        "assigned",
        {"acme-corp", "northpeak-logistics"},
        {"sales"},
        {"acct-acme"},
    ),
    "daniel": ("internal", "assigned", {"brightwater-health", "kestrel-bank"}, {"sales"}, set()),
    "priya": ("internal", "all", set(), {"support"}, set()),
    "tom": ("internal", "all", set(), {"product"}, {"prod-identity"}),
    "sofia": ("internal", "all", set(), {"engineering"}, {"prod-identity"}),
    "grace": (
        "confidential",
        "all",
        set(),
        {"leadership", "customer-success"},
        {"exec-staff", "exec-escalations", "acct-acme"},
    ),
    "arjun": ("confidential", "all", set(), {"leadership"}, {"exec-staff", "exec-escalations"}),
    "lena": ("internal", "assigned", set(), {"it"}, set()),
}


def test_halden_personas_match_the_documented_table(dataset: Dataset) -> None:
    world = dataset.worlds["halden"]
    channel_names = {
        world.channel_group_id(cid): info.channel.name
        for cid, info in world.raw.slack_channels.items()
        if info.private
    }
    actual = {}
    for persona in world.personas:
        ctx = world.context(persona)
        groups = _keys(world, ctx.group_ids, "group") | {
            channel_names[g] for g in ctx.group_ids if g in channel_names
        }
        actual[persona] = (
            ctx.clearance,
            ctx.account_scope,
            _keys(world, ctx.assigned_account_ids, "account"),
            _keys(world, ctx.department_ids, "department"),
            groups,
        )
    assert actual == PERSONAS
    admins = [m.email for m in world.manifest.members if m.is_admin]
    assert admins == ["lena.brandt@halden.example"]


def test_group_keys_are_unique_per_organization_and_reused_across_them(dataset: Dataset) -> None:
    keys = {slug: [g.key for g in w.manifest.groups] for slug, w in dataset.worlds.items()}
    for slug, values in keys.items():
        assert len(values) == len(set(values)), slug
    assert "exec-staff" in keys["halden"]
    assert "exec-staff" in keys["veridian"]


def test_cross_org_collisions_are_distinct_resources(dataset: Dataset) -> None:
    halden, veridian = dataset.worlds["halden"], dataset.worlds["veridian"]
    collisions = {c.record for c in dataset.ground_truth["halden"].cross_org_collisions}
    assert {
        "ticket:HAL-4821",
        "doc:scim-ga-decision",
        "doc:acme-exec-escalation-notes",
    } <= collisions
    for key in collisions:
        ours, theirs = halden.raw.records[key], veridian.raw.records[key]
        assert ours.text != theirs.text, key
        assert halden.org_id != veridian.org_id
        for persona in halden.personas:
            assert not halden.can_read(persona, theirs), (persona, key)
        for persona in veridian.personas:
            assert not veridian.can_read(persona, ours), (persona, key)
    # Account keys and names also collide; they are different accounts.
    assert {"acme-corp", "brightwater-health"} <= halden.account_keys & veridian.account_keys
    assert halden.account_id("acme-corp") != veridian.account_id("acme-corp")


def test_decision_d8_is_encoded_in_the_dataset(dataset: Dataset) -> None:
    """Maya reads one Kestrel document by explicit grant; Kestrel stays invisible."""
    world = dataset.worlds["halden"]
    rca = world.raw.records["doc:kestrel-sso-incident-customer-rca"]
    assert world.can_read("maya", rca)
    assert world.is_grants_only_readable_by_grant_only("maya", rca)
    assert not world.sees_account("maya", "kestrel-bank")
    assert world.shown_account("maya", rca) is None
    assert world.shown_account("daniel", rca) == "kestrel-bank"
    assert "Kestrel Bank" in rca.text  # the content names the customer; that stays readable
    for key in (
        "ticket:HAL-4702",
        "chat:C0ACCTKES01:1789143600.000100",
        "doc:kestrel-account-brief",
    ):
        assert not world.can_read("maya", world.raw.records[key]), key


def test_access_mechanisms_are_all_exercised(dataset: Dataset) -> None:
    world = dataset.worlds["halden"]
    records = world.raw.records
    readers = {key: world.readers(r) for key, r in records.items()}
    assert readers["doc:project-tern-memo"] == {"grace", "arjun"}  # restricted, group grant
    assert readers["doc:acme-exec-escalation-notes"] == {"grace", "arjun"}  # confidential + dept
    assert readers["doc:postmortem-inc-2026-031"] == {"sofia", "priya"}  # dept + dept grant
    assert readers["chat:G0PRODIDEN1:1788430500.000100"] == {"tom", "sofia"}  # private channel
    assert readers["chat:G0ACCTACME1:1790244300.000100"] == {"maya", "grace"}
    assert readers["doc:lattice-pilot-access-log"] == {"lena"}  # IT department only
    assert "maya" not in readers["ticket:HAL-4889"]  # account scope
    assert readers["ticket:HAL-4951"] == set()  # unmapped: never indexed
    assert readers["doc:company-handbook"] == set(world.personas)


def test_same_question_groups_change_the_evidence(dataset: Dataset) -> None:
    groups = Counter(i.same_question_group for _, i in dataset.goldens if i.same_question_group)
    assert len(groups) >= 8
    assert groups["acme-commitments"] >= 3
    assert all(count >= 2 for count in groups.values())
