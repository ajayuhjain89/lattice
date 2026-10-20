"""A source purge and the access-audit coupling (migrations 0013, 0023, 0024; P12.8 review).

``lattice_require_access_audit`` refuses, at commit, any ``lattice_app`` write to an
access table without an ``access.*`` event of the same transaction naming its membership.
0023 exempts exactly the purge's cascade -- the delete of a grant whose document, or of a
group membership whose group, no longer exists -- because a group grant names no
membership and no event could ever satisfy it. These tests prove the exemption is that and
nothing more, against the real roles and the real deferred trigger:

* a legitimate purge of a source with grants and synced groups commits, with one event
  per member who loses a group (A, B, C); without them nothing of it commits (H);
* a purge left ``deleting`` resumes, and a completed one is a no-op (I, J);
* a direct grant or administrator-group delete still needs its event (D, E), also in the
  same transaction as a purge (F), also when the session rebinds its organization before
  commit (0024) -- and another organization's rows are out of reach (G);
* nobody can read more after a purge than before (K); the audit trail is untouched (L).
"""

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz import Document, readable_documents_predicate, resolve_authorization_context
from lattice.db.engine import Database

from ..factories import create_member, create_org, create_source

REFUSED = "without an audit event"
DOCUMENTS = Document.__table__


@dataclass
class Tenant:
    org: uuid.UUID
    actor: uuid.UUID  # an active administrator
    maya: uuid.UUID  # in the purged source's synced group; granted its document
    omar: uuid.UUID  # granted the surviving document; in an administrator group
    purged_source: uuid.UUID
    kept_source: uuid.UUID
    purged_doc: uuid.UUID
    kept_doc: uuid.UUID  # grants-only: readable through its grant alone
    open_doc: uuid.UUID  # policy path: readable by every member
    synced_group: uuid.UUID
    admin_group: uuid.UUID


async def _document(db: Database, org: uuid.UUID, source: uuid.UUID, mode: str) -> uuid.UUID:
    async with db.tenant_transaction(org) as s:
        found: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO documents (organization_id, sensitivity, access_mode, "
                    "department_ids, status, source_id, external_id, document_kind, title, "
                    "authority, source_updated_at, content_hash, access_hash, body) "
                    "VALUES (:o, 'internal', :m, '{}', 'active', :s, gen_random_uuid()::text, "
                    "'doc', 'Fixture document', 'working', now(), "
                    "sha256(convert_to(gen_random_uuid()::text, 'UTF8')), "
                    "sha256(convert_to(gen_random_uuid()::text, 'UTF8')), 'Fixture body.') "
                    "RETURNING id"
                ),
                {"o": org, "s": source, "m": mode},
            )
        ).scalar_one()
    return found


async def _group(
    db: Database, org: uuid.UUID, source: uuid.UUID | None, members: tuple[uuid.UUID, ...]
) -> uuid.UUID:
    async with db.tenant_transaction(org) as s:
        group: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO access_groups (organization_id, key, origin, source_id, "
                    "external_ref, name) VALUES (:o, :k, :origin, :s, :ref, 'Group') "
                    "RETURNING id"
                ),
                {
                    "o": org,
                    "k": f"group-{uuid.uuid4().hex[:8]}",
                    "origin": "admin" if source is None else "source",
                    "s": source,
                    "ref": None if source is None else f"C{uuid.uuid4().hex[:8]}",
                },
            )
        ).scalar_one()
        for member in members:
            await s.execute(
                text(
                    "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
                    "VALUES (:o, :g, :m)"
                ),
                {"o": org, "g": group, "m": member},
            )
    return group


async def _grant(
    db: Database, org: uuid.UUID, document: uuid.UUID, kind: str, principal: uuid.UUID, origin: str
) -> None:
    column = {"membership": "membership_id", "group": "group_id"}[kind]
    async with db.tenant_transaction(org) as s:
        await s.execute(
            text(
                "INSERT INTO document_grants (organization_id, document_id, principal_type, "  # noqa: S608 - fixed column
                f"{column}, origin) VALUES (:o, :d, :t, :p, :origin)"
            ),
            {"o": org, "d": document, "t": kind, "p": principal, "origin": origin},
        )


async def _tenant(db: Database, slug: str) -> Tenant:
    org = await create_org(db, slug)
    actor = (await create_member(db, org, f"admin@{slug}.example", is_admin=True)).membership_id
    maya = (await create_member(db, org, f"maya@{slug}.example")).membership_id
    omar = (await create_member(db, org, f"omar@{slug}.example")).membership_id
    purged_source = await create_source(db, org, "chat", "chat_export")
    kept_source = await create_source(db, org, "handbook")
    purged_doc = await _document(db, org, purged_source, "grants_only")
    kept_doc = await _document(db, org, kept_source, "grants_only")
    open_doc = await _document(db, org, kept_source, "policy")
    synced_group = await _group(db, org, purged_source, (maya,))
    admin_group = await _group(db, org, None, (omar,))
    # A private channel's message: its channel group, a source grant naming a member, and
    # an administrator's grant -- every kind the cascade removes.
    await _grant(db, org, purged_doc, "group", synced_group, "source")
    await _grant(db, org, purged_doc, "membership", maya, "source")
    await _grant(db, org, purged_doc, "membership", omar, "admin")
    await _grant(db, org, kept_doc, "membership", omar, "admin")
    await _grant(db, org, kept_doc, "group", admin_group, "admin")
    # Disconnected (step 1 of the product's disconnect, audited there): deleting, its
    # documents deleted. The purge (step 2) is what is under test.
    async with db.tenant_transaction(org) as s:
        await s.execute(
            text("UPDATE sources SET status = 'deleting' WHERE id = :s"), {"s": purged_source}
        )
        await s.execute(
            text("UPDATE documents SET status = 'deleted' WHERE source_id = :s"),
            {"s": purged_source},
        )
    return Tenant(
        org=org,
        actor=actor,
        maya=maya,
        omar=omar,
        purged_source=purged_source,
        kept_source=kept_source,
        purged_doc=purged_doc,
        kept_doc=kept_doc,
        open_doc=open_doc,
        synced_group=synced_group,
        admin_group=admin_group,
    )


@pytest.fixture
async def halden(owner_db: Database) -> Tenant:
    return await _tenant(owner_db, "halden")


@pytest.fixture
async def corvane(owner_db: Database) -> Tenant:
    return await _tenant(owner_db, "corvane")


async def _event(s: AsyncSession, org: uuid.UUID, membership: uuid.UUID) -> None:
    await s.execute(
        text(
            "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
            "VALUES (:o, 'member:test', 'access.member_changed', "
            "jsonb_build_object('membership_id', CAST(:m AS text)), 'test')"
        ),
        {"o": org, "m": str(membership)},
    )


async def _purge(s: AsyncSession, t: Tenant, source: uuid.UUID | None = None) -> bool:
    done: bool = (
        await s.execute(
            text("SELECT lattice_purge_source(:a, :s)"),
            {"a": t.actor, "s": source or t.purged_source},
        )
    ).scalar_one()
    return done


async def _state(db: Database, org: uuid.UUID) -> dict[str, Any]:
    """Everything access-relevant in one organization, as the owner sees it."""
    queries = {
        "sources": "SELECT id, status FROM sources",
        "documents": "SELECT id, status FROM documents",
        "grants": "SELECT document_id, principal_type, membership_id, group_id, origin "
        "FROM document_grants",
        "groups": "SELECT id FROM access_groups",
        "group_members": "SELECT group_id, membership_id FROM access_group_members",
        "memberships": "SELECT id, status, clearance, account_scope, is_admin FROM memberships",
        "audit": "SELECT id, action, target FROM audit_events",
    }
    async with db.tenant_snapshot(org) as s:
        return {
            name: {tuple(str(v) for v in row) for row in (await s.execute(text(sql))).all()}
            for name, sql in queries.items()
        }


async def _readable(db: Database, org: uuid.UUID, membership: uuid.UUID) -> set[uuid.UUID]:
    async with db.tenant_snapshot(org) as s:
        ctx = await resolve_authorization_context(s, organization_id=org, membership_id=membership)
        return set(
            (await s.execute(select(DOCUMENTS.c.id).where(readable_documents_predicate(ctx))))
            .scalars()
            .all()
        )


# --- the legitimate purge (A, B, C) --------------------------------------------------------


async def test_a_purge_removes_the_sources_grants_and_groups_with_an_event_per_member(
    app_db: Database, owner_db: Database, halden: Tenant, corvane: Tenant
) -> None:
    theirs = await _state(owner_db, corvane.org)
    async with app_db.tenant_transaction(halden.org) as s:
        assert await _purge(s, halden) is True
        # Maya loses the private channel's group: an access change, audited one by one.
        await _event(s, halden.org, halden.maya)
    after = await _state(owner_db, halden.org)
    assert (str(halden.purged_source), "deleting") not in after["sources"]
    assert {d for d, _ in after["documents"]} == {str(halden.kept_doc), str(halden.open_doc)}
    assert {g[0] for g in after["grants"]} == {str(halden.kept_doc)}
    assert after["groups"] == {(str(halden.admin_group),)}
    assert after["group_members"] == {(str(halden.admin_group), str(halden.omar))}
    [changed] = [a for a in after["audit"] if a[1] == "access.member_changed"]
    assert str(halden.maya) in changed[2]
    assert await _state(owner_db, corvane.org) == theirs


async def test_a_purge_without_each_members_event_commits_nothing(
    app_db: Database, owner_db: Database, halden: Tenant
) -> None:
    before = await _state(owner_db, halden.org)
    for events in ((), (halden.omar,), (halden.actor,)):
        with pytest.raises(DBAPIError, match=REFUSED):
            async with app_db.tenant_transaction(halden.org) as s:
                assert await _purge(s, halden) is True
                for member in events:
                    await _event(s, halden.org, member)
        # Rolled back whole (H): the source is still deleting, nothing of it is gone.
        assert await _state(owner_db, halden.org) == before


# --- resume and re-run (I, J) --------------------------------------------------------------


async def test_an_interrupted_purge_resumes_and_a_completed_one_changes_nothing(
    app_db: Database, owner_db: Database, halden: Tenant, corvane: Tenant
) -> None:
    with pytest.raises(DBAPIError, match=REFUSED):
        async with app_db.tenant_transaction(halden.org) as s:
            await _purge(s, halden)
    async with app_db.tenant_transaction(halden.org) as s:
        assert await _purge(s, halden) is True
        await _event(s, halden.org, halden.maya)
    done = await _state(owner_db, halden.org)
    theirs = await _state(owner_db, corvane.org)
    async with app_db.tenant_transaction(halden.org) as s:
        assert await _purge(s, halden) is False  # already gone: nothing to do
    assert await _state(owner_db, halden.org) == done
    assert await _state(owner_db, corvane.org) == theirs
    # A source that was never disconnected is never purged.
    with pytest.raises(DBAPIError, match="source_not_disconnected"):
        async with app_db.tenant_transaction(halden.org) as s:
            await _purge(s, halden, halden.kept_source)


# --- what stays audit-enforced (D, E, F) ---------------------------------------------------

_KEPT_GRANT = (
    "DELETE FROM document_grants WHERE document_id = :d AND membership_id = :m AND origin = 'admin'"
)
_ADMIN_GROUP_MEMBER = "DELETE FROM access_group_members WHERE group_id = :g AND membership_id = :m"


def _unrelated(t: Tenant) -> list[tuple[str, dict[str, uuid.UUID]]]:
    return [
        (_KEPT_GRANT, {"d": t.kept_doc, "m": t.omar}),
        (_ADMIN_GROUP_MEMBER, {"g": t.admin_group, "m": t.omar}),
    ]


@pytest.mark.parametrize("which", [0, 1], ids=["grant", "group member"])
async def test_a_direct_access_delete_still_needs_its_event(
    app_db: Database, owner_db: Database, halden: Tenant, which: int
) -> None:
    sql, params = _unrelated(halden)[which]
    with pytest.raises(DBAPIError, match=REFUSED):
        async with app_db.tenant_transaction(halden.org) as s:
            assert (await s.execute(text(sql), params)).rowcount == 1  # type: ignore[attr-defined]
    async with app_db.tenant_transaction(halden.org) as s:
        await s.execute(text(sql), params)
        await _event(s, halden.org, halden.omar)


@pytest.mark.parametrize("which", [0, 1], ids=["grant", "group member"])
async def test_an_unrelated_delete_gains_no_exemption_from_a_purge_beside_it(
    app_db: Database, owner_db: Database, halden: Tenant, which: int
) -> None:
    before = await _state(owner_db, halden.org)
    sql, params = _unrelated(halden)[which]
    for first in (True, False):  # before or after the purge, in its transaction
        with pytest.raises(DBAPIError, match=REFUSED):
            async with app_db.tenant_transaction(halden.org) as s:
                if first:
                    await s.execute(text(sql), params)
                assert await _purge(s, halden) is True
                await _event(s, halden.org, halden.maya)
                if not first:
                    await s.execute(text(sql), params)
        assert await _state(owner_db, halden.org) == before
    # With Omar's own event the same transaction commits: the refusal was the missing event.
    async with app_db.tenant_transaction(halden.org) as s:
        await s.execute(text(sql), params)
        assert await _purge(s, halden) is True
        await _event(s, halden.org, halden.maya)
        await _event(s, halden.org, halden.omar)


async def test_a_grant_of_a_document_the_purge_deletes_goes_with_it(
    app_db: Database, owner_db: Database, halden: Tenant
) -> None:
    # Inside the purged source's graph the cascade would remove it anyway.
    async with app_db.tenant_transaction(halden.org) as s:
        await s.execute(text(_KEPT_GRANT), {"d": halden.purged_doc, "m": halden.omar})
        assert await _purge(s, halden) is True
        await _event(s, halden.org, halden.maya)
    assert {g[0] for g in (await _state(owner_db, halden.org))["grants"]} == {str(halden.kept_doc)}


# --- organizations (G) ---------------------------------------------------------------------


async def test_another_organizations_source_and_access_are_out_of_reach(
    app_db: Database, owner_db: Database, halden: Tenant, corvane: Tenant
) -> None:
    theirs = await _state(owner_db, corvane.org)
    async with app_db.tenant_transaction(halden.org) as s:
        assert await _purge(s, halden, corvane.purged_source) is False
        for sql, params in _unrelated(corvane):
            assert (await s.execute(text(sql), params)).rowcount == 0  # type: ignore[attr-defined]
    assert await _state(owner_db, corvane.org) == theirs


@pytest.mark.parametrize("which", [0, 1], ids=["grant", "group member"])
@pytest.mark.parametrize("rebind", ["other", "none"])
async def test_rebinding_the_organization_before_commit_exempts_nothing(
    app_db: Database, owner_db: Database, halden: Tenant, corvane: Tenant, which: int, rebind: str
) -> None:
    """0024: the deferred trigger reads documents and groups through row-level security,
    so a session that rebinds ``app.org_id`` before commit hides the still-existing parent.
    The exemption applies only to a row of the organization the session is bound to."""
    before = await _state(owner_db, halden.org)
    sql, params = _unrelated(halden)[which]
    with pytest.raises(DBAPIError, match=REFUSED):
        async with app_db.tenant_transaction(halden.org) as s:
            assert (await s.execute(text(sql), params)).rowcount == 1  # type: ignore[attr-defined]
            await s.execute(
                text("SELECT set_config('app.org_id', :o, true)"),
                {"o": str(corvane.org) if rebind == "other" else ""},
            )
    assert await _state(owner_db, halden.org) == before


# --- readability and the audit trail (K, L) ------------------------------------------------


async def test_a_purge_never_widens_what_anyone_can_read(
    app_db: Database, owner_db: Database, halden: Tenant
) -> None:
    people = (halden.actor, halden.maya, halden.omar)
    before = {m: await _readable(app_db, halden.org, m) for m in people}
    async with app_db.tenant_transaction(halden.org) as s:
        assert await _purge(s, halden) is True
        await _event(s, halden.org, halden.maya)
    after = {m: await _readable(app_db, halden.org, m) for m in people}
    for member in people:
        assert after[member] <= before[member]
    # Administrative capability reads nothing extra; the survivors keep exactly their paths.
    assert after[halden.actor] == {halden.open_doc}
    assert after[halden.maya] == {halden.open_doc}
    assert after[halden.omar] == {halden.open_doc, halden.kept_doc}


async def test_the_audit_trail_survives_a_purge_and_stays_append_only(
    app_db: Database, owner_db: Database, halden: Tenant
) -> None:
    async with app_db.tenant_transaction(halden.org) as s:
        await _event(s, halden.org, halden.maya)  # an earlier, unrelated access event
    before = (await _state(owner_db, halden.org))["audit"]
    async with app_db.tenant_transaction(halden.org) as s:
        assert await _purge(s, halden) is True
        await _event(s, halden.org, halden.maya)
    after = (await _state(owner_db, halden.org))["audit"]
    assert before < after
    assert len(after - before) == 1
    # Append-only (0008): nobody rewrites an event; the API role cannot delete one either.
    attempts = (
        (app_db, "UPDATE audit_events SET reason = 'x'"),
        (app_db, "DELETE FROM audit_events"),
        (owner_db, "UPDATE audit_events SET reason = 'x'"),
    )
    for db, sql in attempts:
        with pytest.raises(DBAPIError):
            async with db.tenant_transaction(halden.org) as s:
                await s.execute(text(sql))
    assert (await _state(owner_db, halden.org))["audit"] == after
