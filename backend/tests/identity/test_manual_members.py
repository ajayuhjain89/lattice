"""Members added by hand sign in by their exact identifier (Phase 12, P12.2; C2.1).

The identifier is the exact value of the connection's identifying claim: compared byte
for byte, case preserved, never matched by e-mail -- also when the claim *is* ``email``.
Nobody is created at sign-in."""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text

from lattice.access.lifecycle import create_member
from lattice.access.models import NewMemberRequest
from lattice.db.engine import Database

from ..conftest import AppFactory
from ..factories import create_member as create_global_member
from ..factories import create_oidc_connection, create_org
from .fake_idp import FakeIdp
from .test_oidc import FAILED, KEY, _configure, _me, _sign_in


@pytest.fixture
def idp() -> FakeIdp:
    return FakeIdp()


@pytest.fixture
async def client(app_client: AppFactory, idp: FakeIdp) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(_configure(idp)) as c:
        yield c


async def _org(owner: Database, claim: str) -> uuid.UUID:
    org = await create_org(owner, "halden")
    await create_oidc_connection(
        owner, org, issuer="https://idp.halden.example", login_key=KEY, subject_claim=claim
    )
    return org


async def _add(app_db: Database, owner: Database, org: uuid.UUID, identifier: str) -> None:
    admin = await create_global_member(owner, org, "lena@halden.example", is_admin=True)
    await create_member(
        app_db,
        organization_id=org,
        actor_membership_id=admin.membership_id,
        request=NewMemberRequest(
            email="pat@halden.example", name="Pat Doe", identifier=identifier, reason="New hire"
        ),
    )


async def _memberships(owner: Database, org: uuid.UUID) -> int:
    async with owner.tenant_transaction(org) as s:
        return int((await s.execute(text("SELECT count(*) FROM memberships"))).scalar_one())


async def test_signs_in_with_the_exact_subject_only(
    client: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, app_db: Database
) -> None:
    org = await _org(owner_db, "sub")
    await _add(app_db, owner_db, org, "00uPat-Okta")
    for wrong in ("00upat-okta", "00uPat-Okta ", "pat@halden.example"):
        response = await _sign_in(client, idp, wrong)
        assert response.headers["location"] == FAILED, wrong
        assert await _me(client) is None
    response = await _sign_in(client, idp, "00uPat-Okta")
    assert response.status_code == 303
    me = await _me(client)
    assert me is not None
    assert me["user"]["email"] == "pat@halden.example"
    assert await _memberships(owner_db, org) == 2  # nothing created at sign-in


async def test_an_email_claim_is_still_an_identifier_never_an_email_match(
    client: httpx.AsyncClient, idp: FakeIdp, owner_db: Database, app_db: Database
) -> None:
    """The member's e-mail is pat@halden.example; the identity provider's email claim
    for them is pat.doe@corp.example. Only the claim value signs in."""
    org = await _org(owner_db, "email")
    await _add(app_db, owner_db, org, "pat.doe@corp.example")
    idp.claims = {"email": "pat@halden.example"}
    assert (await _sign_in(client, idp, "anything")).headers["location"] == FAILED
    idp.claims = {"email": "PAT.DOE@corp.example"}  # a different case is a different value
    assert (await _sign_in(client, idp, "anything")).headers["location"] == FAILED
    idp.claims = {"email": "pat.doe@corp.example"}
    assert (await _sign_in(client, idp, "anything")).status_code == 303
    me = await _me(client)
    assert me is not None
    assert me["user"]["email"] == "pat@halden.example"
    assert await _memberships(owner_db, org) == 2
