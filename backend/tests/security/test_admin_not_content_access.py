"""Administrative capability never implies document access (SEC-15, T3).

Permanent tests. An administrator manages Lattice; what they can *read* is decided by
exactly the same policy as for anyone else, from the same attributes. The
AuthorizationContext has no administrator or role field at all, and the resolver does
not read ``memberships.is_admin`` or ``role_label``.
"""

from sqlalchemy import text

from lattice.authz import (
    AuthorizationContext,
    explain_for_reviewer,
    load_document_access,
)
from lattice.db.engine import Database

from .conftest import Halden, context_for, readable_names
from .test_authz_personas import EXPECTED


def test_the_authorization_context_has_no_capability_or_role_field() -> None:
    fields = set(AuthorizationContext.model_fields)
    assert not {f for f in fields if "admin" in f or "role" in f or "capab" in f}, fields


async def test_the_administrator_reads_only_what_their_attributes_allow(
    app_db: Database, halden: Halden
) -> None:
    lena = await readable_names(app_db, halden, "lena")
    assert lena == EXPECTED["lena"] == {"company-handbook", "it-admin-runbook"}
    for denied in (
        "board-mna-memo",  # restricted
        "compensation-review",  # restricted, granted to someone else
        "pricing-strategy",  # confidential
        "acme-exec-escalation",  # confidential, Leadership
        "acme-success-plan",  # account-specific
        "acme-private-channel",  # grants-only
        "support-runbook",  # another department
    ):
        assert denied not in lena


async def _set_capability(
    db: Database, world: Halden, person: str, *, is_admin: bool, role_label: str
) -> None:
    async with db.tenant_transaction(world.org) as s:
        await s.execute(
            text("UPDATE memberships SET is_admin = :a, role_label = :r WHERE id = :m"),
            {"a": is_admin, "r": role_label, "m": world.members[person]},
        )


async def test_granting_or_removing_admin_changes_no_one_s_access(
    owner_db: Database, app_db: Database, halden: Halden
) -> None:
    for person in EXPECTED:
        before_ctx = await context_for(app_db, halden.org, halden.members[person])
        before = await readable_names(app_db, halden, person)

        await _set_capability(owner_db, halden, person, is_admin=True, role_label="Administrator")
        promoted_ctx = await context_for(app_db, halden.org, halden.members[person])
        assert await readable_names(app_db, halden, person) == before, person
        assert promoted_ctx == before_ctx, person

        await _set_capability(owner_db, halden, person, is_admin=False, role_label="Employee")
        assert await readable_names(app_db, halden, person) == before, person
        assert await context_for(app_db, halden.org, halden.members[person]) == before_ctx


async def test_an_admin_cannot_use_explanations_to_learn_about_unreadable_documents(
    app_db: Database, halden: Halden
) -> None:
    lena = await context_for(app_db, halden.org, halden.members["lena"])
    arjun = await context_for(app_db, halden.org, halden.members["arjun"])
    async with app_db.tenant_snapshot(halden.org) as s:
        memo = (await load_document_access(s, halden.org, [halden.documents["board-mna-memo"]]))[
            halden.documents["board-mna-memo"]
        ]
        handbook = (
            await load_document_access(s, halden.org, [halden.documents["company-handbook"]])
        )[halden.documents["company-handbook"]]
    # Unreadable to the admin: indistinguishable from a document that does not exist.
    assert explain_for_reviewer(lena, arjun, memo) is None
    assert explain_for_reviewer(lena, arjun, None) is None
    # A document the admin can read may be explained, for any member of the org.
    explanation = explain_for_reviewer(lena, arjun, handbook)
    assert explanation is not None
    assert explanation.readable


def test_the_resolver_does_not_read_the_admin_flag_or_role_label() -> None:
    from lattice.authz import resolve

    statement = resolve._CONTEXT_SQL.text
    assert "is_admin" not in statement
    assert "role_label" not in statement
