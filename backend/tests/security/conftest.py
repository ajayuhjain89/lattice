"""The Halden persona world used by the persona, revocation and admin suites.

People, departments, accounts and assignments come from the real seed manifests
(data/synthetic/halden and veridian). Access groups and document access records are
added here: they are Phase 2 test fixtures, not the Phase 3 dataset.
"""

import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import select, text

from lattice.authz import (
    AuthorizationContext,
    load_document_access,
    readable,
    readable_documents_predicate,
    resolve_authorization_context,
)
from lattice.authz.sql import DOCUMENTS
from lattice.db.engine import Database
from lattice.seed import load_manifest, seed_manifest

from ..factories import add_grant, create_document, create_group

REPO = Path(__file__).resolve().parents[3]
MANIFESTS = (
    REPO / "data/synthetic/halden/org.yaml",
    REPO / "data/synthetic/veridian/org.yaml",
)


@dataclass
class Halden:
    org: uuid.UUID
    veridian: uuid.UUID
    members: dict[str, uuid.UUID]  # first name -> membership id
    departments: dict[str, uuid.UUID]
    accounts: dict[str, uuid.UUID]
    groups: dict[str, uuid.UUID]
    documents: dict[str, uuid.UUID]
    names: dict[uuid.UUID, str]  # document id -> fixture name


async def _ids(db: Database, org: uuid.UUID, sql: str) -> dict[str, uuid.UUID]:
    async with db.tenant_transaction(org) as s:
        return {str(k): v for k, v in (await s.execute(text(sql))).all()}


@pytest.fixture
async def halden(owner_db: Database) -> Halden:
    orgs: dict[str, uuid.UUID] = {}
    for path in MANIFESTS:
        manifest = load_manifest(path)
        await seed_manifest(owner_db, manifest)
        async with owner_db.unscoped_transaction() as s:
            orgs[manifest.organization.slug] = (
                await s.execute(
                    text("SELECT id FROM organizations WHERE slug = :s"),
                    {"s": manifest.organization.slug},
                )
            ).scalar_one()
    org = orgs["halden"]
    members = await _ids(
        owner_db,
        org,
        "SELECT split_part(u.email, '.', 1), m.id FROM memberships m "
        "JOIN users u ON u.id = m.user_id",
    )
    departments = await _ids(owner_db, org, "SELECT key, id FROM departments")
    accounts = await _ids(owner_db, org, "SELECT key, id FROM accounts")

    groups = {
        # Admin-defined group; Leadership's typical grant principal.
        "exec-staff": await create_group(
            owner_db, org, "Exec staff", (members["grace"], members["arjun"])
        ),
        # Mirrors a private Slack channel for the Acme account team.
        "acct-acme": await create_group(
            owner_db, org, "#acct-acme", (members["maya"], members["grace"]), origin="source"
        ),
    }

    d, a, g, m = departments, accounts, groups, members
    documents = {
        # The permanent critical scenario (authorization-model §10), at the authz level.
        "acme-success-plan": await create_document(owner_db, org, account_id=a["acme-corp"]),
        "acme-exec-escalation": await create_document(
            owner_db,
            org,
            sensitivity="confidential",
            account_id=a["acme-corp"],
            department_ids=(d["leadership"],),
        ),
        "kestrel-renewal": await create_document(owner_db, org, account_id=a["kestrel-bank"]),
        "support-runbook": await create_document(owner_db, org, department_ids=(d["support"],)),
        "product-roadmap": await create_document(
            owner_db, org, department_ids=(d["product"], d["engineering"])
        ),
        "company-handbook": await create_document(owner_db, org),
        "pricing-strategy": await create_document(owner_db, org, sensitivity="confidential"),
        "board-mna-memo": await create_document(
            owner_db,
            org,
            sensitivity="restricted",
            access_mode="grants_only",
            grants=(("group", g["exec-staff"]),),
        ),
        # Private channel: grants-only but internal (who ≠ how sensitive).
        "acme-private-channel": await create_document(
            owner_db,
            org,
            access_mode="grants_only",
            account_id=a["acme-corp"],
            grants=(("group", g["acct-acme"]),),
        ),
        "compensation-review": await create_document(
            owner_db,
            org,
            sensitivity="restricted",
            access_mode="grants_only",
            grants=(("membership", m["arjun"]),),
        ),
        # Confidential engineering postmortem shared with Support by department grant.
        "incident-postmortem": await create_document(
            owner_db,
            org,
            sensitivity="confidential",
            department_ids=(d["engineering"],),
            grants=(("department", d["support"]),),
        ),
        "it-admin-runbook": await create_document(owner_db, org, department_ids=(d["it"],)),
        # A per-person grant on a document of an account outside Maya's scope.
        "kestrel-shared-with-maya": await create_document(
            owner_db, org, account_id=a["kestrel-bank"], grants=(("membership", m["maya"]),)
        ),
        "deleted-handbook": await create_document(owner_db, org, status="deleted"),
        # Grants-only with an unrelated grant: nobody on the policy path, only Sofia.
        "eng-private-channel": await create_document(
            owner_db, org, access_mode="grants_only", grants=(("membership", m["sofia"]),)
        ),
    }
    # A department that no longer exists: create, reference, delete.
    async with owner_db.tenant_transaction(org) as s:
        ghost: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO departments (organization_id, key, name) "
                    "VALUES (:o, 'disbanded', 'Disbanded') RETURNING id"
                ),
                {"o": org},
            )
        ).scalar_one()
    documents["disbanded-team-notes"] = await create_document(
        owner_db, org, department_ids=(ghost,)
    )
    async with owner_db.tenant_transaction(org) as s:
        await s.execute(text("DELETE FROM departments WHERE id = :g"), {"g": ghost})

    # Another tenant's unrestricted documents, one on its own "Acme Corp" (same key).
    veridian_accounts = await _ids(owner_db, orgs["veridian"], "SELECT key, id FROM accounts")
    documents["veridian-handbook"] = await create_document(owner_db, orgs["veridian"])
    documents["veridian-acme-plan"] = await create_document(
        owner_db, orgs["veridian"], account_id=veridian_accounts["acme-corp"]
    )
    return Halden(
        org=org,
        veridian=orgs["veridian"],
        members=members,
        departments=departments,
        accounts=accounts,
        groups=groups,
        documents=documents,
        names={v: k for k, v in documents.items()},
    )


async def context_for(
    db: Database, org: uuid.UUID, membership_id: uuid.UUID
) -> AuthorizationContext:
    async with db.tenant_snapshot(org) as s:
        return await resolve_authorization_context(
            s, organization_id=org, membership_id=membership_id
        )


async def readable_names(db: Database, world: Halden, person: str) -> set[str]:
    """What ``person`` can read, computed both ways as the runtime role; asserts the SQL
    predicate and the reference policy agree before returning."""
    membership_id = world.members[person]
    async with db.tenant_snapshot(world.org) as s:
        ctx = await resolve_authorization_context(
            s, organization_id=world.org, membership_id=membership_id
        )
        via_sql: set[uuid.UUID] = set(
            (
                await s.execute(select(DOCUMENTS.c.id).where(readable_documents_predicate(ctx)))
            ).scalars()
        )
        access = await load_document_access(s, world.org, list(world.documents.values()))
    via_reference = {i for i, doc in access.items() if readable(ctx, doc)}
    assert via_sql == via_reference, person
    return {world.names[i] for i in via_sql}


async def add_member_grant(
    db: Database, world: Halden, document: str, principal_type: str, principal: uuid.UUID
) -> None:
    await add_grant(db, world.org, world.documents[document], principal_type, principal)
