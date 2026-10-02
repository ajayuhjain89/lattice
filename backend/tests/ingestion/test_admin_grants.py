"""Administrator grants (decision D-12): Lattice configuration applied by a separate,
all-or-nothing, idempotent step after ingestion -- never by a connector, and never
touched by a sync.
"""

from datetime import date
from typing import Any

import pytest

from lattice.admin_grants import AdminGrant, AdminGrantsFile, apply_admin_grants, load_admin_grants
from lattice.db.engine import Database

from .conftest import Org, rows, seed, sync, sync_all
from .helpers import document, edit_front_matter

RCA = "doc:kestrel-sso-incident-customer-rca"
MAYA = "maya.okafor@halden.example"


def _grants(*entries: tuple[str, str] | dict[str, Any]) -> AdminGrantsFile:
    return AdminGrantsFile(
        grants=[
            AdminGrant(
                **(e if isinstance(e, dict) else {"document": e[0], "member": e[1]}),
                reason="Test",
                granted_on=date(2026, 9, 16),
            )
            for e in entries
        ]
    )


async def _admin_grants(db: Database, org: Org) -> list[tuple[Any, ...]]:
    return await rows(
        db,
        org.id,
        "SELECT d.external_id, u.email, g.ctid::text, g.xmin::text FROM document_grants g "
        "JOIN documents d ON d.id = g.document_id JOIN memberships m ON m.id = g.membership_id "
        "JOIN users u ON u.id = m.user_id WHERE g.origin = 'admin' ORDER BY 1, 2",
    )


async def test_admin_grants_apply_idempotently_and_survive_every_sync(
    owner_db: Database, halden: Org
) -> None:
    await sync_all(owner_db, halden, None)
    grants = load_admin_grants(halden.root / "admin-grants.yaml")
    first = await apply_admin_grants(owner_db, "halden", grants)
    assert (first.added, first.unchanged, first.removed, first.problems) == (1, 0, 0, [])
    applied = await _admin_grants(owner_db, halden)
    assert [(r[0], r[1]) for r in applied] == [("kestrel-sso-incident-customer-rca", MAYA)]

    second = await apply_admin_grants(owner_db, "halden", grants)
    assert (second.added, second.unchanged) == (0, 1)

    # A source-side access change on the same document replaces only source grants.
    edit_front_matter(halden, "kestrel-sso-incident-customer-rca", grants=["department:support"])
    changed = await sync(owner_db, halden, "drive", None)
    assert changed.access_updated == 1
    await sync(owner_db, halden, "drive", None, mode="acl_only")
    await sync(owner_db, halden, "drive", None, mode="reindex", rechunk=True)
    assert await _admin_grants(owner_db, halden) == applied
    source = await rows(
        owner_db,
        halden.id,
        "SELECT g.principal_type FROM document_grants g JOIN documents d ON d.id = g.document_id "
        "WHERE d.external_id = 'kestrel-sso-incident-customer-rca' AND g.origin = 'source'",
    )
    assert source == [("department",)]


@pytest.mark.parametrize(
    ("entry", "code"),
    [
        (("doc:no-such-document", MAYA), "unknown_document"),
        (("doc:kestrel-sso-incident-customer-rca", "nobody@halden.example"), "unknown_principal"),
        (("wiki:kestrel-sso-incident-customer-rca", MAYA), "invalid_document_ref"),
        (("doc:", MAYA), "invalid_document_ref"),
        # A ticket key must name a ticket: document IDs are resolved per connector type.
        (("ticket:kestrel-sso-incident-customer-rca", MAYA), "unknown_document"),
        ({"document": RCA, "member": MAYA, "source": "sharepoint"}, "unknown_source"),
        ((RCA, MAYA.upper()), "duplicate_grant"),  # emails match case-insensitively
        # Another organization's member, and another organization's document.
        ((RCA, "noor.haddad@veridian.example"), "unknown_principal"),
        (("doc:veridian-only-document", MAYA), "unknown_document"),
    ],
)
async def test_any_unresolvable_entry_fails_the_whole_application(
    owner_db: Database, halden: Org, entry: Any, code: str
) -> None:
    await sync(owner_db, halden, "drive", None)
    good = ("doc:project-tern-memo", MAYA)
    report = await apply_admin_grants(owner_db, "halden", _grants(good, (RCA, MAYA), entry))
    assert [(p.index, p.code) for p in report.problems] == [(2, code)]
    assert await _admin_grants(owner_db, halden) == []


async def test_documents_of_another_organization_do_not_exist_here(
    owner_db: Database, halden: Org
) -> None:
    veridian = await seed(owner_db, "veridian")
    await sync(owner_db, veridian, "drive", None)
    await sync(owner_db, halden, "drive", None)
    # Both organizations have doc:acme-exec-escalation-notes; each resolves its own.
    report = await apply_admin_grants(
        owner_db, "veridian", _grants((RCA, "noor.haddad@veridian.example"))
    )
    assert [p.code for p in report.problems] == ["unknown_document"]
    report = await apply_admin_grants(
        owner_db,
        "veridian",
        _grants(("doc:acme-exec-escalation-notes", "noor.haddad@veridian.example")),
    )
    assert report.added == 1
    assert await _admin_grants(owner_db, halden) == []


async def test_prune_revokes_grants_no_longer_in_the_file(owner_db: Database, halden: Org) -> None:
    await sync(owner_db, halden, "drive", None)
    both = _grants((RCA, MAYA), ("doc:project-tern-memo", MAYA))
    assert (await apply_admin_grants(owner_db, "halden", both)).added == 2
    tern_before = await document(owner_db, halden, "project-tern-memo")

    one = _grants((RCA, MAYA))
    kept = await apply_admin_grants(owner_db, "halden", one)
    assert (kept.removed, len(await _admin_grants(owner_db, halden))) == (0, 2)
    dry = await apply_admin_grants(owner_db, "halden", one, prune=True, dry_run=True)
    assert (dry.removed, len(await _admin_grants(owner_db, halden))) == (1, 2)
    pruned = await apply_admin_grants(owner_db, "halden", one, prune=True)
    assert pruned.removed == 1
    assert [r[0] for r in await _admin_grants(owner_db, halden)] == [
        "kestrel-sso-incident-customer-rca"
    ]
    tern_after = await document(owner_db, halden, "project-tern-memo")
    assert tern_after["acl_updated_at"] > tern_before["acl_updated_at"]


async def test_dry_run_and_problems_write_nothing(owner_db: Database, halden: Org) -> None:
    # Before ingestion no document exists: the application fails, nothing is written.
    early = await apply_admin_grants(owner_db, "halden", _grants((RCA, MAYA)))
    assert [p.code for p in early.problems] == ["unknown_document"]
    await sync(owner_db, halden, "drive", None)
    dry = await apply_admin_grants(owner_db, "halden", _grants((RCA, MAYA)), dry_run=True)
    assert (dry.added, dry.problems) == (1, [])
    assert await _admin_grants(owner_db, halden) == []
    unknown = await apply_admin_grants(owner_db, "nowhere", _grants((RCA, MAYA)))
    assert [(p.index, p.code) for p in unknown.problems] == [(-1, "unknown_organization")]


async def test_applied_and_pruned_grants_are_audited(owner_db: Database, halden: Org) -> None:
    await sync_all(owner_db, halden, None)
    grants = load_admin_grants(halden.root / "admin-grants.yaml")
    await apply_admin_grants(owner_db, "halden", grants)
    await apply_admin_grants(owner_db, "halden", grants)  # idempotent: no second event
    await apply_admin_grants(owner_db, "halden", AdminGrantsFile(), prune=True)
    events = await rows(
        owner_db,
        halden.id,
        "SELECT actor, action, target ->> 'reference', reason FROM audit_events "
        "WHERE action LIKE 'access.grant_%' ORDER BY created_at",
    )
    assert [(e[0], e[1]) for e in events] == [
        ("operator:admin-grants", "access.grant_created"),
        ("operator:admin-grants", "access.grant_revoked"),
    ]
    assert all(str(e[2]).startswith("rec_") for e in events)
    assert events[0][3] == grants.grants[0].reason
    assert events[1][3] == "Removed from admin-grants.yaml (--prune)"
