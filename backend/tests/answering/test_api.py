"""The Phase 6 HTTP surface: POST /v1/ask, GET /v1/evidence/{id}, GET /v1/traces/{id},
GET /v1/admin/providers. Thin routes over the services; these tests pin the contract
(authentication, CSRF, strict request schema, organization only from the session),
SEC-5 (unreadable and nonexistent are the same 404), SEC-9/SEC-15 (trace views
re-authorized; administrators see operational structure only) and masked secrets."""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from lattice.db.engine import Database

from ..conftest import CSRF, AppFactory
from ..factories import login
from ..retrieval.world import Org, new_org
from .helpers import Harness, configure_generation, deps, set_credential

SECRET = "AIza-api-test-key-DO-NOT-LEAK-0042"
CONFIDENTIAL = "Escalation budget LATTICE-CANARY-API-9999 for onboarding."


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.member("maya")
    await org.member("grace", clearance="confidential")
    await org.member("lena")
    await org.execute("UPDATE memberships SET is_admin = true WHERE id = :m", m=org.members["lena"])
    await org.document(
        "checklist", "Onboarding checklist", ["The onboarding checklist has five steps."]
    )
    await org.document("budget", "Onboarding budget", [CONFIDENTIAL], sensitivity="confidential")
    await configure_generation(org)
    return org


@pytest.fixture
async def api(app_client: AppFactory) -> AsyncIterator[tuple[httpx.AsyncClient, Harness]]:
    harness = Harness()

    def configure(app: Any) -> None:
        app.state.ask_dependencies = deps(harness, {"k": SecretStr(SECRET)})

    async with app_client(configure) as client:
        yield client, harness


async def _as(client: httpx.AsyncClient, org: Org, name: str) -> None:
    response = await login(client, f"{name}@{org.slug}.example")
    assert response.status_code == 200


async def test_ask_requires_a_session_and_csrf(
    api: tuple[httpx.AsyncClient, Harness], org: Org
) -> None:
    client, _ = api
    body = {"question": "What does the onboarding checklist say?"}
    assert (await client.post("/v1/ask", json=body, headers=CSRF)).status_code == 401
    await _as(client, org, "maya")
    assert (await client.post("/v1/ask", json=body)).json()["error"]["code"] == "csrf_rejected"


@pytest.mark.parametrize(
    "extra",
    [
        {"organization_id": str(uuid.uuid4())},
        {"membership_id": str(uuid.uuid4())},
        {"clearance": "restricted"},
        {"limit": 20},
        {"provider": "gemini"},
    ],
)
async def test_no_authority_can_be_supplied(
    api: tuple[httpx.AsyncClient, Harness], org: Org, extra: dict[str, Any]
) -> None:
    client, harness = api
    await _as(client, org, "maya")
    response = await client.post("/v1/ask", json={"question": "checklist?", **extra}, headers=CSRF)
    assert response.status_code == 422
    value = str(next(iter(extra.values())))
    if len(value) > 6:  # short values could occur by chance in the request ID
        assert value not in response.text  # never echoed
    assert harness.requests == []


async def test_a_question_that_is_too_long_is_refused_without_echo(
    api: tuple[httpx.AsyncClient, Harness], org: Org
) -> None:
    client, _ = api
    await _as(client, org, "maya")
    question = "secret-question " * 200
    response = await client.post("/v1/ask", json={"question": question}, headers=CSRF)
    assert response.status_code == 422
    assert "secret-question" not in response.text


async def test_ask_returns_the_answer_contract(
    api: tuple[httpx.AsyncClient, Harness], org: Org
) -> None:
    client, _ = api
    await _as(client, org, "grace")
    response = await client.post(
        "/v1/ask", json={"question": "What does the onboarding checklist say?"}, headers=CSRF
    )
    assert response.status_code == 200
    answer = response.json()
    assert answer["status"] == "answered"
    evidence = {e["evidence_id"]: e for e in answer["evidence"]}
    for claim in answer["claims"]:
        for citation in claim["citations"]:
            assert evidence[citation["evidence_id"]]["used_for_answer"]
    held = [e for e in answer["evidence"] if e["not_used_reason"] == "egress_policy"]
    assert held
    assert held[0]["classification"] == "confidential"
    assert response.headers["x-request-id"]


def _without_request_id(response: httpx.Response) -> dict[str, Any]:
    return {k: v for k, v in response.json()["error"].items() if k != "request_id"}


async def test_evidence_lookup_is_404_for_unreadable_and_nonexistent_alike(
    api: tuple[httpx.AsyncClient, Harness], org: Org
) -> None:
    client, _ = api
    await _as(client, org, "maya")
    unreadable = org.chunks["budget"][0]
    missing = uuid.uuid4()
    first = await client.get(f"/v1/evidence/{unreadable}")
    second = await client.get(f"/v1/evidence/{missing}")
    assert first.status_code == second.status_code == 404
    assert _without_request_id(first) == _without_request_id(second)
    readable = await client.get(f"/v1/evidence/{org.chunks['checklist'][0]}")
    assert readable.status_code == 200
    assert readable.json()["text"] == "The onboarding checklist has five steps."
    assert "sensitivity" not in readable.text  # the label is exposed as `classification`
    await _as(client, org, "grace")
    assert (await client.get(f"/v1/evidence/{unreadable}")).status_code == 200


async def test_trace_views(
    api: tuple[httpx.AsyncClient, Harness], org: Org, owner_db: Database
) -> None:
    client, _ = api
    await _as(client, org, "grace")
    asked = await client.post(
        "/v1/ask", json={"question": "What does the onboarding checklist say?"}, headers=CSRF
    )
    trace_id = asked.json()["trace_id"]
    own = (await client.get(f"/v1/traces/{trace_id}")).json()
    assert own["view"] == "asker"
    assert own["question"] == "What does the onboarding checklist say?"
    assert own["answer"] is not None
    assert own["answer_hidden"] is False

    # An administrator sees operational structure only (D-5, SEC-15).
    await _as(client, org, "lena")
    admin = await client.get(f"/v1/traces/{trace_id}")
    assert admin.status_code == 200
    view = admin.json()
    assert view["view"] == "operational"
    for hidden in ("question", "answer", "evidence_ids", "scope", "validation"):
        assert hidden not in view
    assert "onboarding" not in admin.text.lower()
    assert "count" not in admin.text  # no candidate or evidence counts

    # Another member gets the same 404 as for a trace that does not exist.
    await _as(client, org, "maya")
    other = await client.get(f"/v1/traces/{trace_id}")
    missing = await client.get(f"/v1/traces/{uuid.uuid4()}")
    assert other.status_code == missing.status_code == 404

    # Revocation: once a cited document is unreadable, the stored answer is hidden.
    await org.execute(
        "UPDATE memberships SET clearance = 'internal' WHERE id = :m", m=org.members["grace"]
    )
    await org.execute(
        "UPDATE documents SET status = 'deleted' WHERE id = :d", d=org.documents["checklist"]
    )
    await _as(client, org, "grace")
    revoked = (await client.get(f"/v1/traces/{trace_id}")).json()
    assert revoked["answer"] is None
    assert revoked["answer_hidden"] is True
    assert str(org.chunks["checklist"][0]) not in revoked["evidence_ids"]
    assert str(org.chunks["budget"][0]) not in revoked["evidence_ids"]


async def test_admin_providers_is_masked_and_admin_only(
    api: tuple[httpx.AsyncClient, Harness], org: Org
) -> None:
    client, _ = api
    await configure_generation(org, provider="gemini", model="gemini-3.5-flash-lite")
    await set_credential(org, "gemini", "memory:k", data_use="paid")
    await _as(client, org, "maya")
    denied = await client.get("/v1/admin/providers")
    assert denied.status_code == 403
    await _as(client, org, "lena")
    response = await client.get("/v1/admin/providers")
    assert response.status_code == 200
    assert SECRET not in response.text
    assert "memory:k" not in response.text  # not even the reference
    body = response.json()
    assert body["egress"]["external_egress_level"] == "internal"
    gemini = next(p for p in body["providers"] if p["provider"] == "gemini")
    credential = gemini["credential"]
    assert {k: credential[k] for k in ("state", "source", "resolvable", "data_use")} == {
        "state": "active",
        "source": "memory",
        "resolvable": True,
        "data_use": "paid",
    }
    assert credential["managed_by"] == "operator"  # not entered in the product
    assert credential["hint"] is None
    assert gemini["enabled"] is True
    fake = next(
        p for p in body["providers"] if p["provider"] == "fake" and p["purpose"] == "generation"
    )
    assert fake["enabled"] is False  # one enabled generation configuration


async def test_the_openapi_contract_lists_the_phase_6_routes(
    api: tuple[httpx.AsyncClient, Harness],
) -> None:
    client, _ = api
    paths = (await client.get("/openapi.json")).json()["paths"]
    for path in (
        "/v1/ask",
        "/v1/evidence/{evidence_id}",
        "/v1/traces/{trace_id}",
        "/v1/admin/providers",
    ):
        assert path in paths
    document = (await client.get("/openapi.json")).json()
    schema = json.dumps(document)
    assert "secret_ref" not in schema
    # An API key is only ever a write-only request field (P12.5), never in a response.
    components = document["components"]["schemas"]
    holders = sorted(n for n, c in components.items() if "api_key" in c.get("properties", {}))
    assert holders == ["CredentialRequest"]
    field = components["CredentialRequest"]["properties"]["api_key"]
    assert (field["format"], field["writeOnly"]) == ("password", True)


async def test_ask_persists_exactly_one_trace(
    api: tuple[httpx.AsyncClient, Harness], org: Org, owner_db: Database
) -> None:
    client, _ = api
    await _as(client, org, "maya")
    await client.post("/v1/ask", json={"question": "checklist steps"}, headers=CSRF)
    async with owner_db.tenant_transaction(org.id) as s:
        count: int = (await s.execute(text("SELECT count(*) FROM query_traces"))).scalar_one()
    assert count == 1
