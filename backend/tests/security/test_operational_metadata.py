"""Operational-metadata side channels over hand-built worlds (security-model §10, SEC-14,
SEC-15, T20): source lists and counts, sync failures, requested syncs and Activity must
never reveal content, or the existence of documents, sources or questions, that the
viewer may not see. Complements ``operational/`` (the real corpus, every persona)."""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from lattice.db.engine import Database

from ..conftest import CSRF, AppFactory
from ..factories import create_source, login
from ..retrieval.world import Org, new_org

CANARY_QUESTION = "LATTICE-CANARY-QUESTION-7781 is Acme about to churn"
CANARY_ANSWER = "LATTICE-CANARY-ANSWER-7782"
SECRET_TITLE = "Acquisition memo LATTICE-CANARY-TITLE-7783"
OPERATIONAL_KEYS = {
    "trace_id",
    "created_at",
    "membership_id",
    "member_name",
    "status",
    "status_reason",
    "error_code",
    "provider",
    "model",
    "duration_ms",
    "generation_calls",
    "input_tokens",
    "output_tokens",
}
JOB_KEYS = {
    "id",
    "source_id",
    "mode",
    "status",
    "attempts",
    "error_code",
    "created_at",
    "started_at",
    "finished_at",
    "sync_run_id",
    # P12.6: what the job is (a sync or an import check) and the import it reads (an ID).
    "kind",
    "import_id",
}


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.group("exec")
    await org.member("tom")
    await org.member("grace", clearance="confidential", groups=["exec"])
    await org.member("lena")
    await org.execute("UPDATE memberships SET is_admin = true WHERE id = :m", m=org.members["lena"])
    await org.document("guide", "Onboarding guide", ["The onboarding guide has steps."])
    await org.document("faq", "Onboarding FAQ", ["The FAQ answers questions."])
    await org.document(
        "memo",
        SECRET_TITLE,
        ["The acquisition is confidential."],
        sensitivity="restricted",
        access_mode="grants_only",
        grants=[("group", "exec")],
    )
    await org.execute(
        "UPDATE sources SET config = CAST(:c AS jsonb) WHERE id = :s",
        c=json.dumps({"path": "/srv/exports/halden/LATTICE-CANARY-PATH/documents"}),
        s=org.source_id,
    )
    return org


@pytest.fixture
async def client(app_client: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client() as c:
        yield c


async def _as(client: httpx.AsyncClient, org: Org, name: str) -> None:
    assert (await login(client, f"{name}@{org.slug}.example")).status_code == 200


async def _secret_source(org: Org) -> uuid.UUID:
    """A second source whose only document only Grace (exec) may read."""
    source = await create_source(org.owner, org.id, "board", "document_repository")
    await org.execute(
        "UPDATE sources SET name = 'Board LATTICE-CANARY-SOURCE-7784' WHERE id = :s", s=source
    )
    original = org.source_id
    org.source_id = source
    try:
        await org.document(
            "board-minutes",
            "Board minutes",
            ["The board met."],
            sensitivity="restricted",
            access_mode="grants_only",
            grants=[("group", "exec")],
        )
    finally:
        org.source_id = original
    return source


# --- sources: members --------------------------------------------------------------------


async def test_a_source_a_member_cannot_read_from_does_not_exist_for_them(
    client: httpx.AsyncClient, org: Org
) -> None:
    await _as(client, org, "tom")
    without = (await client.get("/v1/sources")).json()
    source = await _secret_source(org)
    with_it = await client.get("/v1/sources")
    assert with_it.json() == without  # byte-for-byte the same view
    assert str(source) not in with_it.text
    assert "CANARY" not in with_it.text

    await _as(client, org, "grace")
    grace = {s["name"] for s in (await client.get("/v1/sources")).json()["sources"]}
    assert "Board LATTICE-CANARY-SOURCE-7784" in grace


async def test_member_counts_are_computed_inside_their_predicate(
    client: httpx.AsyncClient, org: Org
) -> None:
    await _as(client, org, "tom")
    [tom] = (await client.get("/v1/sources")).json()["sources"]
    await _as(client, org, "grace")
    [grace] = (await client.get("/v1/sources")).json()["sources"]
    assert (tom["readable_documents"], grace["readable_documents"]) == (2, 3)
    assert set(tom) == {
        "id",
        "name",
        "connector_type",
        "readable_documents",
        "last_synced_at",
        "last_succeeded_at",
        "last_run_status",
    }


async def test_revoking_access_removes_the_source_on_the_next_request(
    client: httpx.AsyncClient, org: Org
) -> None:
    await _secret_source(org)
    await _as(client, org, "grace")
    assert len((await client.get("/v1/sources")).json()["sources"]) == 2
    await org.execute(
        "DELETE FROM access_group_members WHERE membership_id = :m", m=org.members["grace"]
    )
    await org.execute(
        "UPDATE memberships SET authz_updated_at = now() WHERE id = :m", m=org.members["grace"]
    )
    assert len((await client.get("/v1/sources")).json()["sources"]) == 1


# --- sources: administrators --------------------------------------------------------------


async def _failures_run(org: Org, rows: list[dict[str, Any]]) -> None:
    run = (
        await org.execute(
            "INSERT INTO source_sync_runs (organization_id, source_id, mode, status, "
            "finished_at, failed) VALUES (:o, :s, 'full', 'partial', now(), :n) RETURNING id",
            o=org.id,
            s=org.source_id,
            n=len(rows),
        )
    ).scalar_one()
    for i, row in enumerate(rows):
        await org.execute(
            "INSERT INTO sync_record_failures (organization_id, sync_run_id, external_id, "
            "location, opaque_ref, stage, error_code, message_params) VALUES (:o, :r, :e, :l, "
            ":ref, :st, :c, CAST(:p AS jsonb))",
            o=org.id,
            r=run,
            e=row.get("external_id"),
            l=row.get("location", "LATTICE-CANARY-FILE.md:3"),
            ref=f"rec_{i:016x}",
            st=row.get("stage", "embed"),
            c=row.get("code", "embedding_failed:timeout"),
            p=json.dumps(row.get("params", {})),
        )


async def test_a_failure_names_its_record_only_if_the_admin_can_read_it(
    client: httpx.AsyncClient, org: Org
) -> None:
    await _failures_run(
        org,
        [
            {"external_id": "guide"},  # readable by Lena
            {"external_id": "memo"},  # restricted, grants-only: not readable by Lena
            {"external_id": "never-ingested"},  # no document at all
            {
                "external_id": None,
                "stage": "parse",
                "code": "malformed_record",
                "params": {"field": "sensitivity", "title": SECRET_TITLE, "line": 12},
            },
            {
                "external_id": "memo",
                "stage": "map_access",
                "code": "access_unmappable:account",
                "params": {"external_account_ref": "360009913", "title": SECRET_TITLE},
            },
        ],
    )
    await _as(client, org, "lena")
    response = await client.get(f"/v1/admin/sources/{org.source_id}/failures")
    assert response.status_code == 200
    by_ref = {(f["code"], f["record"]): f for f in response.json()["failures"]}
    kinds = sorted((f["record_kind"], f["record"]) for f in by_ref.values())
    assert ("identifier", "guide") in kinds
    assert all(r.startswith("rec_") for k, r in kinds if k == "opaque")
    assert sum(1 for k, _ in kinds if k == "identifier") == 1
    params = {f["code"]: f["params"] for f in response.json()["failures"]}
    assert params["malformed_record"] == {"field": "sensitivity"}
    assert params["access_unmappable:account"] == {"external_account_ref": "360009913"}
    for leaked in ("memo", "never-ingested", "CANARY", "LATTICE-CANARY-FILE"):
        assert leaked not in response.text


async def test_the_admin_listing_holds_aggregates_only(client: httpx.AsyncClient, org: Org) -> None:
    await _secret_source(org)
    await _as(client, org, "lena")
    response = await client.get("/v1/admin/sources")
    assert response.status_code == 200
    names = {s["name"]: s["documents"] for s in response.json()["sources"]}
    # Every source (admins configure them), with its aggregate total.
    assert names == {"fixture": 3, "Board LATTICE-CANARY-SOURCE-7784": 1}
    for leaked in ("LATTICE-CANARY-PATH", "/srv/exports", SECRET_TITLE, "Board minutes"):
        assert leaked not in response.text


async def test_failures_of_another_organization_are_not_found(
    client: httpx.AsyncClient, org: Org, owner_db: Database
) -> None:
    other = await new_org(owner_db, "veridian")
    await other.member("noor")
    await other.execute(
        "UPDATE memberships SET is_admin = true WHERE id = :m", m=other.members["noor"]
    )
    await _as(client, other, "noor")
    theirs = await client.get(f"/v1/admin/sources/{org.source_id}/failures")
    nothing = await client.get(f"/v1/admin/sources/{uuid.uuid4()}/failures")
    sync = await client.post(f"/v1/admin/sources/{org.source_id}/sync", json={}, headers=CSRF)
    for response in (theirs, nothing, sync):
        assert response.status_code == 404
    assert theirs.json()["error"]["message"] == nothing.json()["error"]["message"]
    listing = (await client.get("/v1/admin/sources")).json()["sources"]
    assert str(org.source_id) not in json.dumps(listing)


# --- requested syncs ---------------------------------------------------------------------


async def test_requesting_a_sync(client: httpx.AsyncClient, org: Org) -> None:
    path = f"/v1/admin/sources/{org.source_id}/sync"
    await _as(client, org, "tom")
    assert (await client.post(path, json={}, headers=CSRF)).status_code == 403
    missing = await client.post(f"/v1/admin/sources/{uuid.uuid4()}/sync", json={}, headers=CSRF)
    assert missing.status_code == 403  # no existence oracle for non-admins

    await _as(client, org, "lena")
    assert (await client.post(path, json={})).json()["error"]["code"] == "csrf_rejected"
    extra = await client.post(path, json={"path": "/etc"}, headers=CSRF)
    assert extra.status_code == 422
    first = await client.post(path, json={"mode": "full"}, headers=CSRF)
    again = await client.post(path, headers=CSRF)
    assert (first.status_code, again.status_code) == (202, 200)
    assert first.json()["id"] == again.json()["id"]
    assert set(first.json()) == JOB_KEYS
    assert first.json()["status"] == "queued"
    listing = {s["id"]: s for s in (await client.get("/v1/admin/sources")).json()["sources"]}
    assert listing[str(org.source_id)]["job"]["id"] == first.json()["id"]

    await org.execute("UPDATE sources SET status = 'paused' WHERE id = :s", s=org.source_id)
    await org.execute("UPDATE jobs SET status = 'failed', finished_at = now(), error_code = 'x'")
    paused = await client.post(path, headers=CSRF)
    assert paused.status_code == 422
    assert paused.json()["error"]["details"] == [{"field": "source", "issue": "source_not_active"}]


# --- activity ----------------------------------------------------------------------------


async def _trace(org: Org, member: str, question: str, **columns: Any) -> uuid.UUID:
    values = {
        "status": "answered",
        "egress": {"provider": "fake", "model": "fake-extractive-v1"},
        "validation": {"generation_calls": 1},
        "timings_ms": {"total": 812.5},
        "usage": {"input_tokens": 900, "output_tokens": 80},
        "answer": {"claims": [{"text": CANARY_ANSWER}]},
        "scope": {"account": "acme-corp"},
        **columns,
    }
    result = await org.execute(
        "INSERT INTO query_traces (organization_id, membership_id, question_text, "
        "question_sensitivity, scope, authz_snapshot, retrieval, egress, validation, answer, "
        "status, error_code, timings_ms, usage, evidence_chunk_ids) "
        "VALUES (:o, :m, :q, 'internal', CAST(:scope AS jsonb), '{}', '{}', "
        "CAST(:egress AS jsonb), CAST(:validation AS jsonb), CAST(:answer AS jsonb), :status, "
        ":error, CAST(:timings AS jsonb), CAST(:usage AS jsonb), ARRAY[gen_random_uuid()]) "
        "RETURNING id",
        o=org.id,
        m=org.members[member],
        q=question,
        scope=json.dumps(values["scope"]),
        egress=json.dumps(values["egress"]),
        validation=json.dumps(values["validation"]),
        answer=json.dumps(values["answer"]),
        status=values["status"],
        error=values.get("error_code"),
        timings=json.dumps(values["timings_ms"]),
        usage=json.dumps(values["usage"]),
    )
    trace_id: uuid.UUID = result.scalar_one()
    return trace_id


async def test_members_list_only_their_own_questions(client: httpx.AsyncClient, org: Org) -> None:
    mine = await _trace(org, "tom", "What does the onboarding guide say?")
    await _trace(org, "grace", CANARY_QUESTION)
    await _as(client, org, "tom")
    response = await client.get("/v1/activity")
    assert response.status_code == 200
    [item] = response.json()["items"]
    assert item["trace_id"] == str(mine)
    assert set(item) == {"trace_id", "created_at", "status", "status_reason", "question"}
    for leaked in ("CANARY", "acme"):
        assert leaked not in response.text


async def test_administrators_see_operational_rows_never_content(
    client: httpx.AsyncClient, org: Org
) -> None:
    await _trace(org, "grace", CANARY_QUESTION)
    await _trace(
        org, "tom", CANARY_QUESTION, status="failed", error_code="generation_failed:timeout"
    )
    await _as(client, org, "lena")
    response = await client.get("/v1/admin/activity")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    for item in items:
        assert set(item) == OPERATIONAL_KEYS
    assert {i["member_name"] for i in items} == {"grace", "tom"}
    failed = next(i for i in items if i["status"] == "failed")
    assert failed["error_code"] == "generation_failed:timeout"
    for leaked in ("CANARY", "acme", "Acme", "churn", "evidence"):
        assert leaked not in response.text
    # The administrator's own list is theirs alone: empty.
    assert (await client.get("/v1/activity")).json()["items"] == []


async def test_activity_pages_newest_first(client: httpx.AsyncClient, org: Org) -> None:
    ids = [await _trace(org, "tom", f"question {i}") for i in range(5)]
    for age, trace in enumerate(reversed(ids)):
        await org.execute(
            "UPDATE query_traces SET created_at = now() - make_interval(mins => :a) WHERE id = :t",
            a=age,
            t=trace,
        )
    await _as(client, org, "tom")
    first = (await client.get("/v1/activity", params={"limit": 2})).json()["items"]
    rest = (
        await client.get("/v1/activity", params={"limit": 10, "before": first[-1]["trace_id"]})
    ).json()["items"]
    order = [i["trace_id"] for i in first + rest]
    assert order == [str(t) for t in reversed(ids)]
    assert (await client.get("/v1/activity", params={"limit": 101})).status_code == 422


async def test_another_organizations_activity_is_invisible(
    client: httpx.AsyncClient, org: Org, owner_db: Database
) -> None:
    await _trace(org, "tom", CANARY_QUESTION)
    other = await new_org(owner_db, "veridian")
    await other.member("noor")
    await other.execute(
        "UPDATE memberships SET is_admin = true WHERE id = :m", m=other.members["noor"]
    )
    await _as(client, other, "noor")
    assert (await client.get("/v1/admin/activity")).json()["items"] == []
    assert (await client.get("/v1/activity")).json()["items"] == []
