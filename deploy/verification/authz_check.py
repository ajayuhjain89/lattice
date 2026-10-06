"""The deployed authorization suite (plan §15.2) against the reference stack (P11.7).

LOCAL VERIFICATION ONLY. Run by ``make prod-authz`` after
``make prod-up VERIFY=1 IDP=1 FAKE_DELAY=20`` and ``make prod-seed VERIFY=1 IDP=1``.
Synthetic Halden and Veridian data only; personas sign in with the verification
overlay's development sign-in (real OIDC sign-in on the stack is `make prod-identity`).
Over HTTPS through Caddy, round robin over api1 and api2:

* the route sweep: the identified routes are read from the API's own routers (a new route
  is swept without editing this file); anonymous callers get 401 everywhere, SCIM without
  its token 401; a member gets one identical 403 from every administrator route; a
  Veridian administrator gets, for every route taking an identifier, the same answer for
  Halden's real identifier as for a random one -- and nothing of Halden's changes;
* administrator is not content (SEC-15): an administrator sees another member's trace as
  operational data only; the asker sees their own, re-authorized;
* revocation: an Access change (suspend) answers 401 on the next request on both
  instances; reactivation revives no session;
* roles: ``check-deployment`` 7/7 and a requested sync per organization succeeds in the
  worker (``lattice_worker``);
* the ``eval retrieval`` and ``eval answer`` security gates, run inside the stack as an
  operator job against the API's role (``lattice_app``): every security count is 0.

    LATTICE_COMPOSE="docker compose …" uv run --project backend python \\
        deploy/verification/authz_check.py
"""

# ruff: noqa: S101, S608 - a verification driver: asserts, read-only queries over IDs the
# API just returned
import ast
import re
import sys
import time
import uuid
from typing import Any

import httpx
from fastapi.routing import APIRoute
from stack import (
    CSRF,
    QUESTION,
    ROOT,
    case,
    client,
    compose,
    ensure_active,
    env_file,
    fake_generation,
    finish,
    member_status,
    ops,
    org_id,
    served,
    sign_in,
    sql,
    wait_for,
)

from lattice.api.routes import access, activity, admin, ask, me, sources

HALDEN_ADMIN = "lena.brandt@halden.example"
HALDEN_MEMBER = "maya.okafor@halden.example"
# Veridian's manifest has no administrator: the sweep gives its most privileged persona
# the capability (audited) and takes it back at the end.
VERIDIAN_ADMIN = "ines.carvalho@veridian.example"
VERIDIAN_MEMBER = "noor.haddad@veridian.example"
WHY = "P11.7 deployed authorization suite (synthetic data)"
SCIM_ROUTES = [
    ("GET", "/scim/v2/ServiceProviderConfig"),
    ("GET", "/scim/v2/Users"),
    ("POST", "/scim/v2/Users"),
    ("GET", "/scim/v2/Users/{resource_id}"),
    ("PUT", "/scim/v2/Users/{resource_id}"),
    ("PATCH", "/scim/v2/Users/{resource_id}"),
    ("DELETE", "/scim/v2/Users/{resource_id}"),
]
# Valid bodies, so that a refusal is about authority, never the request's shape
# (tests/security/test_route_inventory.py).
BODIES: dict[tuple[str, str], Any] = {
    ("POST", "/v1/ask"): {"question": QUESTION},
    ("POST", "/v1/admin/access/members/{membership_id}/preview"): {"clearance": "confidential"},
    ("PATCH", "/v1/admin/access/members/{membership_id}"): {
        "clearance": "confidential",
        "version": "2026-10-02T00:00:00Z",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/grants/{grant_id}/revoke"): {"reason": "Probe"},
}
QUERIES = {("GET", "/v1/admin/access/documents"): "?q=onboarding"}


def routes() -> list[tuple[str, str]]:
    """Every member and administrator route the API serves (its routers, not a list)."""
    found = set()
    for module in (me, ask, admin, sources, activity, access):
        for route in module.router.routes:
            if isinstance(route, APIRoute):
                found |= {(method, route.path) for method in route.methods or ()}
    return sorted(found)


def url(path: str, ids: dict[str, str]) -> str:
    for name, value in ids.items():
        path = path.replace("{" + name + "}", value)
    return path


def random_ids() -> dict[str, str]:
    names = ("evidence_id", "trace_id", "source_id", "membership_id", "document_id", "grant_id")
    return {name: str(uuid.uuid4()) for name in (*names, "resource_id")}


def call(c: httpx.Client, method: str, path: str, ids: dict[str, str]) -> httpx.Response:
    body = BODIES.get((method, path))
    if path.endswith("/grants"):
        body = {"document_id": ids["document_id"], "reason": "Probe"}
    target = "/api" + url(path, ids) + QUERIES.get((method, path), "")
    return c.request(method, target, json=body, headers=CSRF)


def comparable(response: httpx.Response) -> tuple[int, Any]:
    try:
        body = response.json()
    except ValueError:
        return response.status_code, response.text
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body["error"].pop("request_id", None)
    return response.status_code, body


def halden_state(halden: str, maya: str) -> str:
    """What a foreign request must not change: grants, Maya's access, jobs, audit."""
    return sql(
        f"SELECT (SELECT count(*) FROM document_grants WHERE organization_id = '{halden}') "
        f"|| ',' || (SELECT clearance || status FROM memberships WHERE id = '{maya}') "
        f"|| ',' || (SELECT count(*) FROM jobs WHERE organization_id = '{halden}') "
        f"|| ',' || (SELECT count(*) FROM audit_events WHERE organization_id = '{halden}')"
    )


def membership(slug: str, email: str) -> str:
    return sql(
        "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id "
        f"WHERE m.organization_id = '{org_id(slug)}' AND u.email = '{email}'"
    )


# --- cases --------------------------------------------------------------------------------


def setup() -> None:
    ensure_active("halden", HALDEN_MEMBER, f"{WHY}: undo the P11.5 drill's suspension")
    if "fake" not in ops("provider", "list", "--org", "halden"):
        fake_generation("halden", WHY)
    is_admin = sql(
        f"SELECT is_admin FROM memberships WHERE id = '{membership('veridian', VERIDIAN_ADMIN)}'"
    )
    if is_admin != "t":
        ops(
            "member",
            "admin",
            "--org",
            "veridian",
            "--email",
            VERIDIAN_ADMIN,
            "--grant",
            "--reason",
            WHY,
        )


def halden_ids(maya: httpx.Client) -> dict[str, str]:
    halden = org_id("halden")
    answer = maya.post("/api/v1/ask", json={"question": QUESTION}, headers=CSRF)
    assert answer.status_code == 200, answer.status_code
    body = answer.json()
    grant, document = sql(
        "SELECT id || ' ' || document_id FROM document_grants "
        f"WHERE organization_id = '{halden}' ORDER BY created_at LIMIT 1"
    ).split()
    ids = {
        "evidence_id": body["evidence"][0]["evidence_id"],
        "trace_id": body["trace_id"],
        "source_id": sql(
            f"SELECT id FROM sources WHERE organization_id = '{halden}' AND key = 'support'"
        ),
        "membership_id": membership("halden", HALDEN_MEMBER),
        "document_id": document,
        "grant_id": grant,
        "resource_id": str(uuid.uuid4()),
    }
    case(
        "Halden identifiers collected (an answered Ask of Maya's, its evidence, a grant)",
        body["status"] == "answered"
        and all(re.fullmatch(r"[0-9a-f-]{36}", v) for v in ids.values()),
        f"Ask {body['status']}, {len(body['evidence'])} evidence",
    )
    return ids


def anonymous(member_and_admin: list[tuple[str, str]]) -> None:
    refused = []
    with client() as c:
        for method, path in member_and_admin:
            response = call(c, method, path, random_ids())
            code = response.json().get("error", {}).get("code") if response.content else None
            refused.append(response.status_code == 401 and code == "authentication_required")
        scim = []
        for method, path in SCIM_ROUTES:
            body: dict[str, list[str]] | None = (
                {"schemas": []} if method in ("POST", "PUT", "PATCH") else None
            )
            response = c.request(method, "/api" + url(path, random_ids()), json=body)
            scim.append(response.status_code == 401)
    case(
        "anonymous: every member and administrator route answers 401",
        all(refused),
        f"{sum(refused)}/{len(refused)} routes",
    )
    case("SCIM without its token: 401", all(scim), f"{sum(scim)}/{len(scim)} routes")


def member_refused(
    maya: httpx.Client, admin_routes: list[tuple[str, str]], ids: dict[str, str], before: str
) -> None:
    same = []
    for method, path in admin_routes:
        own = call(maya, method, path, ids)
        missing = call(maya, method, path, random_ids())
        same.append(own.status_code == 403 and comparable(own) == comparable(missing))
    case(
        "a member gets one identical 403 from every administrator route",
        all(same),
        f"{sum(same)}/{len(same)} routes",
    )
    after = halden_state(org_id("halden"), ids["membership_id"])
    case("... and nothing of Halden's changed", after == before, after)


def cross_org(identified: list[tuple[str, str]], ids: dict[str, str], before: str) -> None:
    ines = sign_in(VERIDIAN_ADMIN)
    me = ines.get("/api/v1/me").json()
    differ = []
    for method, path in identified:
        foreign = call(ines, method, path, ids)
        missing = call(ines, method, path, random_ids())
        if not (foreign.status_code == 404 and comparable(foreign) == comparable(missing)):
            differ.append(f"{method} {path}: {foreign.status_code}/{missing.status_code}")
        if "{document_id}" in path:
            mixed = call(
                ines,
                method,
                path,
                {**ids, "membership_id": membership("veridian", VERIDIAN_MEMBER)},
            )
            if mixed.status_code != 404:
                differ.append(f"{method} {path} (own member): {mixed.status_code}")
    case(
        "Veridian administrator: Halden's real identifiers answer exactly like random ones",
        bool(me["capabilities"]) and me["organization"]["id"] == org_id("veridian") and not differ,
        f"{len(identified)} routes; " + ("; ".join(differ) or "all 404, identical bodies"),
    )
    after = halden_state(org_id("halden"), ids["membership_id"])
    case(
        "... and nothing of Halden's changed (grants, access, jobs, audit)", after == before, after
    )
    listed = ines.get("/api/v1/admin/access/members").text
    case(
        "Veridian administrator's member directory names no Halden member",
        "halden.example" not in listed and ids["membership_id"] not in listed,
    )


def admin_is_not_content(maya: httpx.Client, ids: dict[str, str]) -> None:
    lena = sign_in(HALDEN_ADMIN)
    theirs = lena.get(f"/api/v1/traces/{ids['trace_id']}").json()
    own = maya.get(f"/api/v1/traces/{ids['trace_id']}").json()
    case(
        "administrator sees another member's trace as operational data only (SEC-15)",
        theirs.get("view") == "operational" and "question" not in theirs and "answer" not in theirs,
        f"view {theirs.get('view')}",
    )
    case(
        "the asker reads their own trace, re-authorized",
        own.get("view") == "asker" and own.get("answer_hidden") is False,
        f"view {own.get('view')}",
    )


def everywhere(c: httpx.Client) -> dict[str, int]:
    """/v1/me through the round robin until both instances answered: {instance: status}."""
    statuses: dict[str, int] = {}
    for _ in range(8):
        response, where = served(lambda: c.get("/api/v1/me"), "/v1/me")
        for instance in where:
            statuses[instance] = response.status_code
        if len(statuses) == 2:
            break
    return statuses


def revocation(ids: dict[str, str]) -> None:
    lena = sign_in(HALDEN_ADMIN)
    maya = sign_in(HALDEN_MEMBER)
    target = f"/api/v1/admin/access/members/{ids['membership_id']}"
    alive = everywhere(maya)

    def change(status: str) -> int:
        version = lena.get(target).json()["version"]
        body = {"status": status, "version": version, "reason": f"{WHY}: revocation"}
        return lena.patch(target, json=body, headers=CSRF).status_code

    suspended = change("suspended")
    after = everywhere(maya)
    case(
        "Access change (suspend): the member's next request is 401 on both instances",
        alive == {"api1": 200, "api2": 200}
        and suspended == 200
        and after == {"api1": 401, "api2": 401},
        f"before {alive}, PATCH {suspended}, after {after}",
    )
    restored = change("active")
    still = everywhere(maya)
    again = everywhere(sign_in(HALDEN_MEMBER))
    case(
        "reactivation revives no session; a new sign-in works on both instances",
        restored == 200
        and still == {"api1": 401, "api2": 401}
        and again == {"api1": 200, "api2": 200},
        f"old session {still}, new {again}",
    )
    case("Maya is active again", member_status("halden", HALDEN_MEMBER) == "active")


def check_deployment() -> None:
    out = compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "ops",
        "check-deployment",
        "--api-pool-size",
        "3",
        check=False,
    )
    passed = len(re.findall(r"^PASS", out, re.M))
    failed = len(re.findall(r"^FAIL", out, re.M))
    case(
        "check-deployment (roles, RLS, privilege snapshot)",
        failed == 0 and passed == 7,
        f"{passed} PASS, {failed} FAIL",
    )


def requested_syncs() -> None:
    for slug, email in (("halden", HALDEN_ADMIN), ("veridian", VERIDIAN_ADMIN)):
        browser = sign_in(email)
        listed = browser.get("/api/v1/admin/sources").json()["sources"]
        source = next(s for s in listed if s["key"] == "drive")
        response = browser.post(f"/api/v1/admin/sources/{source['id']}/sync", headers=CSRF)
        job = response.json().get("id", "") if response.status_code in (200, 202) else ""

        def status(job: str = job) -> str:
            return sql(f"SELECT status FROM jobs WHERE id = '{job}'") if job else "none"

        wait_for(lambda: status() in ("succeeded", "failed"), 180, 1)
        case(
            f"requested sync ({slug}): the worker ran it",
            status() == "succeeded",
            f"HTTP {response.status_code}, job {status()}",
        )


def eval_gates() -> None:
    """The evaluation security gates inside the stack: an operator job on the private
    network, the golden set mounted read-only, reading as lattice_app (RLS) with the
    owner role only for persona lookups. Reports go to the container's /tmp."""
    app_url = f"postgresql+asyncpg://lattice_app:{env_file()['LATTICE_APP_PASSWORD']}@postgres:5432/lattice"
    common = [
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "-e",
        "LATTICE_DATABASE_URL",
        "-v",
        f"{ROOT}/evals/golden:/srv/lattice/evals/golden:ro",
        "ops",
        "eval",
    ]
    paths = [
        "--data",
        "/srv/lattice/data/synthetic",
        "--golden",
        "/srv/lattice/evals/golden",
        "--out",
        "/tmp/reports",  # noqa: S108 - the throwaway container's tmpfs
        "--quiet",
    ]
    for kind, extra in (
        ("retrieval", ["--embedding", "fake"]),
        ("answer", ["--generation", "fake", "--embedding", "fake"]),
    ):
        started = time.monotonic()
        out = compose(
            *common, kind, *extra, *paths, env={"LATTICE_DATABASE_URL": app_url}, check=False
        )
        line = next((row for row in out.splitlines() if row.startswith("security: ")), "")
        security = ast.literal_eval(line.removeprefix("security: ")) if line else None
        scope = next((row for row in out.splitlines() if row.startswith("items")), "no items")
        scope = scope.split(":")[0].split(";")[0]
        numbers = [v for v in (security or {}).values() if isinstance(v, int)]
        case(
            f"eval {kind} security gates inside the stack: every count 0",
            bool(numbers) and not any(numbers),
            f"{scope}; {len(numbers)} counts, {time.monotonic() - started:.0f} s: {security}",
        )


def main() -> int:
    started = time.monotonic()
    setup()
    swept = routes()
    identified = [r for r in swept if "{" in r[1]]
    admin_routes = [r for r in swept if r[1].startswith("/v1/admin/")]
    maya = sign_in(HALDEN_MEMBER)
    ids = halden_ids(maya)
    before = halden_state(org_id("halden"), ids["membership_id"])
    anonymous(swept)
    member_refused(maya, admin_routes, ids, before)
    cross_org(identified, ids, before)
    admin_is_not_content(maya, ids)
    revocation(ids)
    check_deployment()
    requested_syncs()
    eval_gates()
    ops(
        "member",
        "admin",
        "--org",
        "veridian",
        "--email",
        VERIDIAN_ADMIN,
        "--revoke",
        "--reason",
        f"{WHY}: done",
    )
    return finish(started)


if __name__ == "__main__":
    sys.exit(main())
