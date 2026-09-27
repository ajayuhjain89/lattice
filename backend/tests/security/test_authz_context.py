"""AuthorizationContext resolution: derived only from server-side state.

The organization and membership come from the server-side session; every other field
comes from database rows of that organization. Nothing a client sends can change it.
"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import text

from lattice.authz import POLICY_VERSION
from lattice.db.engine import Database
from lattice.identity.sessions import SessionPolicy, SessionService

from .conftest import Halden, context_for


async def test_context_reflects_the_membership_rows(
    owner_db: Database, app_db: Database, halden: Halden
) -> None:
    ctx = await context_for(app_db, halden.org, halden.members["maya"])
    async with owner_db.tenant_transaction(halden.org) as s:
        updated_at: object = (
            await s.execute(
                text("SELECT authz_updated_at FROM memberships WHERE id = :m"),
                {"m": halden.members["maya"]},
            )
        ).scalar_one()
    assert ctx.organization_id == halden.org
    assert ctx.membership_id == halden.members["maya"]
    assert ctx.membership_status == "active"
    assert ctx.clearance == "internal"
    assert ctx.account_scope == "assigned"
    assert ctx.assigned_account_ids == {
        halden.accounts["acme-corp"],
        halden.accounts["northpeak-logistics"],
    }
    assert ctx.department_ids == {halden.departments["sales"]}
    assert ctx.group_ids == {halden.groups["acct-acme"]}
    assert ctx.authz_updated_at == updated_at
    assert ctx.policy_version == POLICY_VERSION


async def test_multi_department_multi_group_member(app_db: Database, halden: Halden) -> None:
    grace = await context_for(app_db, halden.org, halden.members["grace"])
    assert grace.clearance == "confidential"
    assert grace.account_scope == "all"
    assert grace.department_ids == {
        halden.departments["leadership"],
        halden.departments["customer-success"],
    }
    assert grace.group_ids == {halden.groups["exec-staff"], halden.groups["acct-acme"]}


async def test_context_is_resolved_from_the_session_principal(
    owner_db: Database, app_db: Database, halden: Halden
) -> None:
    """The intended request path: session -> Principal -> context. Only the principal's
    organization and membership IDs are inputs."""
    async with owner_db.tenant_transaction(halden.org) as s:
        user_id: UUID = (
            await s.execute(
                text("SELECT user_id FROM memberships WHERE id = :m"),
                {"m": halden.members["priya"]},
            )
        ).scalar_one()
    sessions = SessionService(
        app_db, SessionPolicy(idle_timeout=timedelta(hours=1), absolute_timeout=timedelta(days=1))
    )
    token, _ = await sessions.create(user_id=user_id, organization_id=halden.org)
    principal = await sessions.authenticate(token)
    ctx = await context_for(app_db, principal.organization_id, principal.membership_id)
    assert ctx.membership_id == halden.members["priya"]
    assert ctx.department_ids == {halden.departments["support"]}


async def test_repeated_resolution_has_no_cache(
    owner_db: Database, app_db: Database, halden: Halden
) -> None:
    first = await context_for(app_db, halden.org, halden.members["daniel"])
    async with owner_db.tenant_transaction(halden.org) as s:
        await s.execute(
            text(
                "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
                "VALUES (:o, :g, :m)"
            ),
            {"o": halden.org, "g": halden.groups["acct-acme"], "m": halden.members["daniel"]},
        )
    second = await context_for(app_db, halden.org, halden.members["daniel"])
    assert first.group_ids == frozenset()
    assert second.group_ids == {halden.groups["acct-acme"]}
