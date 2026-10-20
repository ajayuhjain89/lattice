# ruff: noqa: S101, S603, S608 - a verification drill: asserts, the operator's own commands,
# and read-only queries over IDs the API just returned
"""End-to-end organization onboarding against the reference stack (Phase 12, P12.7;
phase-12-plan §20). Run by ``make prod-onboard`` after ``make prod-up IDP=1``.

LOCAL VERIFICATION ONLY. The stack runs in production mode (LATTICE_ENV=production: OIDC
sign-in only, ``__Host-`` cookies, no fake model, no seed, no development sign-in), two API
instances and two web instances behind Caddy, the worker, PostgreSQL, the shared
``imports`` volume and the verification identity provider (deploy/verification/idp.py).
This script plays three people:

* the **operator** runs the audited commands in the ``ops`` container: ``lattice org
  create`` (the client secret on stdin, sealed in the vault), ``lattice outbound allow``
  for the provider's browser-facing origin, ``lattice sync`` with an operator's sources
  file;
* the **administrators and members** are browsers (httpx, one cookie jar each) on
  https://localhost:8443, signing in through the provider's authorization endpoint;
* the **identity provider** is told which clients exist (its admin endpoint) and approves
  whoever the browser names.

Every organization is new and run-unique (``wrenfield-<run>``, ``corvane-<run>``, each
with its own provider client), so a rerun depends on nothing a previous run or another
drill left, and the stack's other organizations are untouched. Nothing is seeded: people,
accounts, providers and sources are created through the product's own API, and the
exports (data/synthetic/onboarding) are uploaded through the ingress's dedicated upload
route. No model is called: the Ask runs evidence-only, the way a production organization
without a generation provider does; the provider steps configure, approve (D-34 step-up)
and withdraw a provider that never receives a request.

Every case prints PASS/FAIL; the exit status is 1 if any failed. No secret is printed.
"""

import io
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import time
import uuid
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stack import (  # the shared stack plumbing, next to this file
    BASE,
    COMPOSE,
    COMPOSE_ENV,
    CSRF,
    ROOT,
    case,
    compose,
    env_file,
    finish,
    sql,
    wait_for,
)

IDP = "https://localhost:9443"
ISSUER = "https://idp:9443"
DATA = ROOT / "data/synthetic/onboarding"
RUN = time.strftime("%m%d%H%M%S")
REASON = "P12.7 onboarding drill (synthetic)"
FAILED = "/sign-in?error=sso_failed"
ENV = env_file()
IDP_ADMIN = {"Authorization": f"Bearer {ENV['VERIFY_IDP_ADMIN_TOKEN']}"}
# The identity drill's client stays registered (it resets the provider itself).
VERIFY_CLIENT = {"lattice-verify": [ENV["VERIFY_OIDC_CLIENT_SECRET"]]}
QUESTION = "What restored the Quillon telemetry feed for Ashgrove?"
# Present only in content Sam and the administrators may not read, and names an
# administrator's operational views must never carry (C9).
PROTECTED = ("Quillon", "4.2.7", "acct-ashgrove-war-room", "discount-guardrails", "Larch Street")
SMALL_IMPORTS = ROOT / "deploy/verification/compose.small-imports.yaml"
MIB = 1024 * 1024


def _tls() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(ROOT / "deploy/.reference-ca.crt"))
    context.load_verify_locations(cafile=str(ROOT / "deploy/.verification/ca.crt"))
    return context


TLS = _tls()


# --- the organizations -------------------------------------------------------------------


class Org:
    def __init__(self, label: str, admin: str, admin_email: str) -> None:
        self.slug = f"{label}-{RUN}"
        self.name = f"{label.title()} {RUN} (synthetic)"
        self.client_id = f"onboard-{RUN}-{label}"
        self.secret = secrets.token_urlsafe(24)
        self.admin_subject = admin
        self.admin_email = admin_email
        self.id = ""
        self.login_key = ""


WRENFIELD = Org("wrenfield", "wrn-ada", "ada.quist@wrenfield.example")
CORVANE = Org("corvane", "crv-noor", "noor.amari@corvane.example")


def idp_clients(*orgs: Org) -> None:
    clients: dict[str, list[str] | None] = {**VERIFY_CLIENT}
    clients.update({o.client_id: [o.secret] for o in orgs})
    response = httpx.put(
        f"{IDP}/admin/state",
        json={"clients": clients, "offsets": {}},
        headers=IDP_ADMIN,
        verify=TLS,
        timeout=10,
    )
    response.raise_for_status()


def idp_offsets(offsets: dict[str, int], *orgs: Org) -> None:
    clients: dict[str, list[str] | None] = {**VERIFY_CLIENT}
    clients.update({o.client_id: [o.secret] for o in orgs})
    httpx.put(
        f"{IDP}/admin/state",
        json={"clients": clients, "offsets": offsets},
        headers=IDP_ADMIN,
        verify=TLS,
        timeout=10,
    ).raise_for_status()


def operator(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """``lattice …`` in the ops container (production mode), as the platform operator."""
    return subprocess.run(
        [*COMPOSE, "run", "--rm", "--no-deps", "-T", "ops", *args],
        cwd=ROOT,
        env={**os.environ, **COMPOSE_ENV},
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def org_create(org: Org, **changes: str) -> subprocess.CompletedProcess[str]:
    values = {
        "--slug": org.slug,
        "--name": org.name,
        "--issuer": ISSUER,
        "--client-id": org.client_id,
        "--first-admin-email": org.admin_email,
        "--first-admin-identifier": org.admin_subject,
        "--reason": REASON,
        **changes,
    }
    argv = [part for pair in values.items() for part in pair]
    return operator(
        "org",
        "create",
        *argv,
        "--client-secret-stdin",
        "--register-private-issuer",
        stdin=org.secret,
    )


def last(stderr: str) -> str:
    """The command's own last line (compose's container progress lines dropped)."""
    lines = [line for line in stderr.splitlines() if "Container " not in line and line.strip()]
    return lines[-1][-200:] if lines else ""


def audit_count(org: Org) -> int:
    return int(sql(f"SELECT count(*) FROM audit_events WHERE organization_id = '{org.id}'"))


# --- the browser ---------------------------------------------------------------------------


class Browser:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=BASE, verify=TLS, timeout=200, follow_redirects=False)

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.client.get(f"/api{path}", **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        headers = {**CSRF, **kwargs.pop("headers", {})}
        return self.client.post(f"/api{path}", headers=headers, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.client.patch(f"/api{path}", headers=CSRF, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.client.put(f"/api{path}", headers=CSRF, **kwargs)

    @property
    def session(self) -> str | None:
        return self.client.cookies.get("__Host-lattice_session")

    def sign_in(self, org: Org, subject: str) -> str:
        """The authorization-code flow at the organization's sign-in URL: the final
        redirect (/ask, or /sign-in?error=…)."""
        for attempt in range(2):
            start = self.get(f"/v1/auth/oidc/{org.login_key}/start")
            assert start.status_code == 303, start.status_code
            location = start.headers["location"]
            if not location.startswith(IDP):
                return str(location)
            approve = self.client.get(f"{location}&persona={subject}")
            assert approve.status_code == 302, approve.status_code
            callback = approve.headers["location"]
            assert callback.startswith(f"{BASE}/api/"), "callback leaves the public origin"
            done = self.client.get(callback.removeprefix(BASE))
            assert done.status_code == 303, done.status_code
            location = str(done.headers["location"])
            if "rate_limited" not in location or attempt:
                return location
            time.sleep(61)  # the pre-authentication limit (per client, per minute)
        return location

    def me_on_both(self) -> set[int]:
        """/v1/me several times (round robin over api1 and api2): the statuses seen."""
        return {self.get("/v1/me").status_code for _ in range(4)}


def setup_record(admin: Browser) -> dict[str, dict[str, Any]]:
    response = admin.get("/v1/admin/setup")
    assert response.status_code == 200, response.status_code
    return {c["key"]: c for c in response.json()["categories"]}


def setup_is(admin: Browser, expected: dict[str, tuple[str, str, str | None]]) -> tuple[bool, str]:
    record = setup_record(admin)
    got = {
        key: (
            record[key]["state"],
            record[key]["reason"],
            None if record[key]["action"] is None else record[key]["action"]["href"],
        )
        for key in expected
    }
    differs = {k: v for k, v in got.items() if v != expected[k]}
    return not differs, json.dumps(differs) if differs else ""


def clean(text: str, *also: str) -> bool:
    return not any(word in text for word in (*PROTECTED, *also))


# --- exports -------------------------------------------------------------------------------


def zipped(folder: Path, *, extra: dict[str, bytes] | None = None, skip: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            name = path.relative_to(folder).as_posix()
            if path.is_file() and name != skip:
                archive.write(path, f"export/{name}")
        for name, data in (extra or {}).items():
            archive.writestr(f"export/{name}", data)
    return buffer.getvalue()


EXPORTS: dict[str, Callable[[], tuple[bytes, str]]] = {
    "handbook": lambda: (zipped(DATA / "documents"), "application/zip"),
    "support": lambda: ((DATA / "tickets/tickets.jsonl").read_bytes(), "application/x-ndjson"),
    "slack": lambda: (zipped(DATA / "slack"), "application/zip"),
}
SOURCES: dict[str, dict[str, Any]] = {
    "handbook": {"connector_type": "document_repository", "authority": "working"},
    "support": {"connector_type": "ticketing", "authority": "working", "account_scoped": True},
    "slack": {"connector_type": "chat_export", "authority": "conversational"},
}


def upload(
    admin: Browser, source_id: str, body: bytes | Iterator[bytes], kind: str
) -> httpx.Response:
    return admin.post(
        f"/v1/admin/sources/{source_id}/imports",
        content=body,
        headers={"Content-Type": kind},
    )


def record(admin: Browser, source_id: str) -> dict[str, Any]:
    response = admin.get(f"/v1/admin/sources/{source_id}")
    assert response.status_code == 200, response.status_code
    found: dict[str, Any] = response.json()
    return found


def settle(
    admin: Browser, source_id: str, job_id: str | None = None, timeout: float = 180
) -> dict[str, Any]:
    """Wait for the worker: the source's latest job (or the given one) finished."""

    def done() -> bool:
        job = record(admin, source_id)["job"]
        return (
            job is not None
            and (job_id is None or job["id"] == job_id)
            and job["status"]
            in (
                "succeeded",
                "failed",
            )
        )

    assert wait_for(done, timeout, step=1), f"job of {source_id} did not finish"
    return record(admin, source_id)


def sync(admin: Browser, source_id: str) -> dict[str, Any]:
    requested = admin.post(f"/v1/admin/sources/{source_id}/sync")
    assert requested.status_code == 202, (requested.status_code, requested.text[:300])
    return settle(admin, source_id, requested.json()["id"])


def ask(person: Browser, question: str = QUESTION) -> dict[str, Any]:
    response = person.post("/v1/ask", json={"question": question})
    assert response.status_code == 200, response.status_code
    found: dict[str, Any] = response.json()
    return found


def import_files(org: Org) -> set[str]:
    """Import directories present for the organization in the shared volume (api1)."""
    script = (
        "import os, sys; p = '/srv/lattice/imports/' + sys.argv[1]; "
        "print('\\n'.join(sorted(os.listdir(p))) if os.path.isdir(p) else '')"
    )
    out = compose("exec", "-T", "api1", "python", "-c", script, org.id)
    return {line for line in out.splitlines() if line}


def escaped_anywhere(name: str) -> bool:
    script = (
        "import os, sys; print(any(sys.argv[1] in f for _, _, fs in "
        "os.walk('/srv/lattice') for f in fs))"
    )
    return compose("exec", "-T", "api1", "python", "-c", script, name).strip() == "True"


def latest_import(source_id: str) -> tuple[str, str]:
    row = sql(
        "SELECT status || '|' || coalesce(rejected_code, '') FROM source_imports "
        f"WHERE source_id = '{source_id}' ORDER BY created_at DESC LIMIT 1"
    )
    status, _, code = row.partition("|")
    return status, code


def interrupted_upload(admin: Browser, source_id: str, declared: int, sent: int) -> None:
    """A browser that goes away mid-upload: headers for ``declared`` bytes, ``sent`` of
    them, then the connection closes. Through Caddy, over TLS."""
    raw = socket.create_connection(("localhost", 8443), timeout=30)
    tls = TLS.wrap_socket(raw, server_hostname="localhost")
    head = (
        f"POST /api/v1/admin/sources/{source_id}/imports HTTP/1.1\r\n"
        "Host: localhost:8443\r\nContent-Type: application/zip\r\n"
        f"Content-Length: {declared}\r\nX-Lattice-Request: 1\r\n"
        f"Cookie: __Host-lattice_session={admin.session}\r\n\r\n"
    )
    tls.sendall(head.encode() + b"PK\x03\x04" + os.urandom(sent - 4))
    time.sleep(1)
    tls.close()


def roll_api(*extra: str) -> None:
    """Recreate api1 then api2 (readiness-gated) with the stack's configuration plus the
    given compose files -- or exactly the stack's own when none is given."""
    files = [part for f in extra for part in ("-f", f)]
    for instance in ("api1", "api2"):
        subprocess.run(
            [*COMPOSE, *files, "up", "-d", "--no-deps", "--wait", instance],
            cwd=ROOT,
            env={**os.environ, **COMPOSE_ENV},
            capture_output=True,
            text=True,
            check=True,
        )


def _sum(text: str, name: str, labels: dict[str, str]) -> float:
    total = 0.0
    for line in text.splitlines():
        if line.startswith(name + "{") and all(f'{k}="{v}"' in line for k, v in labels.items()):
            total += float(line.rsplit(" ", 1)[1])
    return total


def metric_total(name: str, **labels: str) -> float:
    """A counter summed over both API instances (their metrics, published by the overlay)."""
    return sum(
        _sum(httpx.get(f"http://127.0.0.1:{port}/metrics", timeout=5).text, name, labels)
        for port in (19464, 19465)
    )


def worker_metric(name: str, **labels: str) -> float:
    script = (
        "import urllib.request; "
        "print(urllib.request.urlopen('http://127.0.0.1:9465/metrics').read().decode())"
    )
    return _sum(compose("exec", "-T", "worker", "python", "-c", script), name, labels)


# --- the journey ---------------------------------------------------------------------------


class Journey:
    def __init__(self) -> None:
        self.admin = Browser()
        self.riley = Browser()
        self.sam = Browser()
        self.noor = Browser()
        self.sources: dict[str, dict[str, Any]] = {}
        self.accounts: dict[str, dict[str, Any]] = {}
        self.member_ids: dict[str, str] = {}
        self.scim_token = ""
        self.evidence_id = ""
        self.trace_id = ""

    # 1 ------------------------------------------------------------------------------------
    def bootstrap(self) -> None:
        idp_clients(WRENFIELD, CORVANE)
        made = org_create(WRENFIELD)
        WRENFIELD.id = sql(f"SELECT id FROM organizations WHERE slug = '{WRENFIELD.slug}'")
        key = re.search(r"/sso/([A-Za-z0-9_-]+)", made.stdout)
        case(
            "bootstrap: org create (organization, connection, first admin, audit)",
            made.returncode == 0 and "organization created" in made.stdout and bool(key),
            last(made.stderr),
        )
        assert key
        WRENFIELD.login_key = key.group(1)
        case("bootstrap: no secret printed", WRENFIELD.secret not in made.stdout + made.stderr)
        events = sql(
            "SELECT string_agg(action, ',' ORDER BY action) FROM audit_events "
            f"WHERE organization_id = '{WRENFIELD.id}'"
        )
        case(
            "bootstrap: audited as the operator",
            events == "access.member_changed,organization.created,outbound.target_added,"
            "sso.connection_created",
            events,
        )
        before = audit_count(WRENFIELD)
        again = org_create(WRENFIELD)
        case(
            "bootstrap: identical retry writes nothing",
            again.returncode == 0
            and "already exists, unchanged" in again.stdout
            and audit_count(WRENFIELD) == before,
        )
        differing = org_create(WRENFIELD, **{"--name": "Someone else"})
        case(
            "bootstrap: conflicting retry refused, nothing changed",
            differing.returncode == 2
            and "differs: name" in differing.stderr
            and audit_count(WRENFIELD) == before,
            last(differing.stderr),
        )
        stolen = org_create(CORVANE, **{"--client-id": WRENFIELD.client_id})
        case(
            "bootstrap: another organization's provider client refused, nothing created",
            stolen.returncode == 2
            and sql(f"SELECT count(*) FROM organizations WHERE slug = '{CORVANE.slug}'") == "0",
            last(stolen.stderr),
        )
        # The verification provider's discovery names its browser-facing authorization
        # endpoint (the host's port mapping): every discovery endpoint must pass the
        # organization's outbound policy (P12.4), so the operator registers that origin
        # too (runbook §1, private identity providers). Lattice never connects to it.
        allowed = operator(
            "outbound",
            "allow",
            "--org",
            WRENFIELD.slug,
            "--url",
            IDP,
            "--purpose",
            "oidc",
            "--reason",
            "Verification IdP (browser-facing authorization endpoint)",
        )
        case(
            "bootstrap: operator registers the private IdP's browser origin",
            allowed.returncode == 0,
        )

    # 2 ------------------------------------------------------------------------------------
    def first_admin(self) -> None:
        case(
            "sign-in: e-mail as identifier refused",
            Browser().sign_in(WRENFIELD, WRENFIELD.admin_email) == FAILED,
        )
        case("sign-in: identifier is case-exact", Browser().sign_in(WRENFIELD, "WRN-ADA") == FAILED)
        landed = self.admin.sign_in(WRENFIELD, WRENFIELD.admin_subject)
        me = self.admin.get("/v1/me").json()
        case(
            "sign-in: first administrator through the IdP, own organization",
            landed == "/ask"
            and me["organization"]["slug"] == WRENFIELD.slug
            and me["capabilities"] == ["admin"]
            and me["user"]["email"] == WRENFIELD.admin_email,
            landed,
        )
        case("sign-in: session valid on both API instances", self.admin.me_on_both() == {200})
        ok, why = setup_is(
            self.admin,
            {
                "organization": ("attention", "single_administrator", "/access"),
                "identity": ("complete", "verified", "/settings/identity"),
                "members": ("required", "only_administrators", "/access"),
                "provisioning": ("optional", "not_configured", "/settings/identity"),
                "ai_policy": ("optional", "evidence_only", "/settings/providers"),
                "sources": ("required", "no_sources", "/sources"),
                "accounts": ("optional", "not_needed", None),
                "first_answer": ("blocked", "sources_required", None),
            },
        )
        case("setup: truthful record after bootstrap (8 categories, destinations)", ok, why)

    # 3 ------------------------------------------------------------------------------------
    def provisioning(self) -> None:
        made = self.admin.post("/v1/admin/identity/scim/tokens", json={"reason": REASON})
        self.scim_token = made.json().get("revealed_token", "")
        ok, why = setup_is(
            self.admin, {"provisioning": ("optional", "token_not_used_yet", "/settings/identity")}
        )
        case(
            "provisioning: token created once; setup says not used yet",
            made.status_code == 201 and ok,
            why,
        )
        body = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "externalId": "wrn-tess",
            "userName": "tess.vidal@wrenfield.example",
            "name": {"givenName": "Tess", "familyName": "Vidal"},
            "emails": [{"value": "tess.vidal@wrenfield.example", "type": "work", "primary": True}],
            "active": True,
        }
        scim = httpx.post(
            f"{BASE}/api/scim/v2/Users",
            content=json.dumps(body),
            headers={
                "Authorization": f"Bearer {self.scim_token}",
                "Content-Type": "application/scim+json",
            },
            verify=TLS,
            timeout=30,
        )
        ok, why = setup_is(
            self.admin, {"provisioning": ("complete", "in_use", "/settings/identity")}
        )
        case(
            "provisioning: the IdP provisions a member over SCIM; setup in use",
            scim.status_code == 201 and ok,
            why,
        )
        tess = Browser()
        case(
            "provisioning: the provisioned member signs in",
            tess.sign_in(WRENFIELD, "wrn-tess") == "/ask",
        )

    # 4 ------------------------------------------------------------------------------------
    def members(self) -> None:
        for name, email, ident in (
            ("Riley Moss", "riley.moss@wrenfield.example", "wrn-riley"),
            ("Sam Okoro", "sam.okoro@wrenfield.example", "wrn-sam"),
        ):
            made = self.admin.post(
                "/v1/admin/access/members",
                json={"email": email, "name": name, "identifier": ident, "reason": REASON},
            )
            assert made.status_code == 201, made.text[:300]
            self.member_ids[ident] = made.json()["member"]["id"]
            member = made.json()["member"]
            case(
                f"members: {name} added by exact identifier, least access",
                (
                    member["is_admin"],
                    member["clearance"],
                    member["account_scope"],
                    member["assigned_accounts"],
                )
                == (False, "internal", "assigned", 0),
            )
        ok, why = setup_is(self.admin, {"members": ("complete", "members_active", "/access")})
        case("setup: members complete", ok, why)
        case(
            "sign-in: a member added by hand signs in",
            self.riley.sign_in(WRENFIELD, "wrn-riley") == "/ask",
        )
        case("sign-in: second member signs in", self.sam.sign_in(WRENFIELD, "wrn-sam") == "/ask")
        # Administrator capability: preview says no content access changes; then granted.
        tess_id = sql(
            "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id "
            f"WHERE m.organization_id = '{WRENFIELD.id}' "
            "AND u.email = 'tess.vidal@wrenfield.example'"
        )
        current = self.admin.get(f"/v1/admin/access/members/{tess_id}").json()
        preview = self.admin.post(
            f"/v1/admin/access/members/{tess_id}/administrator/preview",
            json={"administrator": True},
        )
        granted = self.admin.post(
            f"/v1/admin/access/members/{tess_id}/administrator",
            json={"administrator": True, "version": current["version"], "reason": REASON},
        )
        ok, why = setup_is(self.admin, {"organization": ("complete", "active", None)})
        case(
            "members: administrator capability granted (preview: no content access change)",
            preview.status_code == 200
            and preview.json().get("content_access_changes") is False
            and granted.status_code == 200
            and ok,
            why or str(granted.status_code),
        )

    # 5 ------------------------------------------------------------------------------------
    def accounts_setup(self) -> None:
        for key, name in (
            ("ashgrove-transit", "Ashgrove Transit"),
            ("brindle-foods", "Brindle Foods"),
        ):
            made = self.admin.post(
                "/v1/admin/access/accounts", json={"key": key, "name": name, "reason": REASON}
            )
            assert made.status_code == 201, made.text[:300]
            self.accounts[key] = made.json()
        brindle = self.accounts["brindle-foods"]
        edited = self.admin.patch(
            f"/v1/admin/access/accounts/{brindle['id']}",
            json={"version": brindle["version"], "aliases": ["Brindle"], "reason": REASON},
        )
        stale = self.admin.patch(
            f"/v1/admin/access/accounts/{brindle['id']}",
            json={"version": brindle["version"], "name": "Stale edit", "reason": REASON},
        )
        archived = self.admin.post(
            f"/v1/admin/access/accounts/{brindle['id']}/archive",
            json={"version": edited.json()["version"], "reason": REASON},
        )
        reactivated = self.admin.post(
            f"/v1/admin/access/accounts/{brindle['id']}/reactivate",
            json={"version": archived.json()["version"], "reason": REASON},
        )
        case(
            "accounts: created, edited, archived, reactivated; a stale edit refused",
            (edited.status_code, stale.status_code, archived.status_code, reactivated.status_code)
            == (200, 409, 200, 200),
            " ".join(str(r.status_code) for r in (edited, stale, archived, reactivated)),
        )

    # 6 ------------------------------------------------------------------------------------
    def providers(self) -> None:
        api = "/v1/admin/providers"
        page = self.admin.get(api).json()
        supported = {p["provider"]: p for p in page["supported"]}
        case(
            "providers: supported providers and trust classes shown; no test double in production",
            "fake" not in supported
            and supported.get("gemini", {}).get("trust_class") == "external_api"
            and page["step_up"]["available"] is True,
            json.dumps(sorted(supported)),
        )
        made = self.admin.post(
            api,
            json={
                "provider": "gemini",
                "model": "gemini-3.5-flash-lite",
                "training_opt_out": True,
                "reason": REASON,
            },
        )
        provider = next(p for p in made.json()["providers"] if p["provider"] == "gemini")
        dummy = "synthetic-not-a-key-" + secrets.token_hex(8)
        keyed = self.admin.put(
            f"{api}/{provider['id']}/credential",
            json={
                "api_key": dummy,
                "data_use": "paid",
                "expected_credential_id": None,
                "reason": REASON,
            },
        )
        ok, why = setup_is(
            self.admin, {"ai_policy": ("optional", "not_enabled", "/settings/providers")}
        )
        case(
            "providers: a credential is not permission (disabled, unapproved: nothing sent)",
            made.status_code == 201 and keyed.status_code == 200 and dummy not in keyed.text and ok,
            why,
        )

        def change(document: dict[str, Any]) -> httpx.Response:
            version = self.admin.get(api).json()["egress"]["version"]
            return self.admin.post(
                f"{api}/changes",
                json={"change": document, "policy_version": version, "reason": REASON},
            )

        enabled = change({"kind": "provider", "provider_id": provider["id"], "enabled": True})
        ok, why = setup_is(
            self.admin, {"ai_policy": ("attention", "not_approved", "/settings/providers")}
        )
        case(
            "providers: enabling needs no step-up; unapproved, nothing is sent",
            enabled.status_code == 200 and ok,
            why,
        )
        approve = {"kind": "provider", "provider_id": provider["id"], "approval": "internal"}
        direct = change(approve)
        case(
            "D-34: an escalation without a step-up is refused",
            direct.status_code == 422 and "step_up_required" in direct.text,
        )
        escalations_before = metric_total("lattice_egress_escalations_total", outcome="committed")

        def step_up(offsets: dict[str, int]) -> tuple[str, str]:
            idp_offsets(offsets, WRENFIELD, CORVANE)
            settings = self.admin.get(api).json()
            started = self.admin.post(
                f"{api}/step-ups",
                json={
                    "change": approve,
                    "policy_version": settings["egress"]["version"],
                    "reason": REASON,
                    "confirmation": settings["confirmation"],
                },
            )
            assert started.status_code == 201, started.text[:300]
            body = started.json()
            query = dict(
                p.split("=", 1) for p in body["authorization_url"].split("?", 1)[1].split("&")
            )
            assert (query["prompt"], query["max_age"]) == ("login", "0")
            approved = self.admin.client.get(
                f"{body['authorization_url']}&persona={WRENFIELD.admin_subject}"
            )
            back = self.admin.client.get(approved.headers["location"].removeprefix(BASE))
            assert back.headers["location"] == f"/settings/providers?step_up={body['step_up_id']}"
            outcome = self.admin.get(f"{api}/step-ups/{body['step_up_id']}").json()["outcome"]
            return body["step_up_id"], outcome

        _, stale = step_up({"auth_time": -400})
        case("D-34: auth_time older than 5 minutes refused", stale == "not_fresh", stale)
        grant, fresh = step_up({})
        applied = self.admin.post(f"{api}/step-ups/{grant}/apply")
        cells = {
            (c["provider_id"], c["level"]): c["permitted"]
            for c in applied.json().get("lattice", [])
        }
        case(
            "D-34: fresh re-authentication grants exactly this change, committed once",
            fresh == "granted"
            and applied.status_code == 200
            and cells.get((provider["id"], "internal")) is True,
        )
        case(
            "D-34: a used grant is refused",
            self.admin.post(f"{api}/step-ups/{grant}/apply").status_code == 409,
        )
        case(
            "D-34: committed escalation counted (metric)",
            metric_total("lattice_egress_escalations_total", outcome="committed")
            > escalations_before,
        )
        # Provider unusable -> evidence only: the credential revoked (a lowering).
        current = next(
            p for p in self.admin.get(api).json()["providers"] if p["id"] == provider["id"]
        )
        revoked = self.admin.post(
            f"{api}/{provider['id']}/credential/revoke",
            json={"expected_credential_id": current["credential"]["id"], "reason": REASON},
        )
        ok, why = setup_is(
            self.admin, {"ai_policy": ("attention", "credential_missing", "/settings/providers")}
        )
        case(
            "providers: credential revoked -> evidence only, setup says why",
            revoked.status_code == 200 and ok,
            why,
        )
        withdraw = change({"kind": "provider", "provider_id": provider["id"], "approval": None})
        voided_grant, regranted = step_up({})
        lowered = change({"kind": "provider", "provider_id": provider["id"], "enabled": False})
        voided = self.admin.post(f"{api}/step-ups/{voided_grant}/apply")
        case(
            "D-34: lowering needs none; a policy change voids an unused grant",
            withdraw.status_code == 200
            and regranted == "granted"
            and lowered.status_code == 200
            and voided.status_code == 409,
            f"{withdraw.status_code} {regranted} {lowered.status_code} {voided.status_code}",
        )
        case("D-34: the ordinary session is unaffected", self.admin.me_on_both() == {200})
        ok, why = setup_is(
            self.admin, {"ai_policy": ("optional", "not_enabled", "/settings/providers")}
        )
        case("providers: back to evidence only (disabled, unapproved)", ok, why)
        idp_clients(WRENFIELD, CORVANE)

    # 7 ------------------------------------------------------------------------------------
    def sources_setup(self) -> None:
        for key, extra in SOURCES.items():
            body = {
                "key": key,
                "name": f"{key.title()} export",
                "default_sensitivity": "internal",
                "account_scoped": False,
                "reason": REASON,
                **extra,
            }
            made = self.admin.post("/v1/admin/sources", json=body)
            assert made.status_code == 201, made.text[:300]
            self.sources[key] = made.json()
        path = self.admin.post(
            "/v1/admin/sources",
            json={**body, "key": "elsewhere", "path": "/srv/lattice/data", "reason": REASON},
        )
        case("sources: no host path accepted from the API", path.status_code == 422)
        ok, why = setup_is(
            self.admin,
            {
                "sources": ("required", "awaiting_import", "/sources"),
                "accounts": ("blocked", "sources_required", None),
            },
        )
        case("setup: sources awaiting their first export", ok, why)
        sync_refused = self.admin.post(f"/v1/admin/sources/{self.sources['handbook']['id']}/sync")
        case(
            "sources: sync refused before an import (import_required)",
            sync_refused.status_code == 422 and "import_required" in sync_refused.text,
        )

    # 8 ------------------------------------------------------------------------------------
    def uploads(self) -> None:
        accepted_before = metric_total("lattice_imports_total", outcome="accepted")
        checks_before = worker_metric(
            "lattice_jobs_total", kind="import_check", outcome="succeeded"
        )
        for key, source in self.sources.items():
            body, kind = EXPORTS[key]()
            sent = upload(self.admin, source["id"], body, kind)
            done = settle(self.admin, source["id"])
            case(
                f"upload: {key} through Caddy -> ImportStore -> check by the worker",
                sent.status_code == 201
                and sent.json()["import"]["current"] is True
                and done["job"]["kind"] == "import_check"
                and done["job"]["import_id"] == sent.json()["import"]["id"]
                and done["current_import"]["check"]["error_code"] is None,
                f"{sent.status_code}",
            )
            case(
                f"upload: {key} responses carry no names or content",
                clean(sent.text) and clean(json.dumps(done)),
            )
        case(
            "observability: accepted uploads and checks counted",
            metric_total("lattice_imports_total", outcome="accepted") >= accepted_before + 3
            and worker_metric("lattice_jobs_total", kind="import_check", outcome="succeeded")
            >= checks_before + 3,
        )
        support = record(self.admin, self.sources["support"]["id"])
        refs = {
            r["external_ref"]: r["records"]
            for r in support["current_import"]["check"]["unresolved_references"]
        }
        case(
            "check: unresolved ticket organizations reported before any sync",
            refs == {"7100001": 2, "7100002": 1},
            json.dumps(refs),
        )
        unmapped = self.admin.get("/v1/admin/access/account-references/unmapped").json()
        listed = {(i["external_ref"], i["records_waiting"]) for i in unmapped["items"]}
        case(
            "check: the references are listed for mapping",
            listed == {("7100001", 2), ("7100002", 1)},
        )
        on_disk = import_files(WRENFIELD)
        current = {
            record(self.admin, s["id"])["current_import"]["id"] for s in self.sources.values()
        }
        case(
            "ImportStore: each import under the organization's own directory, named by its ID",
            on_disk == current,
            f"{len(on_disk)} on disk",
        )

    # 9 ------------------------------------------------------------------------------------
    def first_sync(self) -> None:
        for key, source in self.sources.items():
            done = sync(self.admin, source["id"])
            run_import = sql(
                f"SELECT import_id FROM source_sync_runs WHERE id = '{done['job']['sync_run_id']}'"
            )
            case(
                f"sync: {key} by the worker from the exact import the job named",
                done["job"]["status"] == "succeeded"
                and run_import == done["current_import"]["id"] == done["job"]["import_id"],
            )
        ok, why = setup_is(
            self.admin,
            {
                "sources": ("complete", "synced", "/sources"),
                "accounts": ("attention", "unmapped_references", "/access/accounts/references"),
                "first_answer": ("required", "no_answer_yet", "/ask"),
            },
        )
        case("setup: synced; unmapped references; ready for a first answer", ok, why)
        # Riley is assigned Ashgrove now -- before the mapping -- so the next Ask proves
        # that a missing mapping never widens access, through the real read path.
        riley = self.member_ids["wrn-riley"]
        version = self.admin.get(f"/v1/admin/access/members/{riley}").json()["version"]
        assigned = self.admin.patch(
            f"/v1/admin/access/members/{riley}",
            json={
                "accounts": [self.accounts["ashgrove-transit"]["id"]],
                "version": version,
                "reason": REASON,
            },
        )
        early = ask(self.riley)
        refs = {e["display_ref"] for e in early["evidence"]}
        case(
            "fail closed: unmapped tickets unreadable even to the assigned member",
            assigned.status_code == 200 and not {r for r in refs if r and r.startswith("WRN-")},
            json.dumps(sorted(r for r in refs if r)),
        )
        tickets = sql(
            f"SELECT count(*) FROM documents WHERE source_id = '{self.sources['support']['id']}' "
            "AND status = 'active'"
        )
        case("fail closed: no ticket indexed without its account", tickets == "0", tickets)

    # 10 -----------------------------------------------------------------------------------
    def mapping(self) -> None:
        for ref, account in (("7100001", "ashgrove-transit"), ("7100002", "brindle-foods")):
            mapped = self.admin.post(
                "/v1/admin/access/account-references",
                json={
                    "source_id": self.sources["support"]["id"],
                    "ref_kind": "ticketing_organization",
                    "external_ref": ref,
                    "account_id": self.accounts[account]["id"],
                    "reason": REASON,
                },
            )
            case(
                f"mapping: {ref} -> {account}",
                mapped.status_code in (200, 201),
                str(mapped.status_code),
            )
        ok, why = setup_is(self.admin, {"accounts": ("attention", "sync_required", "/sources")})
        case("setup: mappings wait for the next sync", ok, why)
        sync(self.admin, self.sources["support"]["id"])
        ok, why = setup_is(self.admin, {"accounts": ("complete", "mapped", "/access/accounts")})
        case("setup: every reference mapped after the sync", ok, why)

    # 11 -----------------------------------------------------------------------------------
    def first_ask(self) -> None:
        answer = ask(self.riley)
        refs = {e["display_ref"] for e in answer["evidence"]}
        case(
            "Ask: the assigned member gets authorized evidence (evidence only, no model)",
            answer["status"] == "evidence_only" and "WRN-1001" in refs and "WRN-1003" not in refs,
            f"{answer['status']} {sorted(r for r in refs if r)}",
        )
        ticket = next(e for e in answer["evidence"] if e["display_ref"] == "WRN-1001")
        self.evidence_id, self.trace_id = ticket["evidence_id"], answer["trace_id"]
        opened = self.riley.get(f"/v1/evidence/{self.evidence_id}")
        case("Ask: the evidence opens", opened.status_code == 200 and "4.2.7" in opened.text)
        trace = self.riley.get(f"/v1/traces/{self.trace_id}")
        case(
            "Ask: the trace opens for the asker",
            trace.status_code == 200 and trace.json()["view"] == "asker",
        )
        ok, why = setup_is(self.admin, {"first_answer": ("complete", "evidence_returned", None)})
        case("setup: first answer complete (evidence returned, no generation provider)", ok, why)

    # 12 -----------------------------------------------------------------------------------
    def denied_ask(self) -> None:
        for who, person in (("member without access", self.sam), ("administrator", self.admin)):
            denied = ask(person)
            case(
                f"denied Ask ({who}): nothing retrieved, nothing quoted",
                denied["status"] == "insufficient_evidence"
                and denied["evidence"] == []
                and clean(json.dumps(denied)),
                denied["status"],
            )
            hidden = person.get(f"/v1/evidence/{self.evidence_id}")
            missing = person.get(f"/v1/evidence/{uuid.uuid4()}")
            case(
                f"denied evidence ({who}): same 404 as a random ID",
                hidden.status_code == missing.status_code == 404
                and hidden.json()["error"]["code"] == missing.json()["error"]["code"],
            )
        case(
            "denied trace (member): 404",
            self.sam.get(f"/v1/traces/{self.trace_id}").status_code == 404,
        )
        operational = self.admin.get(f"/v1/traces/{self.trace_id}")
        case(
            "trace (administrator): operational view, no question, answer or evidence",
            operational.status_code == 200
            and operational.json()["view"] == "operational"
            and clean(operational.text, "Ashgrove"),
        )
        case(
            "accounts: no side channel (Sam sees no account)",
            self.sam.get("/v1/accounts").json()["accounts"] == [],
        )
        views = [
            "/v1/admin/setup",
            "/v1/admin/sources",
            "/v1/admin/access/account-references/unmapped",
            "/v1/admin/audit-events",
            *(f"/v1/admin/sources/{s['id']}" for s in self.sources.values()),
            *(f"/v1/admin/sources/{s['id']}/failures" for s in self.sources.values()),
        ]
        leaks = [v for v in views if not clean(self.admin.get(v).text)]
        case(
            "privacy: administrator setup/source/validation views carry no content "
            "or private names",
            not leaks,
            ",".join(leaks),
        )

    # 13 -----------------------------------------------------------------------------------
    def ingress(self) -> None:
        handbook = self.sources["handbook"]["id"]
        limits = record(self.admin, handbook)["upload"]
        case(
            "ingress: production upload limits unchanged (100 MiB)",
            limits.get("max_upload_bytes") == 100 * MIB,
            json.dumps(limits)[:200],
        )
        big = zipped(DATA / "documents", extra={"assets/padding.bin": os.urandom(3 * MIB)})
        through = upload(self.admin, handbook, big, "application/zip")
        case(
            "ingress: the dedicated upload route takes a 3 MiB export (ignored entries counted)",
            through.status_code == 201 and through.json()["import"]["ignored_entries"] == 1,
            str(through.status_code),
        )
        settle(self.admin, handbook)
        ordinary = self.admin.post(f"/v1/admin/sources/{handbook}/check", content=b"x" * (2 * MIB))
        case(
            "ingress: ordinary routes keep the 1 MB edge cap",
            ordinary.status_code == 413,
            str(ordinary.status_code),
        )
        api_cap = self.admin.post(
            "/v1/ask",
            content=json.dumps({"question": "x" * 100_000}),
            headers={"Content-Type": "application/json"},
        )
        case(
            "ingress: the API's 64 KiB cap still applies elsewhere",
            api_cap.status_code == 413 and "request_too_large" in api_cap.text,
            str(api_cap.status_code),
        )
        slip = zipped(DATA / "slack", extra={"../onboard-escape.json": b"[]"})
        refused = upload(self.admin, self.sources["slack"]["id"], slip, "application/zip")
        case(
            "ingress: a path escaping the import root is refused; nothing written outside",
            refused.status_code == 422
            and "unsafe_path" in refused.text
            and not escaped_anywhere("onboard-escape"),
        )
        cross = upload(
            self.noor_signed_in(), self.sources["support"]["id"], b"PK\x03\x04", "application/zip"
        )
        nowhere = upload(self.noor, str(uuid.uuid4()), b"PK\x03\x04", "application/zip")
        case(
            "ingress: another organization's source answers as a random ID",
            cross.status_code == nowhere.status_code == 404
            and cross.json()["error"]["code"] == nowhere.json()["error"]["code"],
        )

        # Boundaries with a deliberately small, test-only configuration (the smallest the
        # settings accept), so nothing near 100 MiB is transferred.
        roll_api(str(SMALL_IMPORTS))
        try:
            fresh = self.noor.post(
                "/v1/admin/sources",
                json={
                    "key": "boundary",
                    "name": "Boundary",
                    "connector_type": "document_repository",
                    "default_sensitivity": "internal",
                    "authority": "working",
                    "account_scoped": False,
                    "reason": REASON,
                },
            ).json()["id"]
            declared = upload(
                self.noor, fresh, b"PK\x03\x04" + os.urandom(2 * MIB), "application/zip"
            )
            case(
                "boundary: an upload over the route's limit is 413 too_large",
                declared.status_code == 413 and "too_large" in declared.text,
                str(declared.status_code),
            )

            def chunks() -> Iterator[bytes]:
                yield b"PK\x03\x04"
                for _ in range(8):
                    yield os.urandom(256 * 1024)

            streamed = upload(self.noor, fresh, chunks(), "application/zip")
            case(
                "boundary: a chunked body (no length) is counted while it streams",
                streamed.status_code == 413 and "too_large" in streamed.text,
                str(streamed.status_code),
            )
            case("boundary: the refused uploads leave no files", import_files(CORVANE) == set())
            interrupted_upload(self.noor, fresh, declared=900 * 1024, sent=300 * 1024)
            gone = wait_for(lambda: latest_import(fresh) == ("deleted", "upload_incomplete"), 30)
            case(
                "boundary: an interrupted upload is refused (upload_incomplete) and removed",
                gone and import_files(CORVANE) == set(),
                "|".join(latest_import(fresh)),
            )
            full = upload(self.admin, handbook, zipped(DATA / "documents"), "application/zip")
            case(
                "boundary: a storage refusal is a safe 503 (storage_full), no internals",
                full.status_code == 503
                and "storage_full" in full.text
                and "Traceback" not in full.text
                and "/srv/" not in full.text,
                str(full.status_code),
            )
        finally:
            roll_api()
        restored = record(self.admin, handbook)["upload"]["max_upload_bytes"]
        case("boundary: the stack's own limits restored", restored == 100 * MIB)

    def noor_signed_in(self) -> Browser:
        if self.noor.session is None:
            self.noor.sign_in(CORVANE, CORVANE.admin_subject)
        return self.noor

    # 14 -----------------------------------------------------------------------------------
    def replacement(self) -> None:
        slack = self.sources["slack"]["id"]
        current = record(self.admin, slack)["current_import"]["id"]
        rejected_before = metric_total("lattice_imports_total", outcome="rejected")
        for label, body in (
            ("not an archive", b"plain text"),
            ("a truncated archive", zipped(DATA / "slack")[:-300]),
            ("an export missing its layout", zipped(DATA / "slack", skip="users.json")),
        ):
            refused = upload(self.admin, slack, body, "application/zip")
            case(
                f"replacement: {label} refused, current import kept",
                refused.status_code == 422
                and record(self.admin, slack)["current_import"]["id"] == current,
                str(refused.status_code),
            )
        case(
            "observability: refused uploads counted",
            metric_total("lattice_imports_total", outcome="rejected") >= rejected_before + 3,
        )
        broken = zipped(DATA / "slack", extra={"users.json": b"{not json"}, skip="users.json")
        accepted = upload(self.admin, slack, broken, "application/zip")
        checked = settle(self.admin, slack)
        blocked = self.admin.post(f"/v1/admin/sources/{slack}/sync")
        still = ask(self.riley)
        case(
            "replacement: an unreadable export is checked, blocks syncs, keeps the evidence",
            accepted.status_code == 201
            and checked["current_import"]["check"]["error_code"] is not None
            and checked["next_action"] == "replace_import"
            and blocked.status_code == 422
            and "import_invalid" in blocked.text
            and any((e["display_ref"] or "").startswith("WRN-") for e in still["evidence"]),
            checked["next_action"],
        )
        broken_record, broken_why = setup_is(
            self.admin, {"sources": ("attention", "import_invalid", "/sources")}
        )
        # Recovery is a new upload of a good export: the superseded one is not kept.
        gone = wait_for(
            lambda: sql(f"SELECT status FROM source_imports WHERE id = '{current}'") == "deleted",
            120,
            step=2,
        )
        case(
            "replacement: the superseded import is removed once no job reads it",
            gone and current not in import_files(WRENFIELD),
        )
        body, kind = EXPORTS["slack"]()
        upload(self.admin, slack, body, kind)
        settle(self.admin, slack)
        case(
            "replacement: recovered by uploading a good export, then synced",
            sync(self.admin, slack)["job"]["status"] == "succeeded",
        )
        recovered, recovered_why = setup_is(
            self.admin, {"sources": ("complete", "synced", "/sources")}
        )
        case(
            "setup record (P12.8): an unreadable current export is attention·import_invalid, "
            "complete·synced again once recovered",
            broken_record and recovered,
            broken_why or recovered_why,
        )

        # A replacement while a sync is queued: the sync reads the import it was given.
        handbook = self.sources["handbook"]["id"]
        compose("pause", "worker")
        try:
            queued = self.admin.post(f"/v1/admin/sources/{handbook}/sync").json()
            replaced = upload(self.admin, handbook, zipped(DATA / "documents"), "application/zip")
        finally:
            compose("unpause", "worker")
        done = settle(self.admin, handbook, queued["id"])
        run_import = sql(
            f"SELECT import_id FROM source_sync_runs WHERE id = '{done['job']['sync_run_id']}'"
        )
        case(
            "replacement while a sync is queued: the sync reads its own import; the new one waits",
            replaced.status_code == 201
            and replaced.json()["check_queued"] is False
            and run_import == queued["import_id"] != replaced.json()["import"]["id"]
            and done["current_import"]["id"] == replaced.json()["import"]["id"],
        )

    # 15 -----------------------------------------------------------------------------------
    def lifecycle(self) -> None:
        slack = self.sources["slack"]
        version = record(self.admin, slack["id"])["version"]
        paused = self.admin.post(
            f"/v1/admin/sources/{slack['id']}/pause", json={"version": version, "reason": REASON}
        )
        refused = self.admin.post(f"/v1/admin/sources/{slack['id']}/sync")
        searchable = ask(self.riley, "What did the Ashgrove war room say about the depot gateways?")
        stale = self.admin.post(
            f"/v1/admin/sources/{slack['id']}/resume", json={"version": version, "reason": REASON}
        )
        resumed = self.admin.post(
            f"/v1/admin/sources/{slack['id']}/resume",
            json={"version": paused.json()["version"], "reason": REASON},
        )
        case(
            "lifecycle: pause refuses syncs, content stays searchable; "
            "stale resume refused; resume",
            paused.status_code == 200
            and refused.status_code == 422
            and "source_not_active" in refused.text
            and any(e["source_type"] == "chat" for e in searchable["evidence"])
            and stale.status_code == 409
            and resumed.status_code == 200,
            f"{paused.status_code} {refused.status_code} {stale.status_code} {resumed.status_code}",
        )
        case(
            "lifecycle: sync after resume",
            sync(self.admin, slack["id"])["job"]["status"] == "succeeded",
        )
        # The private channel is mapped to Ashgrove first: the disconnect must take this
        # source's mappings with it and leave the account.
        ashgrove = self.accounts["ashgrove-transit"]["id"]
        channel = self.admin.post(
            "/v1/admin/access/account-references",
            json={
                "source_id": slack["id"],
                "ref_kind": "chat_channel",
                "external_ref": "G0WASHWAR1",
                "account_id": ashgrove,
                "reason": REASON,
            },
        )
        preview = self.admin.get(f"/v1/admin/sources/{slack['id']}/disconnect-preview").json()
        imports = record(self.admin, slack["id"])["current_import"]["id"]
        gone = self.admin.post(
            f"/v1/admin/sources/{slack['id']}/disconnect",
            json={
                "confirm_key": "slack",
                "version": record(self.admin, slack["id"])["version"],
                "reason": REASON,
            },
        )
        after = ask(self.riley, "What did the Ashgrove war room say about the depot gateways?")
        event = sql(
            "SELECT count(*) FROM audit_events WHERE action = 'source.disconnected' "
            f"AND organization_id = '{WRENFIELD.id}'"
        )
        case(
            "lifecycle: disconnect previews counts, is audited, removes only that source's records",
            preview["documents"] >= 1
            and gone.status_code == 200
            and event == "1"
            and not any(e["source_type"] == "chat" for e in after["evidence"])
            and any((e["display_ref"] or "").startswith("WRN-") for e in after["evidence"]),
        )
        left = sql(f"SELECT count(*) FROM account_external_refs WHERE source_id = '{slack['id']}'")
        case(
            "lifecycle: the source's account mappings go with it; the account stays",
            channel.status_code in (200, 201)
            and preview["account_mappings"] == 1
            and left == "0"
            and self.admin.get(f"/v1/admin/access/accounts/{ashgrove}").status_code == 200,
            f"{channel.status_code} {preview['account_mappings']} {left}",
        )
        case(
            "lifecycle: nobody gains access by a disconnect",
            ask(self.sam)["evidence"] == [] and ask(self.admin)["evidence"] == [],
        )
        removed = wait_for(lambda: imports not in import_files(WRENFIELD), 120, step=2)
        case("lifecycle: the disconnected source's imports are removed by the worker", removed)
        case("lifecycle: other sources' imports kept", len(import_files(WRENFIELD)) >= 2)
        case(
            "lifecycle: the trace still opens (re-authorized on read)",
            self.riley.get(f"/v1/traces/{self.trace_id}").status_code == 200,
        )

    # 16 -----------------------------------------------------------------------------------
    def operator_sources(self) -> None:
        handbook = self.sources["handbook"]["id"]
        version = sql(f"SELECT version FROM sources WHERE id = '{handbook}'")
        mappings = sql(
            "SELECT count(*) FROM account_external_refs "
            f"WHERE source_id = '{self.sources['support']['id']}'"
        )
        own = operator(
            "sync",
            "--org",
            WRENFIELD.slug,
            "--sources",
            "/srv/lattice/data/synthetic/onboarding/sources.yaml",
            "--embedding",
            "none",
        )
        managed = sql(
            "SELECT managed_by FROM sources WHERE key = 'operator-handbook' "
            f"AND organization_id = '{WRENFIELD.id}'"
        )
        case(
            "operator: a sources file still syncs an operator-managed source",
            own.returncode in (0, 3) and managed == "operator",
            last(own.stderr),
        )
        taken = operator(
            "sync",
            "--org",
            WRENFIELD.slug,
            "--sources",
            "/srv/lattice/data/synthetic/onboarding/sources-conflict.yaml",
            "--embedding",
            "none",
        )
        case(
            "operator: a sources file cannot take over a product-managed source",
            taken.returncode != 0
            and "managed_in_product" in taken.stdout + taken.stderr
            and sql(f"SELECT version FROM sources WHERE id = '{handbook}'") == version,
        )
        case(
            "operator: administrator-created mappings preserved",
            sql(
                "SELECT count(*) FROM account_external_refs "
                f"WHERE source_id = '{self.sources['support']['id']}'"
            )
            == mappings
            == "2",
        )
        operator_source = sql(
            "SELECT id FROM sources WHERE key = 'operator-handbook' "
            f"AND organization_id = '{WRENFIELD.id}'"
        )
        refused = upload(self.admin, operator_source, zipped(DATA / "documents"), "application/zip")
        case(
            "operator: an operator-managed source takes no upload",
            refused.status_code == 422 and "operator_managed" in refused.text,
        )

    # 17 -----------------------------------------------------------------------------------
    def tenants(self) -> None:
        made = org_create(CORVANE)
        CORVANE.id = sql(f"SELECT id FROM organizations WHERE slug = '{CORVANE.slug}'")
        key = re.search(r"/sso/([A-Za-z0-9_-]+)", made.stdout)
        assert key, made.stderr
        CORVANE.login_key = key.group(1)
        operator(
            "outbound",
            "allow",
            "--org",
            CORVANE.slug,
            "--url",
            IDP,
            "--purpose",
            "oidc",
            "--reason",
            "Verification IdP (browser-facing authorization endpoint)",
        )
        case(
            "tenants: a Wrenfield identity is nobody at Corvane",
            Browser().sign_in(CORVANE, "wrn-riley") == FAILED,
        )
        case(
            "tenants: Corvane's administrator signs in",
            self.noor.sign_in(CORVANE, CORVANE.admin_subject) == "/ask",
        )
        ok, why = setup_is(
            self.noor,
            {
                "sources": ("required", "no_sources", "/sources"),
                "members": ("required", "only_administrators", "/access"),
                "ai_policy": ("optional", "evidence_only", "/settings/providers"),
            },
        )
        case("tenants: setup status is the organization's own", ok, why)
        case(
            "tenants: no sources, accounts or providers of the other organization",
            self.noor.get("/v1/admin/sources").json()["sources"] == []
            and self.noor.get("/v1/admin/providers").json()["providers"] == []
            and self.noor.get("/v1/admin/access/accounts").json()["items"] == [],
        )
        random = str(uuid.uuid4())
        statuses = set()
        for target in (self.sources["support"]["id"], random):
            for path in (
                f"/v1/admin/sources/{target}",
                f"/v1/admin/sources/{target}/disconnect-preview",
                f"/v1/admin/sources/{target}/failures",
            ):
                statuses.add(self.noor.get(path).status_code)
        case(
            "tenants: another organization's source IDs answer exactly as random ones",
            statuses == {404},
        )
        mapping = {
            "ref_kind": "ticketing_organization",
            "external_ref": "7100001",
            "account_id": self.accounts["ashgrove-transit"]["id"],
            "reason": REASON,
        }
        cross = self.noor.post(
            "/v1/admin/access/account-references",
            json={**mapping, "source_id": self.sources["support"]["id"]},
        )
        nowhere = self.noor.post(
            "/v1/admin/access/account-references", json={**mapping, "source_id": random}
        )
        case(
            "tenants: references cannot be attached across organizations",
            cross.status_code == nowhere.status_code
            and cross.json()["error"]["code"] == nowhere.json()["error"]["code"]
            and cross.status_code >= 400,
        )
        case(
            "tenants: evidence and traces of the other organization are 404",
            self.noor.get(f"/v1/evidence/{self.evidence_id}").status_code == 404
            and self.noor.get(f"/v1/traces/{self.trace_id}").status_code == 404,
        )
        riley = self.member_ids["wrn-riley"]
        member_statuses = {
            self.noor.get(f"/v1/admin/access/members/{target}").status_code
            for target in (riley, str(uuid.uuid4()))
        }
        case(
            "tenants: another organization's member IDs answer as random ones",
            member_statuses == {404},
        )
        case(
            "tenants: the audit ledger names nothing of the other organization",
            clean(self.noor.get("/v1/admin/audit-events").text, WRENFIELD.slug, "Ashgrove"),
        )
        case(
            "tenants: credentials do not cross (the vault secret sealed per connection)",
            sql(f"SELECT count(*) FROM encrypted_secrets WHERE organization_id = '{CORVANE.id}'")
            == "1",
        )

    # 18 -----------------------------------------------------------------------------------
    def member_lifecycle(self) -> None:
        sam = self.member_ids["wrn-sam"]
        held = self.sam.session
        version = self.admin.get(f"/v1/admin/access/members/{sam}").json()["version"]
        suspended = self.admin.patch(
            f"/v1/admin/access/members/{sam}",
            json={"status": "suspended", "version": version, "reason": REASON},
        )
        case(
            "members: suspension ends the session on both instances",
            suspended.status_code == 200 and self.sam.me_on_both() == {401},
        )
        case(
            "members: a suspended member cannot sign in",
            Browser().sign_in(WRENFIELD, "wrn-sam") == FAILED,
        )
        restored = self.admin.patch(
            f"/v1/admin/access/members/{sam}",
            json={"status": "active", "version": suspended.json()["version"], "reason": REASON},
        )
        again = Browser()
        case(
            "members: reactivated -- the old session stays ended, sign-in works",
            restored.status_code == 200
            and held is not None
            and again.sign_in(WRENFIELD, "wrn-sam") == "/ask"
            and self.sam.me_on_both() == {401},
        )
        case("members: still no access after reactivation", ask(again)["evidence"] == [])

    # 19 -----------------------------------------------------------------------------------
    def logs(self) -> None:
        text = compose(
            "logs", "--no-color", "--since", "30m", "api1", "api2", "worker", check=False
        )
        secrets_seen = [
            s for s in (WRENFIELD.secret, CORVANE.secret, self.scim_token) if s and s in text
        ]
        case("observability: no secret plaintext in API or worker logs", not secrets_seen)
        case("observability: no protected content in API or worker logs", clean(text))
        case(
            "observability: import refusals and accepted imports logged with codes only",
            "sources.import_rejected" in text and "sources.import_accepted" in text,
        )


def main() -> int:
    t0 = time.monotonic()
    journey = Journey()
    steps = [
        journey.bootstrap,
        journey.first_admin,
        journey.provisioning,
        journey.members,
        journey.accounts_setup,
        journey.providers,
        journey.sources_setup,
        journey.uploads,
        journey.first_sync,
        journey.mapping,
        journey.first_ask,
        journey.denied_ask,
        journey.tenants,
        journey.ingress,
        journey.replacement,
        journey.lifecycle,
        journey.operator_sources,
        journey.member_lifecycle,
        journey.logs,
    ]
    for step in steps:
        print(f"--- {step.__name__}", flush=True)
        try:
            step()
        except Exception as exc:  # a broken step stops the journey; later ones depend on it
            case(f"{step.__name__}: completed", False, f"{type(exc).__name__}: {str(exc)[:300]}")
            break
    try:
        compose("unpause", "worker", check=False)
        idp_clients()
    except Exception:  # noqa: S110 - best effort; the summary matters more
        pass
    print(f"\norganizations: {WRENFIELD.slug}, {CORVANE.slug}")
    return finish(t0)


if __name__ == "__main__":
    sys.exit(main())
