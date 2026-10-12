"""Mappings drive ingestion deterministically (Phase 12, P12.3; phase-12-plan C10), over
the synthetic Halden export, with Veridian present.

Proves: the sources file's mappings land in ``account_external_refs`` (audited
``operator:sync``) and are not rewritten by an unchanged re-run; a reference the latest
sync could not map is listed with its count and nothing from the records; mapping it
indexes the waiting records at the next sync -- through the product's own *Sync now* and
the worker role -- and re-syncing changes nothing; a remap moves the records at the next
sync; an unmap quarantines them (account-scoped) without deleting or reassigning; an
archived account's mappings keep resolving; disconnecting a source removes only its own
mappings and reconnecting creates no duplicates; mapping never widens what anyone may
read beyond the account rule (private channels stay private, the administrator reads
nothing new); names and aliases never enter search text or embeddings (D-10); channel
names never appear in the mapping workflow (D-18); the setup record follows."""

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.ingestion.lifecycle import disconnect_source
from lattice.ingestion.sync import SyncError
from lattice.jobs.worker import Worker
from lattice.providers.embedding import FakeEmbeddingProvider

from ..conftest import CSRF
from ..factories import login
from ..ingestion.conftest import Org, changed_tables, row_versions, sync
from ..ingestion.conftest import halden as halden
from .conftest import events, readable

FAKE = FakeEmbeddingProvider()
FERNHILL = "360009913"


async def _q(db: Database, org: uuid.UUID, sql: str, **params: Any) -> list[Any]:
    async with db.tenant_transaction(org) as s:
        return list((await s.execute(text(sql), params)).all())


async def _id(db: Database, org: Org, sql: str, **params: Any) -> uuid.UUID:
    [(value,)] = await _q(db, org.id, sql, **params)
    assert isinstance(value, uuid.UUID)
    return value


async def _member(db: Database, org: Org, email: str) -> uuid.UUID:
    return await _id(
        db,
        org,
        "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id WHERE u.email = :e",
        e=email,
    )


async def _account(db: Database, org: Org, key: str) -> uuid.UUID:
    return await _id(db, org, "SELECT id FROM accounts WHERE key = :k", k=key)


async def _source(db: Database, org: Org, key: str) -> uuid.UUID:
    return await _id(db, org, "SELECT id FROM sources WHERE key = :k", k=key)


async def _accounts_of(db: Database, org: Org, source: str) -> dict[str, tuple[str | None, str]]:
    """external_id -> (account key, status) of the source's documents."""
    rows = await _q(
        db,
        org.id,
        "SELECT d.external_id, a.key, d.status FROM documents d JOIN sources s ON s.id = "
        "d.source_id LEFT JOIN accounts a ON a.id = d.account_id WHERE s.key = :k",
        k=source,
    )
    return {r[0]: (None if r[1] is None else str(r[1]), r[2]) for r in rows}


async def _mappings(db: Database, org: Org, source: str | None = None) -> set[tuple[str, str]]:
    rows = await _q(
        db,
        org.id,
        "SELECT r.external_ref, a.key FROM account_external_refs r JOIN accounts a "
        "ON a.id = r.account_id JOIN sources s ON s.id = r.source_id "
        "WHERE (CAST(:k AS text) IS NULL OR s.key = :k)",
        k=source,
    )
    return {(r[0], str(r[1])) for r in rows}


@pytest.fixture
async def admin(app_client: Any, halden: Org) -> Any:
    async with app_client() as c:
        assert (await login(c, "lena.brandt@halden.example")).status_code == 200
        yield c


async def _setup_accounts(client: httpx.AsyncClient) -> dict[str, Any]:
    categories = (await client.get("/v1/admin/setup")).json()["categories"]
    found: dict[str, Any] = next(c for c in categories if c["key"] == "accounts")
    return found


async def _sync_now(client: httpx.AsyncClient, worker_db: Database, source_id: uuid.UUID) -> None:
    """The product's path: an administrator requests a sync; the worker runs it."""
    response = await client.post(f"/v1/admin/sources/{source_id}/sync", headers=CSRF)
    assert response.status_code == 202, response.text
    outcome = await Worker(worker_db, embedding=FAKE).run_once()
    assert outcome is not None
    assert outcome.outcome == "succeeded", outcome


async def _map(client: httpx.AsyncClient, path: str, **body: Any) -> httpx.Response:
    reason = {} if path == "/preview" else {"reason": "Customer record"}
    return await client.post(
        f"/v1/admin/access/account-references{path}", json={**reason, **body}, headers=CSRF
    )


def _ticket(source: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {
        "source_id": str(source),
        "ref_kind": "ticketing_organization",
        "external_ref": FERNHILL,
        **extra,
    }


async def test_the_sources_file_writes_its_mappings_once_and_audited(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "support", FAKE)
    await sync(owner_db, halden, "slack", FAKE)
    support = halden.sources.get("support")
    assert support is not None
    assert await _mappings(owner_db, halden, "support") == {
        (str(k), v) for k, v in support.account_refs.items()
    }
    assert await _mappings(owner_db, halden, "slack") == {
        ("G0ACCTACME1", "acme-corp"),
        ("C0EXTACME01", "acme-corp"),
        ("C0ACCTKES01", "kestrel-bank"),
        ("C0ACCTNPK01", "northpeak-logistics"),
        ("C0ACCTBRW01", "brightwater-health"),
    }
    written = await events(owner_db, halden.id)
    assert len(written) == 13
    assert {(e.actor, e.action, e.before) for e in written} == {
        ("operator:sync", "account.mapping_changed", None)
    }
    # The configuration no longer carries mappings; channel sensitivity stays there.
    [(config,)] = await _q(owner_db, halden.id, "SELECT config FROM sources WHERE key = 'slack'")
    assert "account_refs" not in config
    assert config["channels"] == {"G0EXECESC01": {"sensitivity": "confidential"}}
    # An unchanged re-run writes no mapping and no event.
    before = await row_versions(owner_db, halden.id)
    await sync(owner_db, halden, "support", FAKE)
    assert "documents" not in changed_tables(before, await row_versions(owner_db, halden.id))
    assert len(await events(owner_db, halden.id)) == 13


async def test_an_unmapped_reference_is_mapped_and_indexed_at_the_next_sync(
    owner_db: Database, app_db: Database, worker_db: Database, halden: Org, admin: Any
) -> None:
    first = await sync(owner_db, halden, "support", FAKE)
    assert first.failure_codes == {"access_unmappable:account": 1}
    support = await _source(owner_db, halden, "support")
    lena = await _member(owner_db, halden, "lena.brandt@halden.example")
    lena_before = await readable(app_db, halden.id, lena)

    listed = (await admin.get("/v1/admin/access/account-references/unmapped")).json()["items"]
    assert len(listed) == 1
    item = listed[0]
    assert (item["external_ref"], item["ref_kind"], item["records_waiting"], item["state"]) == (
        FERNHILL,
        "ticketing_organization",
        1,
        "unmapped",
    )
    assert item["source"]["name"] == "Halden Support (Zendesk)"
    assert set(item) == {
        "source",
        "ref_kind",
        "external_ref",
        "records_waiting",
        "seen_at",
        "state",
        "account",
    }
    setup = await _setup_accounts(admin)
    assert (setup["state"], setup["reason"], setup["facts"]["unmapped_references"]) == (
        "attention",
        "unmapped_references",
        1,
    )
    assert setup["action"]["href"] == "/access/accounts/references"

    # Create the account explicitly, then map: nothing is indexed until the next sync.
    created = await admin.post(
        "/v1/admin/access/accounts",
        json={"key": "fernhill-studios", "name": "Fernhill Studios", "reason": "New customer"},
        headers=CSRF,
    )
    assert created.status_code == 201
    fernhill = created.json()["id"]
    preview = (await _map(admin, "/preview", **_ticket(support, account_id=fernhill))).json()
    assert (preview["operation"], preview["effect"], preview["records_waiting"]) == (
        "map",
        "indexed",
        1,
    )
    assert (await _map(admin, "", **_ticket(support, account_id=fernhill))).status_code == 201
    documents_before = await _accounts_of(owner_db, halden, "support")
    assert "fernhill-studios" not in {a for a, _ in documents_before.values()}
    pending = (await admin.get("/v1/admin/access/account-references/unmapped")).json()["items"]
    assert [(i["state"], i["account"]["key"]) for i in pending] == [
        ("mapped_pending_sync", "fernhill-studios")
    ]
    setup = await _setup_accounts(admin)
    assert (setup["state"], setup["reason"]) == ("attention", "sync_required")

    await _sync_now(admin, worker_db, support)
    after = await _accounts_of(owner_db, halden, "support")
    fernhill_docs = {k for k, (a, _) in after.items() if a == "fernhill-studios"}
    assert len(fernhill_docs) == 1
    assert {k: v for k, v in after.items() if k not in fernhill_docs} == documents_before
    assert (await admin.get("/v1/admin/access/account-references/unmapped")).json()["items"] == []
    setup = await _setup_accounts(admin)
    assert (setup["state"], setup["reason"]) == ("complete", "mapped")
    # Idempotent: the same mapping and export change nothing.
    before = await row_versions(owner_db, halden.id)
    again = await sync(owner_db, halden, "support", FAKE, record_config=False)
    assert (again.created, again.updated, again.access_updated, again.failed) == (0, 0, 0, 0)
    assert changed_tables(before, await row_versions(owner_db, halden.id)) <= {"sources"}

    # Remap: the record moves at the next sync -- not before.
    acme = str(await _account(owner_db, halden, "acme-corp"))
    remap = await _map(
        admin, "/remap", **_ticket(support, current_account_id=fernhill, account_id=acme)
    )
    assert remap.status_code == 200
    assert await _accounts_of(owner_db, halden, "support") == after
    moved = await sync(owner_db, halden, "support", FAKE, record_config=False)
    assert (moved.access_updated, moved.created, moved.deleted) == (1, 0, 0)
    [ticket] = fernhill_docs
    assert (await _accounts_of(owner_db, halden, "support"))[ticket] == ("acme-corp", "active")

    # Unmap: quarantined at the next sync (account-scoped), never deleted or reassigned.
    unmap = await _map(admin, "/unmap", **_ticket(support, current_account_id=acme))
    assert unmap.status_code == 200
    out = await sync(owner_db, halden, "support", FAKE, record_config=False)
    assert (out.quarantined, out.deleted) == (1, 0)
    assert (await _accounts_of(owner_db, halden, "support"))[ticket] == ("acme-corp", "quarantined")
    listed = (await admin.get("/v1/admin/access/account-references/unmapped")).json()["items"]
    assert [(i["external_ref"], i["state"]) for i in listed] == [(FERNHILL, "unmapped")]
    # Mapped again: restored at the next sync.
    assert (await _map(admin, "", **_ticket(support, account_id=fernhill))).status_code == 201
    back = await sync(owner_db, halden, "support", FAKE, record_config=False)
    assert back.failed == 0
    assert (await _accounts_of(owner_db, halden, "support"))[ticket] == (
        "fernhill-studios",
        "active",
    )
    # Mapping, remapping and unmapping gave the administrator nothing to read.
    assert await readable(app_db, halden.id, lena) == lena_before


async def test_an_archived_accounts_mappings_keep_resolving(
    owner_db: Database, halden: Org, admin: Any
) -> None:
    await sync(owner_db, halden, "support", FAKE)
    await sync(owner_db, halden, "slack", FAKE)
    acme = await _account(owner_db, halden, "acme-corp")
    detail = (await admin.get(f"/v1/admin/access/accounts/{acme}")).json()
    assert detail["references"] == 3  # one ticketing organization, two channels
    before = {
        source: await _accounts_of(owner_db, halden, source) for source in ("support", "slack")
    }
    archived = await admin.post(
        f"/v1/admin/access/accounts/{acme}/archive",
        json={"version": detail["version"], "reason": "Churned"},
        headers=CSRF,
    )
    assert archived.status_code == 200
    for source in ("support", "slack"):
        result = await sync(owner_db, halden, source, FAKE)
        assert (result.access_updated, result.quarantined, result.failed) == (
            0,
            0,
            0 if source == "slack" else 1,
        )
        assert await _accounts_of(owner_db, halden, source) == before[source]
    # A sources file naming a NEW reference for the archived account is refused whole.
    path = halden.root / "sources.yaml"
    path.write_text(
        path.read_text().replace("      # 360009913", "      360009913: acme-corp\n      #")
    )
    halden.reload()
    with pytest.raises(SyncError) as caught:
        await sync(owner_db, halden, "support", FAKE)
    assert caught.value.code == "source_config:account_archived"
    assert (FERNHILL, "acme-corp") not in await _mappings(owner_db, halden)


async def test_remapping_never_opens_a_private_channel(
    owner_db: Database, app_db: Database, halden: Org, admin: Any
) -> None:
    """Account association is one rule among the document's own: a private channel's
    records stay readable only by its members whatever account it maps to."""
    await sync(owner_db, halden, "slack", FAKE)
    slack = await _source(owner_db, halden, "slack")
    maya = await _member(owner_db, halden, "maya.okafor@halden.example")  # assigned Acme
    lena = await _member(owner_db, halden, "lena.brandt@halden.example")  # administrator
    maya_before = await readable(app_db, halden.id, maya)
    lena_before = await readable(app_db, halden.id, lena)
    acme = str(await _account(owner_db, halden, "acme-corp"))
    # #exec-escalations: private, confidential; Maya is not a member.
    mapped = await _map(
        admin,
        "",
        source_id=str(slack),
        ref_kind="chat_channel",
        external_ref="G0EXECESC01",
        account_id=acme,
    )
    assert mapped.status_code == 201
    result = await sync(owner_db, halden, "slack", FAKE, record_config=False)
    assert result.access_updated > 0
    escalations = {
        k
        for k, (a, _) in (await _accounts_of(owner_db, halden, "slack")).items()
        if a == "acme-corp" and k.startswith("G0EXECESC01")
    }
    assert escalations
    assert await readable(app_db, halden.id, maya) == maya_before
    assert await readable(app_db, halden.id, lena) == lena_before


async def test_disconnect_removes_only_the_sources_own_mappings(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "support", FAKE)
    await sync(owner_db, halden, "slack", FAKE)
    veridian = await _q(
        owner_db, (await _veridian(owner_db)), "SELECT count(*) FROM account_external_refs"
    )
    support = await _mappings(owner_db, halden, "support")
    report = await disconnect_source(owner_db, halden.id, "slack")
    assert report.account_mappings == 5
    assert await _mappings(owner_db, halden, "slack") == set()
    assert await _mappings(owner_db, halden, "support") == support
    accounts = await _q(owner_db, halden.id, "SELECT count(*) FROM accounts")
    assert accounts[0][0] == 8
    after = await _q(
        owner_db, (await _veridian(owner_db)), "SELECT count(*) FROM account_external_refs"
    )
    assert after == veridian
    # Reconnecting from the file recreates them once.
    await sync(owner_db, halden, "slack", FAKE)
    await sync(owner_db, halden, "slack", FAKE)
    rows = await _q(
        owner_db,
        halden.id,
        "SELECT count(*) FROM account_external_refs r JOIN sources s ON s.id = r.source_id "
        "WHERE s.key = 'slack'",
    )
    assert rows[0][0] == 5


async def _veridian(owner_db: Database) -> uuid.UUID:
    async with owner_db.unscoped_transaction() as s:
        value: uuid.UUID = (
            await s.execute(text("SELECT id FROM organizations WHERE slug = 'veridian'"))
        ).scalar_one()
    return value


async def test_a_dry_run_writes_no_mapping(owner_db: Database, halden: Org) -> None:
    await sync(owner_db, halden, "support", FAKE, dry_run=True)
    assert await _mappings(owner_db, halden) == set()
    assert await events(owner_db, halden.id) == []


async def test_names_and_aliases_never_enter_search_text_or_embeddings(
    owner_db: Database, halden: Org, admin: Any
) -> None:
    """D-10: editing an account's name or aliases touches no document or chunk; the next
    sync re-embeds and rewrites nothing."""
    await sync(owner_db, halden, "support", FAKE)
    acme = await _account(owner_db, halden, "acme-corp")
    detail = (await admin.get(f"/v1/admin/access/accounts/{acme}")).json()
    before = await row_versions(owner_db, halden.id)
    changed = await admin.patch(
        f"/v1/admin/access/accounts/{acme}",
        json={
            "version": detail["version"],
            "name": "Zephyrine Holdings",
            "aliases": ["Zephyrine"],
            "reason": "Rebrand",
        },
        headers=CSRF,
    )
    assert changed.status_code == 200
    assert changed_tables(before, await row_versions(owner_db, halden.id)) == set()
    result = await sync(owner_db, halden, "support", FAKE)
    assert (result.updated, result.access_updated, result.chunks_embedded) == (0, 0, 0)
    found = await _q(
        owner_db,
        halden.id,
        "SELECT count(*) FROM chunks WHERE text ILIKE '%zephyrine%' "
        "OR tsv @@ plainto_tsquery('simple', 'zephyrine')",
    )
    assert found[0][0] == 0


async def test_channel_names_never_appear_in_the_mapping_workflow(
    owner_db: Database, halden: Org, admin: Any
) -> None:
    """D-18: an account-scoped Slack source lists its unmapped channels by ID only --
    private and public alike; mapping and the account record show IDs, never names."""
    path = halden.root / "sources.yaml"
    path.write_text(
        path.read_text().replace(
            "    authority: conversational\n",
            "    authority: conversational\n    account_scoped: true\n",
        )
    )
    halden.reload()
    await sync(owner_db, halden, "slack", FAKE)
    names: set[str] = set()
    for export in ("channels.json", "groups.json"):
        names |= {c["name"] for c in json.loads((halden.path("slack") / export).read_text())}
    listed = await admin.get("/v1/admin/access/account-references/unmapped")
    refs = {i["external_ref"] for i in listed.json()["items"]}
    assert {"G0PRODIDEN1", "G0EXECESC01", "C0GENERAL01"} <= refs
    slack = await _source(owner_db, halden, "slack")
    acme = str(await _account(owner_db, halden, "acme-corp"))
    preview = await _map(
        admin,
        "/preview",
        source_id=str(slack),
        ref_kind="chat_channel",
        external_ref="G0PRODIDEN1",
        account_id=acme,
    )
    detail = await admin.get(f"/v1/admin/access/accounts/{acme}")
    audit = await admin.get("/v1/admin/audit-events")
    for response in (listed, preview, detail, audit):
        body = response.text
        for name in names:
            assert f'"{name}"' not in body, (name, response.url)
            assert f"#{name}" not in body, (name, response.url)


async def test_the_migrated_configuration_reaches_the_worker(
    owner_db: Database, worker_db: Database, halden: Org, admin: Any
) -> None:
    """The worker rebuilds a source from its row: mappings come from the table it may
    read (lattice_worker SELECT), channel sensitivity from the configuration."""
    await sync(owner_db, halden, "slack", FAKE)
    before = await _accounts_of(owner_db, halden, "slack")
    slack = await _source(owner_db, halden, "slack")
    await _sync_now(admin, worker_db, slack)
    assert await _accounts_of(owner_db, halden, "slack") == before
