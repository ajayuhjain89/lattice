"""Test data builders. They write as lattice_owner through row-level security, binding
the target organization first -- the same way the seed tool does."""

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
