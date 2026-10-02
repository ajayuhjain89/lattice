"""A provisioning world: two organizations, each with an OIDC connection and a SCIM
token, an administrator and a manifest-style (global) member in the first."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from lattice.db.engine import Database

from ..conftest import AppFactory
from ..factories import (
    Member,
    create_member,
    create_oidc_connection,
    create_org,
    create_scim_token,
)


@dataclass
class Tenant:
    org: uuid.UUID
    slug: str
    connection: uuid.UUID
    token: str

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class World:
    halden: Tenant
    veridian: Tenant
    admin: Member
    maya: Member


@pytest.fixture
async def world(owner_db: Database) -> World:
    tenants = []
    for slug in ("halden", "veridian"):
        org = await create_org(owner_db, slug)
        connection = await create_oidc_connection(
            owner_db, org, issuer=f"https://idp.{slug}.example"
        )
        tenants.append(
            Tenant(org, slug, connection, await create_scim_token(owner_db, org, connection))
        )
    halden, veridian = tenants
    return World(
        halden=halden,
        veridian=veridian,
        admin=await create_member(owner_db, halden.org, "lena@halden.example", is_admin=True),
        maya=await create_member(owner_db, halden.org, "maya@halden.example"),
    )


@pytest.fixture
async def scim(app_client: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client() as client:
        yield client


def user(
    external_id: str,
    user_name: str,
    *,
    email: str | None = None,
    active: bool = True,
    given: str = "Pat",
    family: str = "Doe",
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "externalId": external_id,
        "userName": user_name,
        "name": {"givenName": given, "familyName": family},
        "emails": [{"value": email or user_name, "type": "work", "primary": True}],
        "active": active,
    }
    body.update(extra)
    return body


def patch(*operations: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": list(operations),
    }
