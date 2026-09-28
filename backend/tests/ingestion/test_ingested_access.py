"""Ingested access equals the dataset's expected-access oracle (Phase 3 contract).

After syncing both organizations through the real pipeline and applying
``admin-grants.yaml`` (D-12):

1. Record by record, the stored access facts (sensitivity, access mode, account,
   departments, every grant with its origin's principal) equal what the oracle derived
   from the raw fixtures; records the oracle marks unmapped are not stored and are
   reported as ``access_unmappable``.
2. Persona by persona, the documents the **SQL predicate** returns -- as ``lattice_app``,
   with the context ``resolve_authorization_context`` builds from the database -- are
   exactly the records the oracle says the persona may read; and the account each shows
   as structured metadata is exactly the oracle's (D-8).
3. No persona reads anything of the other organization.
"""

import uuid
from collections import defaultdict

import pytest
from sqlalchemy import select, text

from lattice.admin_grants import apply_admin_grants, load_admin_grants
from lattice.authz import (
    load_document_access,
    readable_documents_predicate,
    resolve_authorization_context,
    visible_document_account,
)
from lattice.authz.sql import DOCUMENTS
from lattice.dataset.access import ExpectedAccess, OrgWorld, Unmapped
from lattice.dataset.lint import Dataset, load_dataset
from lattice.db.engine import Database

from .conftest import DATA, GOLDEN, ORGS, PREFIX, Org, rows, seed, sync_all


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_dataset(DATA, GOLDEN)


async def _ingest(owner_db: Database) -> dict[str, Org]:
    orgs = {slug: await seed(owner_db, slug) for slug in ORGS}
    for org in orgs.values():
        results = await sync_all(owner_db, org, None)
        assert all(r.status in ("succeeded", "partial") for r in results.values())
        report = await apply_admin_grants(
            owner_db, org.slug, load_admin_grants(org.root / "admin-grants.yaml")
        )
        assert report.problems == []
        # A sync after the admin grants changes nothing -- in particular not them (D-12).
        again = await sync_all(owner_db, org, None)
        assert all(r.created + r.updated + r.access_updated == 0 for r in again.values())
    return orgs


Facts = tuple[str, str, str | None, frozenset[str], frozenset[tuple[str, str]]]


async def _stored_facts(owner_db: Database, org: Org) -> dict[str, Facts]:
    """Record key -> access facts, with every identifier translated to its key."""
    documents = await rows(
        owner_db,
        org.id,
        "SELECT d.id, s.connector_type, d.external_id, d.sensitivity, d.access_mode, a.key, "
        "ARRAY(SELECT dep.key::text FROM departments dep WHERE dep.id = ANY(d.department_ids)) "
        "FROM documents d JOIN sources s ON s.id = d.source_id "
        "LEFT JOIN accounts a ON a.id = d.account_id WHERE d.status = 'active'",
    )
    grants: dict[object, set[tuple[str, str]]] = defaultdict(set)
    for document_id, principal in await rows(
        owner_db,
        org.id,
        "SELECT g.document_id, CASE g.principal_type "
        " WHEN 'membership' THEN 'membership:' || u.email "
        " WHEN 'department' THEN 'department:' || dep.key "
        " ELSE CASE ag.origin WHEN 'admin' THEN 'group:' || ag.key "
        "      ELSE 'channel:' || ag.external_ref END END "
        "FROM document_grants g "
        "LEFT JOIN memberships m ON m.id = g.membership_id LEFT JOIN users u ON u.id = m.user_id "
        "LEFT JOIN departments dep ON dep.id = g.department_id "
        "LEFT JOIN access_groups ag ON ag.id = g.group_id",
    ):
        kind, _, key = str(principal).partition(":")
        grants[document_id].add((kind, key))
    return {
        f"{PREFIX[str(t)]}:{ext}": (
            str(sens),
            str(mode),
            None if account is None else str(account),
            frozenset(deps),
            frozenset(grants[doc_id]),
        )
        for doc_id, t, ext, sens, mode, account, deps in documents
    }


def _expected_facts(world: OrgWorld, expected: ExpectedAccess) -> Facts:
    departments = {world.department_id(k): k for k in world.department_keys}
    groups = {world.admin_group_id(k): ("group", k) for k in world.admin_group_keys}
    groups |= {world.channel_group_id(c): ("channel", c) for c in world.raw.slack_channels}
    members = {world.membership_id(m.email): m.email for m in world.manifest.members}
    principals: set[tuple[str, str]] = set()
    for grant in expected.access.grants:
        if grant.principal_type == "membership":
            principals.add(("membership", members[grant.principal_id]))
        elif grant.principal_type == "department":
            principals.add(("department", departments[grant.principal_id]))
        else:
            principals.add(groups[grant.principal_id])
    return (
        expected.access.sensitivity,
        expected.access.access_mode,
        expected.account,
        frozenset(departments[d] for d in expected.access.department_ids),
        frozenset(principals),
    )


async def test_ingested_access_matches_the_oracle(
    dataset: Dataset, owner_db: Database, app_db: Database
) -> None:
    orgs = await _ingest(owner_db)
    all_ids: dict[uuid.UUID, str] = {}
    for slug, org in orgs.items():
        world = dataset.worlds[slug]
        stored = await _stored_facts(owner_db, org)

        # 1. Record by record.
        unmapped = {
            key
            for key, record in world.raw.records.items()
            if isinstance(world.expected(record), Unmapped)
        }
        assert set(stored) == set(world.raw.records) - unmapped
        for key, facts in stored.items():
            expected = world.expected(world.raw.records[key])
            assert isinstance(expected, ExpectedAccess)
            assert facts == _expected_facts(world, expected), key
        failures = await rows(
            owner_db,
            org.id,
            "SELECT f.external_id, f.error_code FROM sync_record_failures f",
        )
        assert {(f"ticket:{ext}", code) for ext, code in failures} == {
            (key, "access_unmappable:account") for key in unmapped
        }

        # 2. Persona by persona, through the SQL predicate as the runtime role.
        ids = {
            doc_id: f"{PREFIX[str(t)]}:{ext}"
            for doc_id, t, ext in await rows(
                owner_db,
                org.id,
                "SELECT d.id, s.connector_type, d.external_id FROM documents d "
                "JOIN sources s ON s.id = d.source_id",
            )
        }
        all_ids |= ids
        if slug == "halden":
            halden_docs = set(ids)
            assert len(stored) == 343
        accounts = dict(await rows(owner_db, org.id, "SELECT id, key FROM accounts"))
        memberships = dict(
            await rows(
                owner_db,
                org.id,
                "SELECT u.email, m.id FROM memberships m JOIN users u ON u.id = m.user_id",
            )
        )
        for persona, email in world.personas.items():
            async with app_db.tenant_snapshot(org.id) as s:
                ctx = await resolve_authorization_context(
                    s,
                    organization_id=org.id,
                    membership_id=memberships[email],
                )
                readable_ids: set[uuid.UUID] = set(
                    (
                        await s.execute(
                            select(DOCUMENTS.c.id).where(readable_documents_predicate(ctx))
                        )
                    ).scalars()
                )
                access = await load_document_access(s, org.id, readable_ids)
            readable_keys = {ids[i] for i in readable_ids}
            expected_keys = {
                key for key, record in world.raw.records.items() if world.can_read(persona, record)
            }
            assert readable_keys == expected_keys, (
                persona,
                sorted(readable_keys ^ expected_keys)[:10],
            )
            for doc_id, doc_access in access.items():
                shown = visible_document_account(ctx, doc_access)
                key = ids[doc_id]
                assert (None if shown is None else accounts[shown]) == world.shown_account(
                    persona, world.raw.records[key]
                ), (persona, key)

    # 3. Cross-organization: a Veridian member's predicate, evaluated over Veridian's
    # rows, admits only Veridian documents; Halden's document IDs never appear.
    halden_ids = {i for i, key in all_ids.items() if i in halden_docs}
    async with app_db.tenant_snapshot(orgs["veridian"].id) as s:
        members: list[uuid.UUID] = list(
            (await s.execute(text("SELECT id FROM memberships"))).scalars()
        )
        for membership_id in members:
            ctx = await resolve_authorization_context(
                s, organization_id=orgs["veridian"].id, membership_id=membership_id
            )
            seen: set[uuid.UUID] = set(
                (
                    await s.execute(select(DOCUMENTS.c.id).where(readable_documents_predicate(ctx)))
                ).scalars()
            )
            assert seen
            assert not seen & halden_ids

    # The comparison is not vacuous: the D-8 scenario is in it.
    maya = dataset.worlds["halden"]
    rca = maya.raw.records["doc:kestrel-sso-incident-customer-rca"]
    assert maya.can_read("maya", rca)
    assert maya.shown_account("maya", rca) is None
