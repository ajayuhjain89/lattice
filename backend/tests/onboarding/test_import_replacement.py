"""Replacing a source's export after it synced, and the import store's lifecycle around
it (P12.7; phase-12-plan C8.2, Part III P12.6 deviations 2 and 3).

The approved behaviour: an accepted upload becomes the source's current import at once
and is checked; a refused one changes nothing; a sync reads exactly the import that was
current when it was requested; superseded imports are deleted once no queued or running
job reads them. Proved here on a source that already synced, through the API, the worker
and the authorization predicate:

* a malformed, truncated or unsafe replacement is refused and the current import, the
  indexed evidence and the next sync are untouched;
* a structurally valid replacement whose content cannot be read as a whole becomes
  current, is checked, and blocks syncing (``import_invalid``) -- indexed evidence stays
  readable exactly as before; the previous import is gone once superseded, so the
  administrator recovers by uploading a good export again (a documented limitation);
* a replacement while a sync is queued or running never changes what that sync reads,
  and the import retention never deletes the import an active job holds;
* retention is organization-safe, and a disconnect removes only that source's imports.
"""

import io
import uuid
import zipfile
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.imports.store import ImportUnavailable
from lattice.jobs.worker import Worker

from ..conftest import CSRF
from ..imports.archives import build, entry, files, zip_tree
from .conftest import (
    CORVANE,
    DATA,
    REASON,
    WRENFIELD,
    World,
    add_member,
    bootstrap,
    create_source,
    detail,
    export,
    readable,
    upload,
)


async def _ready(world: World, owner_db: Database) -> tuple[httpx.AsyncClient, Any, uuid.UUID]:
    """Wrenfield bootstrapped, its administrator signed in, Riley added."""
    created = await bootstrap(owner_db, WRENFIELD)
    world.login_keys["wrenfield"] = created.login_key
    admin = world.browser()
    assert await world.sign_in(admin, "wrenfield", WRENFIELD.admin_subject) == "/ask"
    riley = await add_member(admin, "Riley Moss", "riley.moss@wrenfield.example", "wrn-riley")
    return admin, created, uuid.UUID(riley["member"]["id"])


async def _synced(world: World, admin: httpx.AsyncClient, key: str) -> dict[str, Any]:
    """A source of ``key`` whose fixture export was uploaded, checked and synced."""
    source = await create_source(admin, key)
    body, kind = export(key)
    assert (await upload(admin, source["id"], body, kind)).status_code == 201
    assert (await world.work()).outcome == "succeeded"  # the check
    assert (
        await admin.post(f"/v1/admin/sources/{source['id']}/sync", headers=CSRF)
    ).status_code == 202
    assert (await world.work()).outcome == "succeeded"  # the sync
    return await detail(admin, source["id"])


async def _import_status(owner_db: Database, org: uuid.UUID, import_id: str) -> str:
    async with owner_db.tenant_snapshot(org) as s:
        found: str = (
            await s.execute(
                text("SELECT status FROM source_imports WHERE id = :i"), {"i": import_id}
            )
        ).scalar_one()
    return found


def _on_disk(world: World, org: uuid.UUID, import_id: str) -> bool:
    try:
        world.store.tree(org, uuid.UUID(import_id))
    except ImportUnavailable:
        return False
    return True


async def _run_import(owner_db: Database, org: uuid.UUID, run_id: uuid.UUID) -> str:
    async with owner_db.tenant_snapshot(org) as s:
        return str(
            (
                await s.execute(
                    text("SELECT import_id FROM source_sync_runs WHERE id = :r"), {"r": run_id}
                )
            ).scalar_one()
        )


def _slack_with_broken_users() -> bytes:
    """A Slack export with every file in place but ``users.json`` unreadable: valid as an
    archive, invalid as an export (the check cannot read it as a whole)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((DATA / "slack").rglob("*")):
            if path.is_file():
                name = path.relative_to(DATA / "slack").as_posix()
                data = b"{not json" if name == "users.json" else path.read_bytes()
                archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.usefixtures("onboarding_keys")
async def test_a_refused_replacement_changes_nothing(
    owner_db: Database, app_db: Database, world_factory: Any
) -> None:
    async with world_factory() as world:
        admin, created, riley = await _ready(world, owner_db)
        org = created.organization_id
        slack = await _synced(world, admin, "slack")
        current = slack["current_import"]["id"]
        before = await readable(app_db, org, riley)
        assert any(ext.startswith("G0WASHWAR1:") for _, ext in before)

        truncated = zip_tree(DATA / "slack")[:-200]
        unsafe = files({"../escape.json": b"[]", "users.json": b"[]", "channels.json": b"[]"})
        symlink_entry = entry("channels.json", b"/etc/passwd", mode=0o120777)
        cases = {
            "archive_invalid": b"plain text, not an archive",
            "archive_invalid ": truncated,  # a zip cut short (no central directory)
            "unsafe_path": unsafe,
            "link_refused": build(entry("users.json", b"[]"), symlink_entry),
            "unsupported_layout": files({"notes.txt": b"hello"}),
        }
        for code, body in cases.items():
            refused = await upload(admin, slack["id"], body)
            assert refused.status_code == 422, (code, refused.status_code)
            assert refused.json()["error"]["details"][0]["issue"] == code.strip()
        # Declared longer than sent: the body is counted, never trusted.
        short = await admin.post(
            f"/v1/admin/sources/{slack['id']}/imports",
            content=zip_tree(DATA / "slack"),
            headers={**CSRF, "Content-Type": "application/zip", "Content-Length": "999999"},
        )
        assert short.status_code in (400, 422), short.status_code

        record = await detail(admin, slack["id"])
        assert record["current_import"]["id"] == current
        # The refusal is on the record (its files already removed), with the code that
        # says what to fix; the source stays syncable from its current import.
        assert record["latest_upload"]["status"] == "deleted"
        assert record["latest_upload"]["rejected_code"] == "upload_incomplete"
        assert record["can_sync"] is True, record["sync_blocked_by"]
        assert record["next_action"] == "none"
        assert await readable(app_db, org, riley) == before
        assert not list((world.store.root / str(org)).glob("*/archive.partial"))
        # The current import still syncs, unchanged.
        assert (
            await admin.post(f"/v1/admin/sources/{slack['id']}/sync", headers=CSRF)
        ).status_code == 202
        outcome = await world.work()
        assert (outcome.outcome, outcome.sync_status, outcome.error_code) == (
            "succeeded",
            "succeeded",
            None,
        )
        assert await readable(app_db, org, riley) == before
        assert not (world.store.root / "escape.json").exists()


@pytest.mark.usefixtures("onboarding_keys")
async def test_an_unreadable_replacement_blocks_syncs_and_keeps_the_evidence(
    owner_db: Database, app_db: Database, world_factory: Any
) -> None:
    async with world_factory() as world:
        admin, created, riley = await _ready(world, owner_db)
        org = created.organization_id
        slack = await _synced(world, admin, "slack")
        previous = slack["current_import"]["id"]
        before = await readable(app_db, org, riley)

        accepted = await upload(admin, slack["id"], _slack_with_broken_users())
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["import"]["current"] is True
        assert (await world.work()).outcome == "succeeded"  # the check ran and recorded
        record = await detail(admin, slack["id"])
        assert record["current_import"]["check"]["error_code"] is not None
        assert record["can_sync"] is False
        refused = await admin.post(f"/v1/admin/sources/{slack['id']}/sync", headers=CSRF)
        assert refused.status_code == 422
        assert refused.json()["error"]["details"][0]["issue"] == "import_invalid"
        # Nothing indexed changed: a check writes nothing and no sync ran.
        assert await readable(app_db, org, riley) == before

        # The superseded import is not kept once no job reads it: recovery is a new upload.
        assert await _import_status(owner_db, org, previous) == "superseded"
        await Worker(world.worker_db, embedding=None, store=world.store).import_retention()
        assert await _import_status(owner_db, org, previous) == "deleted"
        assert not _on_disk(world, org, previous)
        body, _ = export("slack")
        assert (await upload(admin, slack["id"], body)).status_code == 201
        assert (await world.work()).outcome == "succeeded"
        assert (await detail(admin, slack["id"]))["can_sync"] is True
        assert (
            await admin.post(f"/v1/admin/sources/{slack['id']}/sync", headers=CSRF)
        ).status_code == 202
        assert (await world.work()).outcome == "succeeded"
        assert await readable(app_db, org, riley) == before


@pytest.mark.usefixtures("onboarding_keys")
@pytest.mark.parametrize("phase", ["queued", "running"])
async def test_a_replacement_never_changes_what_an_active_sync_reads(
    owner_db: Database, app_db: Database, world_factory: Any, phase: str
) -> None:
    async with world_factory() as world:
        admin, created, riley = await _ready(world, owner_db)
        org = created.organization_id
        handbook = await _synced(world, admin, "handbook")
        first = handbook["current_import"]["id"]

        requested = await admin.post(f"/v1/admin/sources/{handbook['id']}/sync", headers=CSRF)
        assert requested.status_code == 202
        assert requested.json()["import_id"] == first
        worker = Worker(world.worker_db, embedding=None, store=world.store)
        claimed = await worker.claim() if phase == "running" else None

        # A replacement with one more page: accepted and current, but not checked now
        # (one job per source at a time) and not read by the sync already requested.
        tree = {
            p.relative_to(DATA / "documents").as_posix(): p.read_bytes()
            for p in (DATA / "documents").rglob("*.md")
        }
        tree["handbook/new-page.md"] = (
            b"---\nid: wrn-new-page\ntitle: New page\nauthor: Ada Quist\n"
            b"created: 2026-10-10T09:00:00Z\nupdated: 2026-10-10T09:00:00Z\n---\n## New\n\nText.\n"
        )
        replaced = await upload(admin, handbook["id"], files(tree))
        assert replaced.status_code == 201, replaced.text
        second = replaced.json()["import"]["id"]
        assert replaced.json()["check_queued"] is False
        assert await _import_status(owner_db, org, first) == "superseded"

        # Retention spares the import the active job holds.
        await worker.import_retention()
        assert await _import_status(owner_db, org, first) == "superseded"
        assert _on_disk(world, org, first)

        outcome = await (worker.execute(claimed) if claimed else world.work())
        assert outcome.outcome == "succeeded", outcome
        job = (await admin.get(f"/v1/admin/sources/{handbook['id']}")).json()["job"]
        assert job["import_id"] == first
        assert await _run_import(owner_db, org, uuid.UUID(job["sync_run_id"])) == first
        reads = await readable(app_db, org, riley)
        assert ("handbook", "wrn-new-page") not in reads  # the old snapshot was synced

        # Once the job finished, the superseded import goes; the new one is next.
        await worker.import_retention()
        assert await _import_status(owner_db, org, first) == "deleted"
        assert not _on_disk(world, org, first)
        assert (await detail(admin, handbook["id"]))["current_import"]["id"] == second
        assert (
            await admin.post(f"/v1/admin/sources/{handbook['id']}/check", headers=CSRF)
        ).status_code == 202
        assert (await world.work()).outcome == "succeeded"
        synced = await admin.post(f"/v1/admin/sources/{handbook['id']}/sync", headers=CSRF)
        assert synced.json()["import_id"] == second
        assert (await world.work()).outcome == "succeeded"
        assert ("handbook", "wrn-new-page") in await readable(app_db, org, riley)


@pytest.mark.usefixtures("onboarding_keys")
async def test_retention_and_disconnect_are_confined_to_their_organization_and_source(
    owner_db: Database, world_factory: Any
) -> None:
    async with world_factory() as world:
        admin, created, riley = await _ready(world, owner_db)
        org = created.organization_id
        admin_id = created.admin_membership_id
        handbook = await _synced(world, admin, "handbook")
        slack = await _synced(world, admin, "slack")

        corvane = await bootstrap(owner_db, CORVANE)
        world.login_keys["corvane"] = corvane.login_key
        noor = world.browser()
        assert await world.sign_in(noor, "corvane", CORVANE.admin_subject) == "/ask"
        theirs = await _synced(world, noor, "handbook")
        their_import = theirs["current_import"]["id"]

        # Abandoned upload directories of one organization and stray files never touch
        # the other's live imports.
        worker = Worker(world.worker_db, embedding=None, store=world.store)
        await worker.import_retention()
        for o, i in (
            (org, handbook["current_import"]["id"]),
            (org, slack["current_import"]["id"]),
            (corvane.organization_id, their_import),
        ):
            assert _on_disk(world, o, i)

        preview = (await admin.get(f"/v1/admin/sources/{slack['id']}/disconnect-preview")).json()
        assert preview["imports"] >= 1
        gone = await admin.post(
            f"/v1/admin/sources/{slack['id']}/disconnect",
            json={"confirm_key": "slack", "version": slack["version"], "reason": REASON},
            headers=CSRF,
        )
        assert gone.status_code == 200, gone.text
        # Purged, not left half-way: a private channel's synced group had a member (Riley),
        # and losing it is an audited access change (P12.7 fix).
        assert gone.json()["purged"] is True
        async with owner_db.tenant_snapshot(org) as s:
            left: int = (
                await s.execute(
                    text("SELECT count(*) FROM sources WHERE id = :s"), {"s": slack["id"]}
                )
            ).scalar_one()
            changed = (
                await s.execute(
                    text(
                        "SELECT actor, target ->> 'membership_id', reason FROM audit_events "
                        "WHERE action = 'access.member_changed' "
                        "AND reason LIKE 'Source disconnected%'"
                    )
                )
            ).all()
        assert left == 0
        assert [(a, m) for a, m, _ in changed] == [(f"member:{admin_id}", str(riley))]
        await worker.import_retention()
        assert not _on_disk(world, org, slack["current_import"]["id"])
        assert _on_disk(world, org, handbook["current_import"]["id"])
        assert _on_disk(world, corvane.organization_id, their_import)
        assert (await admin.get(f"/v1/admin/sources/{slack['id']}")).status_code == 404
