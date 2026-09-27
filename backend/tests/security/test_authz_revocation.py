"""Revocation takes effect on the very next authorization check (SEC-4, T8).

Every change below is a single committed row write -- nothing is re-ingested,
re-embedded or rebuilt, and there is no cache to invalidate. After the write, a newly
resolved context must reflect it, through both the SQL predicate and the reference
policy (``readable_names`` asserts they agree).
"""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select, text

from lattice.authz import (
    AuthorizationContextUnavailable,
    assert_all_readable,
    readable_documents_predicate,
    resolve_authorization_context,
)
from lattice.authz.sql import DOCUMENTS
from lattice.db.engine import Database
from lattice.errors import LatticeError
from lattice.identity.sessions import SessionPolicy, SessionService

from .conftest import Halden, context_for, readable_names


async def _write(db: Database, world: Halden, sql: str, **params: object) -> None:
    async with db.tenant_transaction(world.org) as s:
        result = await s.execute(text(sql), params)
        assert result.rowcount >= 1, sql  # type: ignore[attr-defined]


async def _documents_fingerprint(db: Database, world: Halden) -> list[tuple[object, ...]]:
    """Every document row as stored: proves membership-side changes rewrite nothing."""
    async with db.tenant_transaction(world.org) as s:
        rows = await s.execute(text("SELECT * FROM documents ORDER BY id"))
        return [tuple(r) for r in rows.all()]


Change = Callable[[Database, Halden], Awaitable[None]]


async def _unassign_maya_from_acme(db: Database, w: Halden) -> None:
    await _write(
        db,
        w,
        "DELETE FROM account_assignments WHERE membership_id = :m AND account_id = :a",
        m=w.members["maya"],
        a=w.accounts["acme-corp"],
    )


async def _remove_priya_from_support(db: Database, w: Halden) -> None:
    await _write(
        db,
        w,
        "DELETE FROM membership_departments WHERE membership_id = :m AND department_id = :d",
        m=w.members["priya"],
        d=w.departments["support"],
    )


async def _remove_maya_from_channel(db: Database, w: Halden) -> None:
    await _write(
        db,
        w,
        "DELETE FROM access_group_members WHERE membership_id = :m AND group_id = :g",
        m=w.members["maya"],
        g=w.groups["acct-acme"],
    )


async def _remove_arjuns_personal_grant(db: Database, w: Halden) -> None:
    await _write(
        db,
        w,
        "DELETE FROM document_grants WHERE document_id = :d AND membership_id = :m",
        d=w.documents["compensation-review"],
        m=w.members["arjun"],
    )


async def _remove_support_department_grant(db: Database, w: Halden) -> None:
    await _write(
        db,
        w,
        "DELETE FROM document_grants WHERE document_id = :d AND department_id = :g",
        d=w.documents["incident-postmortem"],
        g=w.departments["support"],
    )


async def _remove_exec_group_grant(db: Database, w: Halden) -> None:
    await _write(
        db,
        w,
        "DELETE FROM document_grants WHERE document_id = :d AND group_id = :g",
        d=w.documents["board-mna-memo"],
        g=w.groups["exec-staff"],
    )


async def _lower_grace_clearance(db: Database, w: Halden) -> None:
    await _write(
        db,
        w,
        "UPDATE memberships SET clearance = 'internal', authz_updated_at = now() WHERE id = :m",
        m=w.members["grace"],
    )


async def _narrow_tom_to_assigned(db: Database, w: Halden) -> None:
    await _write(
        db,
        w,
        "UPDATE memberships SET account_scope = 'assigned' WHERE id = :m",
        m=w.members["tom"],
    )


async def _delete_the_acme_channel_group(db: Database, w: Halden) -> None:
    await _write(db, w, "DELETE FROM access_groups WHERE id = :g", g=w.groups["acct-acme"])


# (change, person, documents lost, documents that must survive the change)
MEMBER_SIDE: list[tuple[Change, str, set[str], set[str]]] = [
    # The private channel survives: it is reached through a group grant, not the account.
    (_unassign_maya_from_acme, "maya", {"acme-success-plan"}, {"acme-private-channel"}),
    (_remove_priya_from_support, "priya", {"support-runbook", "incident-postmortem"}, set()),
    (_remove_maya_from_channel, "maya", {"acme-private-channel"}, {"acme-success-plan"}),
    (_remove_arjuns_personal_grant, "arjun", {"compensation-review"}, {"board-mna-memo"}),
    (_remove_support_department_grant, "priya", {"incident-postmortem"}, {"support-runbook"}),
    (_remove_exec_group_grant, "grace", {"board-mna-memo"}, {"acme-exec-escalation"}),
    (_remove_exec_group_grant, "arjun", {"board-mna-memo"}, {"compensation-review"}),
    # Clearance never reached the grant path: board-mna-memo stays (Exec staff grant).
    (
        _lower_grace_clearance,
        "grace",
        {"acme-exec-escalation", "pricing-strategy"},
        {"board-mna-memo", "acme-success-plan"},
    ),
    (
        _narrow_tom_to_assigned,
        "tom",
        {"acme-success-plan", "kestrel-renewal", "kestrel-shared-with-maya"},
        {"product-roadmap"},
    ),
    (_delete_the_acme_channel_group, "grace", {"acme-private-channel"}, {"board-mna-memo"}),
]


@pytest.mark.parametrize(
    ("change", "person", "lost", "kept"),
    MEMBER_SIDE,
    ids=[f"{c.__name__.strip('_')}-{p}" for c, p, _, _ in MEMBER_SIDE],
)
async def test_access_change_applies_to_the_next_check(
    owner_db: Database,
    app_db: Database,
    halden: Halden,
    change: Change,
    person: str,
    lost: set[str],
    kept: set[str],
) -> None:
    before = await readable_names(app_db, halden, person)
    assert lost <= before
    assert kept <= before
    documents_before = await _documents_fingerprint(owner_db, halden)

    await change(owner_db, halden)

    after = await readable_names(app_db, halden, person)
    assert after == before - lost
    # Nothing about the documents was rewritten to achieve the revocation.
    assert await _documents_fingerprint(owner_db, halden) == documents_before


DOCUMENT_SIDE = [
    ("UPDATE documents SET sensitivity = 'confidential' WHERE id = :d", "acme-success-plan"),
    ("UPDATE documents SET access_mode = 'grants_only' WHERE id = :d", "acme-success-plan"),
    ("UPDATE documents SET status = 'deleted' WHERE id = :d", "acme-success-plan"),
    (
        "UPDATE documents SET department_ids = ARRAY[CAST(:dept AS uuid)] WHERE id = :d",
        "acme-success-plan",
    ),
]


@pytest.mark.parametrize(("statement", "document"), DOCUMENT_SIDE)
async def test_document_access_change_applies_to_the_next_check(
    owner_db: Database, app_db: Database, halden: Halden, statement: str, document: str
) -> None:
    assert document in await readable_names(app_db, halden, "maya")
    await _write(
        owner_db,
        halden,
        statement,
        d=halden.documents[document],
        dept=halden.departments["leadership"],
    )
    assert document not in await readable_names(app_db, halden, "maya")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE memberships SET status = 'suspended' WHERE id = :m",
        "UPDATE memberships SET status = 'removed' WHERE id = :m",
        "UPDATE users SET status = 'disabled' WHERE id = "
        "(SELECT user_id FROM memberships WHERE id = :m)",
    ],
)
async def test_deactivation_stops_both_authentication_and_authorization(
    owner_db: Database, app_db: Database, halden: Halden, statement: str
) -> None:
    grace = halden.members["grace"]
    async with owner_db.tenant_transaction(halden.org) as s:
        user_id: UUID = (
            await s.execute(text("SELECT user_id FROM memberships WHERE id = :m"), {"m": grace})
        ).scalar_one()
    sessions = SessionService(
        app_db, SessionPolicy(idle_timeout=timedelta(hours=1), absolute_timeout=timedelta(days=1))
    )
    token, _ = await sessions.create(user_id=user_id, organization_id=halden.org)
    assert "board-mna-memo" in await readable_names(app_db, halden, "grace")

    await _write(owner_db, halden, statement, m=grace)

    with pytest.raises(LatticeError):  # E1: the session is rejected on the next request
        await sessions.authenticate(token)
    with pytest.raises(AuthorizationContextUnavailable):  # and no context can be resolved
        await context_for(app_db, halden.org, grace)


async def test_suspended_organization_resolves_no_context(
    owner_db: Database, app_db: Database, halden: Halden
) -> None:
    async with owner_db.unscoped_transaction() as s:
        await s.execute(
            text("UPDATE organizations SET status = 'suspended' WHERE id = :o"), {"o": halden.org}
        )
    with pytest.raises(AuthorizationContextUnavailable):
        await context_for(app_db, halden.org, halden.members["priya"])


async def test_a_revocation_is_consistent_within_one_snapshot_and_visible_to_the_next(
    owner_db: Database, app_db: Database, halden: Halden
) -> None:
    """Context, retrieval and the tripwire share one REPEATABLE READ snapshot, so a
    revocation committed mid-request neither trips E4 falsely nor mixes permissions.
    The next request sees it."""
    maya = halden.members["maya"]
    doc_a = halden.documents["acme-success-plan"]
    async with app_db.tenant_snapshot(halden.org) as s:
        ctx = await resolve_authorization_context(s, organization_id=halden.org, membership_id=maya)
        retrieved: set[UUID] = set(
            (
                await s.execute(select(DOCUMENTS.c.id).where(readable_documents_predicate(ctx)))
            ).scalars()
        )
        assert doc_a in retrieved

        await _unassign_maya_from_acme(owner_db, halden)  # commits concurrently

        # Same snapshot: still consistent with the context the request started with.
        await assert_all_readable(s, ctx, retrieved)

    # Next request: new snapshot, new context -- the revocation applies.
    assert "acme-success-plan" not in await readable_names(app_db, halden, "maya")
    async with app_db.tenant_snapshot(halden.org) as s:
        stale = await resolve_authorization_context(
            s, organization_id=halden.org, membership_id=maya
        )
        assert doc_a not in set(
            (
                await s.execute(select(DOCUMENTS.c.id).where(readable_documents_predicate(stale)))
            ).scalars()
        )


async def test_snapshot_is_repeatable_read_and_read_only(app_db: Database, halden: Halden) -> None:
    async with app_db.tenant_snapshot(halden.org) as s:
        isolation: object = (await s.execute(text("SHOW transaction_isolation"))).scalar_one()
        read_only: object = (await s.execute(text("SHOW transaction_read_only"))).scalar_one()
    assert (isolation, read_only) == ("repeatable read", "on")
