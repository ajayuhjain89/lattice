"""Accounts and mappings (Phase 12, P12.3): a small Halden and Veridian, each with a
ticketing and a chat source, administrators and members (``tests/access/conftest``'s
Halden: Lena and Olga administrators, Maya assigned Acme)."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text

from lattice.authz import Document, readable_documents_predicate, resolve_authorization_context
from lattice.db.engine import Database

from ..access.conftest import build
from ..conftest import CSRF, AppFactory
from ..factories import create_source, login
from ..retrieval.world import Org, new_org


@dataclass
class World:
    halden: Org
    veridian: Org
    # source key -> id, per organization
    halden_sources: dict[str, uuid.UUID]
    veridian_sources: dict[str, uuid.UUID]


@pytest.fixture
async def world(owner_db: Database) -> World:
    halden = await build(owner_db)
    veridian = await new_org(owner_db, "veridian")
    await veridian.account("acme", "Acme Corp")
    await veridian.member("victor", scope="all")
    await veridian.execute(
        "UPDATE memberships SET is_admin = true WHERE id = :m", m=veridian.members["victor"]
    )
    sources: list[dict[str, uuid.UUID]] = []
    for org in (halden, veridian):
        found = {}
        for key, kind in (("support", "ticketing"), ("slack", "chat_export")):
            found[key] = await create_source(owner_db, org.id, key=key, connector_type=kind)
        await org.execute(
            "UPDATE sources SET account_scoped = true WHERE id = :s", s=found["support"]
        )
        sources.append(found)
    return World(halden, veridian, sources[0], sources[1])


@pytest.fixture
async def client(app_client: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client() as c:
        yield c


async def as_member(client: httpx.AsyncClient, org: Org, name: str) -> None:
    client.cookies.clear()
    assert (await login(client, f"{name}@{org.slug}.example")).status_code == 200


async def post(client: httpx.AsyncClient, path: str, body: Any) -> httpx.Response:
    return await client.post(f"/v1/admin/access/{path}", json=body, headers=CSRF)


async def patch(client: httpx.AsyncClient, path: str, body: Any) -> httpx.Response:
    return await client.patch(f"/v1/admin/access/{path}", json=body, headers=CSRF)


async def owner_rows(owner: Database, org: uuid.UUID, sql: str, **params: Any) -> list[Any]:
    async with owner.tenant_transaction(org) as s:
        return list((await s.execute(text(sql), params)).all())


async def readable(db: Database, org: uuid.UUID, membership: uuid.UUID) -> set[uuid.UUID]:
    """The documents ``membership`` can read now, by the policy itself (as the API role)."""
    async with db.tenant_snapshot(org) as s:
        ctx = await resolve_authorization_context(s, organization_id=org, membership_id=membership)
        table = Document.__table__
        rows = await s.execute(select(table.c.id).where(readable_documents_predicate(ctx, table)))
        return {r[0] for r in rows}


async def events(owner: Database, org: uuid.UUID, action_like: str = "account.%") -> list[Any]:
    return await owner_rows(
        owner,
        org,
        "SELECT actor, action, target, before, after, reason FROM audit_events "
        "WHERE action LIKE :a ORDER BY created_at, id",
        a=action_like,
    )
