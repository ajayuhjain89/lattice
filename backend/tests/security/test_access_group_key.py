"""The access-group key (D-9) is an identifier, never an authorization input.

Grants and group membership refer to groups by ID. Renaming a group or changing its
key must leave every member's readable set exactly as it was; only membership rows and
grants change access.
"""

import pytest
from sqlalchemy import text

from lattice.authz import AuthorizationContext
from lattice.db.engine import Database

from .conftest import Halden, context_for, readable_names
from .test_authz_personas import EXPECTED


def test_the_context_carries_group_ids_only() -> None:
    assert "group_ids" in AuthorizationContext.model_fields
    assert not any("key" in field for field in AuthorizationContext.model_fields)


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE access_groups SET name = 'Executive leadership' WHERE id = :g",
        "UPDATE access_groups SET key = 'leadership-team' WHERE id = :g",
    ],
)
async def test_renaming_or_rekeying_a_group_changes_no_access(
    owner_db: Database, app_db: Database, halden: Halden, change: str
) -> None:
    before = {p: await context_for(app_db, halden.org, halden.members[p]) for p in EXPECTED}
    async with owner_db.tenant_transaction(halden.org) as s:
        result = await s.execute(text(change), {"g": halden.groups["exec-staff"]})
        assert result.rowcount == 1  # type: ignore[attr-defined]
    for person in EXPECTED:
        assert await readable_names(app_db, halden, person) == EXPECTED[person]
        after = await context_for(app_db, halden.org, halden.members[person])
        assert after.group_ids == before[person].group_ids
