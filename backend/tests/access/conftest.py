"""A small Halden for access-management tests.

Lena is an administrator who can read only internal, unrestricted content (scope all,
IT): the Acme plan, the Kestrel notes and the handbook -- not the executive memo (a
group grant), the confidential pricing sheet or the private Acme channel thread (a
source-synced group). Olga is a second administrator. Maya is an Account Manager
(Acme, Sales), Grace Leadership (confidential, Exec staff), Tom Support (all)."""

from collections.abc import AsyncIterator

import httpx
import pytest

from lattice.db.engine import Database

from ..conftest import AppFactory
from ..factories import login
from ..retrieval.world import Org, new_org

CANARY = "LATTICE-CANARY-ACCESS-9431"


async def source_group(org: Org, key: str, members: list[str]) -> None:
    result = await org.execute(
        "INSERT INTO access_groups (organization_id, key, origin, source_id, external_ref, "
        "name) VALUES (:o, :k, 'source', :s, :ref, :n) RETURNING id",
        o=org.id,
        k=key,
        s=org.source_id,
        ref=f"C-{key}",
        n=f"#{key} {CANARY}",
    )
    org.groups[key] = result.scalar_one()
    for name in members:
        await add_to_group(org, key, name)


async def add_to_group(org: Org, key: str, name: str) -> None:
    await org.execute(
        "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
        "VALUES (:o, :g, :m)",
        o=org.id,
        g=org.groups[key],
        m=org.members[name],
    )


async def build(owner: Database, slug: str = "halden") -> Org:
    org = await new_org(owner, slug)
    await org.account("acme", "Acme Corp")
    await org.account("kestrel", "Kestrel Bank")
    await org.department("sales")
    await org.department("leadership")
    await org.department("it")
    await org.group("exec")
    await org.member("lena", scope="all", departments=["it"])
    await org.member("olga", scope="all", departments=["it"])
    await org.member("maya", scope="assigned", accounts=["acme"], departments=["sales"])
    await org.member("grace", clearance="confidential", departments=["leadership"], groups=["exec"])
    await org.member("tom")
    for admin in ("lena", "olga"):
        await org.execute(
            "UPDATE memberships SET is_admin = true WHERE id = :m", m=org.members[admin]
        )
    await source_group(org, "acct-acme-private", ["maya"])
    await org.document("acme-plan", "Acme Q3 success plan", ["Plan."], account="acme")
    await org.document(
        "kestrel-notes", "Kestrel renewal notes", ["Notes."], account="kestrel", display_ref="KB-7"
    )
    await org.document("handbook", "Employee handbook", ["Rules."])
    await org.document(
        "exec-memo",
        f"Acquisition memo {CANARY}",
        ["Secret."],
        sensitivity="restricted",
        access_mode="grants_only",
        grants=[("group", "exec")],
    )
    await org.document(
        "pricing", f"Pricing sheet {CANARY}", ["Prices."], sensitivity="confidential"
    )
    await org.document(
        "channel-thread",
        f"Acme private thread {CANARY}",
        ["Thread."],
        access_mode="grants_only",
        grants=[("group", "acct-acme-private")],
    )
    return org


@pytest.fixture
async def org(owner_db: Database) -> Org:
    return await build(owner_db)


@pytest.fixture
async def client(app_client: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client() as c:
        yield c


async def sign_in(client: httpx.AsyncClient, org: Org, name: str) -> None:
    client.cookies.clear()
    assert (await login(client, f"{name}@{org.slug}.example")).status_code == 200
