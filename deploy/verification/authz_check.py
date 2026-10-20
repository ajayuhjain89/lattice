"""The deployed authorization suite (plan §15.2, P11.7; expanded for Phase 12 in P12.8).

LOCAL VERIFICATION ONLY. Run by ``make prod-authz`` after
``make prod-up VERIFY=1 IDP=1 FAKE_DELAY=20`` and ``make prod-seed VERIFY=1 IDP=1``, and
within a day of a ``make prod-identity`` on the same reference database (its D-34 drill
leaves the Halden step-up this sweep needs a real identifier of; attempts are kept for a
day after they expire). Synthetic Halden and Veridian data only; personas sign in with
the verification overlay's development sign-in (real OIDC sign-in on the stack is
`make prod-identity`). Over HTTPS through Caddy, round robin over api1 and api2:

* the route sweep: the identified routes are read from every API router (a new route is
  swept without editing this file); every Phase 12 family must be among them -- setup,
  members and administrators, accounts and their external references, OIDC and SCIM
  administration, providers and BYOK, egress policy, D-34 step-ups, sources, imports and
  the source lifecycle. Anonymous callers get 401 everywhere, SCIM without its token 401;
  a member gets one identical 403 from every administrator route; a Veridian
  administrator gets, for every route taking an identifier (in its path or, for the
  account references, the grants and the provider changes, in its body), the same 404 for
  Halden's real identifier as for a random one -- one real identifier of every kind, or
  the sweep fails -- and nothing of Halden's changes;
* CSRF: every state-changing route refuses an administrator's request without the
  ``X-Lattice-Request`` proof, or marked cross-site, before anything changes;
* no secret leaves: no administrator or member read returns a deployment secret, a
  database password, a SCIM token or a stored provider key (a run-unique synthetic BYOK
  key is stored, every read is searched, the key is revoked again);
* administrator is not content (SEC-15): an administrator sees another member's trace as
  operational data only; the asker sees their own, re-authorized;
* revocation: an Access change (suspend) answers 401 on the next request on both
  instances; a suspended member cannot sign in; reactivation revives no session;
  a suspended administrator's session ends and the capability returns only with an
  operator's reinstatement; a suspended organization's sessions end and its members
  cannot sign in while another organization is unaffected; reactivation revives none;
* roles: ``check-deployment`` 8/8 and a requested sync per organization succeeds in the
  worker (``lattice_worker``);
* the ``eval retrieval`` and ``eval answer`` security gates, run inside the stack as an
  operator job against the API's role (``lattice_app``): every security count is 0.

    LATTICE_COMPOSE="docker compose …" uv run --project backend python \\
        deploy/verification/authz_check.py
"""

# ruff: noqa: S101, S608 - a verification driver: asserts, read-only queries over IDs the
# API just returned
import ast
import importlib
import pkgutil
import re
import secrets
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

import lattice.api.routes as route_modules

HALDEN_ADMIN = "lena.brandt@halden.example"
HALDEN_MEMBER = "maya.okafor@halden.example"
# Veridian's manifest has no administrator: the sweep gives its most privileged persona
# the capability (audited) and takes it back at the end.
VERIDIAN_ADMIN = "ines.carvalho@veridian.example"
VERIDIAN_MEMBER = "noor.haddad@veridian.example"
WHY = "P12.8 deployed authorization suite (synthetic data)"
# Sign-in and health are public (their refusals are proved by `make prod-identity`); SCIM
# is swept with its own token rule below.
PUBLIC = {
    ("POST", "/v1/auth/dev-login"),
    ("GET", "/v1/auth/oidc/{login_key}/start"),
    ("GET", "/v1/auth/oidc/{login_key}/callback"),
}
NOT_SWEPT = {"health", "scim"}
SCIM_ROUTES = [
    ("GET", "/scim/v2/ServiceProviderConfig"),
    ("GET", "/scim/v2/Users"),
    ("POST", "/scim/v2/Users"),
    ("GET", "/scim/v2/Users/{resource_id}"),
    ("PUT", "/scim/v2/Users/{resource_id}"),
    ("PATCH", "/scim/v2/Users/{resource_id}"),
    ("DELETE", "/scim/v2/Users/{resource_id}"),
]
# The route families Phase 12 made customer-operable: each must be swept (prefix match on
# the route, or the exact routes named).
FAMILIES: dict[str, tuple[str, ...]] = {
    "organization setup": ("/v1/admin/setup", "/v1/admin/organization"),
    "members and administrators": ("/v1/admin/access/members",),
    "accounts": ("/v1/admin/access/accounts",),
    "account external references": ("/v1/admin/access/account-references",),
    "OIDC administration": ("/v1/admin/identity/oidc",),
    "SCIM token administration": ("/v1/admin/identity/scim/tokens",),
    "providers and BYOK": ("/v1/admin/providers/{provider_id}/credential",),
    "egress policy": ("/v1/admin/providers/changes",),
    "D-34 step-up": ("/v1/admin/providers/step-ups",),
    "sources": ("/v1/admin/sources",),
    "imports and uploads": ("/v1/admin/sources/{source_id}/imports",),
    "source lifecycle": (
        "/v1/admin/sources/{source_id}/pause",
        "/v1/admin/sources/{source_id}/resume",
        "/v1/admin/sources/{source_id}/disconnect",
    ),
}
_VERSION = "2026-10-02T00:00:00Z"
_VERSIONED = {"version": _VERSION, "reason": "Probe"}
_SOURCE_VERSIONED = {"version": 1, "reason": "Probe"}
_REFERENCE = {"ref_kind": "ticketing_organization", "external_ref": "360001001"}
_SETTINGS_CHANGE = {"kind": "settings", "default_query_sensitivity": "restricted"}
# Valid bodies, so that a refusal is about authority, never the request's shape
# (the same as tests/security/test_route_inventory.py). "{name}" values are identifiers,
# filled in per call: a body identifier is swept like a path one.
BODIES: dict[tuple[str, str], Any] = {
    ("POST", "/v1/ask"): {"question": QUESTION},
    ("POST", "/v1/admin/access/members/{membership_id}/preview"): {"clearance": "confidential"},
    ("PATCH", "/v1/admin/access/members/{membership_id}"): {
        "clearance": "confidential",
        **_VERSIONED,
    },
    ("POST", "/v1/admin/access/members/{membership_id}/grants"): {
        "document_id": "{document_id}",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/grants/{grant_id}/revoke"): {"reason": "Probe"},
    ("PATCH", "/v1/admin/organization"): {"name": "Probe", **_VERSIONED},
    ("POST", "/v1/admin/access/members"): {
        "email": "probe@halden.example",
        "name": "Probe",
        "identifier": "probe",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/members/{membership_id}/administrator/preview"): {
        "administrator": True
    },
    ("POST", "/v1/admin/access/members/{membership_id}/administrator"): {
        "administrator": True,
        **_VERSIONED,
    },
    ("POST", "/v1/admin/access/members/{membership_id}/reinstate"): _VERSIONED,
    ("POST", "/v1/admin/access/accounts"): {"key": "probe", "name": "Probe", "reason": "Probe"},
    ("PATCH", "/v1/admin/access/accounts/{account_id}"): {"name": "Probe", **_VERSIONED},
    ("POST", "/v1/admin/access/accounts/{account_id}/archive"): _VERSIONED,
    ("POST", "/v1/admin/access/accounts/{account_id}/reactivate"): _VERSIONED,
    ("POST", "/v1/admin/access/account-references/preview"): {
        **_REFERENCE,
        "source_id": "{source_id}",
        "account_id": "{account_id}",
    },
    ("POST", "/v1/admin/access/account-references"): {
        **_REFERENCE,
        "source_id": "{source_id}",
        "account_id": "{account_id}",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/account-references/remap"): {
        **_REFERENCE,
        "source_id": "{source_id}",
        "current_account_id": "{account_id}",
        "account_id": "{account_id}",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/access/account-references/unmap"): {
        **_REFERENCE,
        "source_id": "{source_id}",
        "current_account_id": "{account_id}",
        "reason": "Probe",
    },
    ("POST", "/v1/admin/identity/oidc"): {
        "issuer": "https://idp.probe.example",
        "client_id": "probe",
        "reason": "Probe",
    },
    ("PATCH", "/v1/admin/identity/oidc/{connection_id}"): {"client_id": "probe", **_VERSIONED},
    ("PUT", "/v1/admin/identity/oidc/{connection_id}/client-secret"): {
        "client_secret": "probe-secret-value",
        **_VERSIONED,
    },
    ("POST", "/v1/admin/identity/oidc/{connection_id}/client-secret/remove"): _VERSIONED,
    ("POST", "/v1/admin/identity/oidc/{connection_id}/status/preview"): {"enabled": False},
    ("POST", "/v1/admin/identity/oidc/{connection_id}/status"): {"enabled": False, **_VERSIONED},
    ("POST", "/v1/admin/identity/oidc/{connection_id}/identity/preview"): {
        "subject_claim": "email"
    },
    ("POST", "/v1/admin/identity/oidc/{connection_id}/identity"): {
        "issuer": "https://idp.probe.example",
        "subject_claim": "sub",
        "proof_id": "{test_id}",
        "confirmation": "https://idp.probe.example",
        **_VERSIONED,
    },
    ("POST", "/v1/admin/identity/oidc/test-sign-in"): {},
    ("POST", "/v1/admin/identity/scim/tokens"): {"reason": "Probe"},
    ("POST", "/v1/admin/identity/scim/tokens/{token_id}/rotate"): {"reason": "Probe"},
    ("POST", "/v1/admin/identity/scim/tokens/{token_id}/revoke"): {"reason": "Probe"},
    ("POST", "/v1/admin/providers"): {"provider": "fake", "model": "fake-probe", "reason": "Probe"},
    ("POST", "/v1/admin/providers/changes/preview"): {
        "change": {"kind": "provider", "provider_id": "{provider_id}", "enabled": False}
    },
    ("POST", "/v1/admin/providers/changes"): {
        "change": _SETTINGS_CHANGE,
        "policy_version": 0,
        "reason": "Probe",
    },
    ("POST", "/v1/admin/providers/step-ups"): {
        "change": _SETTINGS_CHANGE,
        "policy_version": 0,
        "reason": "Probe",
        "confirmation": "Probe",
    },
    ("PUT", "/v1/admin/providers/{provider_id}/endpoint"): {
        "target_id": str(uuid.uuid4()),
        "policy_version": 0,
        "reason": "Probe",
    },
    ("PUT", "/v1/admin/providers/{provider_id}/credential"): {
        "api_key": "probe-api-key-value-0000",
        "data_use": "paid",
        "expected_credential_id": None,
        "reason": "Probe",
    },
    ("POST", "/v1/admin/providers/{provider_id}/credential/revoke"): {
        "expected_credential_id": str(uuid.uuid4()),
        "reason": "Probe",
    },
    ("POST", "/v1/admin/sources"): {
        "connector_type": "document_repository",
        "key": "probe-docs",
        "name": "Probe",
        "default_sensitivity": "internal",
        "authority": "working",
        "account_scoped": False,
        "reason": "Probe",
    },
    ("PATCH", "/v1/admin/sources/{source_id}"): {"name": "Probe", **_SOURCE_VERSIONED},
    ("POST", "/v1/admin/sources/{source_id}/changes/preview"): {
        "name": "Probe",
        **_SOURCE_VERSIONED,
    },
    ("POST", "/v1/admin/sources/{source_id}/pause"): _SOURCE_VERSIONED,
    ("POST", "/v1/admin/sources/{source_id}/resume"): _SOURCE_VERSIONED,
    ("POST", "/v1/admin/sources/{source_id}/disconnect"): {
        "confirm_key": "probe",
        **_SOURCE_VERSIONED,
    },
    # The upload's body is the file itself; an empty one is refused after authority.
}
# Routes whose only identifiers are in the body: swept across organizations like the
# identified ones (the remaining body identifiers are random in both calls).
BODY_IDENTIFIED = [
    ("POST", "/v1/admin/access/account-references/preview"),
    ("POST", "/v1/admin/access/account-references"),
    ("POST", "/v1/admin/access/account-references/remap"),
    ("POST", "/v1/admin/access/account-references/unmap"),
    ("POST", "/v1/admin/providers/changes/preview"),
]
QUERIES = {("GET", "/v1/admin/access/documents"): "?q=onboarding"}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def routes() -> list[tuple[str, str]]:
    """Every member and administrator route the API serves: every router module of
    ``lattice.api.routes`` (a new module is swept too), less the public sign-in."""
    found = set()
    for info in pkgutil.iter_modules(route_modules.__path__):
        if info.name in NOT_SWEPT:
            continue
        module = importlib.import_module(f"lattice.api.routes.{info.name}")
        for route in module.router.routes:
            if isinstance(route, APIRoute):
                found |= {(method, route.path) for method in route.methods or ()}
    return sorted(found - PUBLIC)


def parameters(swept: list[tuple[str, str]]) -> set[str]:
    names: set[str] = set()
    for method, path in swept:
        names |= set(re.findall(r"{(\w+)}", path))
        names |= set(re.findall(r'"{(\w+)}"', repr(BODIES.get((method, path)))))
        names |= set(re.findall(r"'{(\w+)}'", repr(BODIES.get((method, path)))))
    return names


def url(path: str, ids: dict[str, str]) -> str:
    for name, value in ids.items():
        path = path.replace("{" + name + "}", value)
    return path


def fill(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: fill(v, ids) for k, v in value.items()}
    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        return ids.get(value[1:-1], value)
    return value


ID_NAMES: set[str] = set()


def random_ids() -> dict[str, str]:
    return {name: str(uuid.uuid4()) for name in ID_NAMES | {"resource_id"}}


def call(
    c: httpx.Client,
    method: str,
    path: str,
    ids: dict[str, str],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    body = fill(BODIES.get((method, path)), ids)
    target = "/api" + url(path, ids) + QUERIES.get((method, path), "")
    for _ in range(3):
        response = c.request(
            method, target, json=body, headers=CSRF if headers is None else headers
        )
        if response.status_code != 429:  # the per-administrator write limit: wait it out
            return response
        time.sleep(int(response.headers.get("retry-after", "5")) + 1)
    return response


def comparable(response: httpx.Response) -> tuple[int, Any]:
    try:
        body = response.json()
    except ValueError:
        return response.status_code, response.text
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body["error"].pop("request_id", None)
    return response.status_code, body


def halden_state(halden: str, maya: str) -> str:
    """What a foreign or refused request must not change: access, identity, providers,
    egress, step-ups, sources, imports, accounts, mappings, jobs and the audit trail."""
    o = f"organization_id = '{halden}'"
    parts = [
        f"(SELECT count(*) FROM document_grants WHERE {o})",
        f"(SELECT clearance || status || is_admin FROM memberships WHERE id = '{maya}')",
        f"(SELECT count(*) FROM memberships WHERE {o} AND is_admin)",
        f"(SELECT name || status FROM organizations WHERE id = '{halden}')",
        f"(SELECT count(*) FROM jobs WHERE {o})",
        f"(SELECT count(*) FROM audit_events WHERE {o})",
        f"(SELECT count(*) FROM scim_tokens WHERE {o} AND revoked_at IS NULL)",
        f"(SELECT string_agg(client_id || status, ',' ORDER BY id) FROM oidc_connections "
        f"WHERE {o})",
        f"(SELECT count(*) FROM provider_credentials WHERE {o})",
        f"(SELECT string_agg(enabled::text || coalesce(approved_at::text, '-'), ',' ORDER BY id) "
        f"FROM model_providers WHERE {o})",
        f"(SELECT coalesce(max(version), 0) FROM egress_policy_versions WHERE {o})",
        f"(SELECT count(*) FROM step_up_grants WHERE {o})",
        f"(SELECT count(*) FROM source_imports WHERE {o})",
        f"(SELECT string_agg(status || ':' || version, ',' ORDER BY id) FROM sources WHERE {o})",
        f"(SELECT string_agg(name || coalesce(archived_at::text, '-'), ',' ORDER BY id) "
        f"FROM accounts WHERE {o})",
        f"(SELECT count(*) || ':' || count(account_id) FROM account_external_refs WHERE {o})",
    ]
    return sql("SELECT " + " || '|' || ".join(f"coalesce({p}::text, '∅')" for p in parts))


def membership(slug: str, email: str) -> str:
    return sql(
        "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id "
        f"WHERE m.organization_id = '{org_id(slug)}' AND u.email = '{email}'"
    )


def newest(table: str, org: str, where: str = "true") -> str:
    return sql(
        f"SELECT id FROM {table} WHERE organization_id = '{org}' AND {where} "
        "ORDER BY created_at DESC LIMIT 1"
    )


def family_routes(swept: list[tuple[str, str]], prefixes: tuple[str, ...]) -> list[str]:
    return [f"{m} {p}" for m, p in swept if any(p.startswith(prefix) for prefix in prefixes)]


# --- cases --------------------------------------------------------------------------------


def setup() -> None:
    ensure_active("halden", HALDEN_MEMBER, f"{WHY}: undo the P11.5 drill's suspension")
    # Configured and enabled every run: other drills on this database (P11.7, P12.5)
    # leave Halden's generation providers disabled.
    fake_generation("halden", WHY)
    if sql("SELECT status FROM organizations WHERE slug = 'veridian'") != "active":
        ops("org", "reactivate", "--org", "veridian", "--reason", f"{WHY}: an earlier run")
    ensure_active("veridian", VERIDIAN_ADMIN, f"{WHY}: an earlier run")
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


def coverage(swept: list[tuple[str, str]]) -> None:
    missing = {name: family_routes(swept, prefixes) for name, prefixes in FAMILIES.items()}
    empty = [name for name, found in missing.items() if not found]
    detail = ", ".join(f"{name} {len(found)}" for name, found in missing.items())
    case(
        "the sweep covers every Phase 12 route family",
        not empty,
        f"{len(swept)} routes; {detail}" + (f"; MISSING {empty}" if empty else ""),
    )


def halden_ids(maya: httpx.Client, lena: httpx.Client) -> dict[str, str]:
    halden = org_id("halden")
    answer = maya.post("/api/v1/ask", json={"question": QUESTION}, headers=CSRF)
    assert answer.status_code == 200, answer.status_code
    body = answer.json()
    grant, document = sql(
        "SELECT id || ' ' || document_id FROM document_grants "
        f"WHERE organization_id = '{halden}' ORDER BY created_at LIMIT 1"
    ).split()
    # A test sign-in of Lena's, started through the product (no session, no change).
    started = lena.post("/api/v1/admin/identity/oidc/test-sign-in", json={}, headers=CSRF)
    ids = {
        "evidence_id": body["evidence"][0]["evidence_id"],
        "trace_id": body["trace_id"],
        "source_id": sql(
            f"SELECT id FROM sources WHERE organization_id = '{halden}' AND key = 'support'"
        ),
        "membership_id": membership("halden", HALDEN_MEMBER),
        "document_id": document,
        "grant_id": grant,
        "account_id": newest("accounts", halden),
        "connection_id": newest("oidc_connections", halden, "status = 'active'"),
        "token_id": newest("scim_tokens", halden),
        "provider_id": newest("model_providers", halden, "provider = 'fake'"),
        "test_id": started.json().get("test_id", "") if started.status_code == 201 else "",
        "step_up_id": newest("oidc_login_attempts", halden, "purpose = 'step_up'"),
        "resource_id": str(uuid.uuid4()),
    }
    absent = sorted(n for n in ID_NAMES if not _UUID.fullmatch(ids.get(n, "")))
    case(
        "a real Halden identifier of every kind the routes take (none swept vacuously)",
        body["status"] == "answered" and not absent,
        f"Ask {body['status']}; {len(ID_NAMES)} kinds"
        + (f"; MISSING {absent} (test sign-in HTTP {started.status_code})" if absent else ""),
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
        # A path identifier that is not the caller's is a 404; one in the body is a
        # validation issue for some routes (422 ``unknown_source``) -- either way the
        # answer must be exactly the random identifier's.
        expected = (404, 422) if (method, path) in BODY_IDENTIFIED else (404,)
        if not (foreign.status_code in expected and comparable(foreign) == comparable(missing)):
            differ.append(f"{method} {path}: {foreign.status_code}/{missing.status_code}")
        if "{document_id}" in path or "{document_id}" in repr(BODIES.get((method, path))):
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
        f"{len(identified)} routes; "
        + ("; ".join(differ) or "path identifiers 404, body ones 404/422, identical bodies"),
    )
    after = halden_state(org_id("halden"), ids["membership_id"])
    case(
        "... and nothing of Halden's changed (access, identity, providers, egress, step-ups, "
        "sources, imports, accounts, mappings, jobs, audit)",
        after == before,
        after,
    )
    listed = {
        path: ines.get(f"/api{path}").text
        for path in (
            "/v1/admin/access/members",
            "/v1/admin/access/accounts",
            "/v1/admin/access/account-references/unmapped",
            "/v1/admin/sources",
            "/v1/admin/identity",
            "/v1/admin/providers",
            "/v1/admin/setup",
            "/v1/admin/audit-events",
            "/v1/admin/activity",
        )
    }
    leaked = [
        path
        for path, text in listed.items()
        if "halden.example" in text or any(v in text for k, v in ids.items() if k != "resource_id")
    ]
    case(
        "Veridian administrator's lists name nothing of Halden's (members, accounts, "
        "references, sources, identity, providers, setup, audit, activity)",
        not leaked,
        ", ".join(leaked) or f"{len(listed)} lists",
    )


def csrf(lena: httpx.Client, swept: list[tuple[str, str]], ids: dict[str, str]) -> None:
    """Every state-changing route, as Halden's administrator with random identifiers,
    without the CSRF proof and with it but marked cross-site: refused before anything."""
    before = halden_state(org_id("halden"), ids["membership_id"])
    changing = [(m, p) for m, p in swept if m in ("POST", "PUT", "PATCH", "DELETE")]
    refused = []
    for headers in ({}, {**CSRF, "Sec-Fetch-Site": "cross-site"}):
        for method, path in changing:
            response = call(lena, method, path, random_ids(), headers=headers)
            code = response.json().get("error", {}).get("code") if response.content else None
            refused.append(response.status_code == 403 and code == "csrf_rejected")
    after = halden_state(org_id("halden"), ids["membership_id"])
    case(
        "CSRF: every state-changing route refuses a request without the proof or cross-site",
        all(refused) and after == before,
        f"{sum(refused)}/{len(refused)} requests ({len(changing)} routes x 2); "
        f"Halden {'unchanged' if after == before else 'CHANGED'}",
    )


def no_secrets(lena: httpx.Client, maya: httpx.Client, swept: list[tuple[str, str]]) -> None:
    """Store a run-unique synthetic BYOK key, read every route a member or administrator
    can GET, and search each answer for it, the deployment's secrets and token shapes."""
    providers = lena.get("/api/v1/admin/providers").json()
    record = next((p for p in providers["providers"] if p["provider"] == "gemini"), None)
    if record is None:  # disabled and unapproved: nothing is ever sent to it
        created = lena.post(
            "/api/v1/admin/providers",
            json={
                "provider": "gemini",
                "model": "gemini-3.5-flash-lite",
                "training_opt_out": True,
                "reason": f"{WHY}: BYOK exposure probe",
            },
            headers=CSRF,
        ).json()
        record = next(p for p in created["providers"] if p["provider"] == "gemini")
    key = f"lattice-authz-probe-{secrets.token_hex(16)}"
    stored = lena.put(
        f"/api/v1/admin/providers/{record['id']}/credential",
        json={
            "api_key": key,
            "data_use": "unpaid",
            "expected_credential_id": record["credential"]["id"],
            "reason": f"{WHY}: BYOK exposure probe",
        },
        headers=CSRF,
    )
    halden = org_id("halden")
    ids = {
        "evidence_id": "",
        "trace_id": newest("query_traces", halden),
        "source_id": sql(
            f"SELECT id FROM sources WHERE organization_id = '{halden}' AND key = 'support'"
        ),
        "membership_id": membership("halden", HALDEN_MEMBER),
        "account_id": newest("accounts", halden),
        "step_up_id": newest("oidc_login_attempts", halden, "purpose = 'step_up'"),
    }
    env = env_file()
    secret_values = [
        v
        for k, v in env.items()
        if k.endswith(("PASSWORD", "SECRET", "SECRET_NEXT", "TOKEN")) and len(v) >= 12
    ]
    secret_values += [part.split(":", 1)[1] for part in env["LATTICE_SECRET_KEYS"].split(",")]
    reads = [(m, p) for m, p in swept if m == "GET" and "{evidence_id}" not in p]
    found = []
    for who in (lena, maya):
        for method, path in reads:
            if "{" in url(path, ids):
                continue
            text = call(who, method, path, ids).text
            if key in text or any(v in text for v in secret_values):
                found.append(path)
            if re.search(r"lsc_[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY", text):
                found.append(f"{path} (token or key shape)")
    current = lena.get("/api/v1/admin/providers").json()
    credential = next(p for p in current["providers"] if p["id"] == record["id"])["credential"]
    revoked = lena.post(
        f"/api/v1/admin/providers/{record['id']}/credential/revoke",
        json={"expected_credential_id": credential["id"], "reason": f"{WHY}: probe done"},
        headers=CSRF,
    )
    case(
        "no read returns a stored BYOK key, a deployment secret, a SCIM token or a private key",
        stored.status_code == 200 and not found and revoked.status_code == 200,
        f"key stored {stored.status_code}, {len(reads)} reads x 2 people, revoked "
        f"{revoked.status_code}" + (f"; FOUND in {found}" if found else ""),
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


def everywhere(c: httpx.Client, path: str = "/v1/me") -> dict[str, int]:
    """``path`` through the round robin until both instances answered: {instance: status}."""
    statuses: dict[str, int] = {}
    for _ in range(8):
        response, where = served(lambda: c.get(f"/api{path}"), path)
        for instance in where:
            statuses[instance] = response.status_code
        if len(statuses) == 2:
            break
    return statuses


def dev_sign_in_status(email: str) -> int:
    with client() as c:
        response = c.post("/api/v1/auth/dev-login", json={"subject": email}, headers=CSRF)
        if response.status_code == 429:
            time.sleep(int(response.headers["retry-after"]) + 1)
            response = c.post("/api/v1/auth/dev-login", json={"subject": email}, headers=CSRF)
        return response.status_code


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
    refused = dev_sign_in_status(HALDEN_MEMBER)
    case("a suspended member cannot sign in", refused != 200, f"HTTP {refused}")
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


def suspended_administrator(admin_routes: list[tuple[str, str]]) -> None:
    """The operator suspends Veridian's administrator: the session ends on both instances
    and every administrator route answers 401; the operator's reinstatement restores the
    capability for a new sign-in only."""
    ines = sign_in(VERIDIAN_ADMIN)
    alive = everywhere(ines, "/v1/admin/setup")
    ops("member", "suspend", "--org", "veridian", "--email", VERIDIAN_ADMIN, "--reason", WHY)
    after = everywhere(ines, "/v1/admin/setup")
    reads = [(m, p) for m, p in admin_routes if m == "GET"]
    refused = [call(ines, m, p, random_ids()).status_code == 401 for m, p in reads]
    signed = dev_sign_in_status(VERIDIAN_ADMIN)
    ops("member", "reinstate", "--org", "veridian", "--email", VERIDIAN_ADMIN, "--reason", WHY)
    old = everywhere(ines, "/v1/admin/setup")
    new = everywhere(sign_in(VERIDIAN_ADMIN), "/v1/admin/setup")
    case(
        "a suspended administrator: session ends, every administrator read 401, no sign-in; "
        "the operator's reinstatement serves a new session only",
        alive == {"api1": 200, "api2": 200}
        and after == {"api1": 401, "api2": 401}
        and all(refused)
        and signed != 200
        and old == {"api1": 401, "api2": 401}
        and new == {"api1": 200, "api2": 200},
        f"before {alive}, after {after}, {sum(refused)}/{len(refused)} reads 401, sign-in "
        f"{signed}, old session {old}, new {new}",
    )


def suspended_organization(swept: list[tuple[str, str]]) -> None:
    """The operator suspends Veridian: its sessions end, its members cannot sign in, every
    route refuses them; Halden is unaffected. Reactivation revives no session."""
    ines = sign_in(VERIDIAN_ADMIN)
    noor = sign_in(VERIDIAN_MEMBER)
    maya = sign_in(HALDEN_MEMBER)
    ops("org", "suspend", "--org", "veridian", "--reason", WHY)
    try:
        refused = [call(ines, m, p, random_ids()).status_code == 401 for m, p in swept]
        member = everywhere(noor)
        signs = (dev_sign_in_status(VERIDIAN_ADMIN), dev_sign_in_status(VERIDIAN_MEMBER))
        halden = everywhere(maya)
    finally:
        ops("org", "reactivate", "--org", "veridian", "--reason", f"{WHY}: done")
    old = everywhere(ines)
    new = everywhere(sign_in(VERIDIAN_ADMIN))
    case(
        "a suspended organization: every route 401 to its administrator, its member's session "
        "ended, no sign-in, another organization unaffected; reactivation revives no session",
        all(refused)
        and member == {"api1": 401, "api2": 401}
        and 200 not in signs
        and halden == {"api1": 200, "api2": 200}
        and old == {"api1": 401, "api2": 401}
        and new == {"api1": 200, "api2": 200},
        f"{sum(refused)}/{len(refused)} routes 401, member {member}, sign-in {signs}, "
        f"Halden {halden}, old {old}, new {new}",
    )


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
        failed == 0 and passed == 8,  # 7 in P11.7; the secret-keys check since P12.1
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
    ID_NAMES.update(parameters(swept))
    identified = [r for r in swept if "{" in r[1]] + BODY_IDENTIFIED
    admin_routes = [r for r in swept if r[1].startswith("/v1/admin/")]
    coverage(swept)
    maya = sign_in(HALDEN_MEMBER)
    lena = sign_in(HALDEN_ADMIN)
    ids = halden_ids(maya, lena)
    before = halden_state(org_id("halden"), ids["membership_id"])
    anonymous(swept)
    member_refused(maya, admin_routes, ids, before)
    cross_org(identified, ids, before)
    csrf(lena, swept, ids)
    no_secrets(lena, maya, swept)
    admin_is_not_content(maya, ids)
    revocation(ids)
    suspended_administrator(admin_routes)
    suspended_organization(swept)
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
