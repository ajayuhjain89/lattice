"""Test data builders. They write as lattice_owner through row-level security, binding
the target organization first -- the same way the seed tool does."""

import re
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy import text

from lattice.db.engine import Database, bind_organization
from tests.conftest import CSRF


@dataclass(frozen=True)
class Member:
    organization_id: uuid.UUID
    user_id: uuid.UUID
    membership_id: uuid.UUID
    email: str


async def create_org(db: Database, slug: str, status: str = "active") -> uuid.UUID:
    org_id = uuid.uuid4()
    async with db.unscoped_transaction() as s:
        await bind_organization(s, org_id)
        await s.execute(
            text("INSERT INTO organizations (id, slug, name, status) VALUES (:id, :slug, :n, :st)"),
            {"id": org_id, "slug": slug, "n": slug.title(), "st": status},
        )
    return org_id


async def create_member(
    db: Database,
    organization_id: uuid.UUID,
    email: str,
    *,
    status: str = "active",
    is_admin: bool = False,
    dev_identity: bool = True,
) -> Member:
    async with db.tenant_transaction(organization_id) as s:
        user_id: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO users (email, display_name) VALUES (:e, :n) "
                    "ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id"
                ),
                {"e": email, "n": email.split("@")[0]},
            )
        ).scalar_one()
        if dev_identity:
            await s.execute(
                text(
                    "INSERT INTO external_identities (user_id, issuer, subject) "
                    "VALUES (:u, 'dev', :e) ON CONFLICT DO NOTHING"
                ),
                {"u": user_id, "e": email},
            )
        membership_id: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO memberships (organization_id, user_id, status, is_admin, "
                    "role_label) VALUES (:o, :u, :st, :adm, 'Tester') RETURNING id"
                ),
                {"o": organization_id, "u": user_id, "st": status, "adm": is_admin},
            )
        ).scalar_one()
    return Member(organization_id, user_id, membership_id, email)


async def login(
    client: httpx.AsyncClient, email: str, organization: str | None = None
) -> httpx.Response:
    body: dict[str, str] = {"subject": email}
    if organization is not None:
        body["organization"] = organization
    return await client.post("/v1/auth/dev-login", json=body, headers=CSRF)


# --- Authorization (Phase 2) ----------------------------------------------------------


async def create_department(db: Database, organization_id: uuid.UUID, key: str) -> uuid.UUID:
    async with db.tenant_transaction(organization_id) as s:
        value: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO departments (organization_id, key, name) "
                    "VALUES (:o, :k, :n) RETURNING id"
                ),
                {"o": organization_id, "k": key, "n": key.title()},
            )
        ).scalar_one()
    return value


async def create_account(db: Database, organization_id: uuid.UUID, key: str) -> uuid.UUID:
    async with db.tenant_transaction(organization_id) as s:
        value: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO accounts (organization_id, key, name) "
                    "VALUES (:o, :k, :n) RETURNING id"
                ),
                {"o": organization_id, "k": key, "n": key.title()},
            )
        ).scalar_one()
    return value


async def create_group(
    db: Database,
    organization_id: uuid.UUID,
    name: str,
    members: tuple[uuid.UUID, ...] = (),
    *,
    origin: str = "admin",
    key: str | None = None,
) -> uuid.UUID:
    if key is None:  # unique by default, so fixtures never collide with seeded groups
        key = f"{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}-{uuid.uuid4().hex[:8]}"
    async with db.tenant_transaction(organization_id) as s:
        group_id: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO access_groups (organization_id, key, origin, external_ref, "
                    "name) VALUES (:o, :key, :origin, :ref, :n) RETURNING id"
                ),
                {
                    "o": organization_id,
                    "key": key,
                    "origin": origin,
                    "ref": f"ext-{name}" if origin == "source" else None,
                    "n": name,
                },
            )
        ).scalar_one()
        for membership_id in members:
            await s.execute(
                text(
                    "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
                    "VALUES (:o, :g, :m)"
                ),
                {"o": organization_id, "g": group_id, "m": membership_id},
            )
    return group_id


async def create_document(
    db: Database,
    organization_id: uuid.UUID,
    *,
    sensitivity: str = "internal",
    access_mode: str = "policy",
    account_id: uuid.UUID | None = None,
    department_ids: tuple[uuid.UUID, ...] = (),
    status: str = "active",
    grants: tuple[tuple[str, uuid.UUID], ...] = (),
) -> uuid.UUID:
    async with db.tenant_transaction(organization_id) as s:
        document_id: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO documents (organization_id, account_id, sensitivity, "
                    "access_mode, department_ids, status) "
                    "VALUES (:o, :a, :s, :m, CAST(:d AS uuid[]), :st) RETURNING id"
                ),
                {
                    "o": organization_id,
                    "a": account_id,
                    "s": sensitivity,
                    "m": access_mode,
                    "d": list(department_ids),
                    "st": status,
                },
            )
        ).scalar_one()
    for principal_type, principal_id in grants:
        await add_grant(db, organization_id, document_id, principal_type, principal_id)
    return document_id


async def add_grant(
    db: Database,
    organization_id: uuid.UUID,
    document_id: uuid.UUID,
    principal_type: str,
    principal_id: uuid.UUID,
    *,
    origin: str = "admin",
) -> None:
    column = {"membership": "membership_id", "department": "department_id", "group": "group_id"}
    async with db.tenant_transaction(organization_id) as s:
        await s.execute(
            text(
                "INSERT INTO document_grants (organization_id, document_id, principal_type, "  # noqa: S608 - column from a fixed map
                f"{column[principal_type]}, origin) VALUES (:o, :d, :t, :p, :origin)"
            ),
            {
                "o": organization_id,
                "d": document_id,
                "t": principal_type,
                "p": principal_id,
                "origin": origin,
            },
        )
