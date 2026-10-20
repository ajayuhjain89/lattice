"""Ordinary sign-in, the OIDC configuration test sign-in and the D-34 step-up are three
flows of one ``oidc_login_attempts`` table (0013, 0020, 0021). None is ever proof for
another (P12.8 review): the fenced functions that consume an attempt name its purpose.

* a test sign-in or an ordinary sign-in never yields a step-up grant
  (``lattice_create_step_up_grant``: ``purpose = 'step_up'``);
* a step-up or an ordinary sign-in is never the proof of an issuer or claim change
  (``lattice_oidc_change_identity``: ``purpose = 'test'``).

Each refusal is shown beside its control -- the right flow's attempt passes the same call
(in a transaction rolled back, so no audit event is needed).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice.db.engine import Database

from ..factories import create_oidc_connection, create_org, create_provisioned_member

NEW_ISSUER = "https://idp.next.example"


class _Rollback(Exception):
    pass


@dataclass
class World:
    org: uuid.UUID
    connection: uuid.UUID
    lena: uuid.UUID
    session: uuid.UUID
    sign_in: uuid.UUID
    test: uuid.UUID
    step_up: uuid.UUID


async def _attempt(db: Database, org: uuid.UUID, columns: dict[str, object]) -> uuid.UUID:
    base: dict[str, object] = {
        "organization_id": org,
        "state_hash": uuid.uuid4().bytes * 2,
        "binding_hash": uuid.uuid4().bytes * 2,
        "nonce": "n" * 43,
        "code_verifier": "v" * 43,
    }
    values = {**base, **columns}
    names = ", ".join(values)
    params = ", ".join(f":{n}" for n in values)
    async with db.tenant_transaction(org) as s:
        found: uuid.UUID = (
            await s.execute(
                text(
                    f"INSERT INTO oidc_login_attempts ({names}, expires_at, consumed_at) "  # noqa: S608 - fixed names
                    f"VALUES ({params}, now() + interval '10 minutes', now()) RETURNING id"
                ),
                values,
            )
        ).scalar_one()
    return found


@pytest.fixture
async def world(owner_db: Database) -> World:
    org = await create_org(owner_db, "halden")
    connection = await create_oidc_connection(owner_db, org)
    lena = await create_provisioned_member(
        owner_db, org, connection, "lena@halden.example", "sub-lena"
    )
    async with owner_db.tenant_transaction(org) as s:
        await s.execute(
            text("UPDATE memberships SET is_admin = true WHERE id = :m"), {"m": lena.membership_id}
        )
        session: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO sessions (token_hash, user_id, organization_id, expires_at) "
                    "VALUES (sha256('flow'::bytea), :u, :o, now() + interval '1 hour') "
                    "RETURNING id"
                ),
                {"u": lena.user_id, "o": org},
            )
        ).scalar_one()
    common: dict[str, object] = {"connection_id": connection}
    return World(
        org=org,
        connection=connection,
        lena=lena.membership_id,
        session=session,
        sign_in=await _attempt(owner_db, org, common),
        # Lena's matched test sign-in at the new issuer: a valid proof.
        test=await _attempt(
            owner_db,
            org,
            {
                **common,
                "purpose": "test",
                "membership_id": lena.membership_id,
                "proposed_issuer": NEW_ISSUER,
                "outcome": "matched",
                "observed_identifier": "sub-lena",
                "completed_at": datetime.now(UTC),
            },
        ),
        # Lena's step-up, back from the provider and not yet decided: grantable.
        step_up=await _attempt(
            owner_db,
            org,
            {
                **common,
                "purpose": "step_up",
                "membership_id": lena.membership_id,
                "session_id": session,
                "change": '{"kind": "settings"}',
                "reason": "Raise the ceiling",
                "policy_version": 0,
            },
        ),
    )


_GRANT = "SELECT lattice_create_step_up_grant(:a, :s, :t, now(), NULL)"
_CHANGE = "SELECT lattice_oidc_change_identity(:a, :c, :v, :i, 'sub', :p)"


async def _version(owner_db: Database, w: World) -> object:
    async with owner_db.tenant_snapshot(w.org) as s:
        return (
            await s.execute(
                text("SELECT updated_at FROM oidc_connections WHERE id = :c"), {"c": w.connection}
            )
        ).scalar_one()


async def test_only_a_step_up_attempt_yields_a_step_up_grant(
    app_db: Database, world: World
) -> None:
    for other in (world.test, world.sign_in):
        with pytest.raises(DBAPIError, match="step_up_invalid"):
            async with app_db.tenant_transaction(world.org) as s:
                await s.execute(text(_GRANT), {"a": world.lena, "s": world.session, "t": other})
    with pytest.raises(_Rollback):  # the control: the step-up's own attempt is granted
        async with app_db.tenant_transaction(world.org) as s:
            granted: object = (
                await s.execute(
                    text(_GRANT), {"a": world.lena, "s": world.session, "t": world.step_up}
                )
            ).scalar_one()
            assert isinstance(granted, uuid.UUID)
            raise _Rollback


async def test_only_a_test_sign_in_proves_an_identity_change(
    app_db: Database, owner_db: Database, world: World
) -> None:
    params = {
        "a": world.lena,
        "c": world.connection,
        "v": await _version(owner_db, world),
        "i": NEW_ISSUER,
    }
    for other in (world.step_up, world.sign_in):
        with pytest.raises(DBAPIError, match="identity_unverified"):
            async with app_db.tenant_transaction(world.org) as s:
                await s.execute(text(_CHANGE), {**params, "p": other})
    with pytest.raises(_Rollback):  # the control: the matched test sign-in proves it
        async with app_db.tenant_transaction(world.org) as s:
            await s.execute(text(_CHANGE), {**params, "p": world.test})
            raise _Rollback
