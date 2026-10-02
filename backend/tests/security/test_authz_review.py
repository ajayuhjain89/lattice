"""Review contexts (Phase 9): another member's context as it is, or as a proposed
change would make it. They must equal the request-time resolver for an active member,
never let an inactive member read, and validate like any context."""

import pytest
from pydantic import ValidationError

from lattice.authz import (
    load_document_access,
    proposed_context,
    readable,
    resolve_authorization_context,
    resolve_member_context,
)
from lattice.db.engine import Database

from ..access.conftest import build
from ..retrieval.world import Org


@pytest.fixture
async def org(owner_db: Database) -> Org:
    return await build(owner_db)


async def test_an_active_members_review_context_is_their_request_context(
    app_db: Database, org: Org
) -> None:
    async with app_db.tenant_snapshot(org.id) as s:
        for membership in org.members.values():
            review = await resolve_member_context(
                s, organization_id=org.id, membership_id=membership
            )
            request = await resolve_authorization_context(
                s, organization_id=org.id, membership_id=membership
            )
            assert review == request


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE memberships SET status = 'suspended' WHERE id = :m",
        "UPDATE users SET status = 'disabled' "
        "WHERE id = (SELECT user_id FROM memberships WHERE id = :m)",
    ],
)
async def test_an_inactive_member_is_reviewed_but_reads_nothing(
    app_db: Database, org: Org, sql: str
) -> None:
    await org.execute(sql, m=org.members["tom"])
    async with app_db.tenant_snapshot(org.id) as s:
        ctx = await resolve_member_context(
            s, organization_id=org.id, membership_id=org.members["tom"]
        )
        docs = await load_document_access(s, org.id, org.documents.values())
    assert ctx is not None
    assert ctx.membership_status == "suspended"
    assert not any(readable(ctx, d) for d in docs.values())


async def test_another_organizations_member_has_no_review_context(
    app_db: Database, org: Org, owner_db: Database
) -> None:
    other = await build(owner_db, "veridian")
    async with app_db.tenant_snapshot(org.id) as s:
        found = await resolve_member_context(
            s, organization_id=org.id, membership_id=other.members["tom"]
        )
    assert found is None


async def test_proposed_contexts_validate_and_keep_everything_else(
    app_db: Database, org: Org
) -> None:
    async with app_db.tenant_snapshot(org.id) as s:
        maya = await resolve_member_context(
            s, organization_id=org.id, membership_id=org.members["maya"]
        )
    assert maya is not None
    raised = proposed_context(maya, clearance="confidential", accounts=[])
    assert raised.clearance == "confidential"
    assert raised.assigned_account_ids == frozenset()
    assert (raised.department_ids, raised.group_ids, raised.membership_id) == (
        maya.department_ids,
        maya.group_ids,
        maya.membership_id,
    )
    with pytest.raises(ValidationError):
        proposed_context(maya, clearance="restricted")
    with pytest.raises(ValidationError):
        proposed_context(maya, membership_status="admin")
