"""End-to-end organization onboarding through the product (P12.7; phase-12-plan §20).

One organization goes from the operator's ``lattice org create`` to a member's first
answered question, using only the product's routes, the real worker and the import
store -- no seed, no sources file, no owner SQL. At every step the setup record is read,
the step is done, and the record is read again: state, reason and destination change
exactly as the backend data does. Authorization is checked by the predicate itself
(never by columns): the first administrator configures everything and reads nothing;
ticket references nobody mapped stay unreadable (never unscoped) until mapped; the
member assigned the account reads its tickets and the other member does not, in
retrieval, evidence and trace alike. A second organization onboarded beside it sees
none of it and learns nothing about it.
"""

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.orgs.bootstrap import BootstrapRejected

from ..conftest import CSRF
from .conftest import (
    CORVANE,
    REASON,
    WRENFIELD,
    World,
    add_member,
    bootstrap,
    create_account,
    create_source,
    detail,
    export,
    readable,
    setup,
    state,
    upload,
)

QUESTION = "What restored the Quillon telemetry feed for Ashgrove?"
# Words that exist only in content the first administrator and Sam may not read, and
# names an administrator's operational views must never carry (C9).
PROTECTED = ("Quillon", "4.2.7", "acct-ashgrove-war-room", "discount-guardrails", "Larch Street")
FAILED = "/sign-in?error=sso_failed"


def _clean(payload: Any, *, also: tuple[str, ...] = ()) -> None:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    for word in (*PROTECTED, *also):
        assert word not in body, word


async def _membership(owner_db: Database, org: uuid.UUID, email: str) -> uuid.UUID:
    async with owner_db.tenant_snapshot(org) as s:
        found: uuid.UUID = (
            await s.execute(
                text(
                    "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id "
                    "WHERE m.organization_id = :o AND u.email = :e"
                ),
                {"o": org, "e": email},
            )
        ).scalar_one()
    return found


async def _sync(world: World, admin: httpx.AsyncClient, source_id: str) -> dict[str, Any]:
    requested = await admin.post(f"/v1/admin/sources/{source_id}/sync", headers=CSRF)
    assert requested.status_code == 202, requested.text
    outcome = await world.work()
    assert outcome.outcome == "succeeded", outcome
    return await detail(admin, source_id)


async def _ask(person: httpx.AsyncClient, question: str = QUESTION) -> dict[str, Any]:
    response = await person.post("/v1/ask", json={"question": question}, headers=CSRF)
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


@pytest.mark.usefixtures("onboarding_keys")
async def test_an_organization_is_onboarded_from_bootstrap_to_its_first_answer(
    owner_db: Database, app_db: Database, world_factory: Any
) -> None:
    # --- 1. Operator bootstrap: one transaction; an identical retry writes nothing; a
    # conflicting one is refused and changes nothing. -----------------------------------
    created = await bootstrap(owner_db, WRENFIELD)
    assert (created.created, created.client_secret) == (True, "encrypted")
    again = await bootstrap(owner_db, WRENFIELD)
    assert (again.created, again.organization_id) == (False, created.organization_id)
    with pytest.raises(BootstrapRejected, match="differs: name"):
        await bootstrap(owner_db, WRENFIELD, name="Someone else")
    org = created.organization_id

    async with world_factory() as world:
        world.login_keys["wrenfield"] = created.login_key
        admin, riley, sam = world.browser(), world.browser(), world.browser()

        # --- 2. First administrator: ordinary OIDC sign-in, exact identity. -----------
        assert await world.sign_in(admin, "wrenfield", "WRN-ADA") == FAILED  # case matters
        assert await world.sign_in(admin, "wrenfield", WRENFIELD.admin_email) == FAILED
        assert await world.sign_in(admin, "wrenfield", WRENFIELD.admin_subject) == "/ask"
        me = (await admin.get("/v1/me")).json()
        assert me["user"]["email"] == WRENFIELD.admin_email
        assert me["organization"]["slug"] == "wrenfield"
        assert me["capabilities"] == ["admin"]

        # --- 3. The setup record after bootstrap: true, with destinations. -----------
        record = await setup(admin)
        assert state(record, "organization") == ("attention", "single_administrator", "/access")
        assert state(record, "identity") == ("complete", "verified", "/settings/identity")
        assert state(record, "members") == ("required", "only_administrators", "/access")
        assert state(record, "provisioning") == (
            "optional",
            "not_configured",
            "/settings/identity",
        )
        # Evidence only is a legitimate configuration, not a missing step.
        assert state(record, "ai_policy") == ("optional", "evidence_only", "/settings/providers")
        assert state(record, "sources") == ("required", "no_sources", "/sources")
        assert state(record, "accounts") == ("optional", "not_needed", None)
        assert state(record, "first_answer") == ("blocked", "sources_required", None)
        assert "percent" not in json.dumps(record)

        # --- 4. Members by hand: exact identifiers, least access, admin != content. --
        riley_member = await add_member(
            admin, "Riley Moss", "riley.moss@wrenfield.example", "wrn-riley"
        )
        await add_member(admin, "Sam Okoro", "sam.okoro@wrenfield.example", "wrn-sam")
        member = riley_member["member"]
        assert (member["is_admin"], member["clearance"], member["account_scope"]) == (
            False,
            "internal",
            "assigned",
        )
        assert member["assigned_accounts"] == 0
        record = await setup(admin)
        assert state(record, "members") == ("complete", "members_active", "/access")

        # --- 5. Accounts: create, edit, archive, reactivate. --------------------------
        ashgrove = await create_account(admin, "ashgrove-transit", "Ashgrove Transit")
        brindle = await create_account(admin, "brindle-foods", "Brindle Foods")
        edited = await admin.patch(
            f"/v1/admin/access/accounts/{brindle['id']}",
            json={"version": brindle["version"], "aliases": ["Brindle"], "reason": REASON},
            headers=CSRF,
        )
        assert edited.status_code == 200, edited.text
        archived = await admin.post(
            f"/v1/admin/access/accounts/{brindle['id']}/archive",
            json={"version": edited.json()["version"], "reason": REASON},
            headers=CSRF,
        )
        assert archived.status_code == 200, archived.text
        reactivated = await admin.post(
            f"/v1/admin/access/accounts/{brindle['id']}/reactivate",
            json={"version": archived.json()["version"], "reason": REASON},
            headers=CSRF,
        )
        assert reactivated.status_code == 200, reactivated.text

        # --- 6. Sources: created, each waiting for its first export. ------------------
        sources = {key: await create_source(admin, key) for key in ("handbook", "support", "slack")}
        for source in sources.values():
            assert source["next_action"] == "upload_import"
            assert source["managed_by"] == "organization"
        record = await setup(admin)
        assert state(record, "sources") == ("required", "awaiting_import", "/sources")
        assert state(record, "accounts") == ("blocked", "sources_required", None)

        # --- 7. Uploads: each becomes current and is checked by the worker. ----------
        for key, source in sources.items():
            body, kind = export(key)
            response = await upload(admin, source["id"], body, kind)
            assert response.status_code == 201, response.text
            assert response.json()["check_queued"] is True
            checked = await world.work()
            assert checked.outcome == "succeeded", checked
            _clean(response.json())
        support = await detail(admin, sources["support"]["id"])
        check = support["current_import"]["check"]
        unresolved = {r["external_ref"]: r["records"] for r in check["unresolved_references"]}
        assert unresolved == {"7100001": 2, "7100002": 1}
        # The references can be mapped before the first sync (the check reports them).
        unmapped = (await admin.get("/v1/admin/access/account-references/unmapped")).json()
        assert {(i["external_ref"], i["records_waiting"]) for i in unmapped["items"]} == {
            ("7100001", 2),
            ("7100002", 1),
        }
        record = await setup(admin)
        assert state(record, "sources") == ("required", "not_synced", "/sources")
        for key in sources:
            _clean(await detail(admin, sources[key]["id"]))

        # --- 8. First sync, references still unmapped: tickets fail closed. ----------
        for source in sources.values():
            await _sync(world, admin, source["id"])
        record = await setup(admin)
        assert state(record, "sources") == ("complete", "synced", "/sources")
        assert state(record, "accounts") == (
            "attention",
            "unmapped_references",
            "/access/accounts/references",
        )
        assert record["accounts"]["facts"]["unmapped_references"] == 2
        assert state(record, "first_answer") == ("required", "no_answer_yet", "/ask")
        riley_id = await _membership(owner_db, org, "riley.moss@wrenfield.example")
        sam_id = await _membership(owner_db, org, "sam.okoro@wrenfield.example")
        admin_id = created.admin_membership_id
        everyone = {riley_id, sam_id, admin_id}
        for who in everyone:
            assert not {d for d in await readable(app_db, org, who) if d[0] == "support"}
        failures = await admin.get(f"/v1/admin/sources/{sources['support']['id']}/failures")
        assert failures.status_code == 200
        assert {f["code"] for f in failures.json()["failures"]} == {"access_unmappable:account"}
        _clean(failures.json())

        # --- 9. Riley is assigned Ashgrove; the references are mapped; next sync. ----
        riley_access = (await admin.get(f"/v1/admin/access/members/{riley_id}")).json()
        assigned = await admin.patch(
            f"/v1/admin/access/members/{riley_id}",
            json={
                "accounts": [ashgrove["id"]],
                "version": riley_access["version"],
                "reason": REASON,
            },
            headers=CSRF,
        )
        assert assigned.status_code == 200, assigned.text
        for ref, account in (("7100001", ashgrove), ("7100002", brindle)):
            mapped = await admin.post(
                "/v1/admin/access/account-references",
                json={
                    "source_id": sources["support"]["id"],
                    "ref_kind": "ticketing_organization",
                    "external_ref": ref,
                    "account_id": account["id"],
                    "reason": REASON,
                },
                headers=CSRF,
            )
            assert mapped.status_code in (200, 201), mapped.text
        record = await setup(admin)
        assert state(record, "accounts") == ("attention", "sync_required", "/sources")
        await _sync(world, admin, sources["support"]["id"])
        record = await setup(admin)
        assert state(record, "accounts") == ("complete", "mapped", "/access/accounts")

        # --- 10. Exactly who reads what, by the authorization predicate. -------------
        riley_reads = await readable(app_db, org, riley_id)
        sam_reads = await readable(app_db, org, sam_id)
        admin_reads = await readable(app_db, org, admin_id)
        tickets = {ext for key, ext in riley_reads if key == "support"}
        assert tickets == {"WRN-1001", "WRN-1002"}  # Ashgrove only; not Brindle's
        assert ("handbook", "wrn-ashgrove-renewal") in riley_reads
        assert any(k == "slack" and ext.startswith("G0WASHWAR1:") for k, ext in riley_reads)
        for reads in (sam_reads, admin_reads):  # neither assigned, neither in the channel
            assert not {ext for key, ext in reads if key == "support"}
            assert ("handbook", "wrn-ashgrove-renewal") not in reads
            assert not any(ext.startswith("G0WASHWAR1:") for _, ext in reads)
            assert ("handbook", "wrn-support-rotation") in reads  # internal, everyone
        for reads in (riley_reads, sam_reads, admin_reads):  # confidential: nobody yet
            assert ("handbook", "wrn-discount-guardrails") not in reads

        # --- 11. Riley signs in and asks: authorized evidence, no generation. --------
        assert await world.sign_in(riley, "wrenfield", "wrn-riley") == "/ask"
        answer = await _ask(riley)
        assert answer["status"] == "evidence_only", answer["status"]
        refs = {e["display_ref"] for e in answer["evidence"]}
        assert "WRN-1001" in refs
        assert "WRN-1003" not in refs
        assert any("4.2.7" in e["text"] for e in answer["evidence"])
        first = answer["evidence"][0]
        opened = await riley.get(f"/v1/evidence/{first['evidence_id']}")
        assert opened.status_code == 200, opened.text
        trace = await riley.get(f"/v1/traces/{answer['trace_id']}")
        assert trace.status_code == 200
        assert trace.json()["view"] == "asker"
        record = await setup(admin)
        assert state(record, "first_answer") == ("complete", "evidence_returned", None)

        # --- 12. Sam, and the administrator: nothing leaks, in any view. -------------
        assert await world.sign_in(sam, "wrenfield", "wrn-sam") == "/ask"
        for person in (sam, admin):
            denied = await _ask(person)
            assert denied["status"] == "insufficient_evidence", denied["status"]
            assert denied["evidence"] == []
            _clean(denied)
            missing = await person.get(f"/v1/evidence/{uuid.uuid4()}")
            hidden = await person.get(f"/v1/evidence/{first['evidence_id']}")
            assert hidden.status_code == missing.status_code == 404
            assert hidden.json()["error"]["code"] == missing.json()["error"]["code"]
        other = await sam.get(f"/v1/traces/{answer['trace_id']}")
        assert other.status_code == 404
        operational = await admin.get(f"/v1/traces/{answer['trace_id']}")
        assert operational.json()["view"] == "operational"
        _clean(operational.json(), also=("Ashgrove",))
        # Account metadata is no side channel: Sam's visible accounts name no account.
        assert (await sam.get("/v1/accounts")).json()["accounts"] == []
        for path in (
            "/v1/admin/setup",
            "/v1/admin/sources",
            "/v1/admin/access/account-references/unmapped",
            "/v1/admin/audit-events",
        ):
            _clean((await admin.get(path)).text)

        # --- 13. Suspension ends the session; reactivation restores sign-in only. ----
        sam_access = (await admin.get(f"/v1/admin/access/members/{sam_id}")).json()
        suspended = await admin.patch(
            f"/v1/admin/access/members/{sam_id}",
            json={"status": "suspended", "version": sam_access["version"], "reason": REASON},
            headers=CSRF,
        )
        assert suspended.status_code == 200, suspended.text
        assert (await sam.get("/v1/me")).status_code == 401
        assert await world.sign_in(world.browser(), "wrenfield", "wrn-sam") == FAILED
        restored = await admin.patch(
            f"/v1/admin/access/members/{sam_id}",
            json={"status": "active", "version": suspended.json()["version"], "reason": REASON},
            headers=CSRF,
        )
        assert restored.status_code == 200, restored.text
        assert (await sam.get("/v1/me")).status_code == 401  # the old session stays ended
        assert await world.sign_in(sam, "wrenfield", "wrn-sam") == "/ask"
        assert await readable(app_db, org, sam_id) == sam_reads

        # --- 14. A second organization, onboarded beside it, sees none of it. --------
        corvane = await bootstrap(owner_db, CORVANE)
        world.login_keys["corvane"] = corvane.login_key
        noor = world.browser()
        # Identities do not cross: Wrenfield's people are nobody at Corvane.
        assert await world.sign_in(noor, "corvane", "wrn-riley") == FAILED
        assert await world.sign_in(noor, "corvane", CORVANE.admin_subject) == "/ask"
        theirs = await setup(noor)
        assert state(theirs, "sources") == ("required", "no_sources", "/sources")
        assert state(theirs, "members") == ("required", "only_administrators", "/access")
        assert (await noor.get("/v1/admin/sources")).json()["sources"] == []
        random = str(uuid.uuid4())
        for target in (sources["support"]["id"], random):
            for method, path, payload in (
                ("GET", f"/v1/admin/sources/{target}", None),
                ("POST", f"/v1/admin/sources/{target}/sync", None),
                ("GET", f"/v1/admin/sources/{target}/disconnect-preview", None),
            ):
                response = await noor.request(method, path, json=payload, headers=CSRF)
                assert response.status_code == 404, (path, response.status_code)
        foreign = await upload(
            noor, sources["support"]["id"], export("support")[0], "application/x-ndjson"
        )
        unknown = await upload(noor, random, export("support")[0], "application/x-ndjson")
        assert foreign.status_code == unknown.status_code == 404
        assert foreign.json()["error"] == {
            **unknown.json()["error"],
            "request_id": foreign.json()["error"]["request_id"],
        }
        mapping = {
            "ref_kind": "ticketing_organization",
            "external_ref": "7100001",
            "account_id": ashgrove["id"],
            "reason": REASON,
        }
        cross = await noor.post(
            "/v1/admin/access/account-references",
            json={**mapping, "source_id": sources["support"]["id"]},
            headers=CSRF,
        )
        nowhere = await noor.post(
            "/v1/admin/access/account-references",
            json={**mapping, "source_id": random},
            headers=CSRF,
        )
        assert cross.status_code == nowhere.status_code
        assert cross.json()["error"]["code"] == nowhere.json()["error"]["code"]
        assert (await noor.get(f"/v1/admin/access/accounts/{ashgrove['id']}")).status_code == 404
        assert (await noor.get(f"/v1/evidence/{first['evidence_id']}")).status_code == 404
        assert (await noor.get(f"/v1/traces/{answer['trace_id']}")).status_code == 404
        assert (await _ask(noor))["evidence"] == []
        _clean((await noor.get("/v1/admin/audit-events")).text, also=("wrenfield", "Ashgrove"))
